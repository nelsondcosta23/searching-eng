"""
Expresso Jobs Scraper — uses non-headless Chrome via Xvfb virtual display.

expressoemprego.pt runs ASP.NET WebForms with a WAF that blocks direct URL
access and headless Chrome. The working flow is:
  1. Load homepage (sets session cookies, bypasses WAF)
  2. Submit the visible search form (ctl00$ContentPlaceHeader$wucPesquisaV3$txtPesquisa)
  3. Results page URL: /emprego/pesquisa/{query}
  4. Job link pattern: /emprego/{slug}/{city}/{id}
  5. Open each job detail in a new tab to extract description

Requires: DISPLAY=:99 (Xvfb started by docker-entrypoint.sh)
"""
import sys
import os
import time
import re
import random
import json
import shutil
import tempfile
from urllib.parse import quote as _url_quote

PLATAFORMA = "Expresso Jobs"
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))
EXPRESSO_MAX_PAGES = int(os.environ.get('EXPRESSO_MAX_PAGES', '5'))

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from automation.profile_fetcher import get_job_titles, get_negative_keywords, get_negative_companies, get_user_id, strict_keyword_match
    KEYWORDS = [r.lower() for r in get_job_titles()]
    NEGATIVE_KEYWORDS  = get_negative_keywords()
    NEGATIVE_COMPANIES = get_negative_companies()
    USER_ID = get_user_id()
except ImportError as _e:
    print(f"FATAL: profile_fetcher import failed: {_e}. Aborting expresso_scraper.", file=__import__('sys').stderr)
    __import__('sys').exit(1)

from automation.db_helper import save_job, job_exists
from scrapers._shared import negative_keyword_match, extract_seniority, new_pw_context, apply_stealth
from bs4 import BeautifulSoup

BASE_URL = "https://expressoemprego.pt"

# /emprego/{...}/{id}  — matches any depth of path segments ending with a numeric ID.
# Handles /emprego/slug/city/12345 AND /emprego/category/slug/city/12345.
_JOB_HREF_RE = re.compile(r'^/emprego/(?:[^/]+/)+(\d+)$')


def _configurar_contexto():
    """Create a Playwright browser context for Expresso scraping."""
    ctx = new_pw_context(block_images=True)
    return ctx


def _extract_job_links(soup: BeautifulSoup) -> list[dict]:
    """Extract job listings from a search results page."""
    seen_ids = set()
    jobs = []
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        # Normalize: strip absolute BASE_URL prefix so the regex always sees a relative path
        if href.startswith(BASE_URL):
            href = href[len(BASE_URL):]
        m = _JOB_HREF_RE.match(href)
        if not m:
            continue
        job_id = m.group(1)
        if job_id in seen_ids:
            continue
        seen_ids.add(job_id)
        title = a.get_text(strip=True)
        # Extract city from URL slug (second-to-last segment)
        parts = href.strip('/').split('/')
        city = parts[-2].replace('-', ' ').replace('--', ', ').title() if len(parts) >= 3 else ''
        jobs.append({
            'titulo': title,
            'link': BASE_URL + href,
            'id_externo': job_id,
            'localizacao': city or 'Portugal',
        })
    return jobs


def _extract_detail(ctx, link: str) -> dict:
    """Opens a job detail page in a new Playwright page and extracts structured data."""
    result = {
        'descricao_completa': '',
        'empresa': 'Not specified',
        'observacoes': '',
        'salario': '',
        'tipo_contrato': '',
    }
    page = None
    try:
        page = ctx.new_page()
        apply_stealth(page)
        page.goto(link, wait_until='domcontentloaded', timeout=30000)
        time.sleep(random.uniform(2.0, 3.5))

        soup = BeautifulSoup(page.content(), 'html.parser')

        # Company name — try CSS selectors in priority order, then meta fallbacks
        _company_found = False
        for sel in [
            'span.company-name', 'div.company-name', 'p.company-name',
            '[class*="company-name"]', '[class*="empresa"]', '[class*="company_name"]',
            '.offer-company', '.offer-header h2', 'h2.company',
            '[data-testid*="company"]', '[class*="recruiter"]', '[class*="employer"]',
        ]:
            tag = soup.select_one(sel)
            if tag:
                name = tag.get_text(strip=True)[:80]
                if name:
                    result['empresa'] = name
                    _company_found = True
                    break
        if not _company_found:
            # og:title is usually "Job Title | Company | Site" — take index 1
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                parts = [p.strip() for p in og_title['content'].split('|')]
                if len(parts) >= 2 and parts[1]:
                    result['empresa'] = parts[1][:80]
                    _company_found = True
        if not _company_found:
            # og:description split: "Job Title | Company | ..." — take index 1
            og_desc = soup.find('meta', property='og:description')
            if og_desc and og_desc.get('content'):
                parts = [p.strip() for p in og_desc['content'].split('|')]
                if len(parts) >= 2 and parts[1]:
                    result['empresa'] = parts[1][:80]

        # Description — try multiple containers, pick the longest
        desc_candidates = []
        for sel in ['div.offer-description', 'div[class*="offer-body"]', 'div[class*="job-description"]',
                    'div[itemprop="description"]', 'section[class*="description"]']:
            tag = soup.select_one(sel)
            if tag:
                text = tag.get_text(separator='\n', strip=True)
                if len(text) > 200:
                    desc_candidates.append(text)
        if desc_candidates:
            result['descricao_completa'] = max(desc_candidates, key=len)
        else:
            # Fallback: biggest text block not in nav/footer
            best = ''
            for tag in soup.find_all(['div', 'section', 'article']):
                if tag.find_parent(['nav', 'header', 'footer']):
                    continue
                text = tag.get_text(separator='\n', strip=True)
                if 200 < len(text) < 20000 and len(text) > len(best):
                    best = text
            result['descricao_completa'] = best

        # Structured metadata chips
        obs_list = []
        for sel in ['ul[class*="job-details"]', 'ul[class*="offer-details"]', 'div[class*="offer-tags"]',
                    'ul[class*="chips"]', 'div[class*="badges"]']:
            for container in soup.select(sel):
                for item in container.find_all(['li', 'span']):
                    t = item.get_text(strip=True)
                    if 4 < len(t) < 60 and t not in obs_list:
                        obs_list.append(t)
        if obs_list:
            result['observacoes'] = ' | '.join(obs_list[:6])

        # Salary
        sal_tag = soup.find(class_=re.compile(r'salary|salario|remunera', re.I))
        if sal_tag:
            s = sal_tag.get_text(strip=True)
            if len(s) < 80:
                result['salario'] = s

    except Exception as e:
        print(f'  [Expresso detail] Error: {e}')
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass

    return result


def iniciar_scraper_expresso():
    print(f"\n{'='*55}")
    print(f"  Starting Scraper: {PLATAFORMA} (non-headless + Xvfb)")
    print(f"  Keywords: {KEYWORDS}")
    print(f"{'='*55}")

    # Check for Xvfb display availability on Linux (Docker container environment)
    if sys.platform.startswith('linux'):
        display = os.environ.get('DISPLAY', '').strip()
        if not display:
            print("❌ Expresso Fatal: Xvfb display not available — Expresso scraper aborted")
            return

    ctx = None
    main_page = None
    try:
        ctx = _configurar_contexto()
        main_page = ctx.new_page()
        apply_stealth(main_page)
        total_saved = 0

        # Deduplicate queries
        seen_q: set = set()
        queries = []
        for kw in KEYWORDS:
            clean = kw.strip().lower()
            if clean and clean not in seen_q:
                seen_q.add(clean)
                queries.append(clean)

        # Warm-up: visit homepage once to set ASP.NET session cookies / bypass WAF.
        # All subsequent searches use the direct /pesquisa?K= URL so we don't
        # depend on the brittle ASP.NET WebForms form element ID (txtPesquisa),
        # which has caused 0-result runs when the DOM changed.
        print("  [Expresso] Warming up homepage for session cookies...")
        try:
            main_page.goto(BASE_URL + '/', wait_until='domcontentloaded', timeout=30000)
            time.sleep(random.uniform(3.0, 5.0))
        except Exception as e:
            print(f"  [Expresso] Homepage warm-up warning: {e}")

        seen_links: set = set()  # session-level dedup across all queries + pages

        for q in queries:
            print(f"\n[Expresso] Search: {q}")

            for page in range(1, EXPRESSO_MAX_PAGES + 1):
                try:
                    # Expresso uses &page=N for pagination (ASP.NET standard)
                    search_url = (f"{BASE_URL}/emprego/pesquisa/{_url_quote(q)}"
                                  if page == 1 else
                                  f"{BASE_URL}/emprego/pesquisa/{_url_quote(q)}?page={page}")
                    main_page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
                    time.sleep(random.uniform(3.0, 5.0))

                    print(f"  [page {page}] URL: {main_page.url}")
                    soup = BeautifulSoup(main_page.content(), 'html.parser')
                    job_list = _extract_job_links(soup)
                    print(f"  → {len(job_list)} job links found on page {page}")

                    if not job_list:
                        break  # no more results

                    new_on_page = 0
                    for job in job_list:
                        titulo = job['titulo']
                        link   = job['link']

                        if not titulo or len(titulo) < 5:
                            continue
                        if link in seen_links:
                            continue
                        seen_links.add(link)
                        if not strict_keyword_match(titulo.lower(), KEYWORDS):
                            continue
                        if negative_keyword_match(titulo.lower(), NEGATIVE_KEYWORDS):
                            continue
                        if job_exists(link):
                            continue
                        print(f"  [NEW] {titulo} | {job['localizacao']}")
                        detail = _extract_detail(ctx, link)

                        if NEGATIVE_COMPANIES and any(nc in (detail['empresa'] or '').lower() for nc in NEGATIVE_COMPANIES):
                            continue

                        ok = save_job(
                            user_id=USER_ID,
                            plataforma=PLATAFORMA,
                            id_externo=job['id_externo'],
                            titulo=titulo,
                            empresa=detail['empresa'],
                            localizacao=job['localizacao'],
                            link=link,
                            data_pub='Recent',
                            categoria=q,
                            descricao_completa=detail['descricao_completa'],
                            observacoes=detail['observacoes'],
                            salario=detail['salario'] or None,
                            tipo_contrato=detail['tipo_contrato'] or None,
                            nivel_experiencia=extract_seniority(titulo, detail['descricao_completa']) or None,
                        )
                        if ok:
                            total_saved += 1
                            new_on_page += 1
                            print(f"    ✅ Saved")

                        if MAX_JOBS > 0 and total_saved >= MAX_JOBS:
                            break

                    # If every job on this page was already in DB, stop paginating
                    if new_on_page == 0 and page > 1:
                        print(f"  → No new jobs on page {page}. Stopping pagination.")
                        break

                except Exception as e:
                    print(f"  [Expresso] Error on query '{q}' page {page}: {e}")
                    break

                if MAX_JOBS > 0 and total_saved >= MAX_JOBS:
                    break

            if MAX_JOBS > 0 and total_saved >= MAX_JOBS:
                print(f"  [LIMIT] Max {MAX_JOBS} jobs reached.")
                break

        print(f"\n{'='*55}")
        print(f"  Expresso Done: {total_saved} new jobs saved.")
        print(f"{'='*55}")

    except Exception as e:
        print(f"  [Expresso] Fatal error: {e}")
    finally:
        if ctx:
            try:
                ctx.close()
            except Exception:
                pass


if __name__ == '__main__':
    iniciar_scraper_expresso()
