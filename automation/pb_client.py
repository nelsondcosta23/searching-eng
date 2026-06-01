"""PocketBase REST client for the searching-eng platform.

Two collections managed here:
  user_profiles  — one record per user_id (profile + preferences)
  job_results    — top-N jobs pushed per user; replaced on each webhook run

The client auto-authenticates as admin and renews the token transparently.
Every public method returns a safe default (None / False / 0) on any error
so callers can fall back to local SQLite without crashing the pipeline.
"""

import os
import time
from typing import Optional

import requests

PB_URL           = os.environ.get('PB_URL',            'http://pocketbase:8090')
PB_ADMIN_EMAIL   = os.environ.get('PB_ADMIN_EMAIL',    '')
PB_ADMIN_PASSWORD = os.environ.get('PB_ADMIN_PASSWORD', '')

_TIMEOUT = 10  # seconds per request


# ─────────────────────────────────────────────────────────────────────────────
# Collection schemas
# ─────────────────────────────────────────────────────────────────────────────

def _text(name, required=False):
    return {'name': name, 'type': 'text', 'required': required}

def _number(name, required=False):
    return {'name': name, 'type': 'number', 'required': required}

def _bool(name, required=False):
    return {'name': name, 'type': 'bool', 'required': required}


_SCHEMA_USER_PROFILES = [
    _text('user_id',             required=True),
    _text('job_titles'),
    _text('keywords'),
    _text('negative_keywords'),
    _text('negative_companies'),
    _text('locations'),
    _bool('is_remote'),
    _number('min_salary'),
    _text('experience_levels'),
    _text('search_description'),
    _text('contract_type'),
    _text('required_languages'),
    _text('job_profile'),
    _bool('is_active'),
    _text('callback_url'),
    _text('last_webhook_sent'),
]

_SCHEMA_JOB_RESULTS = [
    _text('user_id',           required=True),
    _text('titulo'),
    _text('empresa'),
    _text('plataforma'),
    _text('link'),
    _text('localizacao'),
    _number('relevance_score'),
    _text('status'),
    _text('salario'),
    _text('tipo_contrato'),
    _text('nivel_experiencia'),
    _text('data_scraped'),
    _text('data_publicacao'),
    _text('categoria'),
    _text('observacoes'),
    _text('recrutador_nome'),
    _text('recrutador_link'),
    _text('descricao_completa'),
]


# ─────────────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────────────

class PocketBaseClient:

    def __init__(
        self,
        base_url: str = PB_URL,
        email: str = PB_ADMIN_EMAIL,
        password: str = PB_ADMIN_PASSWORD,
    ):
        self._url   = base_url.rstrip('/')
        self._email = email
        self._pw    = password
        self._token: Optional[str] = None
        self._token_ts: float = 0.0

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _auth(self) -> bool:
        if not self._email or not self._pw:
            return False
        try:
            r = requests.post(
                f'{self._url}/api/admins/auth-with-password',
                json={'identity': self._email, 'password': self._pw},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                self._token    = r.json()['token']
                self._token_ts = time.time()
                return True
            print(f'[PocketBase] Admin auth failed: {r.status_code} {r.text[:200]}')
        except Exception as e:
            print(f'[PocketBase] Admin auth error: {e}')
        return False

    def _hdrs(self) -> dict:
        # PocketBase admin tokens expire after 30 min; renew at 25 min
        if not self._token or (time.time() - self._token_ts) > 1500:
            if not self._auth():
                return {}
        return {'Authorization': self._token}

    def _req(self, method: str, path: str, **kwargs) -> Optional[requests.Response]:
        hdrs = self._hdrs()
        if not hdrs:
            return None
        try:
            r = requests.request(
                method, f'{self._url}{path}',
                headers=hdrs, timeout=_TIMEOUT, **kwargs,
            )
            if r.status_code == 401:
                if self._auth():
                    r = requests.request(
                        method, f'{self._url}{path}',
                        headers=self._hdrs(), timeout=_TIMEOUT, **kwargs,
                    )
            return r
        except Exception as e:
            print(f'[PocketBase] {method} {path} error: {e}')
            return None

    # ── Collection setup ─────────────────────────────────────────────────────

    def _collection_exists(self, name: str) -> bool:
        r = self._req('GET', f'/api/collections/{name}')
        return r is not None and r.status_code == 200

    def _create_collection(self, name: str, schema: list) -> bool:
        # Explicit null rules = admin-only in PocketBase v0.22 base collections.
        # Leaving them unset risks becoming public if PocketBase defaults change.
        r = self._req('POST', '/api/collections', json={
            'name': name, 'type': 'base', 'schema': schema,
            'listRule':   None,
            'viewRule':   None,
            'createRule': None,
            'updateRule': None,
            'deleteRule': None,
        })
        if r is None:
            return False
        if r.status_code in (200, 400):
            body = r.json()
            if r.status_code == 400 and 'already' not in str(body).lower():
                print(f'[PocketBase] Create "{name}" failed: {body}')
                return False
        return True

    def _add_missing_fields(self, name: str, schema: list) -> None:
        """Add any fields that exist in schema but are missing from the live collection."""
        r = self._req('GET', f'/api/collections/{name}')
        if r is None or r.status_code != 200:
            return
        existing_names = {f['name'] for f in r.json().get('schema', [])}
        missing = [f for f in schema if f['name'] not in existing_names]
        if not missing:
            return
        current_schema = r.json().get('schema', [])
        patch = self._req('PATCH', f'/api/collections/{name}', json={
            'schema': current_schema + missing,
        })
        if patch and patch.status_code == 200:
            print(f'[PocketBase] Added {len(missing)} field(s) to "{name}": '
                  f'{[f["name"] for f in missing]}')

    def ensure_collections(self) -> bool:
        """Idempotent — create user_profiles and job_results if absent; add missing fields."""
        ok = True
        for name, schema in [
            ('user_profiles', _SCHEMA_USER_PROFILES),
            ('job_results',   _SCHEMA_JOB_RESULTS),
        ]:
            if self._collection_exists(name):
                self._add_missing_fields(name, schema)
            else:
                success = self._create_collection(name, schema)
                print(f'[PocketBase] Collection "{name}": {"created ✓" if success else "FAILED ✗"}')
                ok = ok and success
        return ok

    # ── User profiles ─────────────────────────────────────────────────────────

    def get_profile(self, user_id: str) -> Optional[dict]:
        """Return the user_profiles record for user_id, or None if not found."""
        r = self._req(
            'GET', '/api/collections/user_profiles/records',
            params={'filter': f'(user_id="{user_id}")', 'perPage': 1},
        )
        if r is None or r.status_code != 200:
            return None
        items = r.json().get('items', [])
        return items[0] if items else None

    def upsert_profile(self, user_id: str, profile: dict) -> bool:
        """Create or update the user_profiles record for user_id."""
        existing = self.get_profile(user_id)
        payload  = {**profile, 'user_id': user_id}
        if existing:
            r = self._req('PATCH', f'/api/collections/user_profiles/records/{existing["id"]}', json=payload)
        else:
            r = self._req('POST', '/api/collections/user_profiles/records', json=payload)
        if r is None or r.status_code not in (200, 201):
            code = r.status_code if r else 'no-response'
            print(f'[PocketBase] upsert_profile {user_id[:8]} failed: {code}')
            return False
        return True

    def list_profiles(self) -> list:
        """Return all user_profiles records."""
        r = self._req(
            'GET', '/api/collections/user_profiles/records',
            params={'perPage': 500},
        )
        if r is None or r.status_code != 200:
            return []
        return r.json().get('items', [])

    # ── Job results ───────────────────────────────────────────────────────────

    def bulk_upsert_job_results(self, user_id: str, jobs: list) -> dict:
        """Upsert all jobs for a user into job_results (efficient: 1 batch GET + N PATCH/POST).

        Existing records matched by link are PATCHed (score/status updated).
        New links are POSTed. Returns {inserted, updated, failed}.
        """
        # One batch load to build link→id map (avoids N GET requests)
        existing: dict = {}
        page = 1
        while True:
            r = self._req(
                'GET', '/api/collections/job_results/records',
                params={
                    'filter': f'(user_id="{user_id}")',
                    'fields': 'id,link',
                    'perPage': 500,
                    'page': page,
                },
            )
            if r is None or r.status_code != 200:
                break
            data = r.json()
            for rec in data.get('items', []):
                if rec.get('link'):
                    existing[rec['link']] = rec['id']
            if page >= data.get('totalPages', 1):
                break
            page += 1

        inserted = updated = failed = 0
        for job in jobs:
            link    = job.get('link', '')
            payload = {**job, 'user_id': user_id}
            pb_id   = existing.get(link)
            if pb_id:
                r = self._req('PATCH', f'/api/collections/job_results/records/{pb_id}', json=payload)
                if r and r.status_code == 200:
                    updated += 1
                else:
                    failed += 1
            else:
                r = self._req('POST', '/api/collections/job_results/records', json=payload)
                if r and r.status_code == 200:
                    inserted += 1
                    if link:
                        existing[link] = r.json().get('id', '')
                else:
                    failed += 1
        return {'inserted': inserted, 'updated': updated, 'failed': failed}

    def update_job_status_by_link(self, link: str, status: str) -> bool:
        """Update status for all job_results records matching a link (called by verifier)."""
        r = self._req(
            'GET', '/api/collections/job_results/records',
            params={'filter': f'(link="{link}")', 'fields': 'id', 'perPage': 100},
        )
        if r is None or r.status_code != 200:
            return False
        items = r.json().get('items', [])
        if not items:
            return True  # not in PocketBase yet — nothing to do
        ok = True
        for rec in items:
            patch = self._req(
                'PATCH', f'/api/collections/job_results/records/{rec["id"]}',
                json={'status': status},
            )
            if patch is None or patch.status_code != 200:
                ok = False
        return ok

    # kept for backward compatibility (used by old webhook push of top-N)
    def push_job_results(self, user_id: str, jobs: list) -> int:
        result = self.bulk_upsert_job_results(user_id, jobs)
        return result['inserted'] + result['updated']

    # ── GDPR erasure ─────────────────────────────────────────────────────────

    def delete_user_data(self, user_id: str) -> bool:
        """Delete all PocketBase records for a user (GDPR Art. 17 right to erasure).

        Deletes: all job_results rows for the user, plus the user_profiles record.
        Non-fatal — returns False on any error so the caller can still confirm
        the SQLite deletion succeeded.
        """
        errors = 0

        # Delete job_results records (paginated in case of many records)
        page = 1
        while True:
            r = self._req(
                'GET', '/api/collections/job_results/records',
                params={
                    'filter': f'(user_id="{user_id}")',
                    'fields': 'id',
                    'perPage': 500,
                    'page': page,
                },
            )
            if r is None or r.status_code != 200:
                break
            data = r.json()
            items = data.get('items', [])
            for rec in items:
                dr = self._req('DELETE', f'/api/collections/job_results/records/{rec["id"]}')
                if dr is None or dr.status_code not in (200, 204):
                    errors += 1
            if page >= data.get('totalPages', 1) or not items:
                break
            page += 1

        # Delete user_profiles record
        profile = self.get_profile(user_id)
        if profile:
            dr = self._req('DELETE', f'/api/collections/user_profiles/records/{profile["id"]}')
            if dr is None or dr.status_code not in (200, 204):
                errors += 1

        if errors:
            print(f'[PocketBase] delete_user_data {user_id[:8]}: {errors} deletion(s) failed')
        return errors == 0

    # ── Connectivity check ────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return True if PocketBase is reachable and credentials are set."""
        if not self._email or not self._pw:
            return False
        try:
            r = requests.get(f'{self._url}/api/health', timeout=3)
            return r.status_code == 200
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

_client: Optional[PocketBaseClient] = None


def get_client() -> PocketBaseClient:
    global _client
    if _client is None:
        _client = PocketBaseClient()
    return _client
