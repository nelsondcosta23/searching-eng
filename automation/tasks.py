"""Celery tasks for the job scraping pipeline.

Task hierarchy:
  dispatch_all_users()          — queued by cron, fans out per-user tasks
    └─ scrape_user_task(uid)    — runs full scrape pipeline for one user
         ├─ run_scraper subprocess (LinkedIn, Companies, ...)
         ├─ score_user(uid)     — TF-IDF scorer
         └─ dispatch_webhook(uid) — push top jobs to callback_url

All tasks are idempotent: running them twice produces the same DB state.
"""

import os
import sys
import subprocess
import sqlite3
from datetime import datetime
from celery import group, chain
from celery.utils.log import get_task_logger

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from automation.celery_app import app

logger = get_task_logger(__name__)

BASE_DIR     = os.environ.get('APP_DIR', '/app')
DB_PATH      = os.path.join(BASE_DIR, 'database', 'vagas.db')
SCRAPERS_DIR = os.path.join(BASE_DIR, 'scrapers')


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_active_users() -> list[dict]:
    """Returns all active users from the local DB."""
    override = os.environ.get('TARGET_USER_ID', '').strip()
    if override:
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
                'SELECT user_id, COALESCE(job_profile, "generalist") '
                'FROM users_perfil WHERE is_active = 1 ORDER BY created_at'
            ).fetchall()
            return [{'user_id': r[0], 'job_profile': r[1]} for r in rows if r[0]]
    except Exception as e:
        logger.error(f'Could not load users from DB: {e}')
        return []


def _run_phase(name: str, script_path: str, extra_args: list = None, env_extra: dict = None) -> bool:
    """Run a phase script as a subprocess. Returns True on success."""
    env = {**os.environ, 'PYTHONUNBUFFERED': '1', **(env_extra or {})}
    cmd = [sys.executable, script_path] + (extra_args or [])
    result = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, env=env)
    if result.stdout:
        logger.info(result.stdout)
    if result.stderr:
        logger.warning(result.stderr)
    if result.returncode != 0:
        logger.error(f'{name} failed (exit {result.returncode})')
    return result.returncode == 0


# ─────────────────────────────────────────────────────────────────────────────
# Entry point — called by cron
# ─────────────────────────────────────────────────────────────────────────────

@app.task(bind=True, queue='scraping.api', name='automation.tasks.dispatch_all_users')
def dispatch_all_users(self):
    """Fan out one scrape_user_task per active user.

    Called by cron at 00:00. Each user gets an independent Celery task so
    they can run in parallel across workers instead of sequentially.
    """
    users = _get_active_users()
    if not users:
        logger.info('No active users found.')
        return {'dispatched': 0}

    logger.info(f'Dispatching scrape tasks for {len(users)} user(s)')

    # Group: all users run in parallel, each on their own worker
    job = group(
        scrape_user_task.s(u['user_id'], u['job_profile'])
        for u in users
    )
    result = job.apply_async()
    logger.info(f'Dispatched {len(users)} tasks. Group ID: {result.id}')

    # Global post-processing runs after ALL users finish
    result.save()
    return {'dispatched': len(users), 'group_id': str(result.id)}


# ─────────────────────────────────────────────────────────────────────────────
# Per-user pipeline
# ─────────────────────────────────────────────────────────────────────────────

@app.task(
    bind=True,
    queue='scraping.selenium',
    name='automation.tasks.scrape_user_task',
    soft_time_limit=3600,   # 1h soft limit — sends SoftTimeLimitExceeded
    time_limit=4200,         # 70min hard kill
)
def scrape_user_task(self, user_id: str, job_profile: str = 'generalist'):
    """Full scraping pipeline for one user.

    Runs the existing orchestrator logic but as an isolated Celery task:
      1. Import and call run_for_user() from orchestrator
      2. Score + enrich
      3. Dispatch webhook

    Running as a task means each user is independent — one user's Selenium
    crash doesn't block the other 999 users.
    """
    logger.info(f'[{user_id[:8]}] Starting scrape task')
    start = datetime.now()

    env_extra = {'TARGET_USER_ID': user_id}

    try:
        # Import run_for_user from the existing orchestrator
        # This reuses all existing scraper logic without changes
        sys.path.insert(0, BASE_DIR)
        from automation.orchestrator import run_for_user
        results = run_for_user(user_id, job_profile)
    except Exception as exc:
        logger.error(f'[{user_id[:8]}] run_for_user failed: {exc}')
        raise self.retry(exc=exc)

    elapsed = int((datetime.now() - start).total_seconds())
    logger.info(f'[{user_id[:8]}] Done in {elapsed}s')
    return {'user_id': user_id, 'elapsed_s': elapsed, 'results': str(results)}


@app.task(
    bind=True,
    queue='post.processing',
    name='automation.tasks.run_global_post_processing',
)
def run_global_post_processing(self):
    """Run verifier + webhook dispatch once all users have been scraped.

    Should be called after the dispatch_all_users group completes.
    """
    logger.info('Running global post-processing: verifier + webhook')

    _run_phase('Verifier', os.path.join(BASE_DIR, 'automation', 'job_verifier.py'))
    _run_phase('Webhook', os.path.join(BASE_DIR, 'automation', 'webhook_dispatcher.py'))

    logger.info('Global post-processing done.')
    return {'status': 'ok'}


@app.task(
    bind=True,
    queue='post.processing',
    name='automation.tasks.rescore_user',
)
def rescore_user(self, user_id: str):
    """Force-rescore all jobs for a user (call after profile update)."""
    env_extra = {'TARGET_USER_ID': user_id}
    ok = _run_phase(
        f'Rescore {user_id[:8]}',
        os.path.join(BASE_DIR, 'automation', 'job_scorer.py'),
        ['--rescore-all'],
        env_extra=env_extra,
    )
    return {'user_id': user_id, 'success': ok}
