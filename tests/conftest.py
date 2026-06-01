"""Shared fixtures for the searching-eng test suite.

Environment vars are set at module level (before any app module is imported)
so api.main, db_helper, etc. read the correct values on first import.
"""
import os
import sys
import sqlite3

import pytest

# ── Env vars — must be set BEFORE api.main is imported ───────────────────────
_TEST_API_KEY  = "test-api-key-pytest-AAAA1111"
_TEST_USER_ID  = "user-test-00000000-0000-0000-0000-000001"

os.environ.setdefault("API_KEY",           _TEST_API_KEY)
os.environ.setdefault("OWNER_USER_ID",    "")        # no per-user restriction in tests
os.environ.setdefault("METRICS_ENABLED",  "0")       # skip Prometheus init
os.environ.setdefault("LOG_LEVEL",        "error")   # suppress info noise
os.environ.setdefault("SENTRY_DSN",       "")        # disable Sentry
os.environ.setdefault("REDIS_URL",        "")        # no Redis in tests
# Must include "tech" so the sync test can send job_profile="tech"
os.environ.setdefault(
    "VALID_JOB_PROFILES",
    "tech,data,design,engineering,marketing,hr,finance,sales,generalist",
)


# ── Schema helper ─────────────────────────────────────────────────────────────

def init_test_db(db_path: str) -> None:
    """Create the minimal schema used by the API and db_helper."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users_perfil (
            user_id             TEXT PRIMARY KEY,
            is_active           INTEGER DEFAULT 1,
            job_titles          TEXT NOT NULL DEFAULT '',
            keywords            TEXT,
            negative_keywords   TEXT,
            negative_companies  TEXT,
            locations           TEXT,
            is_remote           INTEGER DEFAULT 0,
            min_salary          INTEGER DEFAULT 0,
            experience_levels   TEXT,
            job_profile         TEXT DEFAULT 'generalist',
            contract_type       TEXT,
            required_languages  TEXT,
            search_description  TEXT,
            callback_url        TEXT,
            last_webhook_sent   TIMESTAMP,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS jobs_global (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            link                    TEXT NOT NULL UNIQUE,
            titulo                  TEXT,
            empresa                 TEXT,
            plataforma              TEXT,
            id_externo              TEXT,
            localizacao             TEXT,
            categoria               TEXT,
            descricao               TEXT,
            observacoes             TEXT,
            recrutador_nome         TEXT,
            recrutador_link         TEXT,
            salario                 TEXT,
            tipo_contrato           TEXT,
            nivel_experiencia       TEXT,
            data_publicacao         TEXT,
            data_scraped            TEXT DEFAULT CURRENT_TIMESTAMP,
            data_ultima_verificacao TEXT,
            status                  TEXT DEFAULT 'Ativa'
        );

        CREATE TABLE IF NOT EXISTS jobs_users (
            job_id          INTEGER NOT NULL,
            user_id         TEXT    NOT NULL,
            relevance_score INTEGER,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (job_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT    DEFAULT CURRENT_TIMESTAMP,
            event   TEXT    NOT NULL,
            user_id TEXT,
            ip      TEXT,
            detail  TEXT
        );
    """)
    conn.commit()
    conn.close()


# ── DB fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def session_db(tmp_path_factory):
    """One shared DB for the entire test session (API integration tests)."""
    db_path = str(tmp_path_factory.mktemp("db") / "vagas.db")
    init_test_db(db_path)
    return db_path


@pytest.fixture
def tmp_db(tmp_path):
    """Fresh DB per unit test (db_helper tests)."""
    db_path = str(tmp_path / "vagas.db")
    init_test_db(db_path)
    return db_path


# ── API client fixtures ───────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def api_client(session_db):
    """FastAPI TestClient backed by the session DB.

    Patches api.main AND automation.db_helper DB_PATH so all writes
    (direct in main.py + via execute_with_retry) land in the test DB.
    """
    import api.main as main_mod
    import automation.db_helper as db_mod

    main_mod.DB_PATH        = session_db
    db_mod.DB_PATH          = session_db   # sync endpoint writes via db_helper
    main_mod.API_KEY        = _TEST_API_KEY
    main_mod._API_KEYS      = frozenset([_TEST_API_KEY])
    main_mod.OWNER_USER_ID  = ""
    # _pb_available would try to connect to PocketBase; keep it always-False
    main_mod._pb_available = lambda: False

    from fastapi.testclient import TestClient
    with TestClient(main_mod.app, raise_server_exceptions=True) as client:
        yield client


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {_TEST_API_KEY}"}


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Clear in-memory auth-failure counters between tests."""
    yield
    try:
        import api.main as main_mod
        main_mod._mem_fails.clear()
    except Exception:
        pass
