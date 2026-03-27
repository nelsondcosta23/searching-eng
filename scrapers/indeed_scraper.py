import time
import random
import sqlite3
import requests
import re
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup 
import undetected_chromedriver as uc
import os

# Global Configurations
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
PLATAFORMA = 'Indeed PT (Selenium)'

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from automation.profile_fetcher import generate_indeed_urls, get_user_id
    from automation.db_helper import save_job, job_exists
    PESQUISAS = generate_indeed_urls()
    USER_ID = get_user_id()
except ImportError:
    print("Warning: Could not load profile_fetcher. Using default search.")
    PESQUISAS = {
        'IT - Python - Portugal': 'https://pt.indeed.com/jobs?q=python&l=Portugal'
    }
    USER_ID = "Unknown"

MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))  # 0 = unlimited

def configurar_driver():
    """Configures the Undetected ChromeDriver."""
    print("Configuring Undetected ChromeDriver (Xvfb Mode)...")
    options = uc.ChromeOptions()
    
    # Docker optimizations
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--headless=new")
    profile_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tmp', 'chrome-profile')
    options.add_argument(f"--user-data-dir={profile_dir}")
    
    driver = uc.Chrome(options=options)
    return driver

def processar_uma_pesquisa(driver, categoria_nome, url_pesquisa, vagas_ja_inseridas=0):
    print(f"Search: {categoria_nome}")
    
    try:
        driver.get(url_pesquisa)
        espera = random.uniform(8.0, 12.0) 
        
        time.sleep(espera)
        
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.cardOutline')))
        
        html_content = driver.page_source
        soup = BeautifulSoup(html_content, 'html.parser')
        vagas_html = soup.find_all(class_='cardOutline') 
        
        print(f"Found {len(vagas_html)} potential jobs on the page.")
        novas_vagas_cont = 0
        
        for vaga in vagas_html:
            try:
                link_tag = vaga.find(attrs={'data-jk': True})
                if not link_tag: continue
                    
                titulo = link_tag.get_text().strip()
                link_relativo = link_tag.get('href', '')
                link_absoluto = link_relativo if link_relativo.startswith('http') else f"https://pt.indeed.com{link_relativo}"
                
                empresa = "Not specified"
                empresa_tag = vaga.find(attrs={'data-testid': 'company-name'})
                if empresa_tag: empresa = empresa_tag.get_text().strip()
                
                localizacao = "Not specified"
                localizacao_tag = vaga.find(attrs={'data-testid': 'text-location'})
                if localizacao_tag: localizacao = localizacao_tag.get_text().strip()
                
                data_pub = "Recent"
                data_pub_tag = vaga.find(class_='date')
                if data_pub_tag: data_pub = data_pub_tag.get_text().replace('Posted', '').replace('data-', '').strip()

                id_externo = link_tag.get('data-jk')
                if not job_exists(link_absoluto):
                    if save_job(
                        user_id=USER_ID, plataforma=PLATAFORMA, id_externo=id_externo,
                        titulo=titulo, empresa=empresa, localizacao=localizacao,
                        link=link_absoluto, data_pub=data_pub, categoria=categoria_nome
                    ):
                        novas_vagas_cont += 1
                        total_agora = vagas_ja_inseridas + novas_vagas_cont
                        if MAX_JOBS > 0 and total_agora >= MAX_JOBS:
                            print(f"  [LIMIT REACHED] Max {MAX_JOBS} jobs saved globally. Stopping search.")
                            break
            except: continue

        print(f"Finished: {novas_vagas_cont} new jobs indexed for {categoria_nome}.")
        return novas_vagas_cont

    except Exception as e:
        print(f"Critical Error accessing Indeed for {categoria_nome}: {e}")
        logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        driver.save_screenshot(os.path.join(logs_dir, 'indeed_error.png'))
        with open(os.path.join(logs_dir, 'indeed_error.html'), 'w', encoding='utf-8') as f:
            f.write(driver.page_source)
        print("Error details saved to logs/")
        return 0

def iniciar_scraper_indeed():
    print(f"Starting Scraper: {PLATAFORMA}")
    
    
    
    
    try:
        driver = configurar_driver()
        
        total_novas = 0
        # Iterate over the searches
        for cat_nome, cat_url in PESQUISAS.items():
            novas = processar_uma_pesquisa(driver, cat_nome, cat_url, total_novas)
            total_novas += novas
            if MAX_JOBS > 0 and total_novas >= MAX_JOBS:
                print(f"[GLOBAL LIMIT REACHED] Stopping multi-search on Indeed.")
                break

        print("\n" + "=" * 20)
        print(f"Processing completed. {total_novas} jobs added globally on Indeed PT.")
    finally:
        if 'driver' in locals() and driver:
            driver.quit()
            print("Selenium driver closed.")
        

if __name__ == '__main__':
    iniciar_scraper_indeed()