"""
Webhook Dispatcher
==================
Runs after the scraper orchestrator.
For each active user with a `callback_url`, finds the top 5 most-relevant
jobs scraped TODAY and POSTs them as JSON to that URL.

Payload format:
{
  "event": "new_jobs_found",
  "user_id": "25b5c883-...",
  "scraped_at": "2026-05-03T23:00:00",
  "total_sent": 5,
  "jobs": [ { ...all fields... } ]
}
"""

import sqlite3
import contextlib
import os
import sys
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from automation.db_helper import execute_with_retry

DB_PATH   = os.environ.get('DB_PATH', '/app/database/vagas.db')
MAX_JOBS  = int(os.environ.get('WEBHOOK_MAX_JOBS', '5'))
WEBHOOK_SECRET = os.environ.get('EXTERNAL_WEBHOOK_SECRET', '')
WEBHOOK_RETRIES = int(os.environ.get('WEBHOOK_RETRIES', '3'))
WEBHOOK_TIMEOUT = int(os.environ.get('WEBHOOK_TIMEOUT', '15'))


def _day_range(date_str: str) -> tuple:
    """Returns (start_of_day, start_of_next_day) as 'YYYY-MM-DD HH:MM:SS' strings.
    Sargable replacement for `DATE(col) = ?` so idx_vagas_user_scraped is used.
    """
    d = datetime.strptime(date_str, '%Y-%m-%d')
    return (d.strftime('%Y-%m-%d %H:%M:%S'), (d + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'))

BANNER = "=" * 55

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def get_active_users_with_callback():
    """Returns all active users that have a callback_url configured."""
    with contextlib.closing(get_conn()) as conn:
        return conn.execute(
            "SELECT user_id, callback_url FROM users_perfil "
            "WHERE is_active = 1 AND callback_url IS NOT NULL AND callback_url != ''",
        ).fetchall()


def get_todays_top_jobs(user_id: str, limit: int = 5):
    """Returns the top N most relevant jobs scraped today for a given user."""
    today_start, today_end = _day_range(datetime.now().strftime('%Y-%m-%d'))
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            """
            SELECT
                id, user_id, titulo, empresa, localizacao, plataforma, categoria,
                link, data_publicacao, data_scraped, status,
                descricao_completa, recrutador_nome, recrutador_link,
                observacoes, salario, tipo_contrato, nivel_experiencia,
                COALESCE(relevance_score, 0) AS relevance_score
            FROM vagas
            WHERE user_id = ?
              AND status = 'Ativa'
              AND data_scraped >= ?
              AND data_scraped < ?
            ORDER BY COALESCE(relevance_score, 0) DESC, data_scraped DESC
            LIMIT ?
            """,
            (user_id, today_start, today_end, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_webhook_sent(user_id: str):
    """Updates the last_webhook_sent timestamp for the user (retries on locked DB)."""
    try:
        execute_with_retry(
            "UPDATE users_perfil SET last_webhook_sent = CURRENT_TIMESTAMP WHERE user_id = ?",
            (user_id,),
        )
    except Exception as e:
        # Don't fail the dispatcher run on a marker UPDATE failure — the next
        # cron tick will potentially re-send, which is preferable to a crash.
        print(f"  ⚠ Could not mark webhook_sent for {user_id[:8]}...: {e}")


def send_webhook(user_id: str, callback_url: str, jobs: list) -> bool:
    """Sends the jobs payload to the callback URL via HTTP POST.

    Retries up to WEBHOOK_RETRIES times with exponential backoff (1s, 4s, 16s)
    on transient failures (5xx, network/timeout). Does NOT retry on 4xx since
    they indicate a permanent client problem (bad URL, auth, payload).
    """
    # Truncate long descriptions to cap payload size (A4)
    truncated_jobs = []
    for job in jobs:
        j = dict(job)
        desc = j.get('descricao_completa') or ''
        if len(desc) > 2000:
            j['descricao_completa'] = desc[:2000] + '…'
        truncated_jobs.append(j)

    payload = {
        "event": "new_jobs_found",
        "user_id": user_id,
        "scraped_at": datetime.now().isoformat(),
        "total_sent": len(truncated_jobs),
        "jobs": truncated_jobs,
    }
    data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    print(f"  📦 Payload size: {len(data) // 1024} KB")

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "SearchingEng-WebhookDispatcher/1.0",
        "X-Webhook-Secret": WEBHOOK_SECRET,
    }

    attempts = max(1, WEBHOOK_RETRIES)
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(callback_url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT) as resp:
                print(f"  ✅ Webhook sent → HTTP {resp.status} (attempt {attempt}/{attempts})")
                return True
        except urllib.error.HTTPError as e:
            # Don't retry on 4xx — they signal a permanent client problem.
            if 400 <= e.code < 500:
                print(f"  ❌ HTTP {e.code} {e.reason} — not retrying (4xx is permanent)")
                return False
            print(f"  ⚠ HTTP {e.code} {e.reason} on attempt {attempt}/{attempts}")
        except urllib.error.URLError as e:
            print(f"  ⚠ URL Error: {e.reason} on attempt {attempt}/{attempts}")
        except Exception as e:
            print(f"  ⚠ Unexpected error: {e} on attempt {attempt}/{attempts}")

        if attempt < attempts:
            backoff = 4 ** (attempt - 1)  # 1s, 4s, 16s, 64s, ...
            print(f"  ⏳ Backing off {backoff}s before retry...")
            time.sleep(backoff)

    print(f"  ❌ Webhook failed after {attempts} attempt(s).")
    return False


def main():
    print(f"\n{BANNER}")
    print("# WEBHOOK DISPATCHER — Pushing today's top jobs")
    print(BANNER)

    if not os.path.exists(DB_PATH):
        print("❌ Database not found. Skipping.")
        return

    users = get_active_users_with_callback()

    if not users:
        print("ℹ️  No active users with a callback_url configured. Nothing to send.")
        return

    print(f"📋 Found {len(users)} user(s) with webhook configured.\n")

    for row in users:
        user_id     = row["user_id"]
        callback_url = row["callback_url"]

        print(f"{BANNER}")
        print(f"👤 User: {user_id[:8]}...")
        print(f"🔗 URL:  {callback_url}")

        jobs = get_todays_top_jobs(user_id, limit=MAX_JOBS)

        if not jobs:
            print(f"  ℹ️  No new jobs found today for this user. Skipping webhook.")
            continue

        print(f"  📦 Sending {len(jobs)} job(s)...")
        ok = send_webhook(user_id, callback_url, jobs)
        if ok:
            mark_webhook_sent(user_id)

    print(f"\n{BANNER}")
    print("# WEBHOOK DISPATCHER — Done")
    print(BANNER)


if __name__ == "__main__":
    main()
