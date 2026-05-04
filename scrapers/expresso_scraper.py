import sys
import os
import time
import re
import random
import shutil

PLATAFORMA = "Expresso Jobs"
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from automation.profile_fetcher import get_target_roles, get_negative_keywords, get_user_id, generate_expresso_urls, strict_keyword_match
    KEYWORDS = [r.lower() for r in get_target_roles()]
    NEGATIVE_KEYWORDS = get_negative_keywords()
    USER_ID = get_user_id()
except ImportError:
    print("Warning: Could not load profile_fetcher. Using default keywords.")
    KEYWORDS = ["python", "developer"]
    NEGATIVE_KEYWORDS = []
    USER_ID = "Unknown"

from automation.db_helper import save_job, job_exists
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
import subprocess

def get_chrome_major_version():
    try:
        result = subprocess.run(
            ['google-chrome', '--version'],
            capture_output=True, text=True, timeout=5
        )
        match = re.search(r'(\d+)\.', result.stdout)
        if match:
            version = int(match.group(1))
            return version
    except Exception:
        pass
    return None

def configurar_driver():
    options = uc.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument(f"--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.randint(115, 122)}.0.0.0 Safari/537.36")
    
    chrome_version = get_chrome_major_version()
    driver = uc.Chrome(options=options, headless=True, version_main=chrome_version)
    driver.set_page_load_timeout(30)
    
    v_str = driver.capabilities.get('browserVersion', 'Unknown')
    print(f"  [{PLATAFORMA.split()[0]}] Detected Chrome version: {v_str}")
    
    return driver

def extrair_descricao_expresso(driver, link, raw_desc_fallback=""):
    resultado = {
        'descricao_completa': '',
        'salario': '',
        'tipo_contrato': '',
        'nivel_experiencia': '',
        'observacoes': '',
        'empresa': 'Not specified',
        'localizacao': 'Not specified'
    }

    try:
        driver.execute_script(f"window.open('{link}', '_blank');")
        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(random.uniform(2.0, 3.5))

        deep_soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Try to get company and location from the job page header
        # Usually it's in a header or meta tags
        for meta in deep_soup.find_all('meta'):
            if meta.get('property') == 'og:description':
                desc_meta = meta.get('content', '')
                if '|' in desc_meta:
                    partes = desc_meta.split('|')
                    if len(partes) >= 2:
                        resultado['empresa'] = partes[0].strip()
                        resultado['localizacao'] = partes[1].strip()

        desc_found = False
        DESCRIPTION_SELECTORS = [
            ('div', {'class': re.compile(r'job-description|conteudo-oferta|offer-details', re.I)}),
            ('div', {'id': re.compile(r'description|details', re.I)}),
            ('section', {'class': re.compile(r'description', re.I)})
        ]

        # Strateg 1
        for tag_name, attrs in DESCRIPTION_SELECTORS:
            desc_tag = deep_soup.find(tag_name, attrs)
            if desc_tag:
                text = desc_tag.get_text(separator='\n', strip=True)
                if len(text) > 400:
                    resultado['descricao_completa'] = text
                    desc_found = True
                    break

        if not desc_found:
            article_body = deep_soup.find(id=re.compile(r'article-body|content|main', re.I))
            if article_body:
                resultado['descricao_completa'] = article_body.get_text(separator='\n', strip=True)
                desc_found = True
            
            if not desc_found or len(resultado['descricao_completa']) < 200:
                best_block = ""
                for tag in deep_soup.find_all(['div', 'section', 'article']):
                    if tag.find_parent(['nav', 'header', 'footer']): continue
                    text = tag.get_text(separator='\n', strip=True)
                    if len(text) > len(best_block) and len(text) < 15000:
                        best_block = text
                
                if len(best_block) > 250:
                    resultado['descricao_completa'] = best_block
                    desc_found = True

        # Salary
        salary_tag = deep_soup.find(class_=re.compile(r'salary|salario|remuner', re.I))
        if salary_tag:
            s_text = salary_tag.get_text(strip=True)
            if len(s_text) < 100: resultado['salario'] = s_text

        obs_list = []
        for tag_name, attrs in [('ul', {'class': re.compile(r'job-details|meta|criteria|info-list', re.I)}), ('div', {'class': re.compile(r'badge|chip|tag|meta', re.I)})]:
            for container in deep_soup.find_all(tag_name, attrs):
                for item in container.find_all(['li', 'span', 'div']):
                    t = item.get_text(strip=True)
                    if t and 5 < len(t) < 80 and t not in obs_list:
                        obs_list.append(t)

        if obs_list:
            resultado['observacoes'] = " | ".join(list(dict.fromkeys(obs_list))[:6])

    except Exception as e:
        print(f"  [Expresso Deep] Error: {e}")
    finally:
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(driver.window_handles[0])

    return resultado

def iniciar_scraper_expresso():
    print(f"\n{'='*50}")
    print(f"  Starting Scraper: {PLATAFORMA} (Direct Search)")
    print(f"  Keywords: {KEYWORDS}")
    print(f"{'='*50}")

    urls = generate_expresso_urls()
    print(f"  Generated {len(urls)} search URLs for {PLATAFORMA}.")

    driver = None
    try:
        driver = configurar_driver()
        total_novas_global = 0

        for key_name, search_url in urls.items():
            print(f"\n[Expresso] Search: {key_name}")
            try:
                driver.get(search_url)
                time.sleep(random.uniform(3.0, 5.0))
                
                # Scroll a bit
                driver.execute_script("window.scrollTo(0, 500);")
                time.sleep(1)
                
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                
                job_links = []
                # Find all links on the page that point to an offer
                for a in soup.find_all('a'):
                    if 'href' in a.attrs and '/ofertas/' in a['href']:
                        link = a['href']
                        if not link.startswith('http'):
                            link = 'https://expressoemprego.pt' + link
                        
                        titulo = a.get_text(strip=True)
                        if not titulo or len(titulo) < 5:
                            continue
                            
                        # Avoid duplicates
                        if not any(j['link'] == link for j in job_links):
                            job_links.append({'titulo': titulo, 'link': link})
                
                if not job_links:
                    print("  → No job links found for this search.")
                    continue
                    
                print(f"  → Found {len(job_links)} jobs on search page.")
                
                for j in job_links:
                    titulo = j['titulo']
                    link = j['link']
                    
                    texto_busca = titulo.lower()
                    if not strict_keyword_match(texto_busca, KEYWORDS):
                        continue

                    if NEGATIVE_KEYWORDS and any(nkw in texto_busca for nkw in NEGATIVE_KEYWORDS):
                        continue

                    if not job_exists(link):
                        id_externo = None
                        match = re.search(r'/(\d+)$', link)
                        if match:
                            id_externo = match.group(1)

                        print(f"  [NEW JOB] {titulo}")
                        detalhes = extrair_descricao_expresso(driver, link)

                        salvo = save_job(
                            user_id=USER_ID, plataforma=PLATAFORMA, id_externo=id_externo,
                            titulo=titulo, empresa=detalhes['empresa'], localizacao=detalhes['localizacao'],
                            link=link, data_pub="Recent", categoria="Search",
                            descricao_completa=detalhes['descricao_completa'],
                            observacoes=detalhes['observacoes']
                        )

                        if salvo:
                            total_novas_global += 1
                            print(f"    ✅ Saved: {titulo} @ {detalhes['empresa']}")

                        if MAX_JOBS > 0 and total_novas_global >= MAX_JOBS:
                            break
                            
            except Exception as e:
                print(f"  [Expresso] Error processing search {key_name}: {e}")

            if MAX_JOBS > 0 and total_novas_global >= MAX_JOBS:
                print("  [GLOBAL LIMIT REACHED] Stopping multi-search.")
                break

        print(f"\n{'='*50}")
        print(f"  Expresso Done: {total_novas_global} total new jobs indexed.")
        print(f"{'='*50}")

    except Exception as e:
        print(f"  [Expresso] Pipeline error: {e}")
    finally:
        if driver:
            driver.quit()
            tmp_dir = getattr(driver, '_tmp_profile_dir', None)
            if tmp_dir and os.path.exists(tmp_dir):
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass

if __name__ == '__main__':
    iniciar_scraper_expresso()