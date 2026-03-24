import feedparser
import sqlite3
import requests
import re
from bs4 import BeautifulSoup 
from datetime import datetime
import os

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

def processar_um_feed(categoria_nome, url_feed):
    print(f"Category: {categoria_nome}")
    
    feed = feedparser.parse(url_feed)
    
    if feed.status != 200:
        print(f"Error accessing {categoria_nome} feed (Status: {feed.status}). Skipping.")
        return 0

    novas_vagas_cont = 0
    total_vagas_no_feed = len(feed.entries)
    
    if total_vagas_no_feed == 0:
        print(f"No active jobs found in {categoria_nome} feed.")
        return 0

    print(f"Found {total_vagas_no_feed} potential jobs in the feed.")

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

        # Fetch text for keyword filtering
        texto_busca = (titulo + " " + raw_desc).lower()
        if not any(kw in texto_busca for kw in KEYWORDS):
            continue

        if NEGATIVE_KEYWORDS and any(nkw in texto_busca for nkw in NEGATIVE_KEYWORDS):
            print(f"  [BLOCKED] '{titulo}' contains a negative keyword.")
            continue

        # 4. Integrated Integration
        if not job_exists(link):
            id_externo = None
            match = re.search(r'/anuncio/(\d+)', link)
            if match:
                id_externo = match.group(1)

            salvo = save_job(
                user_id=USER_ID, plataforma=PLATAFORMA, id_externo=id_externo,
                titulo=titulo, empresa=empresa, localizacao=localizacao,
                link=link, data_pub=data_pub, categoria=categoria_nome
            )

            if salvo:
                novas_vagas_cont += 1

    print(f"Finished: {novas_vagas_cont} new jobs indexed for {categoria_nome}.")
    return novas_vagas_cont

def iniciar_scraper_expresso():
    print(f"== Starting Scraper: {PLATAFORMA} (Multi-Category via RSS) ==")
    
    total_novas = 0
    try:
        for cat_nome, cat_url in RSS_FEEDS.items():
            novas = processar_um_feed(cat_nome, cat_url)
            total_novas += novas
            if MAX_JOBS > 0 and total_novas >= MAX_JOBS:
                break

        print("\n" + "=" * 20)
        print(f"Processing finished. {total_novas} jobs added globally on Expresso.")
    except Exception as e:
        print(f"An error occurred in the pipeline: {e}")

if __name__ == '__main__':
    iniciar_scraper_expresso()