"""
Job Search Orchestrator — Multi-user, priority-based, with random jitter.

Execution model:
  1. Jitter: sleeps a random delay (0–JITTER_MAX_MINUTES) before starting,
     so cron-triggered runs happen at unpredictable times within each window.
  2. Multi-user: automatically iterates ALL active users in users_perfil.
     TARGET_USER_ID env var overrides to a single user (for manual runs).
  3. Tiered scraping (best → worst quality, by QA score):
       Tier 1  Sapo (72) + LinkedIn (71)          — Selenium, best signal
       Tier 2  Companies (64) + ITJobs (55)        — API, fast & reliable
       Tier 3  Indeed (58) + Landing (35) + Expresso (38) — last resort
     After each tier, if cumulative new jobs ≥ MIN_TIER_YIELD the lower
     tiers are skipped — saving 20–50 min of Selenium time per user.
  4. Post-processing per user: scorer + enrichment.
     Verifier + webhook run once at the end (all users).
"""
import subprocess
import sys
import os
import time
import random
import argparse
import sqlite3
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR     = os.environ.get('APP_DIR', '/app')
SCRAPERS_DIR = os.path.join(BASE_DIR, 'scrapers')
DB_PATH      = os.path.join(BASE_DIR, 'database', 'vagas.db')

# ─────────────────────────────────────────────────────────────────────────────
# Scraper tiers — ordered best-to-worst by QA relevance score
# ─────────────────────────────────────────────────────────────────────────────
# Tier 1: highest quality signal — run first, sequential (Selenium)
TIER_1_SEQUENTIAL = [
    ('Sapo Jobs',   'sapo_scraper.py'),      # QA score 72 — best avg relevance
    ('LinkedIn PT', 'linkedin_scraper.py'),  # QA score 71 — real CTO/Lead roles
]

# Tier 2: API-based, fast, reliable — run in parallel
TIER_2_PARALLEL = [
    ('Companies',    'companies_scraper.py'),  # QA score 64 — ATS APIs
    ('ITJobs',       'itjobs_scraper.py'),      # QA score 55 — PT platform
]

# Tier 3: last resort — only if tiers 1+2 yielded too little
TIER_3_SEQUENTIAL = [
    ('Indeed PT',    'indeed_scraper.py'),   # QA score 58 — low yield
    ('Landing.jobs', 'landing_scraper.py'),  # QA score 35 — poor relevance
    ('Expresso Jobs','expresso_scraper.py'), # QA score 38 — very low yield
]

# Skip lower tiers when cumulative new jobs for this user reaches this threshold.
# Env var allows per-deployment tuning without code changes.
MIN_TIER_YIELD = int(os.environ.get('MIN_TIER_YIELD', '5'))

# Global lock — prevents two orchestrator instances from running simultaneously.
GLOBAL_LOCK = os.path.join(BASE_DIR, 'database', 'orchestrator.lock')


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_active_users() -> list[str]:
    """Returns all active user_ids from users_perfil.

    If TARGET_USER_ID is set in the environment (e.g. for a manual run),
    returns only that user — no DB query needed.
    """
    override = os.environ.get('TARGET_USER_ID', '').strip()
    if override:
        return [override]
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as c:
            rows = c.execute(
                'SELECT user_id FROM users_perfil WHERE is_active = 1 ORDER BY created_at'
            ).fetchall()
            return [r[0] for r in rows if r[0]]
    except Exception as e:
        print(f'[orchestrator] Could not load users from DB: {e}')
        return []


def count_new_jobs(user_id: str, since: datetime) -> int:
    """Returns jobs saved for user_id since the given datetime."""
    try:
        since_str = since.strftime('%Y-%m-%d %H:%M:%S')
        with sqlite3.connect(DB_PATH, timeout=10) as c:
            return c.execute(
                'SELECT COUNT(*) FROM vagas WHERE user_id = ? AND data_scraped >= ?',
                (user_id, since_str)
            ).fetchone()[0]
    except Exception:
        return 0


def run_scraper(name: str, filename: str) -> tuple[str, bool, int]:
    """Runs a single scraper subprocess. Returns (name, success, duration_s)."""
    path = os.path.join(SCRAPERS_DIR, filename)
    t_start = datetime.now()

    print(f"\n{'='*55}")
    print(f"[{t_start.strftime('%H:%M:%S')}] ▶  {name}")
    print(f"{'='*55}")

    env = {**os.environ, 'PYTHONUNBUFFERED': '1'}
    if 'DISPLAY' not in env:
        env['DISPLAY'] = ':99'

    try:
        result = subprocess.run(
            [sys.executable, path],
            capture_output=True, text=True,
            cwd=BASE_DIR, timeout=7200, env=env,
        )
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(f'[STDERR — {name}]\n{result.stderr}', file=sys.stderr)

        duration = int((datetime.now() - t_start).total_seconds())
        success  = result.returncode == 0
        print(f"{'✅' if success else '❌'} {name}: {'OK' if success else f'Error ({result.returncode})'} — {duration}s")
        return name, success, duration

    except subprocess.TimeoutExpired:
        duration = int((datetime.now() - t_start).total_seconds())
        print(f'❌ {name}: Killed — exceeded 7200s timeout.')
        return name, False, duration
    except Exception as e:
        duration = int((datetime.now() - t_start).total_seconds())
        print(f'❌ {name}: Unexpected error — {e}')
        return name, False, duration


def run_sequential(scrapers: list) -> dict:
    """Runs scrapers one after another. Detects Chrome version once."""
    results = {}
    if not scrapers:
        return results
    _detect_chrome_version()
    for name, filename in scrapers:
        _, success, duration = run_scraper(name, filename)
        results[name] = {'success': success, 'duration': duration}
    return results


def run_parallel(scrapers: list) -> dict:
    """Runs scrapers concurrently (API-based, safe to parallelise)."""
    results = {}
    if not scrapers:
        return results
    with ThreadPoolExecutor(max_workers=len(scrapers)) as executor:
        futures = {executor.submit(run_scraper, name, fn): name for name, fn in scrapers}
        for future in as_completed(futures):
            name, success, duration = future.result()
            results[name] = {'success': success, 'duration': duration}
    return results


def _detect_chrome_version() -> str:
    """Detects installed Chrome major version once; caches in CHROME_VERSION."""
    if os.environ.get('CHROME_VERSION'):
        return os.environ['CHROME_VERSION']
    try:
        import re as _re
        result = subprocess.run(
            ['google-chrome', '--version'],
            capture_output=True, text=True, timeout=5, cwd=BASE_DIR,
        )
        m = _re.search(r'(\d+)\.', result.stdout)
        if m:
            os.environ['CHROME_VERSION'] = m.group(1)
            print(f'  [Chrome] Detected version: {m.group(1)}')
            return m.group(1)
    except Exception as e:
        print(f'  [Chrome] Version detection failed: {e}')
    return ''


def _run_phase(name: str, script_path: str, extra_args: list = None) -> bool:
    """Runs a post-processing phase script. Returns True on success."""
    cmd = [sys.executable, script_path] + (extra_args or [])
    result = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        print(f'❌ {name} failed (exit {result.returncode}).')
    return result.returncode == 0


def _check_zero_yield(results: dict, user_id: str, run_start: datetime):
    """Warns when a scraper ran OK but contributed 0 jobs for this user."""
    try:
        since_str = run_start.strftime('%Y-%m-%d %H:%M:%S')
        with sqlite3.connect(DB_PATH, timeout=10) as c:
            rows = c.execute(
                'SELECT plataforma, COUNT(*) FROM vagas '
                'WHERE user_id=? AND data_scraped>=? GROUP BY plataforma',
                (user_id, since_str)
            ).fetchall()
        by_plat = {r[0]: r[1] for r in rows}
        zero = [
            name for name, info in results.items()
            if info.get('success') and
               not any(name.lower().split()[0] in k.lower() for k in by_plat)
        ]
        if zero:
            print(f'\n⚠  ZERO-YIELD: ran OK but saved 0 jobs: {zero}')
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Per-user scraping run
# ─────────────────────────────────────────────────────────────────────────────

def run_for_user(user_id: str) -> dict:
    """Runs tiered scraping + post-processing for a single user.

    Returns combined scraper results dict.
    """
    os.environ['TARGET_USER_ID'] = user_id
    uid_short = user_id[:8]
    run_start = datetime.now()

    print(f"\n{'#'*60}")
    print(f"# USER: {user_id}")
    print(f"# Start: {run_start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"# MIN_TIER_YIELD: {MIN_TIER_YIELD} new jobs per tier")
    print(f"{'#'*60}")

    all_results: dict = {}

    # ── Tier 1: Best quality (Selenium) ────────────────────────────────────
    print(f"\n{'#'*55}")
    print(f"# TIER 1 — Quality scrapers: {[n for n,_ in TIER_1_SEQUENTIAL]}")
    print(f"{'#'*55}")
    all_results.update(run_sequential(TIER_1_SEQUENTIAL))
    tier1_jobs = count_new_jobs(user_id, run_start)
    print(f'\n[tier1] New jobs found: {tier1_jobs}')

    if tier1_jobs >= MIN_TIER_YIELD:
        print(f'[tier1] ✅ Threshold met ({tier1_jobs} ≥ {MIN_TIER_YIELD}) — skipping Tiers 2 & 3')
    else:
        # ── Tier 2: API scrapers (fast) ─────────────────────────────────────
        print(f"\n{'#'*55}")
        print(f"# TIER 2 — API scrapers: {[n for n,_ in TIER_2_PARALLEL]}")
        print(f"{'#'*55}")
        all_results.update(run_parallel(TIER_2_PARALLEL))
        tier2_jobs = count_new_jobs(user_id, run_start)
        print(f'\n[tier2] Cumulative new jobs: {tier2_jobs}')

        if tier2_jobs >= MIN_TIER_YIELD:
            print(f'[tier2] ✅ Threshold met ({tier2_jobs} ≥ {MIN_TIER_YIELD}) — skipping Tier 3')
        else:
            # ── Tier 3: Last resort ──────────────────────────────────────────
            print(f"\n{'#'*55}")
            print(f"# TIER 3 — Last resort: {[n for n,_ in TIER_3_SEQUENTIAL]}")
            print(f"{'#'*55}")
            all_results.update(run_sequential(TIER_3_SEQUENTIAL))
            tier3_jobs = count_new_jobs(user_id, run_start)
            print(f'\n[tier3] Cumulative new jobs: {tier3_jobs}')

    # ── Scorer + Enrichment per user ─────────────────────────────────────────
    print(f"\n{'#'*55}")
    print(f'# SCORING — user {uid_short}')
    print(f"{'#'*55}")
    _run_phase('Scorer', os.path.join(BASE_DIR, 'automation', 'job_scorer.py'))

    print(f"\n{'#'*55}")
    print(f'# ENRICHMENT — user {uid_short}')
    print(f"{'#'*55}")
    _run_phase('Enrichment', os.path.join(BASE_DIR, 'automation', 'job_scorer.py'), ['--backfill'])

    _check_zero_yield(all_results, user_id, run_start)

    elapsed = int((datetime.now() - run_start).total_seconds())
    total_new = count_new_jobs(user_id, run_start)
    print(f'\n[user {uid_short}] Done in {elapsed//60}m {elapsed%60}s — {total_new} new jobs')

    return all_results


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Job search orchestrator')
    parser.add_argument(
        '--jitter', type=int, default=0, metavar='MINUTES',
        help='Sleep a random 0–MINUTES delay before starting (randomises run time).'
    )
    args = parser.parse_args()

    # Random jitter — makes scraping time unpredictable within the cron window
    if args.jitter > 0:
        delay_s = random.randint(0, args.jitter * 60)
        print(f'[jitter] Waiting {delay_s // 60}m {delay_s % 60}s before starting...')
        time.sleep(delay_s)

    # Global lock — prevents concurrent orchestrator instances
    if os.path.exists(GLOBAL_LOCK):
        try:
            with open(GLOBAL_LOCK) as f:
                pid = f.read().strip()
            if pid and os.path.exists(f'/proc/{pid}'):
                print(f'[lock] Orchestrator already running (PID {pid}). Exiting.')
                sys.exit(0)
            else:
                print(f'[lock] Stale lock (PID {pid} dead). Removing.')
                os.remove(GLOBAL_LOCK)
        except Exception:
            os.remove(GLOBAL_LOCK)

    try:
        with open(GLOBAL_LOCK, 'w') as f:
            f.write(str(os.getpid()))

        t_start = datetime.now()
        users = get_active_users()

        if not users:
            print('[orchestrator] No active users found. Exiting.')
            sys.exit(0)

        print(f"\n{'#'*60}")
        print(f'# SCRAPER ORCHESTRATOR — Multi-user, tiered')
        print(f'# Start: {t_start.strftime("%Y-%m-%d %H:%M:%S")}')
        print(f'# Active users: {len(users)}')
        print(f'# Tier yield threshold: {MIN_TIER_YIELD} new jobs')
        print(f"{'#'*60}")

        # ── Process each user in sequence ────────────────────────────────────
        all_user_results = {}
        for uid in users:
            results = run_for_user(uid)
            all_user_results[uid] = results

        # ── Shared post-processing (all users) ───────────────────────────────
        print(f"\n{'#'*55}")
        print('# VERIFICATION — Checking expired links (all users)')
        print(f"{'#'*55}")
        _run_phase('Verifier', os.path.join(BASE_DIR, 'automation', 'job_verifier.py'))

        print(f"\n{'#'*55}")
        print('# WEBHOOK — Dispatching to external software (all users)')
        print(f"{'#'*55}")
        _run_phase('Webhook', os.path.join(BASE_DIR, 'automation', 'webhook_dispatcher.py'))

        # ── Final summary ─────────────────────────────────────────────────────
        t_end = datetime.now()
        total_s = int((t_end - t_start).total_seconds())

        print(f"\n{'#'*60}")
        print(f'# DONE — {len(users)} user(s) — {total_s // 60}m {total_s % 60}s')
        print(f"{'#'*60}")
        for uid, results in all_user_results.items():
            print(f'\n  User {uid[:8]}:')
            for name, info in results.items():
                icon = '✅' if info['success'] else '❌'
                print(f'    {icon} {name:<28} ({info["duration"]}s)')

    finally:
        if os.path.exists(GLOBAL_LOCK):
            os.remove(GLOBAL_LOCK)


if __name__ == '__main__':
    main()
