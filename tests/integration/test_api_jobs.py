"""Integration tests: GET /api/v1/jobs, /jobs/{id}, /stats."""
import sqlite3
import pytest


def _create_user(api_client, auth_headers, uid):
    api_client.post(
        "/api/v1/users/sync",
        json={"user_id": uid, "job_titles": ["Engineer"]},
        headers=auth_headers,
    )


def _insert_job(db_path: str, user_id: str, link: str, titulo: str,
                plataforma: str = "TestPlat", score: int = 75) -> int:
    """Insert a job directly into the test DB; returns the jobs_global id."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT OR IGNORE INTO jobs_global
           (link, titulo, empresa, plataforma, localizacao, data_scraped, status)
           VALUES (?, ?, 'Acme', ?, 'Lisboa', CURRENT_TIMESTAMP, 'Ativa')""",
        (link, titulo, plataforma),
    )
    job_id = conn.execute(
        "SELECT id FROM jobs_global WHERE link = ?", (link,)
    ).fetchone()[0]
    conn.execute(
        "INSERT OR IGNORE INTO jobs_users (job_id, user_id, relevance_score) VALUES (?, ?, ?)",
        (job_id, user_id, score),
    )
    conn.commit()
    conn.close()
    return job_id


class TestGetJobs:
    _UID = "user-jobs-get-001"

    def test_empty_user_returns_empty_list(self, api_client, auth_headers):
        uid = "user-jobs-empty-xyz"
        _create_user(api_client, auth_headers, uid)
        r = api_client.get(
            "/api/v1/jobs", params={"user_id": uid, "run_date": "all"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0
        assert r.json()["jobs"] == []

    def test_returns_jobs_for_user(self, api_client, auth_headers, session_db):
        uid = "user-jobs-has-data"
        _create_user(api_client, auth_headers, uid)
        _insert_job(session_db, uid, "https://ex.com/job/101", "Python Dev")
        _insert_job(session_db, uid, "https://ex.com/job/102", "Go Dev")

        r = api_client.get(
            "/api/v1/jobs",
            params={"user_id": uid, "run_date": "all"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["total"] == 2

    def test_jobs_include_expected_fields(self, api_client, auth_headers, session_db):
        uid = "user-jobs-fields"
        _create_user(api_client, auth_headers, uid)
        _insert_job(session_db, uid, "https://ex.com/job/201", "Field Test Job", score=80)

        r = api_client.get(
            "/api/v1/jobs",
            params={"user_id": uid, "run_date": "all"},
            headers=auth_headers,
        )
        job = r.json()["jobs"][0]
        assert "titulo" in job
        assert "empresa" in job
        assert "link" in job
        assert "relevance_score" in job

    def test_invalid_run_date_returns_422(self, api_client, auth_headers):
        r = api_client.get(
            "/api/v1/jobs",
            params={"user_id": "some-user", "run_date": "not-a-date"},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_limit_respected(self, api_client, auth_headers, session_db):
        uid = "user-jobs-limit"
        _create_user(api_client, auth_headers, uid)
        for i in range(5):
            _insert_job(session_db, uid, f"https://ex.com/job/limit/{i}", f"Job {i}")

        r = api_client.get(
            "/api/v1/jobs",
            params={"user_id": uid, "run_date": "all", "limit": 2},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert len(r.json()["jobs"]) <= 2

    def test_sort_by_relevance(self, api_client, auth_headers, session_db):
        uid = "user-jobs-sort"
        _create_user(api_client, auth_headers, uid)
        _insert_job(session_db, uid, "https://ex.com/sort/low",  "Low Score Job",  score=10)
        _insert_job(session_db, uid, "https://ex.com/sort/high", "High Score Job", score=90)

        r = api_client.get(
            "/api/v1/jobs",
            params={"user_id": uid, "run_date": "all", "sort_by_relevance": True},
            headers=auth_headers,
        )
        jobs = r.json()["jobs"]
        assert jobs[0]["relevance_score"] >= jobs[-1]["relevance_score"]

    def test_status_filter(self, api_client, auth_headers, session_db):
        uid = "user-jobs-status"
        _create_user(api_client, auth_headers, uid)
        _insert_job(session_db, uid, "https://ex.com/stat/active", "Active Job")
        # Mark one as Expirada
        conn = sqlite3.connect(session_db)
        conn.execute(
            "UPDATE jobs_global SET status = 'Expirada' WHERE link = ?",
            ("https://ex.com/stat/active",)
        )
        conn.commit()
        conn.close()

        r = api_client.get(
            "/api/v1/jobs",
            params={"user_id": uid, "run_date": "all", "status": "Ativa"},
            headers=auth_headers,
        )
        for job in r.json()["jobs"]:
            assert job["status"] == "Ativa"

    def test_requires_user_id(self, api_client, auth_headers):
        r = api_client.get("/api/v1/jobs", headers=auth_headers)
        assert r.status_code == 422


class TestGetSingleJob:
    def test_nonexistent_id_returns_404(self, api_client, auth_headers):
        r = api_client.get("/api/v1/jobs/9999999", headers=auth_headers)
        assert r.status_code == 404

    def test_existing_job_returns_200(self, api_client, auth_headers, session_db):
        uid = "user-single-job"
        _create_user(api_client, auth_headers, uid)
        job_id = _insert_job(session_db, uid, "https://ex.com/single/1", "Single Job Test")

        r = api_client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["titulo"] == "Single Job Test"

    def test_requires_auth(self, api_client, session_db):
        r = api_client.get("/api/v1/jobs/1")
        assert r.status_code == 401


class TestGetStats:
    def test_stats_returns_expected_fields(self, api_client, auth_headers, session_db):
        uid = "user-stats-001"
        _create_user(api_client, auth_headers, uid)
        _insert_job(session_db, uid, "https://ex.com/stats/job1", "Stats Job")

        r = api_client.get(
            "/api/v1/stats", params={"user_id": uid}, headers=auth_headers
        )
        assert r.status_code == 200
        body = r.json()
        assert "total_jobs" in body
        assert "active_jobs" in body
        assert "expired_jobs" in body
        assert "jobs_today" in body
        assert "generated_at" in body

    def test_stats_count_correct(self, api_client, auth_headers, session_db):
        uid = "user-stats-count"
        _create_user(api_client, auth_headers, uid)
        _insert_job(session_db, uid, "https://ex.com/stats/c1", "Job 1")
        _insert_job(session_db, uid, "https://ex.com/stats/c2", "Job 2")

        r = api_client.get(
            "/api/v1/stats", params={"user_id": uid}, headers=auth_headers
        )
        body = r.json()
        assert body["total_jobs"] >= 2

    def test_stats_requires_auth(self, api_client):
        r = api_client.get("/api/v1/stats", params={"user_id": "x"})
        assert r.status_code == 401


class TestProfiles:
    def test_list_profiles_returns_valid_profiles(self, api_client, auth_headers):
        r = api_client.get("/api/v1/profiles", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert "valid_profiles" in body
        assert "generalist" in body["valid_profiles"]
        assert "fallback" in body
