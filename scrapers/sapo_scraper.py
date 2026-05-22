import sqlite3
from bs4 import BeautifulSoup
import os
import time
import random
import json
import sys
import re
import tempfile
import shutil
import subprocess
from datetime import datetime

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from automation.db_helper import save_job, job_exists
try:
    from automation.profile_fetcher import generate_sapo_urls, strict_keyword_match, get_user_id, get_job_titles, get_negative_keywords, get_negative_companies
    PESQUISAS          = generate_sapo_urls()
    USER_ID            = get_user_id()
    KEYWORDS           = get_job_titles()
    NEGATIVE_KEYWORDS  = get_negative_keywords()
    NEGATIVE_COMPANIES = get_negative_companies()
except ImportError as _e:
    print(f"FATAL: profile_fetcher import failed: {_e}. Aborting sapo_scraper.", file=__import__('sys').stderr)
    __import__('sys').exit(1)

from scrapers._shared import make_session, DEFAULT_HEADERS, negative_keyword_match, init_chrome_with_timeout

PLATAFORMA = "Sapo Jobs"
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))  # 0 = unlimited

HEADERS = DEFAULT_HEADERS
session = make_session(retries=3)

def get_chrome_major_version():
    """Returns Chrome major version — reads from CHROME_VERSION env var if
    pre-detected by the orchestrator, otherwise detects via subprocess."""
    cached = os.environ.get('CHROME_VERSION', '')
    if cached:
        try:
            return int(cached)
        except ValueError:
            pass
    try:
        result = subprocess.run(
            ['google-chrome', '--version'],
            capture_output=True, text=True, timeout=5
        )
        match = re.search(r'(\d+)\.', result.stdout)
        if match:
            version = int(match.group(1))
            print(f"  [Sapo] Detected Chrome version: {version}")
            return version
    except Exception as e:
        print(f"  [Sapo] Could not detect Chrome version: {e}")
    return None

def configurar_driver():
    """Configures the Undetected ChromeDriver with stealth optimizations.
    Uses a fresh temp directory per run to avoid SingletonLock conflicts.
    Auto-detects Chrome version to download the matching ChromeDriver.
    """
    # Clean up any stale lock from old persistent profile (legacy path)
    old_profile_dir = "/tmp/sapo-chrome-profile"
    lock_file = os.path.join(old_profile_dir, 'SingletonLock')
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
            print("  [Sapo] Removed stale Chrome lock file.")
        except Exception:
            pass

    # Use a fresh temp directory for this run — avoids ALL lock conflicts
    tmp_profile_dir = tempfile.mkdtemp(prefix='sapo-chrome-')

    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--user-agent={HEADERS['User-Agent']}")
    options.add_argument("--lang=pt-PT,pt;q=0.9,en;q=0.8")
    options.add_argument(f"--user-data-dir={tmp_profile_dir}")

    chrome_version = get_chrome_major_version()
    driver = init_chrome_with_timeout(options, headless=True, version_main=chrome_version)
    driver._tmp_profile_dir = tmp_profile_dir
    return driver

def extrair_detalhes_sapo(driver, link_completo):
    """
    Opens a Sapo job page in a new tab and extracts the full description
    and additional metadata chips (salary, hybrid, company type, etc.).
    Returns a dict with: descricao_completa, observacoes_extra.
    """
    detalhes = {'descricao_completa': '', 'observacoes_extra': []}

    try:
        driver.execute_script(f"window.open({json.dumps(link_completo)}, '_blank');")
        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(1.5 + random.uniform(0.2, 0.8))

        # --- Full Description (JSON-LD 5-Star Extraction) ---
        time.sleep(2.0)
        deep_soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        desc_found = False
        json_ld_scripts = deep_soup.find_all('script', type='application/ld+json')
        for script in json_ld_scripts:
            try:
                ld_data = json.loads(script.string)
                # Handle list of objects or single object
                items = ld_data if isinstance(ld_data, list) else [ld_data]
                for item in items:
                    if item.get('@type') == 'JobPosting' and item.get('description'):
                        raw_desc = item.get('description')
                        # Clean HTML tags and entities
                        clean_desc = BeautifulSoup(raw_desc, 'html.parser').get_text(separator='\n', strip=True)
                        if len(clean_desc) > 200:
                            detalhes['descricao_completa'] = clean_desc
                            desc_found = True
                            print(f"    🌟 5-Star JSON-LD extraction successful.")
                            break
                if desc_found: break
            except:
                continue

        # Strategy 2: Look for specific content containers (Fallback)
        if not desc_found:
            desc_tag = (
                deep_soup.find('div', class_='offer-description') or
                deep_soup.find('div', itemprop='description') or
                deep_soup.find('div', class_='half-horizontal-padding')
            )
            if desc_tag:
                detalhes['descricao_completa'] = desc_tag.get_text(separator='\n', strip=True)
                desc_found = True

        # --- Extra Metadata Chips ---
        tags_items = deep_soup.find_all(class_='offer-tags-item')
        for item in tags_items:
            text = item.get_text(strip=True)
            if text:
                detalhes['observacoes_extra'].append(text)

        # --- Company Description Block ---
        company_block = deep_soup.find('div', class_='company-description')
        if company_block:
            comp_desc = company_block.get_text(separator='\n', strip=True)
            if comp_desc:
                detalhes['descricao_completa'] += f"\n\n--- Sobre a Empresa ---\n{comp_desc}"

    except Exception as e:
        print(f"  [Deep Extraction] Error: {e}")
    finally:
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(driver.window_handles[0])

    return detalhes

def processar_pesquisa(pesquisa_nome, url_pesquisa, driver, total_novas_global):
    print(f"\n[Sapo] Search: {pesquisa_nome}")
    vagas_novas_contagem = 0

    try:
        resposta = session.get(url_pesquisa, headers=HEADERS, timeout=15)
        resposta.raise_for_status()
        soup_pesquisa = BeautifulSoup(resposta.text, 'html.parser')

        # Extract embedded Vue.js JSON data (goldmine of structured data)
        vue_component_tag = soup_pesquisa.find('search-results-component')
        if not vue_component_tag:
            print(f"  → Vue component not found for {pesquisa_nome}. Skipping.")
            return 0

        raw_offers_json = vue_component_tag.get(':offers')
        if not raw_offers_json:
            print(f"  → No offers JSON found for {pesquisa_nome}. Skipping.")
            return 0

        try:
            offers_list = json.loads(raw_offers_json)
        except json.JSONDecodeError:
            return 0

        print(f"  → Found {len(offers_list)} offers in JSON.")

        for offer_dict in offers_list:
            titulo = offer_dict.get('offer_name')
            is_anonymous = offer_dict.get('anonymous')
            empresa = "Confidential Company" if is_anonymous else offer_dict.get('company_name', 'Not Specified')
            link_completo = offer_dict.get('link')
            localizacao = offer_dict.get('location', 'Not specified')
            data_pub = offer_dict.get('publication_date', 'Recent')
            id_externo = str(offer_dict.get('id', ''))

            if not titulo or not link_completo:
                continue

            if not strict_keyword_match(titulo, KEYWORDS):
                continue

            if negative_keyword_match(titulo, NEGATIVE_KEYWORDS):
                continue

            if NEGATIVE_COMPANIES and any(nc in empresa.lower() for nc in NEGATIVE_COMPANIES):
                continue

            # --- Extract all rich data directly from the JSON ---
            obs_list = []
            
            # Snippet from JSON (used as fallback)
            snippet = offer_dict.get('job_description', '')
            if snippet:
                # Clean up html if any
                snippet = re.sub(r'<[^>]+>', '', snippet).strip()

            # Job Work Hours / Contract Type
            job_work_hours = offer_dict.get('job_work_hours', '')
            if job_work_hours:
                obs_list.append(f"Horário: {job_work_hours}")

            # Remote Work Flag
            remote_work = offer_dict.get('remote_work')
            if remote_work:
                obs_list.append("Regime: Remoto")

            # Contract Type
            contract_type = offer_dict.get('contract_type', '')
            if contract_type:
                obs_list.append(f"Contrato: {contract_type}")

            # Experience Level
            experience = offer_dict.get('experience', '') or offer_dict.get('minimum_experience', '')
            if experience:
                obs_list.append(f"Experiência: {experience}")

            # Salary Range (often present in structured data)
            salary_min = offer_dict.get('salary_min') or offer_dict.get('minimum_salary')
            salary_max = offer_dict.get('salary_max') or offer_dict.get('maximum_salary')
            salary_str = ""
            if salary_min or salary_max:
                if salary_min and salary_max:
                    salary_str = f"{salary_min}€ – {salary_max}€"
                elif salary_min:
                    salary_str = f"A partir de {salary_min}€"
                elif salary_max:
                    salary_str = f"Até {salary_max}€"
                obs_list.append(f"Salário: {salary_str}")

            # Academic Level
            academic_level = offer_dict.get('academic_level', '')
            if academic_level:
                obs_list.append(f"Habilitações: {academic_level}")

            if not job_exists(link_completo):
                # Deep extract description and more chips from job page
                detalhes = extrair_detalhes_sapo(driver, link_completo)

                # --- Fallback Logic ---
                final_description = detalhes['descricao_completa']
                if not final_description and snippet:
                    final_description = f"[Snippet Fallback]\n{snippet}..."
                    print(f"    ⚠️ Full description failed, used snippet for: {titulo}")

                # Merge extra chips from deep page (avoiding duplicates)
                for extra_obs in detalhes['observacoes_extra']:
                    if extra_obs not in obs_list:
                        obs_list.append(extra_obs)

                observacoes = " | ".join(obs_list) if obs_list else ""

                if save_job(
                    user_id=USER_ID, plataforma=PLATAFORMA, id_externo=id_externo,
                    titulo=titulo, empresa=empresa, localizacao=localizacao,
                    link=link_completo, data_pub=data_pub, categoria=pesquisa_nome,
                    descricao_completa=final_description,
                    observacoes=observacoes,
                    salario=salary_str or None,
                    tipo_contrato=contract_type or None,
                    nivel_experiencia=experience or None,
                ):
                    vagas_novas_contagem += 1
                    total_novas_global += 1
                    print(f"    ✅ Saved: {titulo} @ {empresa} | {salary_str or 'No salary'}")

                    if MAX_JOBS > 0 and total_novas_global >= MAX_JOBS:
                        break

        print(f"  → Finished '{pesquisa_nome}': {vagas_novas_contagem} new jobs indexed.")
        return vagas_novas_contagem

    except Exception as e:
        print(f"  [Sapo] Error on search '{pesquisa_nome}': {e}")
        return 0

def iniciar_scraper_sapo():
    print(f"\n{'='*50}")
    print(f"  Starting Scraper: {PLATAFORMA}")
    print(f"  Searches: {len(PESQUISAS)}")
    print(f"{'='*50}")

    driver = None
    try:
        driver = configurar_driver()
        total_novas_global = 0

        for pesquisa_nome, url_pesquisa in PESQUISAS.items():
            novas = processar_pesquisa(pesquisa_nome, url_pesquisa, driver, total_novas_global)
            total_novas_global += novas
            if MAX_JOBS > 0 and total_novas_global >= MAX_JOBS:
                print(f"[GLOBAL LIMIT REACHED] Stopping Sapo multi-search.")
                break

        print(f"\n{'='*50}")
        print(f"  Sapo Done: {total_novas_global} total new jobs indexed.")
        print(f"{'='*50}")

    except Exception as e:
        print(f"  [Sapo] General error: {e}")
    finally:
        if driver:
            driver.quit()
            # Clean up the temporary Chrome profile dir to avoid disk bloat
            tmp_dir = getattr(driver, '_tmp_profile_dir', None)
            if tmp_dir and os.path.exists(tmp_dir):
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass

if __name__ == '__main__':
    iniciar_scraper_sapo()