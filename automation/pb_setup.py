"""PocketBase setup and migration script.

Usage:
  docker exec python_scraper python /app/automation/pb_setup.py

What it does:
  1. Waits for PocketBase to be healthy
  2. Creates the user_profiles and job_results collections (idempotent)
  3. Migrates all rows from users_perfil SQLite table → PocketBase user_profiles
  4. Reports the result
"""

import os
import sys
import sqlite3
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from automation.pb_client import get_client

DB_PATH    = os.environ.get('DB_PATH', '/app/database/vagas.db')
MAX_WAIT_S = 30   # seconds to wait for PocketBase to become healthy


def wait_for_pocketbase(pb, timeout: int = MAX_WAIT_S) -> bool:
    print(f"[pb_setup] Waiting for PocketBase (up to {timeout}s)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pb.is_available():
            print("[pb_setup] PocketBase is ready.")
            return True
        time.sleep(2)
    print("[pb_setup] PocketBase did not become available in time.")
    return False


def read_sqlite_users(db_path: str) -> list:
    if not os.path.exists(db_path):
        print(f"[pb_setup] SQLite DB not found at {db_path}")
        return []
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT user_id, is_active, job_titles, locations, is_remote, min_salary, "
            "experience_levels, keywords, negative_keywords, negative_companies, "
            "contract_type, required_languages, search_description, job_profile, callback_url "
            "FROM users_perfil"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[pb_setup] Could not read users_perfil: {e}")
        return []
    finally:
        conn.close()


def migrate_users(pb, users: list) -> dict:
    results = {'ok': 0, 'failed': 0, 'skipped': 0}
    for u in users:
        user_id = u.get('user_id')
        if not user_id:
            results['skipped'] += 1
            continue
        payload = {
            'job_titles':         u.get('job_titles')         or '',
            'keywords':           u.get('keywords')           or '',
            'negative_keywords':  u.get('negative_keywords')  or '',
            'negative_companies': u.get('negative_companies') or '',
            'locations':          u.get('locations')          or '',
            'is_remote':          bool(u.get('is_remote')),
            'min_salary':         int(u.get('min_salary') or 0),
            'experience_levels':  u.get('experience_levels')  or '',
            'search_description': u.get('search_description') or '',
            'contract_type':      u.get('contract_type')      or '',
            'required_languages': u.get('required_languages') or '',
            'job_profile':        u.get('job_profile')        or 'generalist',
            'is_active':          bool(u.get('is_active', 1)),
            'callback_url':       u.get('callback_url')       or '',
        }
        ok = pb.upsert_profile(user_id, payload)
        if ok:
            results['ok'] += 1
            print(f"  ✓ {user_id[:8]}... migrated")
        else:
            results['failed'] += 1
            print(f"  ✗ {user_id[:8]}... FAILED")
    return results


def main():
    print("=" * 55)
    print("# PocketBase Setup & Migration")
    print("=" * 55)

    pb = get_client()

    if not wait_for_pocketbase(pb):
        sys.exit(1)

    print("\n[pb_setup] Creating collections (idempotent)...")
    if not pb.ensure_collections():
        print("[pb_setup] Collection creation failed.")
        sys.exit(1)
    print("[pb_setup] Collections ready.")

    print(f"\n[pb_setup] Reading users_perfil from {DB_PATH}...")
    users = read_sqlite_users(DB_PATH)
    if not users:
        print("[pb_setup] No users found — nothing to migrate.")
    else:
        print(f"[pb_setup] Migrating {len(users)} user(s) to PocketBase...\n")
        results = migrate_users(pb, users)
        print(f"\n[pb_setup] Migration complete: "
              f"{results['ok']} ok, {results['failed']} failed, {results['skipped']} skipped.")

    print("\n[pb_setup] Done. Access the admin UI at http://localhost:8090/_/")


if __name__ == '__main__':
    main()
