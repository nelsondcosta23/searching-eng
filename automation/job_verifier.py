import re
import sqlite3
import sys
import requests
import time
import random
import os
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Patterns to detect expired/unavailable job pages. Defined at module level so
# the regex is compiled once, not once per job verification loop.
_EXPIRY_RE = re.compile(
    r'(expirad|indispon[ií]vel|desativad|removid|no longer available|não encontrad|página não existe|não está activa)',
    re.I,
)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from automation.db_helper import execute_with_retry, transaction

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))

# --- Configuração via variáveis de ambiente (.env) ---
VERIFIER_MAX_JOBS    = int(os.environ.get('VERIFIER_MAX_JOBS', '0'))      # 0 = sem limite
VERIFIER_PAGE_TIMEOUT = int(os.environ.get('VERIFIER_PAGE_TIMEOUT', '15')) # segundos
VERIFIER_SLEEP_BETWEEN = float(os.environ.get('VERIFIER_SLEEP_BETWEEN', '2')) # segundos entre verificações
# Skip jobs verified within the last N days (default 2). Drastically reduces work
# on subsequent runs. Set to 0 to force re-verify everything.
VERIFIER_SKIP_RECENT_DAYS = int(os.environ.get('VERIFIER_SKIP_RECENT_DAYS', '2'))

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

    # Read job list with a SHORT-LIVED connection then close it. The previous
    # design held a single write connection open across HTTP+Selenium probes
    # (potentially hours), starving readers and bloating the WAL. (Audit #25.)
    with transaction() as _conn:
        # Select jobs marked as Active. Skip ones that were re-verified recently —
        # they were already alive in the last VERIFIER_SKIP_RECENT_DAYS, no point
        # hammering Selenium again. (Uses idx_vagas_status_verif COVERING index.)
        if VERIFIER_SKIP_RECENT_DAYS > 0:
            cutoff_clause = "AND (data_ultima_verificacao IS NULL OR data_ultima_verificacao < datetime('now', ?))"
            _cur = _conn.execute(
                f"SELECT id, link, plataforma, titulo FROM vagas WHERE status IN ('Ativa', 'Inacessível') {cutoff_clause}",
                (f'-{VERIFIER_SKIP_RECENT_DAYS} days',),
            )
        else:
            _cur = _conn.execute("SELECT id, link, plataforma, titulo FROM vagas WHERE status IN ('Ativa', 'Inacessível')")
        jobs = _cur.fetchall()
    # _conn is closed here — every UPDATE below uses execute_with_retry().

    def _mark_expired(job_id, ts):
        execute_with_retry(
            "UPDATE vagas SET status = 'Expirada', data_ultima_verificacao = ? WHERE id = ?",
            (ts, job_id),
        )

    def _mark_verified(job_id, ts):
        execute_with_retry(
            "UPDATE vagas SET data_ultima_verificacao = ? WHERE id = ?",
            (ts, job_id),
        )

    def _mark_inacessivel(job_id, ts):
        # Network failure / timeout — not confirmed expired, retry on next run.
        execute_with_retry(
            "UPDATE vagas SET status = 'Inacessível', data_ultima_verificacao = ? WHERE id = ?",
            (ts, job_id),
        )

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
        try:
            time.sleep(random.uniform(VERIFIER_SLEEP_BETWEEN * 0.5, VERIFIER_SLEEP_BETWEEN))

            if 'Sapo' in platform or 'Net-Empregos' in platform:
                response = session.get(link, headers=HEADERS, timeout=15, allow_redirects=True)
                status_code = response.status_code
            else:
                try:
                    response = session.head(link, headers=HEADERS, timeout=10, allow_redirects=True)
                    status_code = response.status_code
                except requests.RequestException:
                    response = session.get(link, headers=HEADERS, timeout=15, allow_redirects=True)
                    status_code = response.status_code
                # Some servers reject HEAD — fall back to GET on 405
                if status_code == 405:
                    response = session.get(link, headers=HEADERS, timeout=15, allow_redirects=True)
                    status_code = response.status_code

            now_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            job_expired = False
            
            if status_code == 404:
                job_expired = True
            elif status_code == 200:
                if 'Sapo' in platform and _EXPIRY_RE.search(response.text):
                    job_expired = True
                elif 'Net-Empregos' in platform and _EXPIRY_RE.search(response.text):
                    job_expired = True
            
            if job_expired:
                print(f"  [EXPIRED] {title[:40]}... ({platform})")
                _mark_expired(job_id, now_date)
                expired_count += 1
            else:
                _mark_verified(job_id, now_date)
            verified_count += 1
        except requests.RequestException as e:
            now_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            print(f"  [INACESSÍVEL] {title[:40]}... — network error: {e}")
            _mark_inacessivel(job_id, now_date)
            continue
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
            driver.set_page_load_timeout(VERIFIER_PAGE_TIMEOUT)

            # Warm-up Indeed
            if any('Indeed' in j[2] for j in selenium_jobs):
                try:
                    driver.get("https://pt.indeed.com/")
                    time.sleep(3)
                except Exception:
                    pass
                
            for job_id, link, platform, title in selenium_jobs:
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
                        _mark_expired(job_id, now_date)
                        expired_count += 1
                    else:
                        _mark_verified(job_id, now_date)
                    verified_count += 1
                except Exception as e:
                    now_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print(f"  [INACESSÍVEL] {title[:40]}... ({platform}) — {e}")
                    _mark_inacessivel(job_id, now_date)
                    continue
        finally:
            if driver:
                driver.quit()

    print(f"\nVerification finished!")
    print(f"- Total processed: {verified_count}")
    print(f"- Jobs marked as Expired: {expired_count}")
    print(f"{'='*50}\n")

if __name__ == '__main__':
    verify_active_jobs()
