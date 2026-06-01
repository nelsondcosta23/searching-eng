"""Unit tests for automation/db_helper.py — uses a fresh temp DB per test."""
import sqlite3
import pytest
import automation.db_helper as db_mod


@pytest.fixture(autouse=True)
def patch_db_path(tmp_db, monkeypatch):
    """Redirect all db_helper operations to the test DB for this test."""
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_db)


# ── save_job_global ───────────────────────────────────────────────────────────

class TestSaveJobGlobal:
    def test_new_job_returns_true(self):
        ok = db_mod.save_job_global(
            user_id="user-001",
            plataforma="TestPlatform",
            id_externo="ext-1",
            titulo="Python Developer",
            empresa="Acme",
            localizacao="Lisboa",
            link="https://example.com/job/1",
        )
        assert ok is True

    def test_new_job_appears_in_jobs_global(self, tmp_db):
        db_mod.save_job_global(
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
            "SELECT titulo FROM jobs_global WHERE link = ?",
            ("https://example.com/job/2",)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "Go Developer"

    def test_new_job_creates_user_association(self, tmp_db):
        db_mod.save_job_global(
            user_id="user-002",
            plataforma="TestPlatform",
            id_externo="ext-3",
            titulo="Backend Engineer",
            empresa="Tech",
            localizacao="Remote",
            link="https://example.com/job/3",
        )
        conn = sqlite3.connect(tmp_db)
        count = conn.execute(
            "SELECT COUNT(*) FROM jobs_users WHERE user_id = ?",
            ("user-002",)
        ).fetchone()[0]
        conn.close()
        assert count == 1

    def test_same_job_second_user_shares_global_row(self, tmp_db):
        """Same link saved by two users → one jobs_global row, two jobs_users rows."""
        link = "https://example.com/job/shared"
        db_mod.save_job_global("user-A", "P", "ext-A", "Shared Job", "Co", "Lisbon", link)
        db_mod.save_job_global("user-B", "P", "ext-B", "Shared Job", "Co", "Lisbon", link)

        conn = sqlite3.connect(tmp_db)
        global_count = conn.execute(
            "SELECT COUNT(*) FROM jobs_global WHERE link = ?", (link,)
        ).fetchone()[0]
        user_count = conn.execute(
            "SELECT COUNT(*) FROM jobs_users WHERE user_id IN ('user-A', 'user-B')"
        ).fetchone()[0]
        conn.close()

        assert global_count == 1   # deduplication works
        assert user_count == 2     # both users see the job

    def test_duplicate_link_same_user_is_idempotent(self):
        """Calling save_job_global twice for the same (link, user) is safe."""
        link = "https://example.com/job/dup"
        r1 = db_mod.save_job_global("user-X", "P", "e1", "Job", "Co", "Loc", link)
        r2 = db_mod.save_job_global("user-X", "P", "e1", "Job", "Co", "Loc", link)
        # Both calls should succeed without raising
        assert r1 is True

    def test_optional_fields_stored(self, tmp_db):
        db_mod.save_job_global(
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
        )
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT descricao, salario, tipo_contrato, nivel_experiencia "
            "FROM jobs_global WHERE link = ?",
            ("https://example.com/job/opt",)
        ).fetchone()
        conn.close()
        assert row[0] == "Full description here"
        assert row[1] == "€50k"
        assert row[2] == "full-time"
        assert row[3] == "Sénior"


# ── job_exists ────────────────────────────────────────────────────────────────

class TestJobExists:
    def test_new_link_returns_false(self):
        assert db_mod.job_exists("https://example.com/nonexistent") is False

    def test_existing_link_without_user_returns_true(self, tmp_db):
        link = "https://example.com/exists-global"
        db_mod.save_job_global("user-001", "P", "e", "Job", "Co", "Loc", link)
        assert db_mod.job_exists(link) is True

    def test_existing_link_with_matching_user_returns_true(self, tmp_db):
        link = "https://example.com/exists-user"
        db_mod.save_job_global("user-111", "P", "e", "Job", "Co", "Loc", link)
        assert db_mod.job_exists(link, user_id="user-111") is True

    def test_existing_link_different_user_returns_false(self, tmp_db):
        link = "https://example.com/other-user"
        db_mod.save_job_global("user-AAA", "P", "e", "Job", "Co", "Loc", link)
        # user-BBB hasn't been associated yet
        assert db_mod.job_exists(link, user_id="user-BBB") is False

    def test_title_empresa_dedup(self, tmp_db):
        link = "https://example.com/title-dedup"
        db_mod.save_job_global(
            "user-DUP", "P", "e", "Exact Title", "Exact Corp", "Loc", link
        )
        # Same title+company already exists for user-DUP — should be flagged
        assert db_mod.job_exists(
            "https://other.com/job/999",
            titulo="Exact Title",
            empresa="Exact Corp",
            user_id="user-DUP",
        ) is True


# ── execute_with_retry ────────────────────────────────────────────────────────

class TestExecuteWithRetry:
    def test_simple_update(self, tmp_db):
        # Insert a user first
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            "INSERT INTO users_perfil (user_id, job_titles) VALUES (?, ?)",
            ("user-retry", "Engineer")
        )
        conn.commit()
        conn.close()

        rows = db_mod.execute_with_retry(
            "UPDATE users_perfil SET job_titles = ? WHERE user_id = ?",
            ("Data Scientist", "user-retry"),
        )
        assert rows == 1

        conn = sqlite3.connect(tmp_db)
        val = conn.execute(
            "SELECT job_titles FROM users_perfil WHERE user_id = ?",
            ("user-retry",)
        ).fetchone()[0]
        conn.close()
        assert val == "Data Scientist"

    def test_no_matching_rows_returns_zero(self):
        rows = db_mod.execute_with_retry(
            "UPDATE users_perfil SET job_titles = ? WHERE user_id = ?",
            ("X", "nonexistent-user"),
        )
        assert rows == 0
