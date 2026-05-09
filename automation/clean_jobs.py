import sqlite3
import sys
import os
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from automation.db_helper import execute_with_retry, transaction

DB_PATH       = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
DIAS_RETENCAO = int(os.environ.get('DIAS_RETENCAO', '45'))

def clean_old_jobs():
    """Remove jobs older than DIAS_RETENCAO days from the database (with retry)."""
    if not os.path.exists(DB_PATH):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Cleanup aborted: Database not found at {DB_PATH}")
        return

    limit = (datetime.now() - timedelta(days=DIAS_RETENCAO)).strftime('%Y-%m-%d %H:%M:%S')

    # Read counts (short connection)
    with transaction() as conn:
        total_before = conn.execute('SELECT COUNT(*) FROM vagas WHERE data_scraped < ?', (limit,)).fetchone()[0]

    if total_before == 0:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Cleanup: nothing to delete.")
        return

    # Delete with retry (separate short-lived connection)
    deleted = execute_with_retry('DELETE FROM vagas WHERE data_scraped < ?', (limit,))

    with transaction() as conn:
        total_after = conn.execute('SELECT COUNT(*) FROM vagas').fetchone()[0]

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Cleanup finished.")
    print(f"  → {deleted} jobs older than {DIAS_RETENCAO} days removed.")
    print(f"  → {total_after} jobs remain in the database.")

if __name__ == '__main__':
    print("== Starting Automatic Database Cleanup ==")
    clean_old_jobs()
