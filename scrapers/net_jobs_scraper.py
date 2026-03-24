import sqlite3
import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import os
import time
import random
import re
from datetime import datetime

# --- CONFIGURATION ---
PLATAFORMA = "Net-Empregos"
URL_RSS = "https://www.net-empregos.com/rssfeed.asp"
DB_PATH = os.environ.get('DB_PATH', '/app/database/vagas.db')
MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))  # 0 = unlimited

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
    KEYWORDS = ["python", "developer", "programmer", "software", "data"]
    NEGATIVE_KEYWORDS = []
    USER_ID = "Unknown"


# Pretend to be a normal browser to avoid being blocked
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# Optimized HTTP Session Configuration
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))

# Removed manual local cursor job checks in favor of db_helper

print("==================================================")
print(f" Starting Hybrid Scraper: {PLATAFORMA}")
print("==================================================")

try:
    # 1. Connect to Database (delegated to db_helper)

    # 2. Read RSS Feed
    print("Reading latest RSS Feed...")
    feed = feedparser.parse(URL_RSS)
    print(f"Found {len(feed.entries)} general jobs in RSS.")

    vagas_inseridas = 0

    # 3. Process each job from RSS
    for entry in feed.entries:
        titulo = entry.get('title', '').strip()
        link = entry.get('link', '').strip()
        empresa = entry.get('publisher', 'Confidential') or entry.get('author', 'Confidential')
        descricao_rss = entry.get('description', '')

        # Filtering: Strict word-boundary keyword match to avoid false positives
        texto_busca = (titulo + " " + descricao_rss).lower()
        
        def keyword_matches(text, keywords):
            for kw in keywords:
                # Use word boundary match: \bcto\b won't match 'rector'
                pattern = r'\b' + re.escape(kw.lower()) + r'\b'
                if re.search(pattern, text):
                    return True
            return False
        
        if not keyword_matches(texto_busca, KEYWORDS):
            continue
        # Anti-spam filter
        if NEGATIVE_KEYWORDS and any(nkw in texto_busca for nkw in NEGATIVE_KEYWORDS):
            print(f"  [BLOCKED] '{titulo}' contains a negative keyword.")
            continue

        if job_exists(link):
            continue

        print(f"\n[NEW JOB FOUND] {titulo}")
        print(f"Extracting deep details from: {link}")

        # 4. Deep Fetch of the job page
        try:
            time.sleep(random.uniform(0.5, 1.5))

            resposta = session.get(link, headers=HEADERS, timeout=10)
            resposta.raise_for_status()
            soup = BeautifulSoup(resposta.text, 'html.parser')

            descricao_completa = ""
            localizacao = "Not specified"

            bloco_descricao = soup.find('div', class_='job-description') or soup.find('article')
            if bloco_descricao:
                descricao_completa = bloco_descricao.get_text(separator='\n', strip=True)
            else:
                descricao_completa = "Could not extract full description via HTML."

            bloco_info = soup.find('ul', class_='job-info-list')
            if bloco_info:
                li_local = bloco_info.find('li', string=lambda t: t and ('Local' in t or 'Zona' in t))
                if li_local:
                    localizacao = li_local.get_text(strip=True).replace('Local:', '').replace('Zona:', '').strip()

            # 5. Save to Database (Schema v3)
            match = re.search(r'-(\d+)\.asp', link)
            id_externo = match.group(1) if match else None
            
            salvo = save_job(
                user_id=USER_ID, plataforma=PLATAFORMA, id_externo=id_externo,
                titulo=titulo, empresa=empresa, localizacao=localizacao,
                link=link, data_pub="Recent", categoria="Unknown",
                descricao_completa=descricao_completa
            )

            if salvo:
                vagas_inseridas += 1
                print("-> Saved successfully to database.")

            if MAX_JOBS > 0 and vagas_inseridas >= MAX_JOBS:
                print(f"\n[LIMIT REACHED] Max {MAX_JOBS} jobs saved. Stopping early.")
                break

        except Exception as e:
            print(f"-> Error accessing/extracting job page: {e}")

    print("\n==================================================")
    print(f" Summary: {vagas_inseridas} new detailed jobs were saved.")
    print("==================================================")

except Exception as e:
    print(f"A general error occurred: {e}")
finally:
    pass