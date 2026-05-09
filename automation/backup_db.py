"""SQLite backup script — uses sqlite3.backup() (safe with concurrent writers in WAL mode).

Cron entry calls this weekly. Keeps the last `BACKUP_KEEP` snapshots and
deletes the rest. Snapshots live in `/app/database/backups/` by default.
"""
import os
import sys
import sqlite3
import glob
from datetime import datetime

DB_PATH = os.environ.get('DB_PATH', '/app/database/vagas.db')
BACKUP_DIR = os.environ.get('BACKUP_DIR', '/app/database/backups')
BACKUP_KEEP = int(os.environ.get('BACKUP_KEEP', '8'))   # weeks


def main():
    if not os.path.exists(DB_PATH):
        print(f"[backup] DB not found at {DB_PATH}; nothing to back up.")
        return 1

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(BACKUP_DIR, f'vagas_{stamp}.db')

    print(f"[backup] {DB_PATH} → {out_path}")

    src = sqlite3.connect(DB_PATH, timeout=30)
    dst = sqlite3.connect(out_path)
    try:
        # Online backup is concurrent-safe and respects WAL.
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"[backup] ✅ Wrote {size_mb:.2f} MB.")

    # Rotate: keep newest BACKUP_KEEP, delete the rest.
    snapshots = sorted(glob.glob(os.path.join(BACKUP_DIR, 'vagas_*.db')), reverse=True)
    for old in snapshots[BACKUP_KEEP:]:
        try:
            os.remove(old)
            print(f"[backup] 🗑  Removed {os.path.basename(old)}")
        except OSError as e:
            print(f"[backup] ⚠ Could not remove {old}: {e}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
