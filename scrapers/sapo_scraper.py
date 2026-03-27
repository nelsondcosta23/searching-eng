import sqlite3
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import os
import time
import random
import json
import sys
from datetime import datetime

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from automation.profile_fetcher import generate_sapo_urls, get_user_id
    from automation.db_helper import save_job, job_exists
    PESQUISAS = generate_sapo_urls()
    USER_ID = get_user_id()
except ImportError:
    print("Warning: Could not load profile_fetcher. Using default search.")
    PESQUISAS = {
        'Default - Python - Porto': "https://emprego.sapo.pt/offers?local=porto&pesquisa=python"
    }
    USER_ID = "Unknown"

PLATAFORMA = "Sapo Jobs"
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))  # 0 = unlimited

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
}

# Optimized HTTP Session Configuration for the Search Page JSON extraction
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))

def configurar_driver():
    """Configures the Undetected ChromeDriver."""
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--headless=new")
    profile_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tmp', 'sapo-chrome-profile')
    options.add_argument(f"--user-data-dir={profile_dir}")
    driver = uc.Chrome(options=options)
    # Pre-open an empty tab to allow multi-tabbing from index 0
    driver.get('about:blank')
    return driver

def processar_pesquisa(pesquisa_nome, url_pesquisa, driver, total_novas_global):
    print(f"Search: {pesquisa_nome}")
    vagas_novas_contagem = 0
    
    try:
        # 1. Download Search Results Page via Fast Requests
        resposta = session.get(url_pesquisa, headers=HEADERS, timeout=15)
        resposta.raise_for_status() 
        soup_pesquisa = BeautifulSoup(resposta.text, 'html.parser')

        vue_component_tag = soup_pesquisa.find('search-results-component')
        if not vue_component_tag:
            return 0

        raw_offers_json = vue_component_tag.get(':offers')
        if not raw_offers_json:
            return 0

        # Decode JSON
        try:
            offers_list = json.loads(raw_offers_json)
        except json.JSONDecodeError:
            return 0

        for offer_dict in offers_list:
            titulo = offer_dict.get('offer_name')
            is_anonymous = offer_dict.get('anonymous')
            empresa = "Confidential Company" if is_anonymous else offer_dict.get('company_name', 'Not Specified')
            link_completo = offer_dict.get('link')
            localizacao = offer_dict.get('location', 'Not specified')
            data_pub = offer_dict.get('publication_date', 'Recent')
            id_externo = str(offer_dict.get('id', ''))

            if not titulo or not link_completo: continue

            if not job_exists(link_completo):
                # We have a NEW job! Time to Deep Extract with Selenium
                descricao_completa = ""
                observacoes = ""
                recrutador_nome = ""
                recrutador_link = ""
                
                # Pre-build observations from the robust Search JSON
                obs_list = []
                if offer_dict.get('job_work_hours'):
                    obs_list.append(f"Tipo: {offer_dict.get('job_work_hours')}")
                if offer_dict.get('remote_work'):
                    obs_list.append("Regime: Remoto")
                
                try:
                    # Open new tab for deep extraction
                    driver.execute_script(f"window.open('{link_completo}', '_blank');")
                    driver.switch_to.window(driver.window_handles[-1])
                    time.sleep(1.5 + random.uniform(0.1, 0.5))
                    
                    deep_soup = BeautifulSoup(driver.page_source, 'html.parser')
                    
                    # 1. Deep Description (incorporates company desc + job desc if available)
                    desc_tag = deep_soup.find('div', class_='offer-description') or deep_soup.find('div', itemprop='description')
                    if desc_tag:
                        descricao_completa = desc_tag.get_text(separator='\\n', strip=True)
                        
                    # 2. Extract Extra Metadata Chips (Salary, Hybrid, Company Type)
                    tags_items = deep_soup.find_all(class_='offer-tags-item')
                    if tags_items:
                        for item in tags_items:
                            text = item.get_text(strip=True)
                            if text and text not in obs_list: # avoid duplicates
                                obs_list.append(text)
                                
                    # 3. Extract Company Description if separated
                    company_block = deep_soup.find('div', class_='company-description')
                    if company_block:
                        comp_desc = company_block.get_text(separator='\\n', strip=True)
                        if comp_desc:
                            descricao_completa += f"\\n\\n--- Sobre a Empresa ---\\n{comp_desc}"
                            
                except Exception as e:
                    print(f"  Deep extraction error: {e}")
                finally:
                    # Safely close deep tab and return
                    if len(driver.window_handles) > 1:
                        driver.close()
                        driver.switch_to.window(driver.window_handles[0])

                if obs_list:
                    observacoes = " | ".join(obs_list)

                # Save Data Extracted
                if save_job(
                    user_id=USER_ID, plataforma=PLATAFORMA, id_externo=id_externo,
                    titulo=titulo, empresa=empresa, localizacao=localizacao,
                    link=link_completo, data_pub=data_pub, categoria=pesquisa_nome,
                    descricao_completa=descricao_completa, recrutador_nome=recrutador_nome,
                    recrutador_link=recrutador_link, observacoes=observacoes
                ):
                    vagas_novas_contagem += 1
                    total_novas_global += 1

                    if MAX_JOBS > 0 and total_novas_global >= MAX_JOBS:
                        break

        print(f"Finished: {vagas_novas_contagem} new jobs indexed for {pesquisa_nome}.")
        return vagas_novas_contagem

    except Exception as e:
        print(f"Error accessing search {pesquisa_nome}: {e}")
        return 0

def iniciar_scraper_sapo():
    print(f"Starting Scraper: {PLATAFORMA}")

    driver = None
    try:
        driver = configurar_driver()
        total_novas_global = 0

        for pesquisa_nome, url_pesquisa in PESQUISAS.items():
            novas = processar_pesquisa(pesquisa_nome, url_pesquisa, driver, total_novas_global)
            total_novas_global += novas
            if MAX_JOBS > 0 and total_novas_global >= MAX_JOBS:
                print(f"[GLOBAL LIMIT REACHED] Stopping multi-search.")
                break

        print(f"Processing finished. {total_novas_global} jobs added globally on Sapo Emprego.")

    except Exception as e:
        print(f"General error accessing {PLATAFORMA}: {e}")
    finally:
        if driver:
            driver.quit()

if __name__ == '__main__':
    iniciar_scraper_sapo()