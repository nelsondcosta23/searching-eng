import sqlite3
import os
import time
import random
import re
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
            conn.execute("UPDATE jobs SET status = 'Expirada' WHERE id = ?")
            conn.execute("UPDATE companies SET company_age = ? WHERE name = ?")
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

def get_normalized_country(localizacao: str, plataforma: str) -> str:
    if not localizacao:
        return "Outro"
    
    loc = localizacao.lower()
    # Normalize acronyms like u.s. -> us, u.k. -> uk to simplify regex matching
    loc = loc.replace("u.s.a.", "usa").replace("u.s.", "us").replace("u.k.", "uk")
    
    # 1. Portugal keywords
    pt_keywords = [
        "portugal", "lisboa", "lisbon", "porto", "coimbra", "braga", "aveiro",
        "setubal", "setúbal", "cascais", "oeiras", "alges", "algés", "faro",
        "leiria", "evora", "évora", "viana do castelo", "guarda", "castelo branco",
        "bragança", "braganca", "beja", "portalegre", "santarém", "santarem",
        "viseu", "vila real", "funchal", "ponta delgada", "açores", "azores", "madeira",
        "loulé", "loule"
    ]
    if any(k in loc for k in pt_keywords):
        return "Portugal"
    if re.search(r'\b(pt)\b', loc):
        return "Portugal"
        
    # 2. United Kingdom keywords
    uk_keywords = [
        "united kingdom", "reino unido", "london", "londres", "manchester", "birmingham",
        "edinburgh", "glasgow", "leeds", "liverpool", "england", "scotland", "wales", "belfast",
        "northern ireland", "cardiff", "bristol", "sheffield", "newcastle"
    ]
    if any(k in loc for k in uk_keywords):
        return "United Kingdom"
    if re.search(r'\b(uk|gb)\b', loc):
        return "United Kingdom"
        
    # 3. United States keywords
    us_keywords = [
        "united states", "estados unidos", "usa", "new york", "nova york", "san francisco",
        "california", "califórnia", "texas", "austin", "seattle", "boston", "chicago",
        "washington", "los angeles", "atlanta", "miami", "denver", "colorado",
        "massachusetts", "illinois", "florida", "flórida", "pennsylvania", "pensilvânia",
        "ohio", "michigan", "georgia", "geórgia", "north carolina", "carolina do norte",
        "virginia", "virgínia", "arizona", "oregon", "utah", "minnesota", "minesota", "tennesse",
        "tenessi", "tennessee", "portland", "philadelphia", "dallas", "houston", "san jose",
        "são francisco", "sao francisco", "columbus", "memphis", "nashville", "rochester"
    ]
    if any(k in loc for k in us_keywords):
        return "United States"
    if re.search(r'\b(us)\b', loc):
        return "United States"
        
    # Fallbacks based on portal
    plat_lower = plataforma.lower()
    if any(p in plat_lower for p in ["sapo", "itjobs", "expresso"]):
        return "Portugal"
        
    return "Outro"



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
    work_mode: str = "",
) -> bool:
    """Write to jobs table (upsert) and populate job_skills. Returns True if saved or updated, False otherwise."""
    from scrapers._shared import (
        extract_seniority,
        extract_salary_from_text,
        extract_work_mode,
        extract_skills,
    )

    # 1. Dynamic extraction and backfilling for empty parameters
    desc_clean = descricao_completa or ""

    if not nivel_experiencia or nivel_experiencia.strip() == "":
        nivel_experiencia = extract_seniority(titulo, desc_clean)

    if not salario or salario.strip() == "":
        salario = extract_salary_from_text(desc_clean)

    if not work_mode or work_mode.strip() == "":
        work_mode = extract_work_mode(localizacao, titulo, desc_clean)

    skills = extract_skills(titulo, desc_clean)

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
                    nivel_experiencia, work_mode, descricao, observacoes, recrutador_nome, recrutador_link,
                    normalized_country
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(link) DO UPDATE SET
                    descricao         = CASE WHEN COALESCE(excluded.descricao,         '') != '' AND COALESCE(descricao,         '') = '' THEN excluded.descricao         ELSE descricao         END,
                    observacoes       = CASE WHEN COALESCE(excluded.observacoes,       '') != '' AND COALESCE(observacoes,       '') = '' THEN excluded.observacoes       ELSE observacoes       END,
                    salario           = CASE WHEN COALESCE(excluded.salario,           '') != '' AND COALESCE(salario,           '') = '' THEN excluded.salario           ELSE salario           END,
                    tipo_contrato     = CASE WHEN COALESCE(excluded.tipo_contrato,     '') != '' AND COALESCE(tipo_contrato,     '') = '' THEN excluded.tipo_contrato     ELSE tipo_contrato     END,
                    nivel_experiencia = CASE WHEN COALESCE(excluded.nivel_experiencia, '') != '' AND COALESCE(nivel_experiencia, '') = '' THEN excluded.nivel_experiencia ELSE nivel_experiencia END,
                    work_mode         = CASE WHEN COALESCE(excluded.work_mode,         '') != '' AND COALESCE(work_mode,         '') = '' THEN excluded.work_mode         ELSE work_mode         END,
                    recrutador_nome   = CASE WHEN COALESCE(excluded.recrutador_nome,   '') != '' AND COALESCE(recrutador_nome,   '') = '' THEN excluded.recrutador_nome   ELSE recrutador_nome   END,
                    recrutador_link   = CASE WHEN COALESCE(excluded.recrutador_link,   '') != '' AND COALESCE(recrutador_link,   '') = '' THEN excluded.recrutador_link   ELSE recrutador_link   END
            ''', (
                link, plataforma, id_externo, titulo, empresa, localizacao,
                data_pub, data_agora, salario, tipo_contrato,
                nivel_experiencia, work_mode, desc_clean, observacoes, recrutador_nome, recrutador_link,
                get_normalized_country(localizacao, plataforma)
            ))

            # Fetch the job's primary key ID to map relational skills
            row = conn.execute('SELECT id FROM jobs WHERE link = ?', (link,)).fetchone()
            if row:
                job_id = row[0]
                # Sync skills (delete old, insert new)
                conn.execute('DELETE FROM job_skills WHERE job_id = ?', (job_id,))
                if skills:
                    skill_params = [(job_id, s) for s in skills]
                    conn.executemany('INSERT INTO job_skills (job_id, skill) VALUES (?, ?)', skill_params)

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
