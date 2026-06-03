import re
import sqlite3
import sys
import requests
import time
import random
import os
from datetime import datetime
from typing import Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Add project root to path for shared modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Patterns to detect expired/unavailable job pages
_EXPIRY_RE = re.compile(
    r'(expirad|indispon[ií]vel|desativad|removid|no longer available|não encontrad|página não existe|não está activa)',
    re.I,
)

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))

VERIFIER_MAX_JOBS    = int(os.environ.get('VERIFIER_MAX_JOBS', '0'))      # 0 = sem limite
VERIFIER_PAGE_TIMEOUT = int(os.environ.get('VERIFIER_PAGE_TIMEOUT', '15')) # segundos
VERIFIER_SLEEP_BETWEEN = float(os.environ.get('VERIFIER_SLEEP_BETWEEN', '2')) # segundos entre verificações
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

    # Read active jobs to verify
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        conn.row_factory = sqlite3.Row
        
        if VERIFIER_SKIP_RECENT_DAYS > 0:
            # Skip recently verified jobs to save requests/time
            cutoff = f"-{VERIFIER_SKIP_RECENT_DAYS} days"
            cursor = conn.execute(
                "SELECT id, link, plataforma, titulo FROM jobs "
                "WHERE status IN ('Ativa', 'Inacessível') "
                "AND (data_scraped <= datetime('now', ?) OR data_scraped IS NULL)", (cutoff,)
            )
        else:
            cursor = conn.execute("SELECT id, link, plataforma, titulo FROM jobs WHERE status IN ('Ativa', 'Inacessível')")
            
        jobs = [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        print(f"Error fetching active jobs: {e}")
        return
    finally:
        if conn:
            conn.close()

    if VERIFIER_MAX_JOBS > 0:
        jobs = jobs[:VERIFIER_MAX_JOBS]
        print(f"[VERIFIER] Limit active: checking only the first {VERIFIER_MAX_JOBS} jobs.")

    print(f"Found {len(jobs)} active jobs to verify.")
    if not jobs:
        return

    def _update_status(job_id: int, status: str):
        try:
            from automation.db_helper import execute_with_retry
            execute_with_retry(
                "UPDATE jobs SET status = ? WHERE id = ?",
                (status, job_id)
            )
        except Exception as e:
            print(f"  Failed to update job status: {e}")

    expired_count = 0
    verified_count = 0

    regular_jobs = [j for j in jobs if 'Indeed' not in j['plataforma'] and 'LinkedIn' not in j['plataforma']]
    selenium_jobs = [j for j in jobs if 'Indeed' in j['plataforma'] or 'LinkedIn' in j['plataforma']]

    # 1. Process regular jobs with requests
    for job in regular_jobs:
        job_id, link, platform, title = job['id'], job['link'], job['plataforma'], job['titulo']
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
                if status_code == 405:
                    response = session.get(link, headers=HEADERS, timeout=15, allow_redirects=True)
                    status_code = response.status_code

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
                _update_status(job_id, 'Expirada')
                expired_count += 1
            else:
                # Keep active
                verified_count += 1
        except requests.RequestException as e:
            print(f"  [INACESSÍVEL] {title[:40]}... — network error: {e}")
            _update_status(job_id, 'Inacessível')
        except Exception as e:
            print(f"Error processing {job_id}: {e}")

    # 2. Process complex jobs with Selenium (Indeed/LinkedIn)
    if selenium_jobs:
        import subprocess as _sub
        import re as _re
        import undetected_chromedriver as uc
        from scrapers._shared import init_chrome_with_timeout

        def _detect_chrome_version() -> Optional[int]:
            try:
                r = _sub.run(['google-chrome', '--version'], capture_output=True, text=True, timeout=5)
                m = _re.search(r'(\d+)\.', r.stdout)
                return int(m.group(1)) if m else None
            except Exception:
                return None

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
            chrome_ver = _detect_chrome_version()
            driver = init_chrome_with_timeout(options, headless=True, version_main=chrome_ver)
            driver.set_page_load_timeout(VERIFIER_PAGE_TIMEOUT)

            # Warm-up Indeed
            if any('Indeed' in j['plataforma'] for j in selenium_jobs):
                try:
                    driver.get("https://pt.indeed.com/")
                    time.sleep(3)
                except Exception:
                    pass
                
            for job in selenium_jobs:
                job_id, link, platform, title = job['id'], job['link'], job['plataforma'], job['titulo']
                try:
                    driver.get(link)
                    job_expired = False
                    
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
                            break
                            
                    if job_expired:
                        print(f"  [EXPIRED] {title[:40]}... ({platform})")
                        _update_status(job_id, 'Expirada')
                        expired_count += 1
                    else:
                        verified_count += 1
                except Exception as e:
                    print(f"  [INACESSÍVEL] {title[:40]}... ({platform}) — {e}")
                    _update_status(job_id, 'Inacessível')
        finally:
            if driver:
                driver.quit()

    print(f"\nVerification finished!")
    print(f"- Total active / checked: {verified_count + expired_count}")
    print(f"- Jobs marked as Expired: {expired_count}")
    print(f"{'='*50}\n")

if __name__ == '__main__':
    verify_active_jobs()
