import sqlite3
import os
import time
import random
import contextlib
from datetime import datetime

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))

# Default retry settings for transient `database is locked` errors. SQLite's
# 20s busy timeout (set in _get_connection) handles waits during statement
# execution; these retries cover commit-time conflicts in WAL mode.
_DEFAULT_RETRIES = 5
_BACKOFF_BASE = 1.0  # seconds


def _get_connection():
    """Returns a new SQLite connection with WAL mode enabled and a 20s timeout."""
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn


def _is_locked_error(err: Exception) -> bool:
    return isinstance(err, sqlite3.OperationalError) and 'database is locked' in str(err).lower()


@contextlib.contextmanager
def transaction(max_attempts: int = _DEFAULT_RETRIES, row_factory=None):
    """Yields a SQLite connection inside a transaction.

    Commits on clean exit, with retry on `database is locked`. Rolls back on
    exception. Connection is always closed. Use for multi-statement writes
    where you need to control statement boundaries.

    Example:
        with transaction() as conn:
            conn.execute("UPDATE vagas SET ...")
            conn.execute("UPDATE users_perfil SET ...")
    """
    conn = _get_connection()
    if row_factory is not None:
        conn.row_factory = row_factory
    try:
        try:
            yield conn
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise

        last_err = None
        for attempt in range(max_attempts):
            try:
                conn.commit()
                return
            except sqlite3.OperationalError as e:
                last_err = e
                if _is_locked_error(e) and attempt < max_attempts - 1:
                    time.sleep(_BACKOFF_BASE + random.uniform(0.1, 0.5))
                    continue
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
        if last_err:
            raise last_err
    finally:
        try:
            conn.close()
        except Exception:
            pass


def execute_with_retry(sql: str, params: tuple = (), max_attempts: int = _DEFAULT_RETRIES) -> int:
    """One-shot UPDATE/INSERT/DELETE with retry on `database is locked`.

    Returns the affected rowcount. The statement is committed automatically.
    For multi-statement transactions use `transaction()` instead.
    """
    for attempt in range(max_attempts):
        conn = None
        try:
            conn = _get_connection()
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount
        except sqlite3.OperationalError as e:
            if _is_locked_error(e) and attempt < max_attempts - 1:
                time.sleep(_BACKOFF_BASE + random.uniform(0.1, 0.5))
                continue
            raise
        finally:
            if conn:
                conn.close()
    return -1  # unreachable

def job_exists(link: str) -> bool:
    """Checks if a job link is already in the database."""
    tentativas = 5
    while tentativas > 0:
        conn = None
        try:
            conn = _get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM vagas WHERE link = ?', (link,))
            return cursor.fetchone() is not None
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

def save_job(
    user_id: str,
    plataforma: str,
    id_externo: str,
    titulo: str,
    empresa: str,
    localizacao: str,
    link: str,
    data_pub: str = "Recent",
    categoria: str = "Unknown",
    descricao_completa: str = "",
    recrutador_nome: str = "",
    recrutador_link: str = "",
    observacoes: str = "",
    # --- New fields (Schema v5) ---
    salario: str = "",
    tipo_contrato: str = "",
    nivel_experiencia: str = "",
) -> bool:
    """Saves a new job to the database with safe retry logic (Schema v5)."""
    data_agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    tentativas = 5
    while tentativas > 0:
        conn = None
        try:
            conn = _get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO vagas (
                    user_id, plataforma, id_externo, titulo, empresa, localizacao,
                    link, data_publicacao, data_scraped, categoria,
                    descricao_completa, status,
                    recrutador_nome, recrutador_link, observacoes,
                    salario, tipo_contrato, nivel_experiencia
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Ativa', ?, ?, ?, ?, ?, ?)
            ''', (
                user_id, plataforma, id_externo, titulo, empresa, localizacao,
                link, data_pub, data_agora, categoria,
                descricao_completa,
                recrutador_nome, recrutador_link, observacoes,
                salario, tipo_contrato, nivel_experiencia,
            ))
            conn.commit()
            return True
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                time.sleep(1 + random.uniform(0.1, 0.5))
                tentativas -= 1
            else:
                raise e
        except sqlite3.IntegrityError:
            # Job already exists (UNIQUE constraint on link / plataforma+id_externo)
            return False
        finally:
            if conn:
                conn.close()
    print(f"[DB ERROR] save_job failed for {plataforma} after multiple retries (Database Locked).")
    return False
