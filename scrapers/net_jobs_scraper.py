import sqlite3
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import os
import sys
import time
import random
import re
from datetime import datetime

PLATAFORMA = "Net-Empregos"
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from automation.profile_fetcher import get_target_roles, get_negative_keywords, get_user_id, generate_net_empregos_urls, strict_keyword_match
    KEYWORDS = [r.lower() for r in get_target_roles()]
    NEGATIVE_KEYWORDS = get_negative_keywords()
    USER_ID = get_user_id()
except ImportError:
    print("Warning: Could not load profile_fetcher. Using default keywords.")
    KEYWORDS = ["python", "developer"]
    NEGATIVE_KEYWORDS = []
    USER_ID = "Unknown"

from automation.db_helper import save_job, job_exists

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept-Language': 'pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7',
}

session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))

def keyword_matches(text, keywords):
    for kw in keywords:
        pattern = r'\b' + re.escape(kw.lower()) + r'\b'
        if re.search(pattern, text):
            return True
    return False

def extrair_detalhes_net(link):
    detalhes = {
        'descricao_completa': '',
        'localizacao': 'Not specified',
        'salario': '',
        'tipo_contrato': '',
        'nivel_experiencia': '',
        'observacoes': '',
    }
    try:
        time.sleep(random.uniform(0.5, 1.5))
        resposta = session.get(link, headers=HEADERS, timeout=12)
        resposta.raise_for_status()
        soup = BeautifulSoup(resposta.text, 'html.parser')

        desc_block = (
            soup.find('div', class_='job-description') or
            soup.find('div', class_=re.compile(r'description|content|body', re.I)) or
            soup.find('article')
        )
        if desc_block:
            detalhes['descricao_completa'] = desc_block.get_text(separator='\n', strip=True)
        else:
            detalhes['descricao_completa'] = "Could not extract full description via HTML."

        obs_list = []
        bloco_info = (
            soup.find('ul', class_='job-info-list') or
            soup.find('ul', class_=re.compile(r'info|details|meta', re.I)) or
            soup.find('table', class_=re.compile(r'info|details', re.I))
        )

        if bloco_info:
            for li in bloco_info.find_all(['li', 'tr']):
                text = li.get_text(separator=' ', strip=True)
                text_lower = text.lower()
                if 'local' in text_lower or 'zona' in text_lower or 'distrito' in text_lower:
                    loc = text.split(':', 1)[-1].strip() if ':' in text else text
                    detalhes['localizacao'] = loc
                    obs_list.append(f"Local: {loc}")
                elif 'salário' in text_lower or 'remuner' in text_lower or 'vencimento' in text_lower:
                    sal = text.split(':', 1)[-1].strip() if ':' in text else text
                    detalhes['salario'] = sal
                    obs_list.append(f"Salário: {sal}")
                elif 'contrato' in text_lower or 'tipo' in text_lower:
                    ct = text.split(':', 1)[-1].strip() if ':' in text else text
                    detalhes['tipo_contrato'] = ct
                    obs_list.append(f"Contrato: {ct}")
                elif 'experiência' in text_lower or 'anos' in text_lower:
                    exp = text.split(':', 1)[-1].strip() if ':' in text else text
                    detalhes['nivel_experiencia'] = exp
                    obs_list.append(f"Experiência: {exp}")
                elif text and len(text) < 100:
                    obs_list.append(text)

        if obs_list:
            detalhes['observacoes'] = " | ".join(obs_list[:8])
    except Exception as e:
        print(f"  [Net Deep] Error accessing job page: {e}")
    return detalhes

def iniciar_scraper_net_empregos():
    print(f"\n{'='*50}")
    print(f"  Starting Scraper: {PLATAFORMA}")
    print(f"  Keywords: {KEYWORDS}")
    print(f"{'='*50}")

    urls = generate_net_empregos_urls()
    print(f"  Generated {len(urls)} search URLs for {PLATAFORMA}.")

    vagas_inseridas = 0

    for key_name, url in urls.items():
        print(f"\n[Net-Empregos] Search: {key_name}")
        try:
            time.sleep(random.uniform(1.0, 2.0))
            resposta = session.get(url, headers=HEADERS, timeout=15)
            resposta.raise_for_status()
            soup = BeautifulSoup(resposta.text, 'html.parser')
            
            jobs = soup.find_all('div', class_='job-item')
            if not jobs:
                print("  → No jobs found for this search.")
                continue
                
            for job in jobs:
                title_elem = job.find('h2') or job.find('a', class_='job-title')
                if not title_elem:
                    title_elem = job.find('a')
                    if not title_elem:
                        continue
                        
                titulo = title_elem.get_text(strip=True)
                
                link_elem = title_elem.find('a') if title_elem.find('a') else title_elem
                link = link_elem['href'] if link_elem and 'href' in link_elem.attrs else ''
                if not link.startswith('http'):
                    link = 'https://www.net-empregos.com/' + link.lstrip('/')
                    
                if not titulo or not link or 'pesquisa-empregos' in link:
                    continue
                
                texto_busca = titulo.lower()
                if not strict_keyword_match(texto_busca, KEYWORDS):
                    continue

                if NEGATIVE_KEYWORDS and any(nkw in texto_busca for nkw in NEGATIVE_KEYWORDS):
                    print(f"  [BLOCKED] '{titulo}' contains a negative keyword.")
                    continue

                if job_exists(link):
                    continue

                print(f"\n  [NEW JOB] {titulo}")
                
                empresa_elem = job.find('li', class_='job-company') or job.find('span', class_='company')
                empresa = empresa_elem.get_text(strip=True) if empresa_elem else 'Confidential'
                
                detalhes = extrair_detalhes_net(link)

                match = re.search(r'-(\d+)\.asp', link)
                id_externo = match.group(1) if match else None

                salvo = save_job(
                    user_id=USER_ID, plataforma=PLATAFORMA, id_externo=id_externo,
                    titulo=titulo, empresa=empresa,
                    localizacao=detalhes['localizacao'],
                    link=link, data_pub="Recent", categoria="Unknown",
                    descricao_completa=detalhes['descricao_completa'],
                    observacoes=detalhes['observacoes']
                )

                if salvo:
                    vagas_inseridas += 1
                    print(f"    ✅ Saved: {titulo} @ {empresa}")

                if MAX_JOBS > 0 and vagas_inseridas >= MAX_JOBS:
                    break

        except Exception as e:
            print(f"  Error processing search {key_name}: {e}")

        if MAX_JOBS > 0 and vagas_inseridas >= MAX_JOBS:
            print(f"  [LIMIT REACHED] Max {MAX_JOBS} jobs. Stopping.")
            break

    print(f"\n{'='*50}")
    print(f"  Net-Empregos Done: {vagas_inseridas} new detailed jobs saved.")
    print(f"{'='*50}")

if __name__ == '__main__':
    iniciar_scraper_net_empregos()