import subprocess
import sys
import os
import time
import argparse
import sqlite3
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR     = os.environ.get('APP_DIR', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
SCRAPERS_DIR = os.path.join(BASE_DIR, 'scrapers')
DB_PATH      = os.path.join(BASE_DIR, 'database', 'vagas.db')

# Scraper Registry
SCRAPER_REGISTRY = {
    'sapo_scraper.py':      {'name': 'Sapo Jobs',     'timeout': 1800},
    'linkedin_scraper.py':  {'name': 'LinkedIn PT',   'timeout': 1800},
    'companies_scraper.py': {'name': 'Companies',     'timeout': 600},
    'itjobs_scraper.py':    {'name': 'ITJobs',        'timeout': 600},
    'indeed_scraper.py':    {'name': 'Indeed PT',     'timeout': 1800},
    'landing_scraper.py':   {'name': 'Landing.jobs',  'timeout': 300},
    'expresso_scraper.py':  {'name': 'Expresso Jobs', 'timeout': 1800},
}

def run_scraper(name: str, filename: str) -> bool:
    """Runs a single scraper script as a subprocess."""
    path = os.path.join(SCRAPERS_DIR, filename)
    timeout = SCRAPER_REGISTRY.get(filename, {}).get('timeout', 1800)
    t_start = datetime.now()

    print(f"\n{'='*55}")
    print(f"[{t_start.strftime('%H:%M:%S')}] Starting: {name} (timeout {timeout}s)")
    print(f"{'='*55}")

    env = {**os.environ, 'PYTHONUNBUFFERED': '1'}
    if 'DISPLAY' not in env and sys.platform.startswith('linux'):
        env['DISPLAY'] = ':99'

    try:
        result = subprocess.run(
            [sys.executable, path],
            capture_output=False, # Print stdout/stderr in real time to terminal
            cwd=BASE_DIR, timeout=timeout, env=env,
        )
        duration = int((datetime.now() - t_start).total_seconds())
        success = result.returncode == 0
        print(f"\n{'✅' if success else '❌'} {name}: {'Completed Successfully' if success else f'Error ({result.returncode})'} — {duration}s")
        return success
    except subprocess.TimeoutExpired:
        print(f"❌ {name}: Terminated — exceeded {timeout}s timeout")
        return False
    except Exception as e:
        print(f"❌ {name}: Unexpected error — {e}")
        return False

def post_process_jobs():
    """Performs post-processing: job classification, company enrichment, and age calculation."""
    print(f"\n{'='*55}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] RUNNING POST-PROCESSING (Classification & Enrichment)")
    print(f"{'='*55}")
    
    # 1. Classification
    print("\n[post-process] Classifying newly scraped jobs...")
    from automation.job_classifier import classify_job
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        
        # Get all unclassified active jobs
        unclassified = conn.execute("SELECT id, titulo, descricao FROM jobs WHERE job_type IS NULL").fetchall()
        if unclassified:
            print(f"  Found {len(unclassified)} unclassified jobs.")
            updates = []
            for r in unclassified:
                jt = classify_job(r['titulo'], r['descricao'] or '')
                updates.append((jt, r['id']))
                
            conn.executemany("UPDATE jobs SET job_type = ? WHERE id = ?", updates)
            conn.commit()
            print(f"  Categorized {len(updates)} jobs.")
        else:
            print("  No new unclassified jobs.")
            
        # 2. Purge non-tech
        deleted = conn.execute("DELETE FROM jobs WHERE job_type = 'Non-tech'")
        conn.commit()
        if deleted.rowcount > 0:
            print(f"  Purged {deleted.rowcount} non-tech jobs from the database.")
            
        # 3. Enrich Companies
        print("\n[post-process] Enriching company ages from Wikidata...")
        from automation.company_enricher import get_or_enrich_company
        
        # Fetch companies of active tech jobs that are not yet cached
        uncached_companies = conn.execute("""
            SELECT DISTINCT j.empresa 
            FROM jobs j
            LEFT JOIN companies c ON LOWER(j.empresa) = LOWER(c.name)
            WHERE j.status = 'Ativa' AND j.job_type != 'Non-tech' AND c.name IS NULL
        """).fetchall()
        
        if uncached_companies:
            print(f"  Found {len(uncached_companies)} new companies to enrich.")
            for row in uncached_companies:
                co_name = row['empresa']
                if co_name:
                    year, age = get_or_enrich_company(co_name)
                    if year:
                        print(f"    Enriched '{co_name}': Founded in {year} (Age: {age})")
                    else:
                        print(f"    Enriched '{co_name}': Wikidata details unknown")
        else:
            print("  All active companies are already cached.")
            
        # 4. Update Vacancy Ages
        print("\n[post-process] Calculating posting age statistics...")
        from automation.job_analytics import update_all_posting_ages
        update_all_posting_ages()
        
    except Exception as e:
        print(f"[post-process] Post-processing failed: {e}")
    finally:
        if conn:
            conn.close()

def main():
    parser = argparse.ArgumentParser(description='On-demand Tech Job-Market Scraper & Intelligence Tool')
    parser.add_argument('--scrapers', type=str, help='Comma-separated list of scrapers to run. Omit to run all.')
    args = parser.parse_args()

    t_start = datetime.now()
    
    # Resolve which scrapers to run
    if args.scrapers:
        targets = [s.strip() for s in args.scrapers.split(',')]
        run_list = []
        for t in targets:
            # Match either exact name, name with .py, or friendly name
            matched = False
            for fname, info in SCRAPER_REGISTRY.items():
                if t.lower() in fname.lower() or t.lower() in info['name'].lower():
                    run_list.append((info['name'], fname))
                    matched = True
                    break
            if not matched:
                print(f"Unknown scraper target: {t}")
    else:
        run_list = [(info['name'], fname) for fname, info in SCRAPER_REGISTRY.items()]

    print(f"\n{'#'*60}")
    print(f"# TECH JOB MARKET INTELLIGENCE RUN")
    print(f"# Start: {t_start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"# Scrapers to execute: {len(run_list)}")
    print(f"{'#'*60}")

    for name, fname in run_list:
        run_scraper(name, fname)

    # Post-process classifications and metadata
    post_process_jobs()

    # Run Link Verifier to check expired jobs
    print(f"\n{'='*55}")
    print(f"# VERIFIER — Checking active jobs validity")
    print(f"{'='*55}")
    verifier_path = os.path.join(BASE_DIR, 'automation', 'job_verifier.py')
    subprocess.run([sys.executable, verifier_path], cwd=BASE_DIR)

    # Generate final report overview to terminal
    from automation.job_analytics import generate_markdown_report
    print("\n" + "="*60)
    print("                JOB MARKET INTELLIGENCE REPORT")
    print("="*60)
    print(generate_markdown_report())
    print("="*60)

    duration = int((datetime.now() - t_start).total_seconds())
    print(f"\nIntelligence run completed in {duration // 60}m {duration % 60}s.")

if __name__ == '__main__':
    main()
