"""
Companies Scraper — direct from each company's career portal.

Reads `config/companies.json` and for each company picks a strategy in this
priority order: Ashby > Lever > Greenhouse > HTML (BeautifulSoup fallback).

ATS APIs used (all public, no auth):
  - Greenhouse: https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true
  - Lever:      https://api.lever.co/v0/postings/{board}?mode=json
  - Ashby:      https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true

For HTML fallback, we use BeautifulSoup with the company's `job_selector`. This
is best-effort — many corporate career pages are SPAs that won't yield jobs
without Selenium. Those silently produce zero results and we move on.
"""
import os
import re
import sys
import json
import time
import random
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup

PLATAFORMA = "Companies"
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))

CONFIG_PATH = os.environ.get(
    'COMPANIES_CONFIG',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'companies.json'),
)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from automation.profile_fetcher import get_job_titles, get_negative_keywords, get_user_id, strict_keyword_match
    KEYWORDS = [r.lower() for r in get_job_titles()]
    NEGATIVE_KEYWORDS = get_negative_keywords()
    USER_ID = get_user_id()
except ImportError:
    print("Warning: Could not load profile_fetcher. Using default keywords.")
    KEYWORDS = ["developer"]
    NEGATIVE_KEYWORDS = []
    USER_ID = "Unknown"
    strict_keyword_match = lambda text, keywords: any(k in text.lower() for k in keywords)

from automation.db_helper import save_job, job_exists
from scrapers._shared import negative_keyword_match, make_session, extract_seniority

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/html;q=0.9, */*;q=0.5',
    'Accept-Language': 'pt-PT,pt;q=0.9,en;q=0.8',
}

session = make_session(retries=2, headers=HEADERS)

DEFAULT_PT_FILTER = r"portugal|lisbo[an]|porto|coimbra|braga|aveiro|setúbal|setubal|cascais|oeiras|alges|algés"
DEFAULT_REMOTE_FILTER = r"remote|portugal|lisbo[an]|porto|emea|europe|worldwide|anywhere"

# Regions to actively reject even if the include-filter matches.
# Avoids false positives like "Remote - Philippines" or "São Paulo, Brazil Remote"
# matching a filter that contains the bare word "remote".
_NON_EU_EXCLUDE = re.compile(
    r"\b(brazil|brasil|argentina|mexico|méxico|colombia|chile|peru|venezuela|"
    r"philippines|india|indonesia|china|japan|singapore|malaysia|vietnam|"
    r"thailand|south africa|kenya|nigeria|egypt|morocco|"
    r"usa|united states|canada|australia|new zealand|"
    r"latam|apac|americas|north america|south america)\b"
    r"|remote\s*[-–—]\s*(us|usa|united\s*states|canada|brazil|brasil|americas|"
    r"latam|apac|asia|china|japan|india|philippines)\b",
    re.I,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _strip_html(html: str) -> str:
    if not html:
        return ''
    text = re.sub(r'<br\s*/?>', '\n', html, flags=re.I)
    text = re.sub(r'</(p|li|h\d|div|tr)\s*>', '\n', text, flags=re.I)
    text = re.sub(r'<li[^>]*>', '• ', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


def _location_passes(location_text: str, filter_regex: str) -> bool:
    """Returns True if the job's location matches the include filter and is NOT in the non-EU exclusion list."""
    if not filter_regex:
        return True
    if not location_text:
        return False
    if not re.search(filter_regex, location_text, re.I):
        return False
    if _NON_EU_EXCLUDE.search(location_text):
        return False
    return True


def _resolve_filter(company: dict, key: str) -> str:
    """Returns the filter regex for a strategy, falling back to PT/remote defaults."""
    explicit = company.get(key)
    if explicit:
        return explicit
    return DEFAULT_REMOTE_FILTER if company.get('remote_company') else DEFAULT_PT_FILTER


# ─────────────────────────────────────────────────────────────────────────────
# Strategy: Greenhouse
# ─────────────────────────────────────────────────────────────────────────────
def _strategy_greenhouse(company: dict) -> list[dict]:
    board = company['greenhouse_board']
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"    [Greenhouse:{board}] HTTP {resp.status_code}")
            return []
        data = resp.json() or {}
    except Exception as e:
        print(f"    [Greenhouse:{board}] Error: {e}")
        return []

    loc_filter = _resolve_filter(company, 'greenhouse_location_filter')
    out = []
    for raw in data.get('jobs', []):
        loc_main = (raw.get('location') or {}).get('name', '') or ''
        loc_offices = " ".join((o.get('name', '') for o in (raw.get('offices') or []) if isinstance(o, dict)))
        loc_combined = f"{loc_main} {loc_offices}".strip()

        if not _location_passes(loc_combined, loc_filter):
            continue

        meta = {m.get('name'): m.get('value') for m in (raw.get('metadata') or []) if isinstance(m, dict)}
        depts = [d.get('name') for d in (raw.get('departments') or []) if isinstance(d, dict) and d.get('name')]
        descricao = _strip_html(raw.get('content') or '')

        obs_parts = []
        if depts: obs_parts.append(f"Departamento: {', '.join(depts)}")
        if meta.get('Region'): obs_parts.append(f"Região: {meta['Region']}")
        if meta.get('Profession'): obs_parts.append(f"Profissão: {meta['Profession']}")

        out.append({
            'id_externo': str(raw.get('id') or ''),
            'titulo': (raw.get('title') or '').strip(),
            'empresa': company['name'],
            'localizacao': loc_combined or 'Portugal',
            'link': raw.get('absolute_url') or '',
            'data_pub': raw.get('first_published') or raw.get('updated_at') or 'Recent',
            'tipo_contrato': '',
            'salario': '',
            'descricao_completa': descricao,
            'observacoes': " | ".join(obs_parts),
            'categoria': company.get('category', 'Unknown'),
            'plataforma': f"{PLATAFORMA}: {company['name']} (Greenhouse)",
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Strategy: Lever
# ─────────────────────────────────────────────────────────────────────────────
def _strategy_lever(company: dict) -> list[dict]:
    board = company['lever_board']
    url = f"https://api.lever.co/v0/postings/{board}?mode=json"
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"    [Lever:{board}] HTTP {resp.status_code}")
            return []
        data = resp.json() or []
    except Exception as e:
        print(f"    [Lever:{board}] Error: {e}")
        return []

    if not isinstance(data, list):
        return []

    loc_filter = _resolve_filter(company, 'lever_location_filter')
    out = []
    for raw in data:
        cats = raw.get('categories') or {}
        loc = cats.get('location', '') or ''
        all_locs = ", ".join(cats.get('allLocations') or [])
        loc_combined = f"{loc} {all_locs}".strip()

        if not _location_passes(loc_combined, loc_filter):
            continue

        sal_obj = raw.get('salaryRange') or {}
        smin, smax, currency = sal_obj.get('min'), sal_obj.get('max'), sal_obj.get('currency', 'EUR')
        if smin and smax:
            salario = f"{currency} {smin:,}–{smax:,}".replace(',', '.')
        elif smin:
            salario = f"{currency} {smin:,}+".replace(',', '.')
        else:
            salario = ''

        descricao = _strip_html(raw.get('description') or raw.get('descriptionBody') or '')

        obs_parts = []
        if cats.get('department'): obs_parts.append(f"Departamento: {cats['department']}")
        if cats.get('team'):       obs_parts.append(f"Equipa: {cats['team']}")
        if raw.get('workplaceType'): obs_parts.append(f"Modelo: {raw['workplaceType']}")

        ts = raw.get('createdAt')
        data_pub = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M:%S') if ts else 'Recent'

        out.append({
            'id_externo': str(raw.get('id') or ''),
            'titulo': (raw.get('text') or '').strip(),
            'empresa': company['name'],
            'localizacao': loc_combined or 'Portugal',
            'link': raw.get('hostedUrl') or raw.get('applyUrl') or '',
            'data_pub': data_pub,
            'tipo_contrato': cats.get('commitment', '') or '',
            'salario': salario,
            'descricao_completa': descricao,
            'observacoes': " | ".join(obs_parts),
            'categoria': company.get('category', 'Unknown'),
            'plataforma': f"{PLATAFORMA}: {company['name']} (Lever)",
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Strategy: Ashby
# ─────────────────────────────────────────────────────────────────────────────
def _strategy_ashby(company: dict) -> list[dict]:
    board = company['ashby_board']
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"    [Ashby:{board}] HTTP {resp.status_code}")
            return []
        data = resp.json() or {}
    except Exception as e:
        print(f"    [Ashby:{board}] Error: {e}")
        return []

    loc_filter = _resolve_filter(company, 'ashby_location_filter')
    out = []
    for raw in data.get('jobs', []):
        if raw.get('isListed') is False:
            continue

        loc = raw.get('location', '') or ''
        secondary = raw.get('secondaryLocations') or []
        sec_str = ", ".join(s.get('location', '') if isinstance(s, dict) else str(s) for s in secondary)
        loc_combined = f"{loc} {sec_str}".strip()

        if not _location_passes(loc_combined, loc_filter):
            continue

        comp = raw.get('compensation') or {}
        salario = comp.get('compensationTierSummary') or ''

        descricao = _strip_html(raw.get('descriptionHtml') or '') or (raw.get('descriptionPlain') or '')

        obs_parts = []
        if raw.get('department'):  obs_parts.append(f"Departamento: {raw['department']}")
        if raw.get('team'):        obs_parts.append(f"Equipa: {raw['team']}")
        if raw.get('workplaceType'): obs_parts.append(f"Modelo: {raw['workplaceType']}")

        out.append({
            'id_externo': str(raw.get('id') or ''),
            'titulo': (raw.get('title') or '').strip(),
            'empresa': company['name'],
            'localizacao': loc_combined or 'Portugal',
            'link': raw.get('jobUrl') or raw.get('applyUrl') or '',
            'data_pub': raw.get('publishedAt') or 'Recent',
            'tipo_contrato': raw.get('employmentType', '') or '',
            'salario': salario,
            'descricao_completa': descricao,
            'observacoes': " | ".join(obs_parts),
            'categoria': company.get('category', 'Unknown'),
            'plataforma': f"{PLATAFORMA}: {company['name']} (Ashby)",
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Strategy: HTML (BeautifulSoup, best-effort)
# ─────────────────────────────────────────────────────────────────────────────
def _strategy_html(company: dict) -> list[dict]:
    selector = company.get('job_selector') or "a[href*='job']"
    url = company['careers_url']
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"    [HTML:{company['slug']}] HTTP {resp.status_code}")
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
    except Exception as e:
        print(f"    [HTML:{company['slug']}] Error: {e}")
        return []

    anchors = soup.select(selector)
    if not anchors:
        all_links = soup.select('a[href]')
        if len(all_links) > 5:
            print(f"    ⚠ Selector '{selector[:40]}' matched 0 — page has {len(all_links)} links (broken selector?)")
        else:
            print(f"    ℹ No links on page — likely SPA/JS-rendered (expected)")
        return []

    out = []
    seen_links = set()
    for a in anchors:
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not href or not text or len(text) < 6:
            continue
        link = urljoin(url, href)
        if link in seen_links:
            continue
        seen_links.add(link)

        # Heuristic skips for non-job navigation/anchors.
        text_lower = text.lower()
        if any(skip in text_lower for skip in ('menu', 'cookie', 'aceitar', 'privacy', 'política', 'about', 'sobre', 'home')):
            continue

        out.append({
            'id_externo': None,
            'titulo': text,
            'empresa': company['name'],
            'localizacao': company.get('city') or 'Portugal',
            'link': link,
            'data_pub': 'Recent',
            'tipo_contrato': '',
            'salario': '',
            'descricao_completa': '',
            'observacoes': f"Categoria: {company.get('category', '')}",
            'categoria': company.get('category', 'Unknown'),
            'plataforma': f"{PLATAFORMA}: {company['name']} (HTML)",
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Strategy dispatch
# ─────────────────────────────────────────────────────────────────────────────
def _pick_strategy(company: dict):
    """Returns (strategy_name, strategy_fn) for a given company config."""
    if company.get('ashby_board'):       return ('ashby',      _strategy_ashby)
    if company.get('lever_board'):       return ('lever',      _strategy_lever)
    if company.get('greenhouse_board'):  return ('greenhouse', _strategy_greenhouse)
    if company.get('job_selector'):      return ('html',       _strategy_html)
    return ('skip', None)


def _passes_keyword_filters(job: dict) -> tuple[bool, str]:
    """Returns (passes, reason). Reason is empty when passes=True.
    Filter uses job TITLE only — description matching causes false positives
    (e.g. 'Software Engineer' passing a CTO filter because desc says 'technology').
    """
    title = job['titulo'].lower()

    if KEYWORDS and not strict_keyword_match(title, KEYWORDS):
        return False, 'no keyword match'

    blocked = negative_keyword_match(title, NEGATIVE_KEYWORDS)
    if blocked:
        return False, f"negative keyword '{blocked}'"

    return True, ''


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def _load_companies() -> list[dict]:
    if not os.path.exists(CONFIG_PATH):
        print(f"  ❌ companies.json not found at {CONFIG_PATH}")
        return []
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"  ❌ Error parsing companies.json: {e}")
        return []


def iniciar_scraper_companies():
    print(f"\n{'='*55}")
    print(f"  Starting Scraper: {PLATAFORMA}")
    print(f"  Keywords: {KEYWORDS}")
    print(f"{'='*55}")

    companies = _load_companies()
    if not companies:
        return

    active = [c for c in companies if c.get('is_active', True)]
    skipped = len(companies) - len(active)
    print(f"  Loaded {len(companies)} companies ({len(active)} active, {skipped} disabled).")

    stats = {'greenhouse': 0, 'lever': 0, 'ashby': 0, 'html': 0, 'skip': 0}
    total_saved = 0
    per_company_saved: dict[str, int] = {}

    for company in active:
        strat_name, strat_fn = _pick_strategy(company)
        stats[strat_name] = stats.get(strat_name, 0) + 1

        if strat_fn is None:
            continue

        print(f"\n  ▶ [{strat_name}] {company['name']}")
        time.sleep(random.uniform(0.3, 0.8))

        try:
            jobs = strat_fn(company)
        except Exception as e:
            print(f"    ❌ Strategy '{strat_name}' raised: {e}")
            continue

        if not jobs:
            print(f"    → No jobs returned.")
            continue

        kept = 0
        seen_titles_this_company: set = set()
        for job in jobs:
            if not job['titulo'] or not job['link']:
                continue

            passes, reason = _passes_keyword_filters(job)
            if not passes:
                continue

            # Deduplicate multi-office postings (same title, different city IDs)
            title_key = job['titulo'].strip().lower()
            if title_key in seen_titles_this_company:
                continue
            seen_titles_this_company.add(title_key)

            if job_exists(job['link']):
                continue

            ok = save_job(
                user_id=USER_ID,
                plataforma=job['plataforma'],
                id_externo=job['id_externo'],
                titulo=job['titulo'],
                empresa=job['empresa'],
                localizacao=job['localizacao'],
                link=job['link'],
                data_pub=job['data_pub'],
                categoria=job['categoria'],
                descricao_completa=job['descricao_completa'],
                observacoes=job['observacoes'],
                salario=job['salario'],
                tipo_contrato=job['tipo_contrato'],
                nivel_experiencia=extract_seniority(job['titulo'], job.get('descricao_completa', '')),
            )
            if ok:
                kept += 1
                total_saved += 1
                print(f"    ✅ {job['titulo']}  ({job['localizacao'][:40]})")

            if MAX_JOBS > 0 and total_saved >= MAX_JOBS:
                break

        per_company_saved[company['name']] = kept
        if kept == 0:
            print(f"    → 0 jobs passed filters (received {len(jobs)} raw).")
        else:
            print(f"    📦 {kept}/{len(jobs)} saved after filters.")

        if MAX_JOBS > 0 and total_saved >= MAX_JOBS:
            print(f"\n  [LIMIT REACHED] Max {MAX_JOBS} jobs. Stopping.")
            break

    print(f"\n{'='*55}")
    print(f"  Companies Done: {total_saved} new jobs saved.")
    print(f"  Strategies used: " + ", ".join(f"{k}={v}" for k, v in stats.items() if v))
    top = sorted(per_company_saved.items(), key=lambda kv: kv[1], reverse=True)[:10]
    if top and top[0][1] > 0:
        print("  Top contributors:")
        for name, n in top:
            if n > 0:
                print(f"    • {name}: {n}")
    print(f"{'='*55}")


if __name__ == '__main__':
    iniciar_scraper_companies()
