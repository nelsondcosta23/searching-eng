"""Migration v7 — Global Job Deduplication

Copies all rows from `vagas` into `jobs_global` + `jobs_users`.

Safe to run multiple times (idempotent via INSERT OR IGNORE).
The `vagas` table is left untouched as a read-only backup.

Usage:
  docker exec python_scraper python /app/migration_v7.py
"""

import sqlite3
import os
import sys

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database', 'vagas.db'))


def run_migration():
    print("=" * 55)
    print("# Migration v7 — Global Job Deduplication")
    print("=" * 55)

    if not os.path.exists(DB_PATH):
        print(f"DB not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.row_factory = sqlite3.Row

    try:
        # Verify new tables exist (init_db.py must run first)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if 'jobs_global' not in tables or 'jobs_users' not in tables:
            print("ERROR: jobs_global / jobs_users tables missing. Run init_db.py first.")
            sys.exit(1)

        total = conn.execute("SELECT COUNT(*) FROM vagas").fetchone()[0]
        already = conn.execute("SELECT COUNT(*) FROM jobs_global").fetchone()[0]
        print(f"\nvagas rows:     {total}")
        print(f"jobs_global rows (before): {already}")

        if total == 0:
            print("vagas is empty — nothing to migrate.")
            return

        print("\nMigrating vagas → jobs_global + jobs_users...")

        # Step 1: Insert unique jobs into jobs_global
        # vagas.link is UNIQUE so each link is already deduplicated
        conn.execute('''
            INSERT OR IGNORE INTO jobs_global (
                id, link, titulo, empresa, plataforma, id_externo, localizacao, categoria,
                descricao, observacoes, recrutador_nome, recrutador_link,
                salario, tipo_contrato, nivel_experiencia,
                data_publicacao, data_scraped, data_ultima_verificacao, status
            )
            SELECT
                id, link, titulo, empresa, plataforma, id_externo, localizacao, categoria,
                descricao_completa, observacoes, recrutador_nome, recrutador_link,
                salario, tipo_contrato, nivel_experiencia,
                data_publicacao, data_scraped, data_ultima_verificacao, status
            FROM vagas
        ''')
        conn.commit()
        global_count = conn.execute("SELECT COUNT(*) FROM jobs_global").fetchone()[0]
        print(f"  jobs_global: {global_count} rows ({global_count - already} inserted)")

        # Step 2: Associate each vagas row with its user in jobs_users
        conn.execute('''
            INSERT OR IGNORE INTO jobs_users (job_id, user_id, relevance_score, created_at)
            SELECT
                jg.id,
                v.user_id,
                v.relevance_score,
                COALESCE(v.data_scraped, CURRENT_TIMESTAMP)
            FROM vagas v
            JOIN jobs_global jg ON v.link = jg.link
            WHERE v.user_id IS NOT NULL
        ''')
        conn.commit()
        users_count = conn.execute("SELECT COUNT(*) FROM jobs_users").fetchone()[0]
        print(f"  jobs_users:  {users_count} rows")

        # Summary
        print(f"\nMigration complete.")
        print(f"  {global_count} unique jobs in jobs_global")
        print(f"  {users_count} user associations in jobs_users")

        dupes = total - global_count
        if dupes > 0:
            print(f"  {dupes} duplicate links collapsed (saved storage)")

    finally:
        conn.close()


if __name__ == '__main__':
    run_migration()
