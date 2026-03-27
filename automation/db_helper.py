import sqlite3
import os
import time
import random
from datetime import datetime

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))

def _get_connection():
    """Returns a new SQLite connection with WAL mode enabled and a 20s timeout."""
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn

def job_exists(link: str) -> bool:
    """Checks if a job link is already in the database."""
    tentativas = 5
    while tentativas > 0:
        conn = None
        try:
            conn = _get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM vagas WHERE link = ?', (link,))
            exists = cursor.fetchone() is not None
            return exists
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                time.sleep(1 + random.uniform(0.1, 0.5))
                tentativas -= 1
            else:
                raise e
        finally:
            if conn:
                conn.close()
    print(f"[DB ERROR] job_exists failed for link {link} after multiple retries (Database Locked).")
    return False

def save_job(user_id: str, plataforma: str, id_externo: str, titulo: str, empresa: str, localizacao: str, link: str, data_pub: str = "Recent", categoria: str = "Unknown", descricao_completa: str = "", recrutador_nome: str = "", recrutador_link: str = "", observacoes: str = "") -> bool:
    """Saves a new job to the database with safe retry logic (Schema v4)."""
    data_agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    tentativas = 5
    while tentativas > 0:
        conn = None
        try:
            conn = _get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO vagas (user_id, plataforma, id_externo, titulo, empresa, localizacao, link, data_publicacao, data_scraped, categoria, descricao_completa, status, recrutador_nome, recrutador_link, observacoes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Ativa', ?, ?, ?)
            ''', (user_id, plataforma, id_externo, titulo, empresa, localizacao, link, data_pub, data_agora, categoria, descricao_completa, recrutador_nome, recrutador_link, observacoes))
            conn.commit()
            return True
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                time.sleep(1 + random.uniform(0.1, 0.5))
                tentativas -= 1
            else:
                raise e
        except sqlite3.IntegrityError:
            # Job is already saved (caught by UNIQUE constraint on the schema)
            return False 
        finally:
            if conn:
                conn.close()
    print(f"[DB ERROR] save_job failed for {plataforma} after multiple retries (Database Locked).")
    return False
