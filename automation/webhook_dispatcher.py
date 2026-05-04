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
import os
import json
import urllib.request
import urllib.error
from datetime import datetime

DB_PATH   = os.environ.get('DB_PATH', '/app/database/vagas.db')
MAX_JOBS  = int(os.environ.get('WEBHOOK_MAX_JOBS', '5'))
WEBHOOK_SECRET = os.environ.get('EXTERNAL_WEBHOOK_SECRET', '')

BANNER = "=" * 55

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def get_active_users_with_callback():
    """Returns all active users that have a callback_url configured."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT user_id, callback_url FROM users_perfil "
        "WHERE is_active = 1 AND callback_url IS NOT NULL AND callback_url != ''",
    ).fetchall()
    conn.close()
    return rows


def get_todays_top_jobs(user_id: str, limit: int = 5):
    """Returns the top N most relevant jobs scraped today for a given user."""
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_conn()
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
          AND DATE(data_scraped) = ?
        ORDER BY COALESCE(relevance_score, 0) DESC, data_scraped DESC
        LIMIT ?
        """,
        (user_id, today, limit),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def mark_webhook_sent(user_id: str):
    """Updates the last_webhook_sent timestamp for the user."""
    conn = get_conn()
    conn.execute(
        "UPDATE users_perfil SET last_webhook_sent = CURRENT_TIMESTAMP WHERE user_id = ?",
        (user_id,),
    )
    conn.commit()
    conn.close()


def send_webhook(user_id: str, callback_url: str, jobs: list) -> bool:
    """Sends the jobs payload to the callback URL via HTTP POST."""
    payload = {
        "event": "new_jobs_found",
        "user_id": user_id,
        "scraped_at": datetime.now().isoformat(),
        "total_sent": len(jobs),
        "jobs": jobs,
    }
    data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")

    req = urllib.request.Request(
        callback_url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "SearchingEng-WebhookDispatcher/1.0",
            "X-Webhook-Secret": WEBHOOK_SECRET,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            print(f"  ✅ Webhook sent → HTTP {status}")
            return True
    except urllib.error.HTTPError as e:
        print(f"  ❌ HTTP Error {e.code}: {e.reason}")
        return False
    except urllib.error.URLError as e:
        print(f"  ❌ URL Error: {e.reason}")
        return False
    except Exception as e:
        print(f"  ❌ Unexpected error: {e}")
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
