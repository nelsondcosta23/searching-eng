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

PLATAFORMA = "ITJobs"
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))

ITJOBS_API_KEY    = os.environ.get('ITJOBS_API_KEY', '').strip()
ITJOBS_PAGE_LIMIT = int(os.environ.get('ITJOBS_PAGE_LIMIT', '50'))
ITJOBS_MAX_PAGES  = int(os.environ.get('ITJOBS_MAX_PAGES', '5'))

API_BASE = "https://api.itjobs.pt"

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from automation.profile_fetcher import get_target_roles, get_negative_keywords, get_user_id, strict_keyword_match
    KEYWORDS = [r.lower() for r in get_target_roles()]
    NEGATIVE_KEYWORDS = get_negative_keywords()
    USER_ID = get_user_id()
except ImportError:
    print("Warning: Could not load profile_fetcher. Using default keywords.")
    KEYWORDS = ["python", "developer"]
    NEGATIVE_KEYWORDS = []
    USER_ID = "Unknown"

from automation.db_helper import save_job, job_exists
from scrapers._shared import negative_keyword_match, make_session

HEADERS = {
    'User-Agent': 'SearchingEng-ITJobsScraper/1.0',
    'Accept': 'application/json',
}

# ITJobs API expects its own UA; replace defaults rather than augment.
session = make_session(retries=3, headers=HEADERS)

# workModel mapping observed on ITJobs (best-effort labels for observacoes)
_WORK_MODEL_LABELS = {1: "Presencial", 2: "Remoto", 3: "Híbrido"}


def _strip_html(html: str) -> str:
    if not html:
        return ''
    text = re.sub(r'<br\s*/?>', '\n', html, flags=re.I)
    text = re.sub(r'</(p|li|h\d|div|tr)\s*>', '\n', text, flags=re.I)
    text = re.sub(r'<li[^>]*>', '• ', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


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
    if work_label == "Remoto":
        localizacao = f"{country} (Remoto)"
    elif work_label:
        localizacao = f"{country} ({work_label})"
    else:
        localizacao = country

    salario = _format_salary(raw.get('salaryMin'), raw.get('salaryMax'))

    descricao = _strip_html(raw.get('body') or '')

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


def _fetch_page(query: str, page: int) -> dict:
    params = {
        'api_key': ITJOBS_API_KEY,
        'q': query,
        'limit': ITJOBS_PAGE_LIMIT,
        'page': page,
    }
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
            data = _fetch_page(q, page=page)
            results = data.get('results') if isinstance(data, dict) else None

            if not results:
                if page == 1:
                    print("  → No jobs for this query.")
                break

            for raw in results:
                job = _normalize_job(raw, fallback_query=q)
                if not job['titulo'] or not job['link']:
                    continue

                texto_busca = f"{job['titulo']} {job['descricao_completa'][:300]}".lower()

                if not strict_keyword_match(texto_busca, KEYWORDS):
                    continue
                blocked_kw = negative_keyword_match(texto_busca, NEGATIVE_KEYWORDS)
                if blocked_kw:
                    print(f"  [BLOCKED] '{job['titulo']}' contains a negative keyword ({blocked_kw}).")
                    continue

                if job_exists(job['link']):
                    continue

                print(f"  [NEW JOB] {job['titulo']} @ {job['empresa']}")

                salvo = save_job(
                    user_id=USER_ID,
                    plataforma=PLATAFORMA,
                    id_externo=job['id_externo'],
                    titulo=job['titulo'],
                    empresa=job['empresa'],
                    localizacao=job['localizacao'],
                    link=job['link'],
                    data_pub=job['data_pub'],
                    categoria='IT',
                    descricao_completa=job['descricao_completa'],
                    observacoes=job['observacoes'],
                    salario=job['salario'],
                    tipo_contrato=job['tipo_contrato'],
                    nivel_experiencia='',
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
