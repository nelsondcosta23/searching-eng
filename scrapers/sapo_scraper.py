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

# --- CONFIGURATION ---
PLATAFORMA = "Sapo Jobs"
DB_PATH = os.environ.get('DB_PATH', '/app/database/vagas.db')
MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))  # 0 = unlimited

# Pretend to be a normal browser to avoid being blocked
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
}

# Optimized HTTP Session Configuration
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))

# Removed manual local cursor job checks in favor of db_helper

print("==================================================")
print(f" Starting Scraper: {PLATAFORMA}")
print("==================================================")

try:
    # (Database connection managed globally by db_helper now)

    total_novas_global = 0

    for pesquisa_nome, url_pesquisa in PESQUISAS.items():
        print(f"\n[PROCESSING SEARCH] {pesquisa_nome}")
        print(f"URL: {url_pesquisa}")
        try:
            # 2. Download Search Results Page
            resposta = session.get(url_pesquisa, headers=HEADERS, timeout=15)
            resposta.raise_for_status() 
            soup_pesquisa = BeautifulSoup(resposta.text, 'html.parser')

            # --- CRITICAL HTML DISCOVERY ---
            vue_component_tag = soup_pesquisa.find('search-results-component')
            if not vue_component_tag:
                print("Could not find 'search-results-component' in HTML. Skipping.")
                continue

            raw_offers_json = vue_component_tag.get(':offers')
            if not raw_offers_json:
                print("Tag found, but it doesn't contain ':offers' attribute. Skipping.")
                continue

            # 3. Decode JSON
            try:
                offers_list = json.loads(raw_offers_json)
                print(f"Found {len(offers_list)} potential jobs.")
            except json.JSONDecodeError as e:
                print(f"Error decoding jobs JSON: {e}")
                continue

            vagas_novas_contagem = 0

            # 4. Process each job
            for offer_dict in offers_list:
                try:
                    titulo = offer_dict.get('offer_name')
                    is_anonymous = offer_dict.get('anonymous')
                    empresa = "Confidential Company" if is_anonymous else offer_dict.get('company_name', 'Not Specified')
                    link_completo = offer_dict.get('link')
                    localizacao = offer_dict.get('location', 'Not specified')

                    if not titulo or not link_completo: continue

                    if job_exists(link_completo): continue

                    print(f"\n[NEW JOB DISCOVERED] {titulo}")
                    print(f"-> Company: {empresa}")
                    print(f"-> Location: {localizacao}")

                    # 5. Deep Fetch for full description
                    try:
                        time.sleep(random.uniform(0.5, 1.5)) 
                        resposta_vaga = session.get(link_completo, headers=HEADERS, timeout=12)
                        resposta_vaga.raise_for_status()
                        soup_vaga = BeautifulSoup(resposta_vaga.text, 'html.parser')

                        descricao_completa = ""
                        bloco_descricao = soup_vaga.find('div', class_='offer-description') or soup_vaga.find('div', itemprop='description')
                        if bloco_descricao:
                            descricao_completa = bloco_descricao.get_text(separator='\n', strip=True)
                        else:
                            descricao_completa = "Could not extract full description via HTML."

                        # 6. Save to Database (Schema v3)
                        id_externo = str(offer_dict.get('id'))
                        data_agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        
                        salvo = save_job(
                            user_id=USER_ID, plataforma=PLATAFORMA, id_externo=id_externo,
                            titulo=titulo, empresa=empresa, localizacao=localizacao,
                            link=link_completo, data_pub="Recent", categoria="Unknown",
                            descricao_completa=descricao_completa
                        )
                        
                        if salvo:
                            vagas_novas_contagem += 1
                            total_novas_global += 1

                            if MAX_JOBS > 0 and total_novas_global >= MAX_JOBS:
                                print(f"\n[LIMIT REACHED] Max {MAX_JOBS} jobs saved. Stopping early.")
                                break

                    except Exception as e:
                        print(f"-> Error accessing/extracting job page: {e}")

                except Exception as e:
                    print(f"Error processing job dictionary: {e}")

            print(f"Finished: {vagas_novas_contagem} new jobs indexed for {pesquisa_nome}.")

        except Exception as e:
            print(f"Error accessing search {pesquisa_nome}: {e}")

    print("\n==================================================")
    print(f" Summary: {total_novas_global} total new jobs from {PLATAFORMA} were saved.")
    print("==================================================")

except Exception as e:
    print(f"General error accessing {PLATAFORMA}: {e}")
finally:
    pass