import time
import random
import requests
import re
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
import os
import sys
import tempfile
import shutil
import subprocess

# Global Configurations
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
PLATAFORMA = 'Indeed PT (Selenium)'

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from automation.profile_fetcher import generate_indeed_urls, strict_keyword_match, get_user_id, get_target_roles, get_negative_keywords
    from automation.db_helper import save_job, job_exists
    PESQUISAS = generate_indeed_urls()
    USER_ID = get_user_id()
    KEYWORDS = get_target_roles()
    NEGATIVE_KEYWORDS = get_negative_keywords()
except ImportError:
    print("Warning: Could not load profile_fetcher. Using default search.")
    PESQUISAS = {
        'IT - Python - Portugal': 'https://pt.indeed.com/jobs?q=python&l=Portugal'
    }
    USER_ID = "Unknown"
    KEYWORDS = []
    NEGATIVE_KEYWORDS = []

from scrapers._shared import negative_keyword_match

MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))   # 0 = unlimited
MAX_PAGES = int(os.environ.get('INDEED_MAX_PAGES', '3'))        # Pages per search (10 results/page)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

def get_chrome_major_version():
    """Detects the installed Chrome major version to match ChromeDriver."""
    try:
        result = subprocess.run(
            ['google-chrome', '--version'],
            capture_output=True, text=True, timeout=5
        )
        match = re.search(r'(\d+)\.', result.stdout)
        if match:
            version = int(match.group(1))
            print(f"  [Indeed] Detected Chrome version: {version}")
            return version
    except Exception as e:
        print(f"  [Indeed] Could not detect Chrome version: {e}")
    return None

def configurar_driver():
    """Configures the Undetected ChromeDriver with stealth optimizations.
    Uses a fresh temp directory per run to avoid SingletonLock conflicts.
    Auto-detects Chrome version to download the matching ChromeDriver.
    """
    print("Configuring Undetected ChromeDriver...")

    # Clean up any stale lock from old persistent profile (legacy path)
    old_profile_dir = "/tmp/indeed-chrome-profile"
    lock_file = os.path.join(old_profile_dir, 'SingletonLock')
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
            print("  [Indeed] Removed stale Chrome lock file.")
        except Exception:
            pass

    # Use a fresh temp directory for this run — avoids ALL lock conflicts
    tmp_profile_dir = tempfile.mkdtemp(prefix='indeed-chrome-')

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

    chrome_version = get_chrome_major_version()
    driver = uc.Chrome(options=options, headless=True, version_main=chrome_version)
    driver._tmp_profile_dir = tmp_profile_dir
    return driver

def extrair_detalhes_vaga(driver, link_absoluto, titulo):
    """
    Opens a job listing in a new tab and performs deep extraction.
    Returns a dict with: descricao_completa, salario, tipo_contrato, observacoes.
    """
    detalhes = {
        'descricao_completa': '',
        'salario': '',
        'tipo_contrato': '',
        'observacoes': '',
    }

    try:
        driver.execute_script(f"window.open('{link_absoluto}', '_blank');")
        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(1.5 + random.uniform(0.5, 1.5))

        deep_soup = BeautifulSoup(driver.page_source, 'html.parser')

        # --- Full Job Description ---
        desc_tag = (
            deep_soup.find('div', attrs={'id': 'jobDescriptionText'}) or
            deep_soup.find(class_='jobsearch-JobComponent-description') or
            deep_soup.find('div', attrs={'data-testid': 'jobsearch-JobComponent-description'})
        )
        if desc_tag:
            detalhes['descricao_completa'] = desc_tag.get_text(separator='\n').strip()

        # --- Salary ---
        salary_tag = (
            deep_soup.find(attrs={'data-testid': 'attribute_snippet_testid'}) or
            deep_soup.find(class_=re.compile(r'salary-snippet|compensation', re.I))
        )
        if salary_tag:
            salary_text = salary_tag.get_text(strip=True)
            if salary_text:
                detalhes['salario'] = salary_text

        # --- Job Type & Observations (chips/attributes) ---
        obs_list = []
        attribute_items = deep_soup.find_all(attrs={'data-testid': re.compile(r'attribute|jobType|workType', re.I)})
        for item in attribute_items:
            t = item.get_text(strip=True)
            if t and len(t) < 80:
                obs_list.append(t)

        # Also try the "job details" chips
        detail_chips = deep_soup.find_all(class_=re.compile(r'jobDetail|JobDetailsTable|metadata', re.I))
        for chip in detail_chips:
            t = chip.get_text(strip=True)
            if t and len(t) < 80 and t not in obs_list:
                obs_list.append(t)

        if obs_list:
            detalhes['observacoes'] = " | ".join(obs_list[:8])

        # Determine contract type from observations
        for obs in obs_list:
            obs_lower = obs.lower()
            if 'full-time' in obs_lower or 'permanente' in obs_lower or 'efetivo' in obs_lower:
                detalhes['tipo_contrato'] = obs
            elif 'part-time' in obs_lower or 'parcial' in obs_lower:
                detalhes['tipo_contrato'] = obs

    except Exception as e:
        print(f"  [Deep Extraction] Error for '{titulo}': {e}")
    finally:
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(driver.window_handles[0])

    return detalhes

def processar_uma_pesquisa(driver, categoria_nome, url_info, vagas_ja_inseridas=0):
    print(f"\n[Indeed] Search: {categoria_nome}")
    
    url_base = url_info['url'] if isinstance(url_info, dict) else url_info
    
    # --- Warm-up: visit home page first to reduce bot detection ---
    try:
        driver.get("https://pt.indeed.com/")
        time.sleep(random.uniform(3.0, 5.0))
    except:
        pass

    novas_vagas_cont = 0

    for page_num in range(MAX_PAGES):
        offset = page_num * 10
        url_pagina = f"{url_base}&start={offset}"

        print(f"  → Page {page_num + 1}/{MAX_PAGES} (offset={offset})")

        try:
            driver.get(url_pagina)
            time.sleep(random.uniform(8.0, 12.0))

            # --- Wait for job cards (Flexible Selectors) ---
            wait_selectors = [
                (By.CSS_SELECTOR, '.jcs-JobTitle'),
                (By.CSS_SELECTOR, '.cardOutline'),
                (By.CSS_SELECTOR, '[data-testid="jobCard"]')
            ]
            
            cards_found = False
            for strategy, selector in wait_selectors:
                try:
                    WebDriverWait(driver, 10).until(EC.presence_of_element_located((strategy, selector)))
                    cards_found = True
                    break
                except:
                    continue

            if not cards_found:
                print(f"  → No job cards detected on page {page_num + 1}. Stopping pagination.")
                break

            soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            # --- Extract Job Cards ---
            # Try to find the common parent of job title and company
            vagas_html = (
                soup.find_all(class_='cardOutline') or 
                soup.find_all(attrs={'data-testid': 'jobCard'}) or
                soup.find_all(class_=re.compile(r'job_seen_beacon|result', re.I))
            )

            if not vagas_html:
                # Last resort: just find all job title links and walk up to a container
                title_links = soup.find_all(class_='jcs-JobTitle')
                vagas_html = [link.find_parent(['div', 'li', 'td']) for link in title_links if link.find_parent(['div', 'li', 'td'])]

            if not vagas_html:
                print(f"  → Empty page {page_num + 1}. Done.")
                break

            print(f"  → Found {len(vagas_html)} potential job cards on page {page_num + 1}.")

            for vaga in vagas_html:
                try:
                    link_tag = vaga.find(attrs={'data-jk': True})
                    if not link_tag:
                        continue

                    titulo = link_tag.get_text().strip()
                    if not strict_keyword_match(titulo, KEYWORDS):
                        continue

                    blocked_kw = negative_keyword_match(titulo, NEGATIVE_KEYWORDS)
                    if blocked_kw:
                        print(f"  [BLOCKED] '{titulo}' contains a negative keyword ({blocked_kw}).")
                        continue

                    link_relativo = link_tag.get('href', '')
                    link_absoluto = link_relativo if link_relativo.startswith('http') else f"https://pt.indeed.com{link_relativo}"

                    empresa = "Not specified"
                    empresa_tag = vaga.find(attrs={'data-testid': 'company-name'})
                    if empresa_tag:
                        empresa = empresa_tag.get_text().strip()

                    localizacao = "Not specified"
                    localizacao_tag = vaga.find(attrs={'data-testid': 'text-location'})
                    if localizacao_tag:
                        localizacao = localizacao_tag.get_text().strip()

                    data_pub = "Recent"
                    data_pub_tag = vaga.find(class_='date')
                    if data_pub_tag:
                        data_pub = data_pub_tag.get_text().replace('Posted', '').strip()

                    # Salary preview from listing card
                    salario_preview = ""
                    salary_card_tag = vaga.find(attrs={'data-testid': 'attribute_snippet_testid'})
                    if salary_card_tag:
                        salario_preview = salary_card_tag.get_text(strip=True)

                    id_externo = link_tag.get('data-jk')

                    if not job_exists(link_absoluto):
                        detalhes = extrair_detalhes_vaga(driver, link_absoluto, titulo)

                        # Prefer deep-extracted salary, fallback to card preview
                        salario_final = detalhes['salario'] or salario_preview

                        if save_job(
                            user_id=USER_ID, plataforma=PLATAFORMA, id_externo=id_externo,
                            titulo=titulo, empresa=empresa, localizacao=localizacao,
                            link=link_absoluto, data_pub=data_pub, categoria=categoria_nome,
                            descricao_completa=detalhes['descricao_completa'],
                            observacoes=detalhes['observacoes']
                        ):
                            novas_vagas_cont += 1
                            total_agora = vagas_ja_inseridas + novas_vagas_cont
                            print(f"    ✅ Saved: {titulo} @ {empresa} [{total_agora} total]")
                            if MAX_JOBS > 0 and total_agora >= MAX_JOBS:
                                print(f"  [LIMIT REACHED] Max {MAX_JOBS} jobs. Stopping.")
                                return novas_vagas_cont, driver
                except Exception:
                    continue

            # Gentle inter-page delay
            if page_num < MAX_PAGES - 1:
                time.sleep(random.uniform(4.0, 7.0))

        except Exception as e:
            print(f"  [Indeed] Error on page {page_num + 1} for '{categoria_nome}': {e}")
            try:
                logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
                os.makedirs(logs_dir, exist_ok=True)
                driver.save_screenshot(os.path.join(logs_dir, f'indeed_error_p{page_num + 1}.png'))
            except Exception as screenshot_err:
                print(f"  [Indeed] Could not save screenshot (Chrome likely crashed): {screenshot_err}")
                # Try to forcefully close the zombie driver and restart it
                try:
                    driver.quit()
                except:
                    pass
                driver = configurar_driver() # Restart driver for next searches
            break

    print(f"  → Finished '{categoria_nome}': {novas_vagas_cont} new jobs indexed.")
    return novas_vagas_cont, driver

def iniciar_scraper_indeed():
    print(f"\n{'='*50}")
    print(f"  Starting Scraper: {PLATAFORMA}")
    print(f"  Searches: {len(PESQUISAS)} | Max Pages/Search: {MAX_PAGES}")
    print(f"{'='*50}")

    driver = None
    try:
        driver = configurar_driver()

        total_novas = 0
        for cat_nome, cat_url in PESQUISAS.items():
            # processar_uma_pesquisa may restart the driver on Chrome crash;
            # re-bind here so subsequent searches use the new instance.
            novas, driver = processar_uma_pesquisa(driver, cat_nome, cat_url, total_novas)
            total_novas += novas
            if MAX_JOBS > 0 and total_novas >= MAX_JOBS:
                print(f"[GLOBAL LIMIT REACHED] Stopping Indeed multi-search.")
                break

        print(f"\n{'='*50}")
        print(f"  Indeed Done: {total_novas} total new jobs indexed.")
        print(f"{'='*50}")

    finally:
        if driver:
            driver.quit()
            print("  Selenium driver closed.")

if __name__ == '__main__':
    iniciar_scraper_indeed()