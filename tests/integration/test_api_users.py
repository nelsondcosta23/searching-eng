"""Integration tests: POST /api/v1/users/sync and DELETE /api/v1/users/{user_id}."""
import sqlite3
import pytest

_UID = "user-integ-0000-0001"


class TestUserSync:
    def test_sync_creates_profile(self, api_client, auth_headers, session_db):
        payload = {
            "user_id": _UID,
            "is_active": True,
            "job_titles": ["Software Engineer", "Backend Developer"],
            "locations": ["Portugal", "Remote"],
            "keywords": ["python", "aws", "kubernetes"],
            "negative_keywords": ["internship", "junior"],
            "job_profile": "tech",
        }
        r = api_client.post("/api/v1/users/sync", json=payload, headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "success"

        # Verify row written to DB
        conn = sqlite3.connect(session_db)
        row = conn.execute(
            "SELECT job_titles, job_profile FROM users_perfil WHERE user_id = ?",
            (_UID,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert "Software Engineer" in row[0]
        assert row[1] == "tech"

    def test_sync_updates_existing_profile(self, api_client, auth_headers, session_db):
        payload = {
            "user_id": _UID,
            "is_active": True,
            "job_titles": ["CTO"],
            "keywords": ["startup", "saas"],
            "job_profile": "tech",
        }
        r = api_client.post("/api/v1/users/sync", json=payload, headers=auth_headers)
        assert r.status_code == 200

        conn = sqlite3.connect(session_db)
        row = conn.execute(
            "SELECT job_titles FROM users_perfil WHERE user_id = ?", (_UID,)
        ).fetchone()
        conn.close()
        assert "CTO" in row[0]

    def test_sync_unknown_job_profile_normalises_to_generalist(
        self, api_client, auth_headers, session_db
    ):
        uid = "user-integ-profile-fallback"
        r = api_client.post(
            "/api/v1/users/sync",
            json={"user_id": uid, "job_titles": ["Engineer"], "job_profile": "nonexistent-profile"},
            headers=auth_headers,
        )
        assert r.status_code == 200

        conn = sqlite3.connect(session_db)
        row = conn.execute(
            "SELECT job_profile FROM users_perfil WHERE user_id = ?", (uid,)
        ).fetchone()
        conn.close()
        assert row[0] == "generalist"

    def test_sync_user_id_too_long_returns_422(self, api_client, auth_headers):
        r = api_client.post(
            "/api/v1/users/sync",
            json={"user_id": "x" * 129, "job_titles": ["Engineer"]},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_sync_missing_job_titles_returns_422(self, api_client, auth_headers):
        r = api_client.post(
            "/api/v1/users/sync",
            json={"user_id": "uid-no-titles"},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_sync_audit_row_written(self, api_client, auth_headers, session_db):
        uid = "user-integ-audit"
        api_client.post(
            "/api/v1/users/sync",
            json={"user_id": uid, "job_titles": ["Engineer"]},
            headers=auth_headers,
        )
        conn = sqlite3.connect(session_db)
        row = conn.execute(
            "SELECT event, user_id FROM audit_log WHERE user_id = ? AND event = 'user_sync'",
            (uid,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "user_sync"


class TestUserDelete:
    def _create_user(self, api_client, auth_headers, uid):
        api_client.post(
            "/api/v1/users/sync",
            json={"user_id": uid, "job_titles": ["Engineer"]},
            headers=auth_headers,
        )

    def test_delete_removes_user_from_db(self, api_client, auth_headers, session_db):
        uid = "user-to-delete-001"
        self._create_user(api_client, auth_headers, uid)

        r = api_client.delete(f"/api/v1/users/{uid}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "deleted"

        conn = sqlite3.connect(session_db)
        row = conn.execute(
            "SELECT user_id FROM users_perfil WHERE user_id = ?", (uid,)
        ).fetchone()
        conn.close()
        assert row is None

    def test_delete_nonexistent_user_returns_200(self, api_client, auth_headers):
        # Idempotent — deleting a non-existent user is not an error
        r = api_client.delete(
            "/api/v1/users/user-never-existed-xyz",
            headers=auth_headers,
        )
        assert r.status_code == 200

    def test_delete_writes_audit_row(self, api_client, auth_headers, session_db):
        uid = "user-to-delete-audit"
        self._create_user(api_client, auth_headers, uid)
        api_client.delete(f"/api/v1/users/{uid}", headers=auth_headers)

        conn = sqlite3.connect(session_db)
        row = conn.execute(
            "SELECT event FROM audit_log WHERE user_id = ? AND event = 'user_delete'",
            (uid,)
        ).fetchone()
        conn.close()
        assert row is not None

    def test_delete_requires_auth(self, api_client):
        r = api_client.delete("/api/v1/users/somebody")
        assert r.status_code == 401

    def test_owner_restriction_blocks_wrong_user(self, api_client, auth_headers):
        import api.main as main_mod
        original = main_mod.OWNER_USER_ID
        main_mod.OWNER_USER_ID = "owner-only-user"
        try:
            r = api_client.delete(
                "/api/v1/users/different-user", headers=auth_headers
            )
        finally:
            main_mod.OWNER_USER_ID = original
        assert r.status_code == 403
