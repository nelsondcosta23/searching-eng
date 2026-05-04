import sqlite3
import requests
import time
import random
import os
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))

# --- Configuração via variáveis de ambiente (.env) ---
VERIFIER_MAX_JOBS    = int(os.environ.get('VERIFIER_MAX_JOBS', '0'))      # 0 = sem limite
VERIFIER_PAGE_TIMEOUT = int(os.environ.get('VERIFIER_PAGE_TIMEOUT', '15')) # segundos
VERIFIER_SLEEP_BETWEEN = float(os.environ.get('VERIFIER_SLEEP_BETWEEN', '2')) # segundos entre verificações

# Session Configuration with Retries
session = requests.Session()
retries = Retry(total=2, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount('http://', HTTPAdapter(max_retries=retries))
session.mount('https://', HTTPAdapter(max_retries=retries))

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
}

def verify_active_jobs():
    print(f"\n{'='*50}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] STARTING VALIDITY CHECK")
    print(f"{'='*50}")

    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}. Ensure the db exists before verifying.")
        return

    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute('PRAGMA journal_mode=WAL;')
    cursor = conn.cursor()

    # Select jobs marked as Active
    cursor.execute("SELECT id, link, plataforma, titulo FROM vagas WHERE status = 'Ativa'")
    jobs = cursor.fetchall()

    # Apply max jobs limit if configured
    if VERIFIER_MAX_JOBS > 0:
        jobs = jobs[:VERIFIER_MAX_JOBS]
        print(f"[VERIFIER] Limit active: checking only the first {VERIFIER_MAX_JOBS} jobs.")

    print(f"Found {len(jobs)} jobs to verify.")

    expired_count = 0
    verified_count = 0

    regular_jobs = [j for j in jobs if 'Indeed' not in j[2] and 'LinkedIn' not in j[2]]
    selenium_jobs = [j for j in jobs if 'Indeed' in j[2] or 'LinkedIn' in j[2]]

    # 1. Process regular jobs with requests
    for job_id, link, platform, title in regular_jobs:
        verified_count += 1
        try:
            time.sleep(random.uniform(VERIFIER_SLEEP_BETWEEN * 0.5, VERIFIER_SLEEP_BETWEEN))
            
            if 'Sapo' in platform or 'Net-Empregos' in platform:
                response = session.get(link, headers=HEADERS, timeout=15, allow_redirects=True)
                status_code = response.status_code
            else:
                try:
                    response = session.head(link, headers=HEADERS, timeout=10, allow_redirects=True)
                    status_code = response.status_code
                except:
                    response = session.get(link, headers=HEADERS, timeout=15, allow_redirects=True)
                    status_code = response.status_code

            now_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            job_expired = False
            
            if status_code == 404:
                job_expired = True
            elif status_code == 200:
                if 'Sapo' in platform and 'Esta oferta já não se encontra disponível' in response.text:
                    job_expired = True
                elif 'Net-Empregos' in platform and ('página não existe' in response.text or 'Esta oferta já não está activa' in response.text):
                    job_expired = True
            
            if job_expired:
                print(f"  [EXPIRED] {title[:40]}... ({platform})")
                cursor.execute("UPDATE vagas SET status = 'Expirada', data_ultima_verificacao = ? WHERE id = ?", (now_date, job_id))
                expired_count += 1
            else:
                cursor.execute("UPDATE vagas SET data_ultima_verificacao = ? WHERE id = ?", (now_date, job_id))

            conn.commit()
        except Exception as e:
            print(f"Error processing {job_id}: {e}")
            continue

    # 2. Process complex jobs with Selenium (Indeed/LinkedIn)
    if selenium_jobs:
        import undetected_chromedriver as uc
        print(f"\nInitializing Selenium for {len(selenium_jobs)} complex jobs...")
        
        options = uc.ChromeOptions()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
        options.add_argument("--lang=pt-PT,pt;q=0.9,en;q=0.8")
        
        driver = None
        try:
            driver = uc.Chrome(options=options, headless=True, version_main=147)
            
            # Warm-up Indeed
            if any('Indeed' in j[2] for j in selenium_jobs):
                try:
                    driver.get("https://pt.indeed.com/")
                    time.sleep(3)
                except: pass
                
            for job_id, link, platform, title in selenium_jobs:
                verified_count += 1
                try:
                    driver.get(link)
                    
                    now_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    job_expired = False
                    
                    # Polling loop: Verifica até 3 vezes (com 2s de intervalo) 
                    # porque os alertas do LinkedIn/Indeed podem demorar a carregar (React/JS)
                    for attempt in range(3):
                        time.sleep(2)
                        
                        source = driver.page_source
                        source_lower = source.lower()
                        title_lower = driver.title.lower()
                        current_url = driver.current_url.lower()
                        
                        if 'Indeed' in platform:
                            if "não encontrado" in title_lower or "não se encontra disponível" in source_lower or "não foi encontrada" in source_lower or "not available" in source_lower:
                                job_expired = True
                        elif 'LinkedIn' in platform:
                            if "no longer accepting" in source_lower or "não aceita mais candidaturas" in source_lower or "não está mais aceitando" in source_lower or "no longer available" in source_lower or "expired" in current_url or "/search" in current_url or "closed-job" in source_lower:
                                job_expired = True
                                
                        if job_expired:
                            break # Encontrou a expiração, não precisa esperar mais
                            
                    if job_expired:
                        print(f"  [EXPIRED] {title[:40]}... ({platform})")
                        cursor.execute("UPDATE vagas SET status = 'Expirada', data_ultima_verificacao = ? WHERE id = ?", (now_date, job_id))
                        expired_count += 1
                    else:
                        cursor.execute("UPDATE vagas SET data_ultima_verificacao = ? WHERE id = ?", (now_date, job_id))
                        
                    conn.commit()
                except Exception as e:
                    print(f"Error processing {job_id}: {e}")
                    continue
        finally:
            if driver:
                driver.quit()

    conn.commit()
    conn.close()

    print(f"\nVerification finished!")
    print(f"- Total processed: {verified_count}")
    print(f"- Jobs marked as Expired: {expired_count}")
    print(f"{'='*50}\n")

if __name__ == '__main__':
    verify_active_jobs()
