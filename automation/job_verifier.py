import sqlite3
import requests
import time
import random
import os
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))

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
    
    print(f"Found {len(jobs)} jobs to verify.")

    expired_count = 0
    verified_count = 0

    for job_id, link, platform, title in jobs:
        verified_count += 1
        try:
            # Small pause between checks to avoid overloading
            time.sleep(random.uniform(0.5, 1.5))
            
            # Try a HEAD first (lighter)
            if 'Sapo' in platform or 'Net-Empregos' in platform:
                # We need the response body to check for specific expiration text
                response = session.get(link, headers=HEADERS, timeout=15, allow_redirects=True)
                status_code = response.status_code
            else:
                try:
                    response = session.head(link, headers=HEADERS, timeout=10, allow_redirects=True)
                    status_code = response.status_code
                except:
                    # If HEAD fails, try a GET (some sites block HEAD)
                    response = session.get(link, headers=HEADERS, timeout=15, allow_redirects=True)
                    status_code = response.status_code

            now_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # Expiration logic based on HTTP code or content
            job_expired = False
            
            if status_code == 404:
                job_expired = True
            elif status_code == 200:
                # Platform-specific checks (if page says "Job Missing" but returns 200)
                if 'Sapo' in platform and 'Esta oferta já não se encontra disponível' in response.text:
                    job_expired = True
                elif 'Net-Empregos' in platform and 'página não existe' in response.text:
                    job_expired = True
            
            if job_expired:
                print(f"  [EXPIRED] {title[:40]}... ({platform})")
                cursor.execute("UPDATE vagas SET status = 'Expirada', data_ultima_verificacao = ? WHERE id = ?", (now_date, job_id))
                expired_count += 1
            else:
                # Just update verification date
                cursor.execute("UPDATE vagas SET data_ultima_verificacao = ? WHERE id = ?", (now_date, job_id))

            # Commit every 10 to save progress
            if verified_count % 10 == 0:
                conn.commit()

        except Exception as e:
            # print(f"  [ERROR] Verifying {title[:20]}: {e}")
            continue

    conn.commit()
    conn.close()

    print(f"\nVerification finished!")
    print(f"- Total processed: {verified_count}")
    print(f"- Jobs marked as Expired: {expired_count}")
    print(f"{'='*50}\n")

if __name__ == '__main__':
    verify_active_jobs()
