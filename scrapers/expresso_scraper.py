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

PLATAFORMA = "Expresso Jobs"
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from automation.profile_fetcher import get_job_titles, get_negative_keywords, get_user_id, strict_keyword_match
    KEYWORDS = [r.lower() for r in get_job_titles()]
    NEGATIVE_KEYWORDS = get_negative_keywords()
    USER_ID = get_user_id()
except ImportError as _e:
    print(f"FATAL: profile_fetcher import failed: {_e}. Aborting expresso_scraper.", file=__import__('sys').stderr)
    __import__('sys').exit(1)

from automation.db_helper import save_job, job_exists
from scrapers._shared import negative_keyword_match, extract_seniority, init_chrome_with_timeout

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

BASE_URL = "https://expressoemprego.pt"

# /emprego/{slug}/{city}/{id}  — the job detail URL pattern
_JOB_HREF_RE = re.compile(r'^/emprego/[^/]+-[^/]+/[^/]+/(\d+)$')


def get_chrome_major_version():
    cached = os.environ.get('CHROME_VERSION', '')
    if cached:
        try:
            return int(cached)
        except ValueError:
            pass
    try:
        import subprocess
        result = subprocess.run(['google-chrome', '--version'], capture_output=True, text=True, timeout=5)
        m = re.search(r'(\d+)\.', result.stdout)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def _configurar_driver() -> uc.Chrome:
    tmp_dir = tempfile.mkdtemp(prefix='expresso-chrome-')
    options = uc.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--lang=pt-PT,pt;q=0.9')
    options.add_argument(f'--user-data-dir={tmp_dir}')
    # Non-headless: requires DISPLAY=:99 (set by docker-entrypoint.sh via Xvfb)
    driver = init_chrome_with_timeout(options, headless=False, version_main=get_chrome_major_version())
    driver.set_page_load_timeout(30)
    driver._tmp_profile_dir = tmp_dir
    return driver


def _extract_job_links(soup: BeautifulSoup) -> list[dict]:
    """Extract job listings from a search results page."""
    seen_ids = set()
    jobs = []
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
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


def _extract_detail(driver: uc.Chrome, link: str) -> dict:
    """Opens a job detail page in a new tab and extracts structured data."""
    result = {
        'descricao_completa': '',
        'empresa': 'Not specified',
        'observacoes': '',
        'salario': '',
        'tipo_contrato': '',
    }
    try:
        driver.execute_script(f"window.open({json.dumps(link)}, '_blank');")
        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(random.uniform(2.5, 4.0))

        soup = BeautifulSoup(driver.page_source, 'html.parser')

        # Company name
        for sel in ['span.company-name', '.offer-company', 'h2.company', '.offer-header h2',
                    'meta[property="og:description"]']:
            if sel.startswith('meta'):
                tag = soup.find('meta', property='og:description')
                if tag and tag.get('content'):
                    result['empresa'] = tag['content'].split('|')[0].strip()[:80]
                    break
            else:
                tag = soup.select_one(sel)
                if tag:
                    result['empresa'] = tag.get_text(strip=True)[:80]
                    break

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
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(driver.window_handles[0])

    return result


def iniciar_scraper_expresso():
    print(f"\n{'='*55}")
    print(f"  Starting Scraper: {PLATAFORMA} (non-headless + Xvfb)")
    print(f"  Keywords: {KEYWORDS}")
    print(f"{'='*55}")

    if not os.environ.get('DISPLAY'):
        print("  ⚠ DISPLAY not set — Xvfb may not be running. Attempting anyway.")

    driver = None
    try:
        driver = _configurar_driver()
        total_saved = 0

        # Deduplicate queries
        seen_q: set = set()
        queries = []
        for kw in KEYWORDS:
            clean = kw.strip().lower()
            if clean and clean not in seen_q:
                seen_q.add(clean)
                queries.append(clean)

        for q in queries:
            print(f"\n[Expresso] Search: {q}")
            try:
                # Step 1: homepage — sets ASP.NET session cookies
                driver.get(BASE_URL + '/')
                time.sleep(random.uniform(3.0, 5.0))

                # Step 2: submit the search form
                search_box = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.ID, 'txtPesquisa'))
                )
                search_box.clear()
                search_box.send_keys(q)
                time.sleep(random.uniform(0.5, 1.0))
                search_box.send_keys(Keys.RETURN)
                time.sleep(random.uniform(5.0, 7.0))

                print(f"  URL: {driver.current_url}")
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                job_list = _extract_job_links(soup)
                print(f"  → {len(job_list)} job links found")

                for job in job_list:
                    titulo = job['titulo']
                    link   = job['link']

                    if not titulo or len(titulo) < 5:
                        continue
                    if not strict_keyword_match(titulo.lower(), KEYWORDS):
                        continue
                    if negative_keyword_match(titulo.lower(), NEGATIVE_KEYWORDS):
                        continue
                    if job_exists(link):
                        continue

                    print(f"  [NEW] {titulo} | {job['localizacao']}")
                    detail = _extract_detail(driver, link)

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
                        print(f"    ✅ Saved")

                    if MAX_JOBS > 0 and total_saved >= MAX_JOBS:
                        break

            except Exception as e:
                print(f"  [Expresso] Error on query '{q}': {e}")

            if MAX_JOBS > 0 and total_saved >= MAX_JOBS:
                print(f"  [LIMIT] Max {MAX_JOBS} jobs reached.")
                break

        print(f"\n{'='*55}")
        print(f"  Expresso Done: {total_saved} new jobs saved.")
        print(f"{'='*55}")

    except Exception as e:
        print(f"  [Expresso] Fatal error: {e}")
    finally:
        if driver:
            driver.quit()
            tmp_dir = getattr(driver, '_tmp_profile_dir', None)
            if tmp_dir and os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == '__main__':
    iniciar_scraper_expresso()
