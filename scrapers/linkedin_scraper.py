import time
import random
import sqlite3
import requests
import re
import os
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup 
import undetected_chromedriver as uc
from xvfbwrapper import Xvfb

# Global Configurations
DB_PATH = os.environ.get('DB_PATH', '/app/database/vagas.db')
PLATAFORMA = 'LinkedIn PT (Selenium)'

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from automation.profile_fetcher import generate_linkedin_urls, get_user_id
    from automation.db_helper import save_job, job_exists
    PESQUISAS = generate_linkedin_urls()
    USER_ID = get_user_id()
except ImportError:
    print("Warning: Could not load profile_fetcher. Using default search.")
    PESQUISAS = {
        'IT - Python - Portugal': 'https://pt.linkedin.com/jobs/search?keywords=python&location=Portugal'
    }
    USER_ID = "Unknown"

MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))  # 0 = unlimited

def configurar_driver():
    """Configures the Undetected ChromeDriver."""
    print("Configuring Undetected ChromeDriver (Xvfb Mode)...")
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--user-data-dir=/tmp/linkedin-chrome-profile")
    
    driver = uc.Chrome(options=options)
    return driver

def processar_uma_pesquisa(driver, categoria_nome, url_pesquisa, vagas_ja_inseridas=0):
    print(f"Search: {categoria_nome}")
    
    try:
        driver.get(url_pesquisa)
        espera = random.uniform(6.0, 10.0) 
        
        time.sleep(espera)
        
        print("Scrolling to load more jobs...")
        for _ in range(3):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(random.uniform(2.0, 4.0))

            try:
                btn_see_more = driver.find_element(By.CSS_SELECTOR, 'button.infinite-scroller__show-more-button')
                if btn_see_more.is_displayed():
                    btn_see_more.click()
                    time.sleep(random.uniform(2.0, 4.0))
            except: pass

        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.base-search-card')))
        
        html_content = driver.page_source
        soup = BeautifulSoup(html_content, 'html.parser')
        vagas_html = soup.find_all(class_='base-search-card')
        if not vagas_html: vagas_html = soup.find_all(class_='job-search-card')

        print(f"Found {len(vagas_html)} potential jobs on the page.")
        novas_vagas_cont = 0
        
        for vaga in vagas_html:
            try:
                titulo_tag = vaga.find(class_='base-search-card__title') or vaga.find('h3', class_='base-search-card__title')
                if not titulo_tag: continue
                titulo = titulo_tag.get_text().strip()
                
                link_tag = vaga.find('a', class_='base-card__full-link') or vaga.find('a')
                if not link_tag: continue
                link_absoluto = link_tag.get('href', '').split('?')[0]
                if not link_absoluto.startswith('http'): continue
                
                empresa = "Not specified"
                empresa_tag = vaga.find(class_='base-search-card__subtitle') or vaga.find(class_='hidden-nested-link')
                if empresa_tag: empresa = empresa_tag.get_text().strip()
                
                localizacao = "Not specified"
                localizacao_tag = vaga.find(class_='job-search-card__location')
                if localizacao_tag: localizacao = localizacao_tag.get_text().strip()
                
                data_pub = "Recent"
                data_pub_tag = vaga.find(class_='job-search-card__listdate') or vaga.find(class_='job-search-card__listdate--new')
                if data_pub_tag: data_pub = data_pub_tag.get_text().strip()

                id_externo = vaga.get('data-entity-id')
                if not job_exists(link_absoluto):
                    if save_job(
                        user_id=USER_ID, plataforma=PLATAFORMA, id_externo=id_externo,
                        titulo=titulo, empresa=empresa, localizacao=localizacao,
                        link=link_absoluto, data_pub=data_pub, categoria=categoria_nome
                    ):
                        novas_vagas_cont += 1
                        total_agora = vagas_ja_inseridas + novas_vagas_cont
                        if MAX_JOBS > 0 and total_agora >= MAX_JOBS:
                            print(f"  [LIMIT REACHED] Max {MAX_JOBS} jobs saved globally. Stopping.")
                            break
            except: continue

        print(f"Finished: {novas_vagas_cont} new jobs indexed for {categoria_nome}.")
        return novas_vagas_cont

    except Exception as e:
        print(f"Critical Error accessing LinkedIn for {categoria_nome}: {e}")
        driver.save_screenshot('/app/logs/linkedin_error.png')
        with open('/app/logs/linkedin_error.html', 'w', encoding='utf-8') as f:
            f.write(driver.page_source)
        print("Error details saved to /app/logs/")
        return 0

def iniciar_scraper_linkedin():
    print(f"Starting Scraper: {PLATAFORMA}")
    
    vdisplay = Xvfb(width=1920, height=1080)
    vdisplay.start()
    
    
    try:
        driver = configurar_driver()
        
        total_novas = 0
        for cat_nome, cat_url in PESQUISAS.items():
            novas = processar_uma_pesquisa(driver, cat_nome, cat_url, total_novas)
            total_novas += novas
            if MAX_JOBS > 0 and total_novas >= MAX_JOBS:
                print(f"[GLOBAL LIMIT REACHED] Stopping multi-search on LinkedIn.")
                break

        print("\n" + "=" * 20)
        print(f"Processing finished. {total_novas} jobs added globally on LinkedIn PT.")
    finally:
        if 'driver' in locals() and driver:
            driver.quit()
            print("Selenium driver closed.")
        vdisplay.stop()
        

# --- 5. EXECUÇÃO ---
if __name__ == '__main__':
    iniciar_scraper_linkedin()
