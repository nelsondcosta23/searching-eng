import subprocess
import sys
import os
from datetime import datetime

# Base paths
BASE_DIR   = os.environ.get('APP_DIR', '.')
SCRAPERS_DIR = os.path.join(BASE_DIR, 'scrapers')

# Execution order of scrapers
SCRAPERS = [
    ('Expresso Jobs', 'expresso_scraper.py'),
    ('Sapo Jobs',     'sapo_scraper.py'),
    ('Net-Jobs',      'net_jobs_scraper.py'),
    ('Indeed PT',     'indeed_scraper.py'),
    ('LinkedIn PT',   'linkedin_scraper.py'),
]

def run_scraper(name, filename):
    """Executes a scraper and returns the output."""
    path = os.path.join(SCRAPERS_DIR, filename)
    print(f"\n{'='*50}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting: {name}")
    print(f"{'='*50}")
    
    try:
        result = subprocess.run(
            [sys.executable, path],
            capture_output=True,
            text=True,
            cwd=BASE_DIR,
            timeout=3600
        )
        
        # Print scraper output
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(f"[STDERR] {result.stderr}", file=sys.stderr)
        
        if result.returncode == 0:
            print(f"✅ {name}: Completed successfully.")
        else:
            print(f"❌ {name}: Terminated with error (code {result.returncode}).")
        
        return result.returncode == 0
    except subprocess.TimeoutExpired as e:
        print(f"❌ {name}: Terminated due to global timeout ({e.timeout}s). Scraper hung.")
        return False
    except Exception as e:
        print(f"❌ {name}: Unexpected error when running script: {e}")
        return False

def main():
    start_time = datetime.now()
    print(f"\n{'#'*50}")
    print(f"# SCRAPER ORCHESTRATOR")
    print(f"# Start: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*50}")

    results = {}
    for name, filename in SCRAPERS:
        success = run_scraper(name, filename)
        results[name] = success

    # 3. Execute Job Verifier at the end
    print("\n" + "#"*50)
    print("# FINAL STEP: CHECKING EXPIRED JOBS")
    print("#"*50)
    
    verifier_path = os.path.join(BASE_DIR, 'automation', 'job_verifier.py')
    subprocess.run([sys.executable, verifier_path], cwd=BASE_DIR)

    end_time = datetime.now()
    duration = (end_time - start_time).seconds // 60
    
    print(f"\n{'#'*50}")
    print(f"# FINAL SUMMARY — Duration: {duration} min")
    print(f"{'#'*50}")
    for name, success in results.items():
        state = "✅ OK" if success else "❌ FAILED"
        print(f"  {state} — {name}")
    print(f"\n[OK] Orchestration finished at: {end_time.strftime('%H:%M:%S')}")

if __name__ == '__main__':
    main()
