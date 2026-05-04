import subprocess
import sys
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR     = os.environ.get('APP_DIR', '/app')
SCRAPERS_DIR = os.path.join(BASE_DIR, 'scrapers')

# ─────────────────────────────────────────────────────────────────────────────
# Scraper Execution Groups
#
# PARALLEL group  → fast RSS/HTTP scrapers (no browser, safe to run together)
# SEQUENTIAL group → Selenium scrapers (heavy Chrome, run one at a time)
# ─────────────────────────────────────────────────────────────────────────────
SCRAPERS_PARALLEL = [
    ('Net-Empregos',  'net_jobs_scraper.py'),
]

SCRAPERS_SEQUENTIAL = [
    ('Expresso Jobs', 'expresso_scraper.py'),
    ('Sapo Jobs',   'sapo_scraper.py'),
    ('Indeed PT',   'indeed_scraper.py'),
    ('LinkedIn PT', 'linkedin_scraper.py'),  # Hybrid: Guest API + Selenium deep extract
]


def run_scraper(name, filename, extra_env=None):
    """Runs a scraper subprocess. Returns (name, success, duration_s)."""
    path = os.path.join(SCRAPERS_DIR, filename)
    t_start = datetime.now()

    print(f"\n{'='*55}")
    print(f"[{t_start.strftime('%H:%M:%S')}] ▶  {name}")
    print(f"{'='*55}")

    env = {**os.environ, 'PYTHONUNBUFFERED': '1'}
    if extra_env:
        env.update(extra_env)

    try:
        result = subprocess.run(
            [sys.executable, path],
            capture_output=True,
            text=True,
            cwd=BASE_DIR,
            timeout=7200,
            env=env,
        )

        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(f"[STDERR — {name}]\n{result.stderr}", file=sys.stderr)

        duration = (datetime.now() - t_start).seconds
        success  = result.returncode == 0

        icon = "✅" if success else "❌"
        print(f"{icon} {name}: {'OK' if success else f'Error (code {result.returncode})'} — {duration}s")
        return name, success, duration

    except subprocess.TimeoutExpired:
        duration = (datetime.now() - t_start).seconds
        print(f"❌ {name}: Killed — exceeded 7200s timeout.")
        return name, False, duration
    except Exception as e:
        duration = (datetime.now() - t_start).seconds
        print(f"❌ {name}: Unexpected error — {e}")
        return name, False, duration


def run_parallel(scrapers):
    """Runs scrapers in parallel threads."""
    results = {}
    if not scrapers:
        return results

    print(f"\n{'#'*55}")
    print(f"# PARALLEL PHASE — {', '.join(n for n, _ in scrapers)}")
    print(f"{'#'*55}")

    with ThreadPoolExecutor(max_workers=len(scrapers)) as executor:
        futures = {executor.submit(run_scraper, name, fn): name for name, fn in scrapers}
        for future in as_completed(futures):
            name, success, duration = future.result()
            results[name] = {'success': success, 'duration': duration}
    return results


def run_sequential(scrapers):
    """Runs scrapers one after another."""
    results = {}
    if not scrapers:
        return results

    print(f"\n{'#'*55}")
    print(f"# SEQUENTIAL PHASE — {len(scrapers)} scrapers")
    print(f"{'#'*55}")

    for name, filename in scrapers:
        _, success, duration = run_scraper(name, filename)
        results[name] = {'success': success, 'duration': duration}
    return results


def run_scorer():
    """Runs the TF-IDF relevance scorer to score all new unscored jobs."""
    print(f"\n{'#'*55}")
    print("# SCORING PHASE — Computing relevance scores")
    print(f"{'#'*55}")
    scorer_path = os.path.join(BASE_DIR, 'automation', 'job_scorer.py')
    subprocess.run([sys.executable, scorer_path], cwd=BASE_DIR)


def run_verifier():
    """Runs the job verifier to mark expired jobs."""
    print(f"\n{'#'*55}")
    print("# VERIFY PHASE — Checking for expired job links")
    print(f"{'#'*55}")
    verifier_path = os.path.join(BASE_DIR, 'automation', 'job_verifier.py')
    subprocess.run([sys.executable, verifier_path], cwd=BASE_DIR)


def run_webhook_dispatcher():
    """Pushes today's top jobs to each user's registered callback URL."""
    print(f"\n{'#'*55}")
    print("# WEBHOOK PHASE — Dispatching results to external software")
    print(f"{'#'*55}")
    dispatcher_path = os.path.join(BASE_DIR, 'automation', 'webhook_dispatcher.py')
    subprocess.run([sys.executable, dispatcher_path], cwd=BASE_DIR)


def main():
    t_start = datetime.now()

    # Count expected searches for info
    try:
        sys.path.insert(0, BASE_DIR)
        from automation.profile_fetcher import generate_linkedin_urls, generate_sapo_urls, get_priority_queries, get_standard_queries
        li_urls    = generate_linkedin_urls()
        prio_count = sum(1 for v in li_urls.values() if v.get('is_priority'))
        std_count  = sum(1 for v in li_urls.values() if not v.get('is_priority'))
        sapo_count = len(generate_sapo_urls())
    except Exception:
        prio_count = std_count = sapo_count = '?'

    print(f"\n{'#'*60}")
    print(f"# SCRAPER ORCHESTRATOR — Pro Edition")
    print(f"# Start: {t_start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"# LinkedIn: {prio_count} priority (4p) + {std_count} standard (2p)")
    print(f"# Sapo: {sapo_count} national searches")
    print(f"{'#'*60}")

    # ── Phase 1: Fast RSS scrapers in parallel ──────────────────────────────
    results_parallel = run_parallel(SCRAPERS_PARALLEL)

    # ── Phase 2: Selenium scrapers sequentially ─────────────────────────────
    results_sequential = run_sequential(SCRAPERS_SEQUENTIAL)

    # ── Phase 3: Relevance Scoring ──────────────────────────────────────────
    run_scorer()

    # ── Phase 4: Expired Link Verification ─────────────────────────────────
    run_verifier()

    # ── Phase 5: Webhook — Push results to external software ────────────────
    run_webhook_dispatcher()

    # ── Final Summary ────────────────────────────────────────────────────────
    all_results = {**results_parallel, **results_sequential}
    t_end = datetime.now()
    elapsed = t_end - t_start
    mins, secs = divmod(elapsed.seconds, 60)

    print(f"\n{'#'*60}")
    print(f"# FINAL SUMMARY — Duration: {mins}m {secs}s")
    print(f"{'#'*60}")
    for scraper_name, info in all_results.items():
        icon = "✅" if info['success'] else "❌"
        print(f"  {icon} {scraper_name:25s} ({info['duration']}s)")
    print(f"\n[DONE] Finished at: {t_end.strftime('%H:%M:%S')}")


if __name__ == '__main__':
    main()
