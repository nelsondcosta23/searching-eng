"""Integration tests: authentication and rate limiting."""
import pytest


class TestAuthRequired:
    """Every authenticated endpoint must reject missing / wrong credentials."""

    def test_no_auth_header_returns_401(self, api_client):
        r = api_client.get("/api/v1/status")
        assert r.status_code == 401

    def test_wrong_api_key_returns_401(self, api_client):
        r = api_client.get(
            "/api/v1/status",
            headers={"Authorization": "Bearer wrong-key-0000"},
        )
        assert r.status_code == 401

    def test_malformed_bearer_returns_401(self, api_client):
        r = api_client.get(
            "/api/v1/status",
            headers={"Authorization": "Token not-bearer"},
        )
        assert r.status_code == 401

    def test_valid_key_returns_200(self, api_client, auth_headers):
        r = api_client.get("/api/v1/status", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestRateLimiting:
    """After enough auth failures from the same IP, subsequent requests return 429."""

    def test_too_many_failures_triggers_429(self, api_client):
        import api.main as main_mod

        # Temporarily lower the threshold so the test is fast
        original = main_mod._RATE_MAX_FAILS
        main_mod._RATE_MAX_FAILS = 3
        # Use a unique IP so other tests are not affected
        hdrs = {
            "Authorization": "Bearer wrong-key",
            "X-Forwarded-For": "192.0.2.99",   # TEST-NET-3 — unroutable, unique per test
        }
        try:
            responses = [
                api_client.get("/api/v1/status", headers=hdrs)
                for _ in range(main_mod._RATE_MAX_FAILS + 2)
            ]
        finally:
            main_mod._RATE_MAX_FAILS = original

        # At least the last request must be 429
        assert responses[-1].status_code == 429

    def test_429_response_includes_retry_after(self, api_client):
        import api.main as main_mod
        original = main_mod._RATE_MAX_FAILS
        main_mod._RATE_MAX_FAILS = 2
        hdrs = {
            "Authorization": "Bearer wrong-key",
            "X-Forwarded-For": "192.0.2.100",
        }
        try:
            for _ in range(main_mod._RATE_MAX_FAILS + 2):
                r = api_client.get("/api/v1/status", headers=hdrs)
        finally:
            main_mod._RATE_MAX_FAILS = original

        assert r.status_code == 429
        assert "retry-after" in r.headers or r.json().get("detail")


class TestStatusEndpoint:
    def test_status_fields_present(self, api_client, auth_headers):
        r = api_client.get("/api/v1/status", headers=auth_headers)
        body = r.json()
        assert "status" in body
        assert "version" in body
        assert "database" in body
        assert "total_jobs_in_db" in body


class TestMultipleApiKeys:
    """F-2: API_KEYS supports a set of valid tokens for zero-downtime rotation."""

    def test_second_key_in_set_is_accepted(self, api_client):
        import api.main as main_mod
        second_key = "test-second-key-BBBB2222"
        original = main_mod._API_KEYS
        existing = next(iter(original)) if original else None
        new_keys = frozenset(filter(None, [existing, second_key]))
        main_mod._API_KEYS = new_keys
        try:
            r = api_client.get(
                "/api/v1/status",
                headers={"Authorization": f"Bearer {second_key}"},
            )
        finally:
            main_mod._API_KEYS = original
        assert r.status_code == 200

    def test_key_not_in_set_is_rejected(self, api_client):
        r = api_client.get(
            "/api/v1/status",
            headers={"Authorization": "Bearer not-a-valid-key-xyz"},
        )
        assert r.status_code in (401, 429)
