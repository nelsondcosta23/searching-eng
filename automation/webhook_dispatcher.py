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
import hmac
import hashlib
import socket
import ipaddress
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from automation.db_helper import execute_with_retry

# PocketBase push is optional — if unavailable the webhook still works normally
try:
    from automation.pb_client import get_client as _get_pb_client
    _PB_ENABLED = True
except ImportError:
    _PB_ENABLED = False

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


def _sign_payload(secret: str, body: bytes) -> str:
    """HMAC-SHA256 signature over the raw JSON body.

    Returns 'sha256=<hex>' — the same format used by GitHub webhooks,
    so recipients can use standard libraries to verify.
    """
    return 'sha256=' + hmac.new(
        secret.encode('utf-8'), body, hashlib.sha256
    ).hexdigest()


def _is_safe_for_dispatch(url: str) -> bool:
    """Re-validates callback_url at dispatch time (DNS-rebinding protection).

    The API validates the URL at sync time, but the IP may change between sync
    and cron execution. Re-resolving here closes the TOCTOU window.
    Returns False (and logs a warning) if the resolved IP is now private/internal.
    """
    try:
        host = urllib.parse.urlparse(url).hostname
        if not host:
            return False
        for _, _, _, _, sockaddr in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(sockaddr[0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
                print(f"  ⛔ SSRF guard: {host} now resolves to private IP {ip} — skipping dispatch")
                return False
        return True
    except Exception as exc:
        print(f"  ⛔ SSRF guard: could not resolve {url!r}: {exc} — skipping dispatch")
        return False

_SENSITIVE_PARAMS = {'secret', 'token', 'key', 'api_key', 'apikey', 'password', 'pass'}

def _redact_url(url: str) -> str:
    """Replace sensitive query param values with *** for safe logging."""
    try:
        parsed = urllib.parse.urlparse(url)
        if not parsed.query:
            return url
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        redacted = {
            k: (['***'] if k.lower() in _SENSITIVE_PARAMS else v)
            for k, v in params.items()
        }
        return urllib.parse.urlunparse(parsed._replace(
            query=urllib.parse.urlencode(redacted, doseq=True)
        ))
    except Exception:
        return url


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


def get_all_active_users():
    """Returns all active users (with or without callback_url)."""
    with contextlib.closing(get_conn()) as conn:
        return conn.execute(
            "SELECT user_id, COALESCE(callback_url, '') AS callback_url "
            "FROM users_perfil WHERE is_active = 1",
        ).fetchall()


def get_all_scored_jobs(user_id: str):
    """Returns ALL active scored jobs for a user (full mirror to PocketBase)."""
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            """
            SELECT
                ju.user_id, jg.titulo, jg.empresa, jg.localizacao, jg.plataforma, jg.categoria,
                jg.link, jg.data_publicacao, jg.data_scraped, jg.status,
                jg.salario, jg.tipo_contrato, jg.nivel_experiencia,
                jg.observacoes, jg.recrutador_nome, jg.recrutador_link,
                SUBSTR(COALESCE(jg.descricao, ''), 1, 2000) AS descricao_completa,
                COALESCE(ju.relevance_score, 0) AS relevance_score
            FROM jobs_global jg
            JOIN jobs_users ju ON jg.id = ju.job_id
            WHERE ju.user_id = ?
              AND jg.status = 'Ativa'
              AND ju.relevance_score IS NOT NULL
            ORDER BY COALESCE(ju.relevance_score, 0) DESC
            LIMIT 500
            """,
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_todays_top_jobs(user_id: str, limit: int = 5):
    """Returns the top N most relevant jobs scraped today for a given user."""
    today_start, today_end = _day_range(datetime.now().strftime('%Y-%m-%d'))
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            """
            SELECT
                jg.id, ju.user_id, jg.titulo, jg.empresa, jg.localizacao, jg.plataforma, jg.categoria,
                jg.link, jg.data_publicacao, jg.data_scraped, jg.status,
                jg.descricao AS descricao_completa, jg.recrutador_nome, jg.recrutador_link,
                jg.observacoes, jg.salario, jg.tipo_contrato, jg.nivel_experiencia,
                COALESCE(ju.relevance_score, 0) AS relevance_score
            FROM jobs_global jg
            JOIN jobs_users ju ON jg.id = ju.job_id
            WHERE ju.user_id = ?
              AND jg.status = 'Ativa'
              AND jg.data_scraped >= ?
              AND jg.data_scraped < ?
            ORDER BY COALESCE(ju.relevance_score, 0) DESC, jg.data_scraped DESC
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

    if not _is_safe_for_dispatch(callback_url):
        return False

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "SearchingEng-WebhookDispatcher/1.0",
    }
    if WEBHOOK_SECRET:
        # HMAC-SHA256 signature — recipients verify with:
        #   expected = 'sha256=' + hmac.new(secret, body, sha256).hexdigest()
        #   assert hmac.compare_digest(sig, expected)
        headers["X-Webhook-Signature"] = _sign_payload(WEBHOOK_SECRET, data)

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


def _push_jobs_to_pocketbase(pb, user_id: str) -> None:
    """Push ALL active scored jobs for one user to PocketBase (full mirror)."""
    try:
        all_jobs = get_all_scored_jobs(user_id)
        if not all_jobs:
            return
        result = pb.bulk_upsert_job_results(user_id, all_jobs)
        print(f"  📌 PocketBase: {result['inserted']} inserted, "
              f"{result['updated']} updated, {result['failed']} failed "
              f"(total {len(all_jobs)} jobs)")
    except Exception as e:
        print(f"  ⚠ PocketBase push failed (non-fatal): {e}")


def main():
    print(f"\n{BANNER}")
    print("# WEBHOOK DISPATCHER — Pushing today's top jobs")
    print(BANNER)

    if not os.path.exists(DB_PATH):
        print("❌ Database not found. Skipping.")
        return

    # Check PocketBase availability once up front
    pb = None
    if _PB_ENABLED:
        pb = _get_pb_client()
        if pb.is_available():
            pb.ensure_collections()
            print("📌 PocketBase: connected — job results will be mirrored\n")
        else:
            pb = None
            print("ℹ️  PocketBase: unavailable — skipping mirror (webhook still runs)\n")

    all_users = get_all_active_users()
    webhook_users = {r['user_id']: r['callback_url'] for r in all_users if r['callback_url']}

    if not all_users:
        print("ℹ️  No active users found. Nothing to do.")
        return

    print(f"📋 Found {len(all_users)} active user(s) "
          f"({len(webhook_users)} with webhook configured).\n")

    for row in all_users:
        user_id      = row["user_id"]
        callback_url = row["callback_url"]

        print(f"{BANNER}")
        print(f"👤 User: {user_id[:8]}...")

        jobs = get_todays_top_jobs(user_id, limit=MAX_JOBS)

        # Webhook push
        if callback_url:
            print(f"🔗 URL:  {_redact_url(callback_url)}")

            # ── B-1: idempotência ─────────────────────────────────────────────
            # The orchestrator runs 6× per day. Guard against double-dispatch
            # by checking last_webhook_sent timestamp before firing.
            with contextlib.closing(get_conn()) as _c:
                already_sent = _c.execute(
                    "SELECT 1 FROM users_perfil "
                    "WHERE user_id = ? AND DATE(last_webhook_sent) = DATE('now')",
                    (user_id,)
                ).fetchone()
            if already_sent:
                print(f"  ℹ️  Webhook already dispatched today — skipping. (PocketBase mirror still runs.)")
            elif not jobs:
                print(f"  ℹ️  No new jobs found today. Skipping webhook.")
            else:
                print(f"  📦 Sending {len(jobs)} job(s)...")
                ok = send_webhook(user_id, callback_url, jobs)
                if ok:
                    mark_webhook_sent(user_id)
            # ─────────────────────────────────────────────────────────────────

        # PocketBase mirror — push ALL scored jobs for this user (not just webhook top-N)
        if pb:
            _push_jobs_to_pocketbase(pb, user_id)

    print(f"\n{BANNER}")
    print("# WEBHOOK DISPATCHER — Done")
    print(BANNER)


if __name__ == "__main__":
    main()
