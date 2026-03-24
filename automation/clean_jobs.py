import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH       = os.environ.get('DB_PATH', '/app/database/vagas.db')
DIAS_RETENCAO = int(os.environ.get('DIAS_RETENCAO', '45'))

def clean_old_jobs():
    """Remove jobs older than DIAS_RETENCAO days from the database."""
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute('PRAGMA journal_mode=WAL;')
    cursor = conn.cursor()

    limit = (datetime.now() - timedelta(days=DIAS_RETENCAO)).strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('SELECT COUNT(*) FROM vagas WHERE data_scraped < ?', (limit,))
    total_before = cursor.fetchone()[0]

    cursor.execute('DELETE FROM vagas WHERE data_scraped < ?', (limit,))
    conn.commit()

    cursor.execute('SELECT COUNT(*) FROM vagas')
    total_after = cursor.fetchone()[0]

    conn.close()

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Cleanup finished.")
    print(f"  → {total_before} jobs older than {DIAS_RETENCAO} days removed.")
    print(f"  → {total_after} jobs remain in the database.")

if __name__ == '__main__':
    print("== Starting Automatic Database Cleanup ==")
    clean_old_jobs()
