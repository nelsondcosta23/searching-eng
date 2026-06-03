import time
import random
import requests
import re
import os
import sys
import json
from datetime import datetime
from bs4 import BeautifulSoup
# Playwright browser pool (Phase 4)

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
PLATAFORMA = 'LinkedIn PT (Playwright)'

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from automation.db_helper import save_job, job_exists
try:
    from automation.profile_fetcher import generate_linkedin_urls, strict_keyword_match, get_user_id, get_job_titles, get_negative_keywords, get_negative_companies
    PESQUISAS          = generate_linkedin_urls()
    USER_ID            = get_user_id()
    KEYWORDS           = get_job_titles()
    NEGATIVE_KEYWORDS  = get_negative_keywords()
    NEGATIVE_COMPANIES = get_negative_companies()
except ImportError as _e:
    print(f"FATAL: profile_fetcher import failed: {_e}. Aborting linkedin_scraper.", file=__import__('sys').stderr)
    __import__('sys').exit(1)

from scrapers._shared import negative_keyword_match, new_pw_context, apply_stealth, strip_html

MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))
PRIORITY_PAGES   = int(os.environ.get('LINKEDIN_PRIORITY_PAGES', '2'))   # pages for priority queries
STANDARD_PAGES   = int(os.environ.get('LINKEDIN_STANDARD_PAGES', '1'))   # pages for standard queries

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

# ─────────────────────────────────────────────────────────────────────────────
# Method 1: LinkedIn Guest API (fast, no Selenium for listing pages)
# ─────────────────────────────────────────────────────────────────────────────
GUEST_API_BASE = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/"

def _normalize_linkedin_url(link: str) -> str:
    """Normalize regional LinkedIn domains (pt.linkedin.com, uk.linkedin.com, etc.)
    to www.linkedin.com so the same job from different searches deduplicates correctly."""
    import re as _re
    return _re.sub(r'https://[a-z]{2}\.linkedin\.com/', 'https://www.linkedin.com/', link)


def _build_guest_url(keywords: str, location: str, start: int, remote: bool) -> str:
    import urllib.parse
    # f_TPR (time filter) is intentionally omitted from the Guest API URL —
    # the endpoint ignores or mishandles it and returns 0 results when present.
    # Freshness filtering is already applied by the Playwright/browser URLs in
    # generate_linkedin_urls() via f_TPR=r86400.
    params = {'keywords': keywords, 'location': location, 'start': start}
    if remote:
        params['f_WT'] = '2'
    return f"{GUEST_API_BASE}?{urllib.parse.urlencode(params)}"

def _fetch_listing_via_guest_api(keywords: str, location: str, start: int, remote: bool, ua: str) -> list[dict]:
    """
    Fetches one page of LinkedIn job listings using the public Guest API.
    Returns a list of job dicts: {titulo, empresa, localizacao, link, data_pub, id_externo}
    """
    url = _build_guest_url(keywords, location, start, remote)
    headers = {
        'User-Agent': ua,
        'Accept-Language': 'pt-PT,pt;q=0.9,en;q=0.8',
        'Referer': 'https://www.linkedin.com/jobs/',
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        cards = soup.find_all(class_='base-search-card')
        if not cards:
            cards = soup.find_all(class_='job-search-card')

        jobs = []
        for vaga in cards:
            try:
                titulo_tag = vaga.find(class_='base-search-card__title') or vaga.find('h3')
                if not titulo_tag:
                    continue
                titulo = titulo_tag.get_text().strip()

                link_tag = vaga.find('a', class_='base-card__full-link') or vaga.find('a')
                if not link_tag:
                    continue
                link = _normalize_linkedin_url(link_tag.get('href', '').split('?')[0])
                if not link.startswith('http'):
                    continue

                empresa_tag = vaga.find(class_='base-search-card__subtitle') or vaga.find(class_='hidden-nested-link')
                empresa = empresa_tag.get_text().strip() if empresa_tag else "Not specified"

                loc_tag = vaga.find(class_='job-search-card__location')
                localizacao = loc_tag.get_text().strip() if loc_tag else "Not specified"

                date_tag = vaga.find(class_='job-search-card__listdate') or vaga.find(class_='job-search-card__listdate--new')
                data_pub = date_tag.get_text().strip() if date_tag else "Recent"

                id_externo = vaga.get('data-entity-id')

                jobs.append({
                    'titulo': titulo, 'empresa': empresa, 'localizacao': localizacao,
                    'link': link, 'data_pub': data_pub, 'id_externo': id_externo,
                })
            except Exception:
                continue

        return jobs

    except Exception as e:
        print(f"  [Guest API] Request failed: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Deep Extraction (Selenium — for individual job description pages)
# ─────────────────────────────────────────────────────────────────────────────
def _configurar_contexto():
    """Create a Playwright browser context for LinkedIn scraping."""
    return new_pw_context(block_images=True)


def _parse_linkedin_soup(soup: BeautifulSoup) -> dict:
    """Extract job details from a LinkedIn job page HTML (works for both HTTP and Selenium sources).

    Centralises the CSS selector logic so changes only need to be made in one place.
    """
    detalhes: dict = {'descricao_completa': '', 'observacoes': '',
                      'salario': '', 'tipo_contrato': '', 'nivel_experiencia': ''}

    desc_tag = (soup.find(class_='show-more-less-html__markup') or
                soup.find('div', {'class': re.compile(r'description__text')}) or
                soup.find(class_='description'))
    if desc_tag:
        detalhes['descricao_completa'] = desc_tag.get_text(separator='\n').strip()

    obs_list = []
    for item in soup.find_all(class_='description__job-criteria-item'):
        hdr = item.find('h3', class_='description__job-criteria-subheader')
        val = item.find('span', class_='description__job-criteria-text')
        if hdr and val:
            hdr_text, val_text = hdr.get_text().strip(), val.get_text().strip()
            obs_list.append(f"{hdr_text}: {val_text}")
            hl = hdr_text.lower()
            if 'seniority' in hl or 'nível' in hl:
                detalhes['nivel_experiencia'] = val_text
            elif 'employment' in hl or 'tipo' in hl:
                detalhes['tipo_contrato'] = val_text
    if obs_list:
        detalhes['observacoes'] = " | ".join(obs_list)

    return detalhes


def _extrair_detalhes_requests(link: str) -> dict:
    """Try to fetch job details via plain HTTP (no Selenium cost).

    LinkedIn's public job pages are partially server-rendered — metadata chips
    (seniority, employment type) are usually present, but the full description
    often requires JS execution. If description is empty the caller falls back
    to Selenium.
    """
    detalhes = {'descricao_completa': '', 'recrutador_nome': '', 'recrutador_link': '',
                 'observacoes': '', 'salario': '', 'tipo_contrato': '', 'nivel_experiencia': ''}
    try:
        ua = random.choice(USER_AGENTS)
        resp = requests.get(link, headers={'User-Agent': ua, 'Accept-Language': 'pt-PT,pt;q=0.9'}, timeout=12)
        if resp.status_code != 200:
            return detalhes
        parsed = _parse_linkedin_soup(BeautifulSoup(resp.text, 'html.parser'))
        detalhes.update(parsed)
    except Exception:
        pass  # Will fall back to Selenium
    return detalhes


def _extrair_detalhes_playwright(ctx, link: str, titulo: str) -> dict:
    """Playwright-based deep extraction fallback when HTTP returns no description."""
    detalhes = {'descricao_completa': '', 'recrutador_nome': '', 'recrutador_link': '',
                 'observacoes': '', 'salario': '', 'tipo_contrato': '', 'nivel_experiencia': ''}
    page = None
    try:
        page = ctx.new_page()
        apply_stealth(page)
        page.goto(link, wait_until='domcontentloaded', timeout=30000)
        time.sleep(1.5 + random.uniform(0.5, 1.5))

        soup = BeautifulSoup(page.content(), 'html.parser')
        parsed = _parse_linkedin_soup(soup)
        detalhes.update(parsed)

        rec_tag = soup.find(class_='message-the-recruiter__name') or soup.find('h3', class_='base-main-card__title')
        if rec_tag:
            detalhes['recrutador_nome'] = rec_tag.get_text().strip()
        rec_link = soup.find('a', class_='base-main-card__info')
        if rec_link:
            detalhes['recrutador_link'] = rec_link.get('href', '').split('?')[0]

    except Exception as e:
        print(f"  [Playwright Deep] Error for '{titulo}': {e}")
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass
    return detalhes


# ─────────────────────────────────────────────────────────────────────────────
# Guest API circuit-breaker
# Flipped to False on the first run-time failure; all subsequent pages and
# searches skip the HTTP attempt and go straight to Playwright.
# ─────────────────────────────────────────────────────────────────────────────
_GUEST_API_OK = True


# ─────────────────────────────────────────────────────────────────────────────
# Main Scraper Logic
# ─────────────────────────────────────────────────────────────────────────────
def _parse_url_parts(full_url: str) -> tuple[str, str, bool]:
    """Extracts keywords, location, and remote flag from a LinkedIn search URL."""
    import urllib.parse
    parsed = urllib.parse.urlparse(full_url)
    params = urllib.parse.parse_qs(parsed.query)
    keywords = urllib.parse.unquote(params.get('keywords', [''])[0])
    location = urllib.parse.unquote(params.get('location', ['Portugal'])[0])
    remote   = 'f_WT' in params
    return keywords, location, remote


def processar_uma_pesquisa(cat_nome: str, url_info: dict, ctx, vagas_ja_inseridas: int, seen_jobs: set) -> int:
    global _GUEST_API_OK

    url       = url_info['url']
    is_prio   = url_info.get('is_priority', False)
    max_pages = PRIORITY_PAGES if is_prio else STANDARD_PAGES
    ua        = random.choice(USER_AGENTS)

    priority_badge = "★ PRIORITY" if is_prio else "standard"
    print(f"\n[LinkedIn] [{priority_badge}] Search: {cat_nome} ({max_pages} pages)")

    keywords, location, remote = _parse_url_parts(url)
    novas_vagas_cont = 0

    for page_num in range(max_pages):
        start = page_num * 25
        print(f"  → Page {page_num + 1}/{max_pages} (offset={start})")

        jobs_on_page = []
        if _GUEST_API_OK:
            # Try Guest API; retry once with a different UA before giving up.
            jobs_on_page = _fetch_listing_via_guest_api(keywords, location, start, remote, ua)
            if not jobs_on_page:
                retry_ua = random.choice([u for u in USER_AGENTS if u != ua] or USER_AGENTS)
                jobs_on_page = _fetch_listing_via_guest_api(keywords, location, start, remote, retry_ua)
            if not jobs_on_page:
                # Open the circuit-breaker: skip Guest API for the rest of this run.
                _GUEST_API_OK = False
                print(f"  → Guest API returned 0 results — disabling for this run, using Playwright only.")

        use_selenium_listing = not jobs_on_page

        if use_selenium_listing:
            print(f"  → Playwright listing (offset={start})...")
            listing_page = None
            try:
                listing_page = ctx.new_page()
                apply_stealth(listing_page)
                paginated_url = f"{url}&start={start}"
                listing_page.goto(paginated_url, wait_until='domcontentloaded', timeout=30000)
                time.sleep(random.uniform(4.0, 7.0))
                for _ in range(2):
                    listing_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(random.uniform(1.5, 3.0))
                listing_page.wait_for_selector('.base-search-card', timeout=15000)
                soup = BeautifulSoup(listing_page.content(), 'html.parser')
                cards = soup.find_all(class_='base-search-card') or soup.find_all(class_='job-search-card')
                for vaga in cards:
                    try:
                        t = vaga.find(class_='base-search-card__title') or vaga.find('h3')
                        l = vaga.find('a', class_='base-card__full-link') or vaga.find('a')
                        if not t or not l:
                            continue
                        link = _normalize_linkedin_url(l.get('href', '').split('?')[0])
                        if not link.startswith('http'):
                            continue
                        emp = vaga.find(class_='base-search-card__subtitle')
                        loc = vaga.find(class_='job-search-card__location')
                        dt  = vaga.find(class_='job-search-card__listdate') or vaga.find(class_='job-search-card__listdate--new')
                        jobs_on_page.append({
                            'titulo': t.get_text().strip(),
                            'empresa': emp.get_text().strip() if emp else "Not specified",
                            'localizacao': loc.get_text().strip() if loc else "Not specified",
                            'link': link,
                            'data_pub': dt.get_text().strip() if dt else "Recent",
                            'id_externo': vaga.get('data-entity-id'),
                        })
                    except Exception:
                        continue
            except Exception as e:
                print(f"  → Playwright listing fallback also failed: {e}")
                break
            finally:
                if listing_page:
                    try:
                        listing_page.close()
                    except Exception:
                        pass

        if not jobs_on_page:
            print(f"  → No jobs found on page {page_num + 1}. Done.")
            break

        print(f"  → {len(jobs_on_page)} jobs on page {page_num + 1}.")

        for job in jobs_on_page:
            titulo_raw  = job.get('titulo', '') or ''
            empresa_raw = job.get('empresa', '') or ''

            # job_exists checks BOTH link AND (titulo, empresa, user_id)
            # — catches multi-office duplicates with different URLs.
            if job_exists(job['link'], titulo=titulo_raw, empresa=empresa_raw, user_id=USER_ID):
                continue

            # Session-level dedup: only track when empresa is known — "Not specified"
            # from listing cards is not reliable enough to dedup on, and would
            # cause a second search to skip the same job when one listing had the
            # company and the other didn't.
            empresa_norm = empresa_raw.strip().lower()
            if empresa_norm and empresa_norm != 'not specified':
                dedup_key = (titulo_raw.strip().lower(), empresa_norm)
                if dedup_key in seen_jobs:
                    continue
                seen_jobs.add(dedup_key)

            # Apply keyword filters BEFORE deep extraction to save Selenium budget.
            titulo  = job.get('titulo', '') or ''
            empresa = (job.get('empresa', '') or '').lower()
            if KEYWORDS and not strict_keyword_match(titulo, KEYWORDS):
                continue
            blocked_kw = negative_keyword_match(titulo, NEGATIVE_KEYWORDS)
            if blocked_kw:
                print(f"    [BLOCKED] '{titulo}' contains a negative keyword ({blocked_kw}).")
                continue
            if NEGATIVE_COMPANIES and any(nc in empresa for nc in NEGATIVE_COMPANIES):
                print(f"    [BLOCKED COMPANY] '{job.get('empresa')}' is in the blocked companies list.")
                continue

            # Try HTTP deep extraction first (faster), fallback to Selenium
            detalhes = _extrair_detalhes_requests(job['link'])
            needs_selenium = not detalhes['descricao_completa']

            if needs_selenium and ctx:
                detalhes = _extrair_detalhes_playwright(ctx, job['link'], job['titulo'])
                time.sleep(random.uniform(0.5, 1.5))

            if save_job(
                user_id=USER_ID, plataforma=PLATAFORMA, id_externo=job['id_externo'],
                titulo=job['titulo'], empresa=job['empresa'], localizacao=job['localizacao'],
                link=job['link'], data_pub=job['data_pub'], categoria=cat_nome,
                descricao_completa=detalhes['descricao_completa'],
                recrutador_nome=detalhes['recrutador_nome'],
                recrutador_link=detalhes['recrutador_link'],
                observacoes=detalhes['observacoes'],
                salario=detalhes['salario'],
                tipo_contrato=detalhes['tipo_contrato'],
                nivel_experiencia=detalhes['nivel_experiencia'],
            ):
                novas_vagas_cont += 1
                total = vagas_ja_inseridas + novas_vagas_cont
                method = "HTTP" if not needs_selenium else "Playwright"
                print(f"    ✅ [{method}] {job['titulo']} @ {job['empresa']} [{total} total]")
                if MAX_JOBS > 0 and total >= MAX_JOBS:
                    print(f"  [LIMIT] Max {MAX_JOBS} jobs reached.")
                    return novas_vagas_cont

        if page_num < max_pages - 1:
            time.sleep(random.uniform(2.0, 4.0))

    print(f"  → Finished '{cat_nome}': {novas_vagas_cont} new jobs.")
    return novas_vagas_cont


def iniciar_scraper_linkedin():
    priority_pesquisas  = {k: v for k, v in PESQUISAS.items() if v.get('is_priority')}
    standard_pesquisas  = {k: v for k, v in PESQUISAS.items() if not v.get('is_priority')}

    print(f"\n{'='*60}")
    print(f"  Starting Scraper: {PLATAFORMA}")
    print(f"  ★ Priority searches: {len(priority_pesquisas)} ({PRIORITY_PAGES} pages each)")
    print(f"  ◇ Standard searches: {len(standard_pesquisas)} ({STANDARD_PAGES} pages each)")
    print(f"{'='*60}")

    ctx = None
    try:
        ctx = _configurar_contexto()
        total = 0
        seen_jobs: set = set()

        for cat, url_info in priority_pesquisas.items():
            novas = processar_uma_pesquisa(cat, url_info, ctx, total, seen_jobs)
            total += novas
            if MAX_JOBS > 0 and total >= MAX_JOBS:
                break

        if not (MAX_JOBS > 0 and total >= MAX_JOBS):
            for cat, url_info in standard_pesquisas.items():
                novas = processar_uma_pesquisa(cat, url_info, ctx, total, seen_jobs)
                total += novas
                if MAX_JOBS > 0 and total >= MAX_JOBS:
                    break

        print(f"\n{'='*60}")
        print(f"  LinkedIn Done: {total} total new jobs indexed.")
        print(f"{'='*60}")

    finally:
        if ctx:
            try:
                ctx.close()
                print("  Playwright context closed.")
            except Exception:
                pass


if __name__ == '__main__':
    iniciar_scraper_linkedin()
