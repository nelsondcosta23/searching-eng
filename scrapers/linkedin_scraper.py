import time
import random
import requests
import re
import os
import sys
import tempfile
import shutil
import subprocess
from datetime import datetime
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
PLATAFORMA = 'LinkedIn PT (Selenium)'

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from automation.profile_fetcher import generate_linkedin_urls, strict_keyword_match, get_user_id, get_target_roles
    from automation.db_helper import save_job, job_exists
    PESQUISAS = generate_linkedin_urls()   # dict: key → {url, is_priority}
    USER_ID = get_user_id()
    KEYWORDS = get_target_roles()
except ImportError:
    print("Warning: Could not load profile_fetcher. Using default search.")
    PESQUISAS = {
        'IT - Python - Portugal': {'url': 'https://pt.linkedin.com/jobs/search?keywords=python&location=Portugal', 'is_priority': True}
    }
    USER_ID = "Unknown"

MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))
PRIORITY_PAGES   = int(os.environ.get('LINKEDIN_PRIORITY_PAGES', '4'))   # pages for priority queries
STANDARD_PAGES   = int(os.environ.get('LINKEDIN_STANDARD_PAGES', '2'))   # pages for standard queries

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

# ─────────────────────────────────────────────────────────────────────────────
# Method 1: LinkedIn Guest API (fast, no Selenium for listing pages)
# ─────────────────────────────────────────────────────────────────────────────
GUEST_API_BASE = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/"

def _build_guest_url(keywords: str, location: str, start: int, remote: bool) -> str:
    import urllib.parse
    params = {'keywords': keywords, 'location': location, 'start': start}
    if remote:
        params['f_WT'] = '2'
        params['f_TPR'] = 'r86400'
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
                link = link_tag.get('href', '').split('?')[0]
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
def _get_chrome_major_version():
    """Detects the installed Chrome major version to match ChromeDriver."""
    try:
        result = subprocess.run(
            ['google-chrome', '--version'],
            capture_output=True, text=True, timeout=5
        )
        match = re.search(r'(\d+)\.', result.stdout)
        if match:
            version = int(match.group(1))
            print(f"  [LinkedIn] Detected Chrome version: {version}")
            return version
    except Exception as e:
        print(f"  [LinkedIn] Could not detect Chrome version: {e}")
    return None

def _configurar_driver():
    # Clean up any stale lock from old persistent profile (legacy path)
    old_profile_dir = "/tmp/linkedin-chrome-profile"
    lock_file = os.path.join(old_profile_dir, 'SingletonLock')
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
            print("  [LinkedIn] Removed stale Chrome lock file.")
        except Exception:
            pass

    # Use a fresh temp directory for this run — avoids ALL lock conflicts
    tmp_profile_dir = tempfile.mkdtemp(prefix='linkedin-chrome-')

    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")
    options.add_argument("--lang=pt-PT,pt;q=0.9,en;q=0.8")
    options.add_argument(f"--user-data-dir={tmp_profile_dir}")

    chrome_version = _get_chrome_major_version()
    driver = uc.Chrome(options=options, headless=True, version_main=chrome_version)
    driver._tmp_profile_dir = tmp_profile_dir
    return driver


def _extrair_detalhes_requests(link: str) -> dict:
    """
    Try to fetch the job description via plain HTTP request first (faster).
    LinkedIn job detail pages are often fully server-rendered.
    """
    detalhes = {'descricao_completa': '', 'recrutador_nome': '', 'recrutador_link': '',
                 'observacoes': '', 'salario': '', 'tipo_contrato': '', 'nivel_experiencia': ''}
    try:
        ua = random.choice(USER_AGENTS)
        resp = requests.get(link, headers={'User-Agent': ua, 'Accept-Language': 'pt-PT,pt;q=0.9'}, timeout=12)
        if resp.status_code != 200:
            return detalhes

        soup = BeautifulSoup(resp.text, 'html.parser')

        desc_tag = (soup.find(class_='show-more-less-html__markup') or
                    soup.find('div', {'class': re.compile(r'description__text')}) or
                    soup.find(class_='description'))
        if desc_tag:
            detalhes['descricao_completa'] = desc_tag.get_text(separator='\n').strip()

        obs_list = []
        criteria = soup.find_all(class_='description__job-criteria-item')
        for item in criteria:
            hdr = item.find('h3', class_='description__job-criteria-subheader')
            val = item.find('span', class_='description__job-criteria-text')
            if hdr and val:
                hdr_text = hdr.get_text().strip()
                val_text = val.get_text().strip()
                obs_list.append(f"{hdr_text}: {val_text}")
                hl = hdr_text.lower()
                if 'seniority' in hl or 'nível' in hl:
                    detalhes['nivel_experiencia'] = val_text
                elif 'employment' in hl or 'tipo' in hl:
                    detalhes['tipo_contrato'] = val_text

        if obs_list:
            detalhes['observacoes'] = " | ".join(obs_list)

    except Exception:
        pass  # Will fall back to Selenium
    return detalhes


def _extrair_detalhes_selenium(driver, link: str, titulo: str) -> dict:
    """Full Selenium-based deep extraction as fallback."""
    detalhes = {'descricao_completa': '', 'recrutador_nome': '', 'recrutador_link': '',
                 'observacoes': '', 'salario': '', 'tipo_contrato': '', 'nivel_experiencia': ''}
    try:
        driver.execute_script(f"window.open('{link}', '_blank');")
        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(1.5 + random.uniform(0.5, 1.5))

        soup = BeautifulSoup(driver.page_source, 'html.parser')

        desc_tag = (soup.find(class_='show-more-less-html__markup') or
                    soup.find('div', {'class': re.compile(r'description__text')}) or
                    soup.find(class_='description'))
        if desc_tag:
            detalhes['descricao_completa'] = desc_tag.get_text(separator='\n').strip()

        rec_tag = soup.find(class_='message-the-recruiter__name') or soup.find('h3', class_='base-main-card__title')
        if rec_tag:
            detalhes['recrutador_nome'] = rec_tag.get_text().strip()

        rec_link = soup.find('a', class_='base-main-card__info')
        if rec_link:
            detalhes['recrutador_link'] = rec_link.get('href', '').split('?')[0]

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

    except Exception as e:
        print(f"  [Selenium Deep] Error for '{titulo}': {e}")
    finally:
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(driver.window_handles[0])
    return detalhes


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


def processar_uma_pesquisa(cat_nome: str, url_info: dict, driver, vagas_ja_inseridas: int) -> int:
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

        # Try fast Guest API first
        jobs_on_page = _fetch_listing_via_guest_api(keywords, location, start, remote, ua)
        use_selenium_listing = not jobs_on_page

        if use_selenium_listing:
            # Fallback: use Selenium to fetch the listing page
            print(f"  → Guest API returned 0 results, falling back to Selenium for listing...")
            try:
                paginated_url = f"{url}&start={start}"
                driver.get(paginated_url)
                time.sleep(random.uniform(5.0, 8.0))
                for _ in range(2):
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(random.uniform(1.5, 3.0))
                wait = WebDriverWait(driver, 15)
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.base-search-card')))
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                cards = soup.find_all(class_='base-search-card') or soup.find_all(class_='job-search-card')
                for vaga in cards:
                    try:
                        t = vaga.find(class_='base-search-card__title') or vaga.find('h3')
                        l = vaga.find('a', class_='base-card__full-link') or vaga.find('a')
                        if not t or not l:
                            continue
                        link = l.get('href', '').split('?')[0]
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
                print(f"  → Selenium listing fallback also failed: {e}")
                break

        if not jobs_on_page:
            print(f"  → No jobs found on page {page_num + 1}. Done.")
            break

        print(f"  → {len(jobs_on_page)} jobs on page {page_num + 1}.")

        for job in jobs_on_page:
            if job_exists(job['link']):
                continue

            # Try HTTP deep extraction first (faster), fallback to Selenium
            detalhes = _extrair_detalhes_requests(job['link'])
            needs_selenium = not detalhes['descricao_completa']

            if needs_selenium and driver:
                detalhes = _extrair_detalhes_selenium(driver, job['link'], job['titulo'])
                time.sleep(random.uniform(0.5, 1.5))  # gentle pause after Selenium use

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
                method = "HTTP" if not needs_selenium else "Selenium"
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

    driver = None
    try:
        driver = _configurar_driver()
        total = 0

        # Priority searches first
        for cat, url_info in priority_pesquisas.items():
            novas = processar_uma_pesquisa(cat, url_info, driver, total)
            total += novas
            if MAX_JOBS > 0 and total >= MAX_JOBS:
                break

        # Then standard searches
        if not (MAX_JOBS > 0 and total >= MAX_JOBS):
            for cat, url_info in standard_pesquisas.items():
                novas = processar_uma_pesquisa(cat, url_info, driver, total)
                total += novas
                if MAX_JOBS > 0 and total >= MAX_JOBS:
                    break

        print(f"\n{'='*60}")
        print(f"  LinkedIn Done: {total} total new jobs indexed.")
        print(f"{'='*60}")

    finally:
        if driver:
            driver.quit()
            print("  Selenium driver closed.")


if __name__ == '__main__':
    iniciar_scraper_linkedin()
