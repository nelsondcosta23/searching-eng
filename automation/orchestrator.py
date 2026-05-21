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
# Scraper registry — quality tier + execution mode per scraper file
#
# tier: 1 = best quality (run first), 2 = medium, 3 = last resort
# mode: 'sequential' = Selenium (heavy, one at a time)
#       'parallel'   = API/HTTP (cheap, safe to run concurrently)
#
# Which scrapers actually run for a user is determined by their `job_profile`
# field (read from users_perfil) mapped via SCRAPERS_<PROFILE> env vars.
# ─────────────────────────────────────────────────────────────────────────────
SCRAPER_REGISTRY: dict[str, dict] = {
    'sapo_scraper.py':      {'name': 'Sapo Jobs',    'tier': 1, 'mode': 'sequential'},
    'linkedin_scraper.py':  {'name': 'LinkedIn PT',  'tier': 1, 'mode': 'sequential'},
    'companies_scraper.py': {'name': 'Companies',    'tier': 2, 'mode': 'parallel'},
    'itjobs_scraper.py':    {'name': 'ITJobs',       'tier': 2, 'mode': 'parallel'},
    'indeed_scraper.py':    {'name': 'Indeed PT',    'tier': 2, 'mode': 'sequential'},
    'landing_scraper.py':   {'name': 'Landing.jobs', 'tier': 3, 'mode': 'parallel'},
    'expresso_scraper.py':  {'name': 'Expresso Jobs','tier': 3, 'mode': 'sequential'},
}

# Minimum new jobs per tier before skipping lower tiers
MIN_TIER_YIELD = int(os.environ.get('MIN_TIER_YIELD', '5'))


def get_scrapers_for_profile(job_profile: str) -> list[str]:
    """Returns the scraper filenames for a given job_profile.

    Reads SCRAPERS_<PROFILE> from env. Falls back to SCRAPERS_GENERALIST if
    the profile is unknown or the env var is missing.
    """
    valid = [p.strip().lower() for p in os.environ.get('VALID_JOB_PROFILES', 'generalist').split(',')]
    norm = (job_profile or 'generalist').lower().strip()
    if norm not in valid:
        print(f'  [profile] Unknown job_profile "{norm}" → using generalist')
        norm = 'generalist'
    key = f'SCRAPERS_{norm.upper()}'
    raw = os.environ.get(key) or os.environ.get('SCRAPERS_GENERALIST', '')
    scrapers = [s.strip() for s in raw.split(',') if s.strip()]
    if not scrapers:
        # Hard fallback — SCRAPERS_GENERALIST not in .env
        scrapers = ['sapo_scraper.py', 'linkedin_scraper.py', 'indeed_scraper.py']
        print(f'  [profile] SCRAPERS_GENERALIST missing from .env — using built-in fallback')
    return scrapers


def build_tiers(scraper_files: list[str]) -> tuple[list, list, list, list]:
    """Splits scraper files into execution groups based on SCRAPER_REGISTRY.

    Returns (tier1_seq, tier2_parallel, tier2_seq, tier3_mixed) where
    tier2_parallel are API scrapers (run concurrently) and tier2_seq are
    medium-quality Selenium scrapers (run one at a time).
    Tier 3 combines parallel and sequential into a single sequential pass.
    """
    t1_seq, t2_par, t2_seq, t3 = [], [], [], []
    for f in scraper_files:
        info = SCRAPER_REGISTRY.get(f)
        if not info:
            print(f'  [registry] Unknown scraper "{f}" — skipped')
            continue
        entry = (info['name'], f)
        if info['tier'] == 1:
            t1_seq.append(entry)
        elif info['tier'] == 2:
            (t2_par if info['mode'] == 'parallel' else t2_seq).append(entry)
        else:
            t3.append(entry)
    return t1_seq, t2_par, t2_seq, t3

# Global lock — prevents two orchestrator instances from running simultaneously.
GLOBAL_LOCK = os.path.join(BASE_DIR, 'database', 'orchestrator.lock')


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_active_users() -> list[dict]:
    """Returns all active users with their job_profile from users_perfil.

    Each entry: {'user_id': str, 'job_profile': str}
    If TARGET_USER_ID env is set, returns only that user (manual run mode).
    """
    override = os.environ.get('TARGET_USER_ID', '').strip()
    if override:
        # Fetch job_profile for this specific user
        try:
            with sqlite3.connect(DB_PATH, timeout=10) as c:
                row = c.execute(
                    'SELECT job_profile FROM users_perfil WHERE user_id = ?', (override,)
                ).fetchone()
                profile = (row[0] or 'generalist') if row else 'generalist'
        except Exception:
            profile = 'generalist'
        return [{'user_id': override, 'job_profile': profile}]
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as c:
            rows = c.execute(
                'SELECT user_id, COALESCE(job_profile, "generalist") FROM users_perfil WHERE is_active = 1 ORDER BY created_at'
            ).fetchall()
            return [{'user_id': r[0], 'job_profile': r[1]} for r in rows if r[0]]
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

def run_for_user(user_id: str, job_profile: str = 'generalist') -> dict:
    """Runs tiered scraping + post-processing for a single user.

    Scraper selection is dynamic: reads SCRAPERS_<JOB_PROFILE> from env,
    maps each scraper to its quality tier via SCRAPER_REGISTRY, then runs
    tiers in order with early exit when MIN_TIER_YIELD is reached.

    Returns combined scraper results dict.
    """
    os.environ['TARGET_USER_ID'] = user_id
    uid_short  = user_id[:8]
    run_start  = datetime.now()

    scrapers = get_scrapers_for_profile(job_profile)
    t1_seq, t2_par, t2_seq, t3 = build_tiers(scrapers)

    print(f"\n{'#'*60}")
    print(f"# USER: {user_id}")
    print(f"# Profile: {job_profile}  |  Scrapers: {len(scrapers)}")
    print(f"# Start: {run_start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"# Tier 1: {[n for n,_ in t1_seq] or '—'}")
    print(f"# Tier 2: {[n for n,_ in t2_par+t2_seq] or '—'}")
    print(f"# Tier 3: {[n for n,_ in t3] or '—'}")
    print(f"# MIN_TIER_YIELD: {MIN_TIER_YIELD}")
    print(f"{'#'*60}")

    all_results: dict = {}

    # ── Tier 1: Best quality (Selenium) ─────────────────────────────────────
    if t1_seq:
        print(f"\n{'#'*55}")
        print(f"# TIER 1 — {[n for n,_ in t1_seq]}")
        print(f"{'#'*55}")
        all_results.update(run_sequential(t1_seq))
    tier1_jobs = count_new_jobs(user_id, run_start)
    print(f'\n[tier1] New jobs: {tier1_jobs}')

    if tier1_jobs >= MIN_TIER_YIELD:
        print(f'[tier1] ✅ {tier1_jobs} ≥ {MIN_TIER_YIELD} — skipping Tiers 2 & 3')
    else:
        # ── Tier 2: API (parallel) + medium Selenium (sequential) ────────────
        if t2_par or t2_seq:
            print(f"\n{'#'*55}")
            print(f"# TIER 2 — {[n for n,_ in t2_par+t2_seq]}")
            print(f"{'#'*55}")
            if t2_par:
                all_results.update(run_parallel(t2_par))
            if t2_seq:
                all_results.update(run_sequential(t2_seq))
        tier2_jobs = count_new_jobs(user_id, run_start)
        print(f'\n[tier2] Cumulative: {tier2_jobs}')

        if tier2_jobs >= MIN_TIER_YIELD:
            print(f'[tier2] ✅ {tier2_jobs} ≥ {MIN_TIER_YIELD} — skipping Tier 3')
        else:
            # ── Tier 3: Last resort ──────────────────────────────────────────
            if t3:
                print(f"\n{'#'*55}")
                print(f"# TIER 3 — {[n for n,_ in t3]}")
                print(f"{'#'*55}")
                all_results.update(run_sequential(t3))
            tier3_jobs = count_new_jobs(user_id, run_start)
            print(f'\n[tier3] Cumulative: {tier3_jobs}')

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
            # psutil.pid_exists works cross-platform (Linux + Windows).
            # os.path.exists('/proc/<pid>') only works on Linux.
            import psutil
            if pid and psutil.pid_exists(int(pid)):
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
        for user in users:
            uid     = user['user_id']
            profile = user['job_profile']
            results = run_for_user(uid, profile)
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
