"""
Landing.jobs Scraper — uses the public JSON API at landing.jobs/api/v1/jobs.

No auth required. Returns structured data: salary, locations, tags, description.
Pagination: 50 results/page, stop when page returns < 50 results.
"""
import os
import re
import sys
import time
import random

PLATAFORMA = "Landing.jobs"
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))
LANDING_MAX_PAGES = int(os.environ.get('LANDING_MAX_PAGES', '5'))

API_BASE = "https://landing.jobs/api/v1"

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from automation.profile_fetcher import get_job_titles, get_negative_keywords, get_negative_companies, get_user_id, get_target_roles, strict_keyword_match
    KEYWORDS           = [r.lower() for r in get_job_titles()]
    NEGATIVE_KEYWORDS  = get_negative_keywords()
    NEGATIVE_COMPANIES = get_negative_companies()
    USER_ID            = get_user_id()
    BROAD_TERMS        = [r.lower() for r in get_target_roles()]
except ImportError as _e:
    print(f"FATAL: profile_fetcher import failed: {_e}. Aborting landing_scraper.", file=__import__('sys').stderr)
    __import__('sys').exit(1)

from automation.db_helper import save_job, job_exists
from scrapers._shared import negative_keyword_match, make_session, extract_seniority, strip_html

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Accept-Language': 'pt-PT,pt;q=0.9,en;q=0.8',
}

session = make_session(retries=3, headers=HEADERS)

# Cache company names for the duration of the run (avoids N+1 HTTP calls)
_company_cache: dict[int, str] = {}



def _get_company_name(company_id: int) -> str:
    if company_id in _company_cache:
        return _company_cache[company_id]
    try:
        r = session.get(f"{API_BASE}/companies/{company_id}", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            name = r.json().get('name') or 'Unknown'
            _company_cache[company_id] = name
            return name
    except Exception:
        pass
    _company_cache[company_id] = 'Unknown'
    return 'Unknown'


def _normalize_job(raw: dict) -> dict:
    """Maps a raw landing.jobs API job to the project schema."""
    locs = raw.get('locations') or []
    city_parts = [f"{l.get('city', '')} ({l.get('country_code', '')})" for l in locs if isinstance(l, dict)]
    if raw.get('remote') and 'Remote' not in city_parts:
        city_parts.append('Remote')
    localizacao = ', '.join(city_parts) if city_parts else 'Remote'

    sal_low  = raw.get('gross_salary_low')
    sal_high = raw.get('gross_salary_high')
    currency = raw.get('currency_code', 'EUR')
    if sal_low and sal_high:
        salario = f"{currency} {int(sal_low):,}–{int(sal_high):,}".replace(',', '.')
    elif sal_low:
        salario = f"{currency} {int(sal_low):,}+".replace(',', '.')
    else:
        salario = ''

    desc_parts = []
    if raw.get('role_description'):
        desc_parts.append(strip_html(raw['role_description']))
    if raw.get('main_requirements'):
        desc_parts.append("Requisitos:\n" + strip_html(raw['main_requirements']))
    if raw.get('nice_to_have'):
        desc_parts.append("Nice to have:\n" + strip_html(raw['nice_to_have']))
    descricao = '\n\n'.join(desc_parts)

    tags = raw.get('tags') or []
    obs_parts = []
    if tags:
        obs_parts.append(f"Skills: {', '.join(tags[:8])}")
    if raw.get('remote'):
        obs_parts.append('Remote')
    if raw.get('relocation_paid'):
        obs_parts.append('Relocation paid')
    observacoes = ' | '.join(obs_parts)

    titulo = (raw.get('title') or '').strip()

    return {
        'id_externo':        str(raw.get('id') or ''),
        'titulo':            titulo,
        'company_id':        raw.get('company_id'),
        'localizacao':       localizacao,
        'link':              raw.get('url') or '',
        'data_pub':          (raw.get('published_at') or 'Recent')[:10],
        'tipo_contrato':     raw.get('type') or '',
        'salario':           salario,
        'descricao_completa': descricao,
        'observacoes':       observacoes,
        'nivel_experiencia': extract_seniority(titulo, descricao),
        'remote':            bool(raw.get('remote')),
        'country_codes':     [l.get('country_code', '') for l in locs if isinstance(l, dict)],
    }


_ALLOWED_COUNTRY_CODES = {'PT', 'GB', 'US'}

def _is_target_country(job: dict) -> bool:
    """Keep only jobs based in PT, GB (UK), or US — including remote roles
    anchored to one of these countries. Remote jobs with no country_code or
    with only foreign country codes (e.g. BR) are rejected."""
    codes = {cc.upper() for cc in job['country_codes'] if cc}
    if codes:
        # Accept if at least one location is in the 3 target countries
        return bool(codes & _ALLOWED_COUNTRY_CODES)
    # No country_code at all: only accept if not marked as remote
    # (a truly country-less, non-remote job is unlikely but treat as unknown → reject)
    return False


def _fetch_keyword_jobs(keyword: str) -> dict[str, dict]:
    """Fetches all pages for a single keyword query. Returns {link: raw_job}."""
    collected: dict[str, dict] = {}
    for page in range(1, LANDING_MAX_PAGES + 1):
        time.sleep(random.uniform(0.4, 0.9))
        try:
            r = session.get(
                f"{API_BASE}/jobs",
                params={'page': page, 'sort': 'published_at', 'q': keyword},
                headers=HEADERS,
                timeout=15,
            )
            if r.status_code != 200:
                print(f"    [Landing] HTTP {r.status_code} — stopping keyword '{keyword}'")
                break
            results = r.json()
            if not isinstance(results, list) or not results:
                break
            for raw in results:
                link = raw.get('url') or ''
                if link and link not in collected:
                    collected[link] = raw
            if len(results) < 50:
                break
        except Exception as e:
            print(f"    [Landing] Error page={page} kw='{keyword}': {e}")
            break
    return collected


def iniciar_scraper_landing():
    print(f"\n{'='*55}")
    print(f"  Starting Scraper: {PLATAFORMA}")
    print(f"  Keywords: {KEYWORDS}")
    print(f"{'='*55}")

    if not KEYWORDS:
        print("  ⚠ No keywords configured. Skipping.")
        return

    # Multi-query: one API call per keyword so every role gets targeted results.
    # Previously the scraper used only the shortest keyword ("cto"), which missed
    # all results for "engineering manager", "tech lead", etc.
    all_raws: dict[str, dict] = {}
    for kw in KEYWORDS:
        print(f"  → Querying: '{kw}'")
        fetched = _fetch_keyword_jobs(kw)
        new_links = len(fetched) - len(set(fetched) & set(all_raws))
        all_raws.update(fetched)
        print(f"    {len(fetched)} jobs fetched, {new_links} unique new")

    print(f"\n  Total unique jobs across all queries: {len(all_raws)}")

    vagas_inseridas = 0
    for link, raw in all_raws.items():
        if MAX_JOBS > 0 and vagas_inseridas >= MAX_JOBS:
            print(f"  [LIMIT REACHED] Max {MAX_JOBS} jobs. Stopping.")
            break

        job = _normalize_job(raw)
        if not job['titulo'] or not job['link']:
            continue

        if not _is_target_country(job):
            continue

        if negative_keyword_match(job['titulo'].lower(), NEGATIVE_KEYWORDS):
            continue

        # Title must match at least one role keyword (strict word-boundary match).
        # We already pre-filtered by keyword via the API query, but the API
        # matches descriptions too — this guards against false positives.
        if not strict_keyword_match(job['titulo'].lower(), KEYWORDS):
            continue

        if job_exists(job['link']):
            continue

        empresa = _get_company_name(job['company_id'])
        if NEGATIVE_COMPANIES and any(nc in empresa.lower() for nc in NEGATIVE_COMPANIES):
            continue

        print(f"  [NEW] {job['titulo']} @ {empresa} | {job['localizacao']}")
        ok = save_job(
            user_id=USER_ID,
            plataforma=PLATAFORMA,
            id_externo=job['id_externo'],
            titulo=job['titulo'],
            empresa=empresa,
            localizacao=job['localizacao'],
            link=job['link'],
            data_pub=job['data_pub'],
            categoria='Tech',
            descricao_completa=job['descricao_completa'],
            observacoes=job['observacoes'],
            salario=job['salario'] or None,
            tipo_contrato=job['tipo_contrato'] or None,
            nivel_experiencia=job['nivel_experiencia'] or None,
        )
        if ok:
            vagas_inseridas += 1
            print(f"    ✅ Saved ({job['salario'] or 'no salary'})")

    print(f"\n{'='*55}")
    print(f"  Landing.jobs Done: {vagas_inseridas} new jobs saved.")
    print(f"{'='*55}")


if __name__ == '__main__':
    iniciar_scraper_landing()
