"""Unit tests for automation/db_helper.py — uses a fresh temp DB per test."""
import sqlite3
import pytest
import automation.db_helper as db_mod


@pytest.fixture(autouse=True)
def patch_db_path(tmp_db, monkeypatch):
    """Redirect all db_helper operations to the test DB for this test."""
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_db)


# ── save_job ───────────────────────────────────────────────────────────

class TestSaveJob:
    def test_new_job_returns_true(self):
        ok = db_mod.save_job(
            user_id="user-001",
            plataforma="TestPlatform",
            id_externo="ext-1",
            titulo="Python Developer",
            empresa="Acme",
            localizacao="Lisboa",
            link="https://example.com/job/1",
        )
        assert ok is True

    def test_new_job_appears_in_jobs(self, tmp_db):
        db_mod.save_job(
            user_id="user-001",
            plataforma="TestPlatform",
            id_externo="ext-2",
            titulo="Go Developer",
            empresa="Corp",
            localizacao="Porto",
            link="https://example.com/job/2",
        )
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT titulo FROM jobs WHERE link = ?",
            ("https://example.com/job/2",)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "Go Developer"

    def test_duplicate_link_is_idempotent(self, tmp_db):
        """Calling save_job twice for the same link is safe and does not duplicate."""
        link = "https://example.com/job/dup"
        r1 = db_mod.save_job("user-X", "P", "e1", "Job", "Co", "Loc", link)
        r2 = db_mod.save_job("user-X", "P", "e1", "Job", "Co", "Loc", link)
        assert r1 is True
        assert r2 is True

        conn = sqlite3.connect(tmp_db)
        count = conn.execute("SELECT COUNT(*) FROM jobs WHERE link = ?", (link,)).fetchone()[0]
        conn.close()
        assert count == 1

    def test_optional_fields_stored(self, tmp_db):
        db_mod.save_job(
            user_id="user-003",
            plataforma="P",
            id_externo="ext-opt",
            titulo="Job",
            empresa="Co",
            localizacao="Loc",
            link="https://example.com/job/opt",
            descricao_completa="Full description here",
            salario="€50k",
            tipo_contrato="full-time",
            nivel_experiencia="Sénior",
            recrutador_nome="Ana Recrutadora",
            recrutador_link="https://linkedin.com/in/ana",
        )
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT descricao, salario, tipo_contrato, nivel_experiencia, recrutador_nome, recrutador_link "
            "FROM jobs WHERE link = ?",
            ("https://example.com/job/opt",)
        ).fetchone()
        conn.close()
        assert row[0] == "Full description here"
        assert row[1] == "€50k"
        assert row[2] == "full-time"
        assert row[3] == "Sénior"
        assert row[4] == "Ana Recrutadora"
        assert row[5] == "https://linkedin.com/in/ana"

    def test_normalized_country_resolution(self, tmp_db):
        # 1. Portugal
        db_mod.save_job("u", "P", "e1", "Job", "Co", "Lisboa", "https://example.com/pt-1")
        # 2. UK
        db_mod.save_job("u", "P", "e2", "Job", "Co", "London, UK", "https://example.com/uk-1")
        # 3. USA
        db_mod.save_job("u", "P", "e3", "Job", "Co", "New York, USA", "https://example.com/us-1")
        # 4. Outro
        db_mod.save_job("u", "P", "e4", "Job", "Co", "Berlin, Germany", "https://example.com/other-1")
        # 5. Fallback portal
        db_mod.save_job("u", "Sapo Jobs", "e5", "Job", "Co", "Remote", "https://example.com/sapo-1")

        conn = sqlite3.connect(tmp_db)
        rows = conn.execute("SELECT link, normalized_country FROM jobs ORDER BY link").fetchall()
        conn.close()

        mapping = dict(rows)
        assert mapping["https://example.com/pt-1"] == "Portugal"
        assert mapping["https://example.com/uk-1"] == "United Kingdom"
        assert mapping["https://example.com/us-1"] == "United States"
        assert mapping["https://example.com/other-1"] == "Outro"
        assert mapping["https://example.com/sapo-1"] == "Portugal"


# ── job_exists ────────────────────────────────────────────────────────────────

class TestJobExists:
    def test_new_link_returns_false(self):
        assert db_mod.job_exists("https://example.com/nonexistent") is False

    def test_existing_link_returns_true(self, tmp_db):
        link = "https://example.com/exists-global"
        db_mod.save_job("user-001", "P", "e", "Job", "Co", "Loc", link)
        assert db_mod.job_exists(link) is True

    def test_title_empresa_dedup(self, tmp_db):
        link = "https://example.com/title-dedup"
        db_mod.save_job(
            "user-DUP", "P", "e", "Exact Title", "Exact Corp", "Loc", link
        )
        # Same title+company already exists — should be flagged
        assert db_mod.job_exists(
            "https://other.com/job/999",
            titulo="Exact Title",
            empresa="Exact Corp",
        ) is True


# ── execute_with_retry ────────────────────────────────────────────────────────

class TestExecuteWithRetry:
    def test_simple_update(self, tmp_db):
        # Insert a job first
        db_mod.save_job("user-retry", "P", "e1", "Old Title", "Co", "Loc", "https://example.com/update")

        rows = db_mod.execute_with_retry(
            "UPDATE jobs SET titulo = ? WHERE link = ?",
            ("New Title", "https://example.com/update"),
        )
        assert rows == 1

        conn = sqlite3.connect(tmp_db)
        val = conn.execute(
            "SELECT titulo FROM jobs WHERE link = ?",
            ("https://example.com/update",)
        ).fetchone()[0]
        conn.close()
        assert val == "New Title"

    def test_no_matching_rows_returns_zero(self):
        rows = db_mod.execute_with_retry(
            "UPDATE jobs SET titulo = ? WHERE link = ?",
            ("X", "https://example.com/nonexistent-link"),
        )
        assert rows == 0
