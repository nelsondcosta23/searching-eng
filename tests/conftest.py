"""Shared fixtures for the scrapper_tcc test suite.

Environment vars are set at module level so db_helper etc. read the correct values on first import.
"""
import os
import sqlite3
import pytest

os.environ.setdefault("LOG_LEVEL", "error")   # suppress info noise
os.environ.setdefault("SENTRY_DSN", "")        # disable Sentry
os.environ.setdefault("METRICS_ENABLED", "0")  # disable metrics

def init_test_db(db_path: str) -> None:
    """Create the minimal schema used by db_helper and automation scripts."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            plataforma           TEXT NOT NULL,
            id_externo           TEXT,
            titulo               TEXT NOT NULL,
            empresa              TEXT NOT NULL,
            localizacao          TEXT,
            link                 TEXT NOT NULL UNIQUE,
            data_publicacao      TEXT,
            data_scraped         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            posting_age_days     INTEGER,
            salario              TEXT,
            tipo_contrato        TEXT,
            nivel_experiencia    TEXT,
            job_type             TEXT,
            descricao            TEXT,
            observacoes          TEXT,
            recrutador_nome      TEXT,
            recrutador_link      TEXT,
            status               TEXT DEFAULT 'Ativa',
            CONSTRAINT unique_job_platform UNIQUE (plataforma, id_externo)
        );

        CREATE TABLE IF NOT EXISTS companies (
            name                 TEXT PRIMARY KEY,
            inception_year       INTEGER,
            company_age          INTEGER,
            description          TEXT,
            last_updated         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()

@pytest.fixture(scope="session")
def session_db(tmp_path_factory):
    """One shared DB for the entire test session."""
    db_path = str(tmp_path_factory.mktemp("db") / "vagas.db")
    init_test_db(db_path)
    return db_path

@pytest.fixture
def tmp_db(tmp_path):
    """Fresh DB per unit test (db_helper tests)."""
    db_path = str(tmp_path / "vagas.db")
    init_test_db(db_path)
    return db_path
