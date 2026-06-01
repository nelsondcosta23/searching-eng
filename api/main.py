"""Job Search Results API — v2.1"""
from fastapi import FastAPI, Query, HTTPException, Depends, Security, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import contextlib
import os
import json
import hmac
import socket
import ipaddress
import time
from collections import defaultdict
from urllib.parse import urlparse
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from pydantic import BaseModel, field_validator

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from automation.monitoring import init_all as _init_monitoring, generate_metrics_response, log as _log


def _day_range(date_str: str) -> Tuple[str, str]:
    """Returns (start_of_day, start_of_next_day) as 'YYYY-MM-DD HH:MM:SS' strings.

    Used to convert `DATE(col) = ?` into a sargable `col >= ? AND col < ?`
    range so the index on data_scraped is actually used by the planner.
    """
    d = datetime.strptime(date_str, '%Y-%m-%d')
    return (d.strftime('%Y-%m-%d %H:%M:%S'), (d + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'))

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
API_KEY  = os.environ.get('API_KEY', '').strip()

# Optional per-user authorization scope.
# When set: /users/sync, GET /jobs, GET /stats, GET /jobs/{id} all reject any
# request targeting a different user_id. Without it, any API_KEY holder can
# read/write any user's data (single-tenant default).
OWNER_USER_ID = os.environ.get('OWNER_USER_ID', '').strip()

# Allow http (non-https) callback URLs only when explicitly enabled.
ALLOW_HTTP_CALLBACKS = os.environ.get('ALLOW_HTTP_CALLBACKS', '0') == '1'
CALLBACK_URL_MAX_LEN = 2048

_INSECURE_DEFAULTS = {'changeme-please', 'change-me', 'secret', 'api_key', ''}

# Serve /docs and /redoc only when explicitly enabled (off by default in production).
ENABLE_API_DOCS = os.environ.get('ENABLE_API_DOCS', '0') == '1'

# ─────────────────────────────────────────────────────────────────────────────
# Brute-force protection — tracks auth failures per client IP
# Redis-backed when REDIS_URL is set; falls back to in-process memory.
# Note: in-memory state is not shared across uvicorn workers (--workers > 1).
# ─────────────────────────────────────────────────────────────────────────────
_RATE_WINDOW    = int(os.environ.get('RATE_LIMIT_WINDOW',    '60'))   # seconds
_RATE_MAX_FAILS = int(os.environ.get('RATE_LIMIT_MAX_FAILS', '10'))   # failures per window

_redis_rl: object = None           # redis.Redis instance if available
_mem_fails: dict  = defaultdict(list)  # ip → [monotonic timestamp, ...]


def _init_rate_limiter() -> None:
    global _redis_rl
    redis_url = os.environ.get('REDIS_URL', '').strip()
    if not redis_url:
        return
    try:
        import redis as _r
        _redis_rl = _r.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        _redis_rl.ping()
    except Exception:
        _redis_rl = None


def _get_client_ip(request: Request) -> str:
    xff = request.headers.get('x-forwarded-for', '')
    if xff:
        return xff.split(',')[0].strip()
    return getattr(request.client, 'host', 'unknown')


def _record_and_check(ip: str) -> bool:
    """Records one auth failure and returns True if the IP is now blocked."""
    if _redis_rl is not None:
        try:
            key = f"auth_fail:{ip}"
            pipe = _redis_rl.pipeline()
            pipe.incr(key)
            pipe.expire(key, _RATE_WINDOW)
            count, _ = pipe.execute()
            return int(count) > _RATE_MAX_FAILS
        except Exception:
            pass
    now = time.monotonic()
    _mem_fails[ip] = [t for t in _mem_fails[ip] if now - t < _RATE_WINDOW]
    _mem_fails[ip].append(now)
    return len(_mem_fails[ip]) > _RATE_MAX_FAILS


def _is_blocked(ip: str) -> bool:
    """Returns True if the IP has already exceeded the failure threshold."""
    if _redis_rl is not None:
        try:
            count = _redis_rl.get(f"auth_fail:{ip}")
            return int(count or 0) > _RATE_MAX_FAILS
        except Exception:
            pass
    now = time.monotonic()
    recent = [t for t in _mem_fails.get(ip, []) if now - t < _RATE_WINDOW]
    return len(recent) > _RATE_MAX_FAILS


def _startup_checks():
    if API_KEY in _INSECURE_DEFAULTS:
        print(
            "[API] FATAL: API_KEY is not set or uses an insecure default value. "
            "Set a strong random API_KEY in .env and restart.",
            flush=True,
        )
        sys.exit(1)
    _init_rate_limiter()
    _init_monitoring()
    _log.info('api_startup', version='2.1.0', db_path=DB_PATH)


app = FastAPI(
    title="Job Search Results API",
    on_startup=[_startup_checks],
    description=(
        "Access scraped job results per user_id.\n\n"
        "Populated automatically by the scraper engine (Expresso, Sapo, Net-Empregos, Indeed, LinkedIn).\n\n"
        "**Auth**: Send `Authorization: Bearer <API_KEY>` header."
    ),
    version="2.1.0",
    docs_url="/docs"        if ENABLE_API_DOCS else None,
    redoc_url="/redoc"      if ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_API_DOCS else None,
)

# CORS — allow external apps (React dashboards, N8N, Zapier, etc.)
# allow_credentials must be False with allow_origins=["*"] — the spec rejects
# the combination and browsers strip credentials. The API uses Bearer auth in
# the Authorization header which works fine in this mode.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Body-size guard — reject oversized requests before they hit route handlers.
# Prevents DoS via huge payloads (e.g. 10 MB job_titles arrays).
# ─────────────────────────────────────────────────────────────────────────────
_MAX_BODY_BYTES = int(os.environ.get('MAX_REQUEST_BODY_BYTES', str(1 * 1024 * 1024)))  # 1 MB default


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl:
        try:
            if int(cl) > _MAX_BODY_BYTES:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"Request body too large (max {_MAX_BODY_BYTES // 1024} KB)."},
                )
        except ValueError:
            pass
    return await call_next(request)


# ─────────────────────────────────────────────────────────────────────────────
# Validators (security)
# ─────────────────────────────────────────────────────────────────────────────
def _validate_callback_url(url: Optional[str]) -> Optional[str]:
    """Validates that callback_url is safe to POST to (anti-SSRF).

    Rejects:
      - Non-http(s) schemes
      - http:// (unless ALLOW_HTTP_CALLBACKS=1)
      - URLs longer than CALLBACK_URL_MAX_LEN
      - Hostnames that resolve to private/loopback/link-local/multicast IPs
      - Cloud metadata IP (169.254.169.254 — AWS/GCP/Azure)
    Raises HTTPException(400) on invalid input.
    """
    if not url:
        return url
    if len(url) > CALLBACK_URL_MAX_LEN:
        raise HTTPException(status_code=400, detail=f"callback_url too long (max {CALLBACK_URL_MAX_LEN} chars).")

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise HTTPException(status_code=400, detail="callback_url must use http or https scheme.")
    if parsed.scheme == 'http' and not ALLOW_HTTP_CALLBACKS:
        raise HTTPException(status_code=400, detail="callback_url must use https. Set ALLOW_HTTP_CALLBACKS=1 to permit http.")

    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="callback_url has no hostname.")

    # Resolve hostname to all IPs and reject any that target internal infra.
    try:
        addrs = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail=f"callback_url hostname could not be resolved: {host}")

    for af, _socktype, _proto, _canon, sockaddr in addrs:
        try:
            ip_obj = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local      # also covers 169.254.0.0/16 (cloud metadata)
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            raise HTTPException(
                status_code=400,
                detail=f"callback_url resolves to a non-public IP ({ip_obj}); rejected to prevent SSRF.",
            )

    return url


# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────
security = HTTPBearer(auto_error=False)


def _enforce_owner(user_id: str):
    """Raises 403 when OWNER_USER_ID is set and the requested user_id doesn't match.

    This prevents any API_KEY holder from reading another user's data in
    single-tenant deployments. No-op when OWNER_USER_ID is not configured.
    """
    if OWNER_USER_ID and user_id != OWNER_USER_ID:
        raise HTTPException(
            status_code=403,
            detail=f"This API instance only serves user_id={OWNER_USER_ID}.",
        )


def _safe_compare(provided: str, expected: str) -> bool:
    """Constant-time API key comparison to defeat timing attacks."""
    if not provided or not expected:
        return False
    try:
        return hmac.compare_digest(provided.encode('utf-8'), expected.encode('utf-8'))
    except Exception:
        return False


def _mask_key(key: str) -> str:
    """Returns the last 4 chars for logging — never logs the full key."""
    if not key or len(key) < 8:
        return '***'
    return f"...{key[-4:]}"


def verify_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
):
    ip = _get_client_ip(request)

    # Reject immediately if IP is already over the failure threshold.
    if _is_blocked(ip):
        raise HTTPException(
            status_code=429,
            detail="Too many authentication failures. Try again later.",
            headers={"Retry-After": str(_RATE_WINDOW)},
        )

    if credentials and _safe_compare(credentials.credentials, API_KEY):
        return credentials.credentials

    # Record the failure (and re-check threshold in one atomic step).
    blocked = _record_and_check(ip)
    _log.warning('auth_failure', ip=ip, blocked=blocked)
    if blocked:
        raise HTTPException(
            status_code=429,
            detail="Too many authentication failures. Try again later.",
            headers={"Retry-After": str(_RATE_WINDOW)},
        )
    raise HTTPException(
        status_code=401,
        detail="Invalid or missing API key. Send via 'Authorization: Bearer <key>' header.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Models
# ─────────────────────────────────────────────────────────────────────────────
class Job(BaseModel):
    id: int
    user_id: Optional[str] = None
    titulo: Optional[str] = None
    empresa: Optional[str] = None
    localizacao: Optional[str] = None
    plataforma: Optional[str] = None
    categoria: Optional[str] = None
    link: Optional[str] = None
    data_publicacao: Optional[str] = None
    data_scraped: Optional[str] = None
    status: Optional[str] = None
    # Recruiter (LinkedIn-only)
    recrutador_nome: Optional[str] = None
    recrutador_link: Optional[str] = None
    # Structured metadata (all platforms)
    observacoes: Optional[str] = None
    salario: Optional[str] = None
    tipo_contrato: Optional[str] = None
    nivel_experiencia: Optional[str] = None
    relevance_score: Optional[int] = None         # 0–100, computed by job_scorer.py
    # Full description (opt-in via ?include_description=true)
    descricao_completa: Optional[str] = None


class JobSummary(BaseModel):
    """Lightweight version of Job — no description field."""
    id: int
    user_id: Optional[str] = None
    titulo: Optional[str] = None
    empresa: Optional[str] = None
    localizacao: Optional[str] = None
    plataforma: Optional[str] = None
    categoria: Optional[str] = None
    link: Optional[str] = None
    data_publicacao: Optional[str] = None
    data_scraped: Optional[str] = None
    status: Optional[str] = None
    recrutador_nome: Optional[str] = None
    recrutador_link: Optional[str] = None
    observacoes: Optional[str] = None
    salario: Optional[str] = None
    tipo_contrato: Optional[str] = None
    nivel_experiencia: Optional[str] = None
    relevance_score: Optional[int] = None


class JobsResponse(BaseModel):
    user_id: str
    total: int
    generated_at: str
    filters: dict
    jobs: List[Job]


class StatsResponse(BaseModel):
    total_jobs: int
    active_jobs: int
    expired_jobs: int
    jobs_by_platform: dict
    jobs_today: int
    generated_at: str

def _valid_job_profiles() -> list[str]:
    """Returns the list of valid job_profile values from VALID_JOB_PROFILES env var."""
    raw = os.environ.get('VALID_JOB_PROFILES', 'generalist')
    return [p.strip().lower() for p in raw.split(',') if p.strip()]


class UserProfile(BaseModel):
    user_id: str
    is_active: bool = True
    job_titles: List[str]
    locations: Optional[List[str]] = []
    is_remote: bool = False
    min_salary: int = 0
    experience_levels: Optional[List[str]] = []
    keywords: Optional[List[str]] = []
    negative_keywords: Optional[List[str]] = []
    negative_companies: Optional[List[str]] = []
    """Company names to completely exclude (partial match, case-insensitive).
    Example: ['Canonical', 'Dellent', 'Randstad'] — no jobs from these companies will be saved."""
    contract_type: Optional[List[str]] = []
    """Preferred contract types. Example: ['full-time']. Jobs with other types are deprioritised."""
    required_languages: Optional[List[str]] = []
    """Languages the user speaks. Example: ['portuguese', 'english'].
    Used to filter out jobs that require languages not in this list."""
    search_description: Optional[str] = None
    """Rich free-text description of the ideal role and professional background.
    Used directly by the TF-IDF scorer to improve relevance matching.
    Example: 'CTO with 10 years in SaaS B2B, leading product and engineering teams...'"""
    job_profile: Optional[str] = None
    """Defines which scrapers run for this user. Must be one of the values
    returned by GET /api/v1/profiles. Unknown values fall back to 'generalist'.
    If omitted, the existing value in the DB is preserved (no overwrite)."""
    callback_url: Optional[str] = None

    # ── B-4: field-level size guards ─────────────────────────────────────────
    # Prevent individual items or lists that are unreasonably large from
    # reaching SQLite or the TF-IDF scorer.  Truncation (not rejection) keeps
    # client integrations working with minor overflows.

    @field_validator(
        'job_titles', 'keywords', 'negative_keywords', 'negative_companies',
        'locations', 'experience_levels', 'contract_type', 'required_languages',
        mode='before',
    )
    @classmethod
    def _cap_list(cls, v):
        """Truncate individual items to 300 chars; cap lists at 200 items."""
        if not v:
            return v
        return [str(item)[:300] for item in v][:200]

    @field_validator('search_description', mode='before')
    @classmethod
    def _cap_description(cls, v):
        """Cap the free-text description at 5 000 chars (well above TF-IDF needs)."""
        return str(v)[:5000] if v else v

    @field_validator('user_id', mode='before')
    @classmethod
    def _validate_user_id(cls, v):
        if len(str(v)) > 128:
            raise ValueError('user_id exceeds maximum length of 128 characters.')
        return v


# ─────────────────────────────────────────────────────────────────────────────
# DB Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn

def _db_exists():
    return os.path.exists(DB_PATH)


def _pb_available() -> bool:
    try:
        from automation.pb_client import get_client
        return get_client().is_available()
    except Exception:
        return False


def get_jobs_from_pb(
    user_id: str,
    status: Optional[str],
    platform: Optional[str],
    limit: int,
    run_date: Optional[str],
    salario_only: bool,
    nivel: Optional[str],
    sort_by_relevance: bool,
    min_score: Optional[int],
) -> Optional[List[dict]]:
    """Read jobs from PocketBase job_results collection. Returns None on any failure."""
    try:
        from automation.pb_client import get_client
        pb = get_client()

        parts = [f'(user_id="{user_id}")']
        if status:
            parts.append(f'status="{status}"')
        if platform:
            parts.append(f'plataforma~"{platform}"')
        if salario_only:
            parts.append('salario!=""')
        if nivel:
            parts.append(f'nivel_experiencia~"{nivel}"')
        if min_score is not None:
            parts.append(f'relevance_score>={min_score}')
        if run_date:
            day_start, day_end = _day_range(run_date)
            parts.append(f'data_scraped>="{day_start}"')
            parts.append(f'data_scraped<"{day_end}"')

        pb_filter = '&&'.join(f'({p})' for p in parts)
        sort = '-relevance_score,data_scraped' if sort_by_relevance else '-data_scraped,-relevance_score'

        r = pb._req(
            'GET', '/api/collections/job_results/records',
            params={'filter': pb_filter, 'sort': sort, 'perPage': limit},
        )
        if r is None or r.status_code != 200:
            return None

        items = r.json().get('items', [])
        # Normalise to same shape as SQLite result (add missing integer id as None)
        result = []
        for item in items:
            result.append({
                'id':                  None,
                'user_id':             item.get('user_id'),
                'titulo':              item.get('titulo'),
                'empresa':             item.get('empresa'),
                'localizacao':         item.get('localizacao'),
                'plataforma':          item.get('plataforma'),
                'categoria':           item.get('categoria'),
                'link':                item.get('link'),
                'data_publicacao':     item.get('data_publicacao'),
                'data_scraped':        item.get('data_scraped'),
                'status':              item.get('status'),
                'descricao_completa':  item.get('descricao_completa'),
                'recrutador_nome':     item.get('recrutador_nome'),
                'recrutador_link':     item.get('recrutador_link'),
                'observacoes':         item.get('observacoes'),
                'salario':             item.get('salario'),
                'tipo_contrato':       item.get('tipo_contrato'),
                'nivel_experiencia':   item.get('nivel_experiencia'),
                'relevance_score':     item.get('relevance_score'),
            })
        return result
    except Exception:
        return None

def get_jobs_from_db(
    user_id: str,
    status: Optional[str],
    platform: Optional[str],
    limit: int,
    include_description: bool,
    run_date: Optional[str],
    salario_only: bool,
    nivel: Optional[str],
    sort_by_relevance: bool = False,
    min_score: Optional[int] = None,
) -> List[dict]:
    if not _db_exists():
        return []

    desc_col = ", jg.descricao AS descricao_completa" if include_description else ""

    query = f"""
        SELECT jg.id, ju.user_id,
               jg.titulo, jg.empresa, jg.localizacao, jg.plataforma, jg.categoria,
               jg.link, jg.data_publicacao,
               COALESCE(jg.data_scraped, '') AS data_scraped,
               jg.status,
               jg.recrutador_nome, jg.recrutador_link, jg.observacoes,
               COALESCE(jg.salario,           '') AS salario,
               COALESCE(jg.tipo_contrato,     '') AS tipo_contrato,
               COALESCE(jg.nivel_experiencia, '') AS nivel_experiencia,
               COALESCE(ju.relevance_score,    0) AS relevance_score
               {desc_col}
        FROM jobs_global jg
        JOIN jobs_users ju ON jg.id = ju.job_id
        WHERE ju.user_id = ?
    """
    params: list = [user_id]

    if run_date:
        day_start, day_end = _day_range(run_date)
        query += " AND jg.data_scraped >= ? AND jg.data_scraped < ?"
        params.extend([day_start, day_end])
    if status:
        query += " AND jg.status = ?"
        params.append(status)
    if platform:
        query += " AND LOWER(jg.plataforma) LIKE ?"
        params.append(f"%{platform.lower()}%")
    if salario_only:
        query += " AND jg.salario IS NOT NULL AND jg.salario != ''"
    if nivel:
        query += " AND LOWER(jg.nivel_experiencia) LIKE ?"
        params.append(f"%{nivel.lower()}%")
    if min_score is not None:
        query += " AND COALESCE(ju.relevance_score, 0) >= ?"
        params.append(min_score)

    if sort_by_relevance:
        query += " ORDER BY COALESCE(ju.relevance_score, 0) DESC, jg.data_scraped DESC LIMIT ?"
    else:
        query += " ORDER BY jg.data_scraped DESC LIMIT ?"
    params.append(limit)

    with contextlib.closing(_get_conn()) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/metrics", tags=["Health"], include_in_schema=False)
def prometheus_metrics():
    """Prometheus metrics endpoint — scraped by Prometheus server."""
    content, media_type = generate_metrics_response()
    return Response(content=content, media_type=media_type)


@app.get("/api/v1/status", tags=["Health"])
def health_check(api_key: str = Depends(verify_api_key)):
    """Returns API health, DB status and total job count."""
    db_ok = _db_exists()
    job_count = 0
    platforms  = {}
    if db_ok:
        try:
            with contextlib.closing(_get_conn()) as conn:
                job_count = conn.execute("SELECT COUNT(*) FROM jobs_global").fetchone()[0]
                rows = conn.execute("SELECT plataforma, COUNT(*) FROM jobs_global GROUP BY plataforma").fetchall()
                platforms = {r[0]: r[1] for r in rows}
        except Exception:
            pass
    return {
        "status": "ok",
        "version": "2.1.0",
        "database": "connected" if db_ok else "not found",
        "total_jobs_in_db": job_count,
        "jobs_per_platform": platforms,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/v1/jobs", response_model=JobsResponse, tags=["Jobs"])
def get_jobs(
    user_id: str = Query(..., description="The Supabase user ID to filter results for."),
    run_date: Optional[str] = Query(
        None,
        description="Filter by scrape date (YYYY-MM-DD). Omit for today. Use `all` for full history.",
    ),
    status: Optional[str] = Query(None, description="'Ativa' | 'Expirada'"),
    platform: Optional[str] = Query(None, description="Partial match: 'linkedin', 'sapo', 'indeed'."),
    nivel: Optional[str] = Query(None, description="Filter by experience level (partial match), e.g. 'senior'."),
    salario_only: bool = Query(False, description="Only return jobs that have a salary value."),
    sort_by_relevance: bool = Query(False, description="Sort by TF-IDF relevance score (descending). Default: false."),
    min_score: Optional[int] = Query(None, ge=0, le=100, description="Minimum relevance_score (0–100). Excludes unscored jobs when set."),
    limit: int = Query(500, ge=1, le=1000, description="Max results (1–1000). Default: 500."),
    include_description: bool = Query(False, description="Include full job description text."),
    api_key: str = Depends(verify_api_key),
):
    """
    Retrieve scraped jobs for a given `user_id`.

    **v2.1 fields**: `salario`, `tipo_contrato`, `nivel_experiencia`, `relevance_score` (0–100)
    **v2.1 filters**: `platform` (partial), `nivel`, `salario_only`, `sort_by_relevance`
    """
    _enforce_owner(user_id)

    if run_date is None:
        effective_date: Optional[str] = datetime.now().strftime('%Y-%m-%d')
    elif run_date.lower() == 'all':
        effective_date = None
    else:
        try:
            datetime.strptime(run_date, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=422, detail="run_date must be in YYYY-MM-DD format or 'all'.")
        effective_date = run_date

    # Try PocketBase first; fall back to SQLite if unavailable or returns None
    jobs = None
    if not include_description and _pb_available():
        jobs = get_jobs_from_pb(
            user_id=user_id,
            status=status,
            platform=platform,
            limit=limit,
            run_date=effective_date,
            salario_only=salario_only,
            nivel=nivel,
            sort_by_relevance=sort_by_relevance,
            min_score=min_score,
        )

    if jobs is None:
        jobs = get_jobs_from_db(
            user_id=user_id,
            status=status,
            platform=platform,
            limit=limit,
            include_description=include_description,
            run_date=effective_date,
            salario_only=salario_only,
            nivel=nivel,
            sort_by_relevance=sort_by_relevance,
            min_score=min_score,
        )

    return JobsResponse(
        user_id=user_id,
        total=len(jobs),
        generated_at=datetime.now().isoformat(),
        filters={
            "run_date": effective_date or "all",
            "status": status,
            "platform": platform,
            "nivel": nivel,
            "salario_only": salario_only,
            "sort_by_relevance": sort_by_relevance,
            "limit": limit,
            "include_description": include_description,
        },
        jobs=jobs,
    )


@app.get("/api/v1/jobs/{job_id}", response_model=Job, tags=["Jobs"])
def get_single_job(
    job_id: int,
    api_key: str = Depends(verify_api_key),
):
    """Retrieve the full detail of a single job by its database ID (always includes description)."""
    if not _db_exists():
        raise HTTPException(status_code=503, detail="Database not found.")

    # When OWNER_USER_ID is set, include it in the WHERE clause so we never
    # fetch a row that belongs to a different user — prevents IDOR enumeration
    # even before reaching the _enforce_owner() application-level check.
    params: list = [job_id]
    owner_filter = ""
    if OWNER_USER_ID:
        owner_filter = " AND ju.user_id = ?"
        params.append(OWNER_USER_ID)

    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute(
            f"""
            SELECT jg.id, ju.user_id,
                   jg.titulo, jg.empresa, jg.localizacao, jg.plataforma, jg.categoria,
                   jg.link, jg.data_publicacao, jg.data_scraped, jg.status,
                   jg.descricao AS descricao_completa,
                   jg.recrutador_nome, jg.recrutador_link, jg.observacoes,
                   jg.salario, jg.tipo_contrato, jg.nivel_experiencia,
                   COALESCE(ju.relevance_score, 0) AS relevance_score
            FROM jobs_global jg
            JOIN jobs_users ju ON jg.id = ju.job_id
            WHERE jg.id = ?{owner_filter}
            LIMIT 1
            """,
            params,
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Job with id={job_id} not found.")

    return dict(row)


@app.get("/api/v1/stats", response_model=StatsResponse, tags=["Stats"])
def get_stats(
    user_id: str = Query(..., description="The user ID to compute stats for."),
    api_key: str = Depends(verify_api_key),
):
    """Returns aggregated statistics for a given user_id."""
    _enforce_owner(user_id)
    if not _db_exists():
        raise HTTPException(status_code=503, detail="Database not found.")

    today_start, today_end = _day_range(datetime.now().strftime('%Y-%m-%d'))
    with contextlib.closing(_get_conn()) as conn:
        total     = conn.execute("SELECT COUNT(*) FROM jobs_users WHERE user_id=?", (user_id,)).fetchone()[0]
        active    = conn.execute(
            "SELECT COUNT(*) FROM jobs_global jg JOIN jobs_users ju ON jg.id=ju.job_id WHERE ju.user_id=? AND jg.status='Ativa'", (user_id,)
        ).fetchone()[0]
        expired   = conn.execute(
            "SELECT COUNT(*) FROM jobs_global jg JOIN jobs_users ju ON jg.id=ju.job_id WHERE ju.user_id=? AND jg.status='Expirada'", (user_id,)
        ).fetchone()[0]
        today_cnt = conn.execute(
            "SELECT COUNT(*) FROM jobs_global jg JOIN jobs_users ju ON jg.id=ju.job_id WHERE ju.user_id=? AND jg.data_scraped >= ? AND jg.data_scraped < ?",
            (user_id, today_start, today_end),
        ).fetchone()[0]
        by_plat   = conn.execute(
            "SELECT jg.plataforma, COUNT(*) FROM jobs_global jg JOIN jobs_users ju ON jg.id=ju.job_id WHERE ju.user_id=? GROUP BY jg.plataforma",
            (user_id,),
        ).fetchall()

    return StatsResponse(
        total_jobs=total,
        active_jobs=active,
        expired_jobs=expired,
        jobs_by_platform={r[0]: r[1] for r in by_plat},
        jobs_today=today_cnt,
        generated_at=datetime.now().isoformat(),
    )


@app.get("/api/v1/profiles", tags=["Users"])
def list_job_profiles(api_key: str = Depends(verify_api_key)):
    """Returns the valid `job_profile` values and which scrapers each activates.

    Call this endpoint to know exactly what to send in the `job_profile` field
    of POST /api/v1/users/sync. Unknown values fall back to `generalist`.
    """
    profiles = _valid_job_profiles()
    result = {}
    for p in profiles:
        key = f'SCRAPERS_{p.upper()}'
        raw = os.environ.get(key, os.environ.get('SCRAPERS_GENERALIST', ''))
        scrapers = [s.strip() for s in raw.split(',') if s.strip()]
        # Strip .py suffix for a cleaner response
        result[p] = [s.replace('_scraper.py', '').replace('_', ' ').title() for s in scrapers]
    return {
        "valid_profiles": profiles,
        "fallback": "generalist",
        "note": "Send one of the valid_profiles values in the job_profile field. Unknown values use 'generalist'.",
        "profiles": result,
    }


@app.post("/api/v1/users/sync", tags=["Users"])
def sync_user_profile(profile: UserProfile, api_key: str = Depends(verify_api_key)):
    """Upsert user scraping profile preferences.

    If `OWNER_USER_ID` env is set, only that exact user_id may be modified.
    `callback_url` is validated for SSRF (anti-pivot to internal services).
    """
    if not _db_exists():
        raise HTTPException(status_code=503, detail="Database not found.")

    # Per-user authorization (opt-in via OWNER_USER_ID env var).
    if OWNER_USER_ID and profile.user_id != OWNER_USER_ID:
        raise HTTPException(
            status_code=403,
            detail=f"This API key may only modify user_id={OWNER_USER_ID}.",
        )

    # SSRF: ensure callback_url cannot pivot the dispatcher into internal infra.
    profile.callback_url = _validate_callback_url(profile.callback_url)

    # Normalise job_profile:
    # - None (field omitted)     → preserve existing DB value, or 'generalist' for new users
    # - "" (empty string)        → 'generalist'
    # - valid value (e.g. "tech") → use it
    # - unknown value            → 'generalist' (with log warning)
    valid_profiles = _valid_job_profiles()

    if profile.job_profile is None:
        # Field was not sent — preserve whatever is already in the DB
        try:
            with contextlib.closing(_get_conn()) as _c:
                _row = _c.execute(
                    'SELECT job_profile FROM users_perfil WHERE user_id = ?', (profile.user_id,)
                ).fetchone()
                job_profile_norm = (_row['job_profile'] or 'generalist') if _row else 'generalist'
        except Exception:
            job_profile_norm = 'generalist'
    else:
        raw_profile = profile.job_profile.lower().strip()
        if raw_profile in valid_profiles:
            job_profile_norm = raw_profile
        else:
            job_profile_norm = 'generalist'
            if raw_profile:
                print(f"[sync] Unknown job_profile '{raw_profile}' → normalised to 'generalist'")

    def _normalise_list(items: list) -> str:
        """Flatten and deduplicate a list that may contain comma-separated strings.

        Guards against clients that send accumulated strings as list items, e.g.
        ["Randstad, Adecco,Canonical"] instead of ["Randstad", "Adecco", "Canonical"].
        Always replaces — never appends to existing DB value.
        """
        flat = []
        seen = set()
        for item in (items or []):
            for part in str(item).split(','):
                clean = part.strip()
                if clean and clean.lower() not in seen:
                    seen.add(clean.lower())
                    flat.append(clean)
        return ", ".join(flat)

    # Convert lists to comma-separated strings for SQLite (always full replacement)
    job_titles_str          = _normalise_list(profile.job_titles)
    locations_str           = _normalise_list(profile.locations)
    exp_levels_str          = _normalise_list(profile.experience_levels)
    keywords_str            = _normalise_list(profile.keywords)
    negative_keywords_str   = _normalise_list(profile.negative_keywords)
    negative_companies_str  = _normalise_list(profile.negative_companies)
    contract_type_str       = _normalise_list(profile.contract_type)
    required_languages_str  = _normalise_list(profile.required_languages)

    upsert_sql = '''
        INSERT INTO users_perfil (
            user_id, is_active, job_titles, locations, is_remote, min_salary,
            experience_levels, keywords, negative_keywords, negative_companies,
            contract_type, required_languages, search_description,
            job_profile, callback_url,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            is_active=excluded.is_active,
            job_titles=excluded.job_titles,
            locations=excluded.locations,
            is_remote=excluded.is_remote,
            min_salary=excluded.min_salary,
            experience_levels=excluded.experience_levels,
            keywords=excluded.keywords,
            negative_keywords=excluded.negative_keywords,
            negative_companies=excluded.negative_companies,
            contract_type=excluded.contract_type,
            required_languages=excluded.required_languages,
            search_description=excluded.search_description,
            job_profile=excluded.job_profile,
            callback_url=excluded.callback_url,
            updated_at=CURRENT_TIMESTAMP
    '''
    params = (
        profile.user_id,
        1 if profile.is_active else 0,
        job_titles_str,
        locations_str,
        1 if profile.is_remote else 0,
        profile.min_salary,
        exp_levels_str,
        keywords_str,
        negative_keywords_str,
        negative_companies_str,
        contract_type_str,
        required_languages_str,
        profile.search_description,
        job_profile_norm,
        profile.callback_url,
    )

    try:
        # Routed through db_helper so the UPSERT retries on `database is locked`
        # under contention (scrapers/scorer running concurrently).
        from automation.db_helper import execute_with_retry as _db_exec
        _db_exec(upsert_sql, params)
    except Exception as e:
        from automation.monitoring import capture_exception
        capture_exception(e, {'endpoint': '/users/sync', 'user_id': profile.user_id})
        raise HTTPException(status_code=500, detail=str(e))

    # Mirror profile to PocketBase (non-fatal — SQLite is authoritative)
    try:
        from automation.pb_client import get_client as _pb_client
        pb = _pb_client()
        if pb.is_available():
            pb.ensure_collections()
            pb.upsert_profile(profile.user_id, {
                'job_titles':          job_titles_str,
                'keywords':            keywords_str,
                'negative_keywords':   negative_keywords_str,
                'negative_companies':  negative_companies_str,
                'locations':           locations_str,
                'is_remote':           profile.is_remote,
                'min_salary':          profile.min_salary or 0,
                'experience_levels':   exp_levels_str,
                'search_description':  profile.search_description or '',
                'contract_type':       contract_type_str,
                'required_languages':  required_languages_str,
                'job_profile':         job_profile_norm,
                'is_active':           profile.is_active,
                'callback_url':        profile.callback_url or '',
            })
    except Exception as _pb_err:
        print(f"[sync] PocketBase mirror failed (non-fatal): {_pb_err}")

    return {"status": "success", "message": f"Profile for {profile.user_id} updated successfully."}


@app.delete("/api/v1/users/{user_id}", tags=["Users"])
def delete_user(
    user_id: str,
    api_key: str = Depends(verify_api_key),
):
    """Permanently delete a user profile and all associated job data.

    Implements GDPR Article 17 (right to erasure). Removes:
    - `users_perfil` row (preferences, callback_url, etc.)
    - All `jobs_users` rows (user↔job associations + relevance scores)
    - PocketBase `user_profiles` + `job_results` records (non-fatal if unavailable)

    Does NOT delete `jobs_global` rows — those are shared across users.
    """
    _enforce_owner(user_id)
    if not _db_exists():
        raise HTTPException(status_code=503, detail="Database not found.")

    try:
        from automation.db_helper import execute_with_retry as _db_exec
        _db_exec("DELETE FROM jobs_users   WHERE user_id = ?", (user_id,))
        _db_exec("DELETE FROM users_perfil WHERE user_id = ?", (user_id,))
    except Exception as e:
        from automation.monitoring import capture_exception
        capture_exception(e, {'endpoint': f'DELETE /users/{user_id}'})
        raise HTTPException(status_code=500, detail=str(e))

    # Mirror deletion to PocketBase (non-fatal — SQLite deletion already succeeded)
    try:
        from automation.pb_client import get_client as _pb_client
        pb = _pb_client()
        if pb.is_available():
            pb.delete_user_data(user_id)
    except Exception as pb_err:
        print(f"[delete_user] PocketBase erasure failed (non-fatal): {pb_err}")

    _log.info('user_deleted', user_id=user_id, gdpr_article=17)
    return {
        "status": "deleted",
        "user_id": user_id,
        "note": "Profile and job associations removed. Shared job data (jobs_global) is preserved.",
    }
