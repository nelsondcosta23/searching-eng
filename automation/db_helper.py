import sqlite3
import os
import time
import random
import contextlib
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))

# Default retry settings for transient `database is locked` errors. SQLite's
# 20s busy timeout (set in _get_connection) handles waits during statement
# execution; these retries cover commit-time conflicts in WAL mode.
_DEFAULT_RETRIES = 5
_BACKOFF_BASE = 1.0  # seconds


def _get_connection():
    """Returns a new SQLite connection with WAL mode and performance PRAGMAs."""
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute('PRAGMA synchronous=NORMAL;')       # safe with WAL; ~2× faster commits
    conn.execute('PRAGMA cache_size=-8000;')         # 8 MB page cache
    conn.execute('PRAGMA mmap_size=134217728;')      # 128 MB memory-mapped I/O
    conn.execute('PRAGMA temp_store=MEMORY;')
    conn.execute('PRAGMA wal_autocheckpoint=1000;')
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
            conn.execute("UPDATE jobs_global SET ...")
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
    salario: str = "",
    tipo_contrato: str = "",
    nivel_experiencia: str = "",
) -> bool:
    """Write to jobs table (upsert). Returns True if saved or updated, False otherwise."""
    data_agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for attempt in range(_DEFAULT_RETRIES):
        conn = None
        try:
            conn = _get_connection()
            # Upsert into jobs — enrich empty fields only (never overwrite)
            conn.execute('''
                INSERT INTO jobs (
                    link, plataforma, id_externo, titulo, empresa, localizacao,
                    data_publicacao, data_scraped, salario, tipo_contrato,
                    nivel_experiencia, descricao, observacoes, recrutador_nome, recrutador_link
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(link) DO UPDATE SET
                    descricao         = CASE WHEN COALESCE(excluded.descricao,         '') != '' AND COALESCE(descricao,         '') = '' THEN excluded.descricao         ELSE descricao         END,
                    observacoes       = CASE WHEN COALESCE(excluded.observacoes,       '') != '' AND COALESCE(observacoes,       '') = '' THEN excluded.observacoes       ELSE observacoes       END,
                    salario           = CASE WHEN COALESCE(excluded.salario,           '') != '' AND COALESCE(salario,           '') = '' THEN excluded.salario           ELSE salario           END,
                    tipo_contrato     = CASE WHEN COALESCE(excluded.tipo_contrato,     '') != '' AND COALESCE(tipo_contrato,     '') = '' THEN excluded.tipo_contrato     ELSE tipo_contrato     END,
                    nivel_experiencia = CASE WHEN COALESCE(excluded.nivel_experiencia, '') != '' AND COALESCE(nivel_experiencia, '') = '' THEN excluded.nivel_experiencia ELSE nivel_experiencia END,
                    recrutador_nome   = CASE WHEN COALESCE(excluded.recrutador_nome,   '') != '' AND COALESCE(recrutador_nome,   '') = '' THEN excluded.recrutador_nome   ELSE recrutador_nome   END,
                    recrutador_link   = CASE WHEN COALESCE(excluded.recrutador_link,   '') != '' AND COALESCE(recrutador_link,   '') = '' THEN excluded.recrutador_link   ELSE recrutador_link   END
            ''', (
                link, plataforma, id_externo, titulo, empresa, localizacao,
                data_pub, data_agora, salario, tipo_contrato,
                nivel_experiencia, descricao_completa, observacoes, recrutador_nome, recrutador_link
            ))
            conn.commit()
            return True
        except sqlite3.OperationalError as e:
            if _is_locked_error(e) and attempt < _DEFAULT_RETRIES - 1:
                time.sleep(_BACKOFF_BASE + random.uniform(0.1, 0.5))
                continue
            raise
        except sqlite3.IntegrityError:
            return False
        finally:
            if conn:
                conn.close()
    return False


def job_exists(link: str, titulo: str = '', empresa: str = '', user_id: str = '') -> bool:
    """Check if this job already exists in the jobs table by link or by title+company."""
    for attempt in range(_DEFAULT_RETRIES):
        conn = None
        try:
            conn = _get_connection()
            # Primary: check link
            row = conn.execute('SELECT 1 FROM jobs WHERE link = ? LIMIT 1', (link,)).fetchone()
            if row:
                return True
            # Secondary: same title+company
            if titulo and empresa:
                t = titulo.strip().lower()
                e = empresa.strip().lower()
                row = conn.execute('''
                    SELECT 1 FROM jobs
                    WHERE LOWER(TRIM(titulo)) = ? AND LOWER(TRIM(empresa)) = ?
                    LIMIT 1
                ''', (t, e)).fetchone()
                if row:
                    return True
            return False
        except sqlite3.OperationalError as e:
            if _is_locked_error(e) and attempt < _DEFAULT_RETRIES - 1:
                time.sleep(_BACKOFF_BASE + random.uniform(0.1, 0.5))
                continue
            raise
        finally:
            if conn:
                conn.close()
    print(f"[DB ERROR] job_exists failed for {link} after retries.")
    return False


# Alias for backward compatibility
save_job_global = save_job
