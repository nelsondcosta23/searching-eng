"""
ITJobs Scraper — uses the official free read-only JSON API at api.itjobs.pt.

Auth: ITJOBS_API_KEY env var.
Endpoint:  https://api.itjobs.pt/job/search.json?api_key=...&q=...&limit=...&page=...
Response shape:
    {total, page, limit, query, results: [{id, title, company:{name}, body,
        types:[{name}], contracts:[{name}], country, publishedAt, slug,
        workModel, salaryMin, salaryMax}, ...]}
"""
import os
import re
import sys
import time
import random
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

PLATAFORMA = "ITJobs"
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))

ITJOBS_API_KEY    = os.environ.get('ITJOBS_API_KEY', '').strip()
ITJOBS_PAGE_LIMIT = int(os.environ.get('ITJOBS_PAGE_LIMIT', '50'))
ITJOBS_MAX_PAGES  = int(os.environ.get('ITJOBS_MAX_PAGES', '5'))

API_BASE = "https://api.itjobs.pt"

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from automation.profile_fetcher import get_negative_keywords, get_negative_companies, get_user_id, strict_keyword_match, get_local_profile
    _profile           = get_local_profile()
    KEYWORDS           = [t.lower() for t in (_profile.get('job_titles') or [])] or ["developer"]
    NEGATIVE_KEYWORDS  = get_negative_keywords()
    NEGATIVE_COMPANIES = get_negative_companies()
    USER_ID            = get_user_id()
except ImportError as _e:
    print(f"FATAL: profile_fetcher import failed: {_e}. Aborting itjobs_scraper.", file=__import__('sys').stderr)
    __import__('sys').exit(1)

from automation.db_helper import save_job, job_exists
from scrapers._shared import negative_keyword_match, make_session, extract_seniority, strip_html

HEADERS = {
    'User-Agent': 'SearchingEng-ITJobsScraper/1.0',
    'Accept': 'application/json',
}

# ITJobs API expects its own UA; replace defaults rather than augment.
session = make_session(retries=3, headers=HEADERS)

# workModel mapping observed on ITJobs (best-effort labels for observacoes)
_WORK_MODEL_LABELS = {1: "Presencial", 2: "Remoto", 3: "Híbrido"}



def _format_salary(smin, smax) -> str:
    """Returns '€6500–€9000', '€6500+' or '' depending on what's present."""
    if smin and smax:
        return f"€{smin}–€{smax}"
    if smin:
        return f"€{smin}+"
    if smax:
        return f"até €{smax}"
    return ''


def _normalize_job(raw: dict, fallback_query: str) -> dict:
    """Maps a raw ITJobs API job into the project's schema fields."""
    job_id = raw.get('id')

    titulo = (raw.get('title') or '').strip()
    company = raw.get('company') or {}
    empresa = (company.get('name') if isinstance(company, dict) else company) or 'Confidential'

    types_list = raw.get('types') or []
    contracts_list = raw.get('contracts') or []
    tipo_parts = [t.get('name') for t in types_list if isinstance(t, dict) and t.get('name')]
    tipo_parts += [c.get('name') for c in contracts_list if isinstance(c, dict) and c.get('name')]
    tipo_contrato = ", ".join(tipo_parts)

    work_model = raw.get('workModel')
    work_label = _WORK_MODEL_LABELS.get(work_model, '')

    country = raw.get('country') or 'PT'
    # City names come from the detail endpoint; filled later before save.
    # For now build a fallback from country + work model.
    if work_label == "Remoto":
        localizacao = f"{country} (Remoto)"
    elif work_label:
        localizacao = f"{country} ({work_label})"
    else:
        localizacao = country

    salario = _format_salary(raw.get('salaryMin'), raw.get('salaryMax'))

    descricao = strip_html(raw.get('body') or '')

    data_pub = raw.get('publishedAt') or 'Recent'

    slug = raw.get('slug') or ''
    if job_id and slug:
        link = f"https://www.itjobs.pt/oferta/{job_id}/{slug}"
    elif job_id:
        link = f"https://www.itjobs.pt/oferta/{job_id}"
    else:
        link = ''

    obs_parts = []
    if work_label:    obs_parts.append(f"Modelo: {work_label}")
    if tipo_contrato: obs_parts.append(f"Tipo: {tipo_contrato}")
    if salario:       obs_parts.append(f"Salário: {salario}")
    if fallback_query: obs_parts.append(f"Query: {fallback_query}")
    observacoes = " | ".join(obs_parts)

    return {
        'id_externo': str(job_id) if job_id else None,
        'titulo': titulo,
        'empresa': empresa,
        'localizacao': localizacao,
        'link': link,
        'data_pub': data_pub,
        'tipo_contrato': tipo_contrato,
        'salario': salario,
        'descricao_completa': descricao,
        'observacoes': observacoes,
    }


def _fetch_location(job_id: int) -> str:
    """Fetches city names for a job via the detail endpoint.

    The search endpoint only returns country code; the detail endpoint has
    a `locations` array with district/city names like 'Lisboa', 'Porto'.
    Only called for jobs we're actually going to save (not wasted on rejects).
    """
    try:
        resp = session.get(
            f"{API_BASE}/job/get.json",
            params={'api_key': ITJOBS_API_KEY, 'id': job_id},
            headers=HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            return ''
        data = resp.json() or {}
        locs = data.get('locations') or []
        names = [l.get('name') for l in locs if isinstance(l, dict) and l.get('name')]
        return ', '.join(names)
    except Exception:
        return ''


def _fetch_page(query: str, page: int, work_model: int = 0) -> dict:
    # Limit to jobs published in the last 7 days. Reduces response size and
    # avoids burning pages on content already in the DB from previous runs.
    since = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    params = {
        'api_key': ITJOBS_API_KEY,
        'q': query,
        'limit': ITJOBS_PAGE_LIMIT,
        'page': page,
        'publishedAt.from': since,
    }
    if work_model:
        params['workModel'] = work_model
    try:
        resp = session.get(f"{API_BASE}/job/search.json", params=params, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"  [ITJobs] HTTP {resp.status_code} for q='{query}' page={page}")
            return {}
        return resp.json() or {}
    except Exception as e:
        print(f"  [ITJobs] Error q='{query}' page={page}: {e}")
        return {}


def _build_queries() -> list[str]:
    """One distinct lowercase query per target role."""
    seen = set()
    out = []
    for role in KEYWORDS:
        clean = (role or '').strip().lower()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def iniciar_scraper_itjobs():
    print(f"\n{'='*50}")
    print(f"  Starting Scraper: {PLATAFORMA}")
    print(f"  Keywords: {KEYWORDS}")
    print(f"{'='*50}")

    if not ITJOBS_API_KEY:
        print("  ❌ ITJOBS_API_KEY not set. Skipping ITJobs scraper.")
        return

    queries = _build_queries()
    if not queries:
        print("  ⚠ No target roles configured. Skipping.")
        return

    print(f"  Generated {len(queries)} queries.")

    vagas_inseridas = 0

    for q in queries:
        print(f"\n[ITJobs] Search: {q}")
        for page in range(1, ITJOBS_MAX_PAGES + 1):
            time.sleep(random.uniform(0.5, 1.0))
            # No workModel filter — return all work models and let the scorer
            # handle the remote preference. Filtering by workModel=2 here causes
            # leadership queries ("cto", "chief technology officer") to return 0
            # results since most PT CTO roles are hybrid, not fully remote.
            data = _fetch_page(q, page=page)
            results = data.get('results') if isinstance(data, dict) else None

            if not results:
                if page == 1:
                    print("  → No jobs for this query.")
                break

            # --- Pass 1: filter ---
            to_save = []
            already_in_db = 0
            for raw in results:
                job = _normalize_job(raw, fallback_query=q)
                if not job['titulo'] or not job['link']:
                    continue
                texto_busca = job['titulo'].lower()
                if not strict_keyword_match(texto_busca, KEYWORDS):
                    continue
                blocked_kw = negative_keyword_match(texto_busca, NEGATIVE_KEYWORDS)
                if blocked_kw:
                    print(f"  [BLOCKED] '{job['titulo']}' contains a negative keyword ({blocked_kw}).")
                    continue
                if NEGATIVE_COMPANIES and any(nc in job['empresa'].lower() for nc in NEGATIVE_COMPANIES):
                    continue
                if job_exists(job['link']):
                    already_in_db += 1
                    continue
                to_save.append(job)

            # If the whole page passed keyword filters but everything was already
            # in the DB, older pages will also be fully known — stop early.
            if not to_save and already_in_db > 0 and page > 1:
                print(f"  → Page {page}: all {already_in_db} matching jobs already in DB. Stopping pagination.")
                break

            # --- Pass 2: batch location fetch (≤5 parallel calls) ---
            jobs_with_id = [j for j in to_save if j['id_externo']]
            if jobs_with_id:
                with ThreadPoolExecutor(max_workers=5) as ex:
                    futures = {
                        ex.submit(_fetch_location, int(j['id_externo'])): j
                        for j in jobs_with_id
                    }
                    for future in as_completed(futures):
                        job = futures[future]
                        try:
                            # timeout=12 > request timeout=10 so the request always
                            # resolves before the future times out (avoids thread leaks)
                            city = future.result(timeout=12)
                        except Exception:
                            city = ''
                        if city:
                            work_suffix = ''
                            if '(Remoto)' in job['localizacao']:
                                work_suffix = ' (Remoto)'
                            elif '(Híbrido)' in job['localizacao']:
                                work_suffix = ' (Híbrido)'
                            job['localizacao'] = city + work_suffix

            # --- Pass 3: save ---
            for job in to_save:
                print(f"  [NEW JOB] {job['titulo']} @ {job['empresa']} | {job['localizacao']}")
                salvo = save_job(
                    user_id=USER_ID,
                    plataforma=PLATAFORMA,
                    id_externo=job['id_externo'],
                    titulo=job['titulo'],
                    empresa=job['empresa'],
                    localizacao=job['localizacao'],
                    link=job['link'],
                    data_pub=job['data_pub'],
                    categoria=q,
                    descricao_completa=job['descricao_completa'],
                    observacoes=job['observacoes'],
                    salario=job['salario'],
                    tipo_contrato=job['tipo_contrato'],
                    nivel_experiencia=extract_seniority(job['titulo'], job['descricao_completa']),
                )
                if salvo:
                    vagas_inseridas += 1
                    print("    ✅ Saved.")
                if MAX_JOBS > 0 and vagas_inseridas >= MAX_JOBS:
                    break

            if MAX_JOBS > 0 and vagas_inseridas >= MAX_JOBS:
                break

            # Last page reached when results returned fewer than the requested limit.
            if len(results) < ITJOBS_PAGE_LIMIT:
                break

        if MAX_JOBS > 0 and vagas_inseridas >= MAX_JOBS:
            print(f"  [LIMIT REACHED] Max {MAX_JOBS} jobs. Stopping.")
            break

    print(f"\n{'='*50}")
    print(f"  ITJobs Done: {vagas_inseridas} new jobs saved.")
    print(f"{'='*50}")


if __name__ == '__main__':
    iniciar_scraper_itjobs()
