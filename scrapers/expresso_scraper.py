import feedparser
import sqlite3
import requests
import re
from bs4 import BeautifulSoup 
from datetime import datetime
import os
import time
import random

import undetected_chromedriver as uc
from xvfbwrapper import Xvfb
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

DB_PATH = os.environ.get('DB_PATH', '/app/database/vagas.db')
MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))  # 0 = unlimited
PLATAFORMA = 'Expresso Jobs'

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from automation.profile_fetcher import get_target_roles, get_negative_keywords, get_user_id
    from automation.db_helper import save_job, job_exists
    KEYWORDS = [r.lower() for r in get_target_roles()]
    NEGATIVE_KEYWORDS = get_negative_keywords()
    USER_ID = get_user_id()
except ImportError:
    print("Warning: Could not load profile_fetcher. Using default keywords.")
    KEYWORDS = ["python", "developer", "cto", "engineering"]
    NEGATIVE_KEYWORDS = []
    USER_ID = "Unknown"

RSS_FEEDS = {
    'Administration': 'https://expressoemprego.pt/rss/administracao',
    'Finance': 'https://expressoemprego.pt/rss/area-financeira',
    'Banking': 'https://expressoemprego.pt/rss/banca',
    'Communication': 'https://expressoemprego.pt/rss/comunicacao',
    'Consulting': 'https://expressoemprego.pt/rss/consultoria',
    'Design': 'https://expressoemprego.pt/rss/design',
    'Education': 'https://expressoemprego.pt/rss/educacao',
    'Engineering': 'https://expressoemprego.pt/rss/engenharia',
    'IT': 'https://expressoemprego.pt/rss/informatica',
    'Internet': 'https://expressoemprego.pt/rss/internet',
    'Marketing': 'https://expressoemprego.pt/rss/marketing-publicidade',
    'Human Resources': 'https://expressoemprego.pt/rss/recursos-humanos',
    'Health': 'https://expressoemprego.pt/rss/saude',
    'Public Sector': 'https://expressoemprego.pt/rss/sector-publico',
    'Telecommunications': 'https://expressoemprego.pt/rss/telecomunicacoes',
    'Sales': 'https://expressoemprego.pt/rss/vendas',
    'Commerce & Services': 'https://expressoemprego.pt/rss/comercio-servicos'
}

def configurar_driver():
    """Configures the Undetected ChromeDriver."""
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--user-data-dir=/tmp/expresso-chrome-profile")
    driver = uc.Chrome(options=options)
    driver.get('about:blank')
    return driver

def processar_um_feed(categoria_nome, url_feed, driver, total_novas_global):
    print(f"Category: {categoria_nome}")
    
    feed = feedparser.parse(url_feed)
    
    if feed.status != 200:
        return 0

    novas_vagas_cont = 0
    total_vagas_no_feed = len(feed.entries)
    
    if total_vagas_no_feed == 0:
        return 0

    for entrada in feed.entries:
        titulo = entrada.title
        link = entrada.link
        data_pub = entrada.get('published', 'No date') 

        raw_desc = entrada.description
        empresa = "Not specified"
        localizacao = "Not specified"

        if '|' in raw_desc:
            partes = raw_desc.split('|')
            if len(partes) >= 2:
                empresa = partes[0].strip()
                localizacao = partes[1].strip()

        # Keyword filtering
        texto_busca = (titulo + " " + raw_desc).lower()
        if not any(kw in texto_busca for kw in KEYWORDS):
            continue

        if NEGATIVE_KEYWORDS and any(nkw in texto_busca for nkw in NEGATIVE_KEYWORDS):
            continue

        # If it's a new job, extract the deep details
        if not job_exists(link):
            id_externo = None
            match = re.search(r'/(\d+)$', link)
            if match:
                id_externo = match.group(1)

            descricao_completa = ""
            observacoes = ""
            
            # Extract Deep Information with Selenium
            try:
                driver.execute_script(f"window.open('{link}', '_blank');")
                driver.switch_to.window(driver.window_handles[-1])
                time.sleep(2.0 + random.uniform(0.1, 0.5))
                
                deep_soup = BeautifulSoup(driver.page_source, 'html.parser')
                
                # Full specific text block in common Expresso structure
                # Typically inside the main app div or elements with class like 'detalhe-anuncio' or 'description'
                desc_tags = deep_soup.find_all(['p', 'div', 'section'])
                best_block = ""
                for tag in desc_tags:
                    text = tag.get_text(separator='\\n', strip=True)
                    # The job description is usually the largest text block
                    if len(text) > len(best_block) and 'Cookies' not in text:
                        best_block = text
                
                # Filter out pure navigation boilerplate
                if len(best_block) > 100:
                    descricao_completa = best_block
                else:
                    descricao_completa = re.sub(r'<[^>]+>', '', raw_desc)
                    
                # Look for Reference or extra metadata chips
                meta_tags = deep_soup.find_all(['span', 'li', 'div'])
                obs_list = []
                for m in meta_tags:
                    t = m.get_text(strip=True)
                    if t.startswith('Ref'):
                        obs_list.append(t)
                observacoes = " | ".join(list(set(obs_list))[:3])
                
            except Exception as e:
                print(f"  Deep extraction error: {e}")
                descricao_completa = re.sub(r'<[^>]+>', '', raw_desc)
            finally:
                if len(driver.window_handles) > 1:
                    driver.close()
                    driver.switch_to.window(driver.window_handles[0])

            salvo = save_job(
                user_id=USER_ID, plataforma=PLATAFORMA, id_externo=id_externo,
                titulo=titulo, empresa=empresa, localizacao=localizacao,
                link=link, data_pub=data_pub, categoria=categoria_nome,
                descricao_completa=descricao_completa, observacoes=observacoes
            )

            if salvo:
                novas_vagas_cont += 1
                total_novas_global += 1

                if MAX_JOBS > 0 and total_novas_global >= MAX_JOBS:
                    break

    print(f"Finished: {novas_vagas_cont} new jobs indexed for {categoria_nome}.")
    return novas_vagas_cont

def iniciar_scraper_expresso():
    print(f"Starting Scraper: {PLATAFORMA}")
    
    vdisplay = Xvfb(width=1920, height=1080)
    vdisplay.start()

    driver = None
    try:
        driver = configurar_driver()
        total_novas_global = 0

        for cat_nome, cat_url in RSS_FEEDS.items():
            novas = processar_um_feed(cat_nome, cat_url, driver, total_novas_global)
            total_novas_global += novas
            if MAX_JOBS > 0 and total_novas_global >= MAX_JOBS:
                print("[GLOBAL LIMIT REACHED] Stopping multi-search.")
                break

        print(f"Processing finished. {total_novas_global} jobs added globally on Expresso.")
    except Exception as e:
        print(f"An error occurred in the pipeline: {e}")
    finally:
        if driver:
            driver.quit()
        vdisplay.stop()

if __name__ == '__main__':
    iniciar_scraper_expresso()