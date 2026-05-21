"""Job Search Results API — v2.0"""
from fastapi import FastAPI, Query, HTTPException, Depends, Security, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import contextlib
import os
import json
import hmac
import socket
import ipaddress
from urllib.parse import urlparse
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from pydantic import BaseModel


def _day_range(date_str: str) -> Tuple[str, str]:
    """Returns (start_of_day, start_of_next_day) as 'YYYY-MM-DD HH:MM:SS' strings.

    Used to convert `DATE(col) = ?` into a sargable `col >= ? AND col < ?`
    range so the index on data_scraped is actually used by the planner.
    """
    d = datetime.strptime(date_str, '%Y-%m-%d')
    return (d.strftime('%Y-%m-%d %H:%M:%S'), (d + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'))

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
API_KEY  = os.environ.get('API_KEY', 'changeme-please')

# Optional per-user authorization scope. If set, /users/sync only allows
# modifying this exact user_id. Without it, any holder of API_KEY can write
# any user's profile (legacy behavior).
OWNER_USER_ID = os.environ.get('OWNER_USER_ID', '').strip()

# Allow http (non-https) callback URLs only when explicitly enabled.
ALLOW_HTTP_CALLBACKS = os.environ.get('ALLOW_HTTP_CALLBACKS', '0') == '1'
CALLBACK_URL_MAX_LEN = 2048

def _startup_checks():
    if API_KEY == 'changeme-please':
        print(
            "[API] WARNING: API_KEY is set to the insecure default 'changeme-please'. "
            "Set a strong API_KEY in .env before deploying.",
            flush=True,
        )


app = FastAPI(
    title="Job Search Results API",
    on_startup=[_startup_checks],
    description=(
        "Access scraped job results per user_id.\n\n"
        "Populated automatically by the scraper engine (Expresso, Sapo, Net-Empregos, Indeed, LinkedIn).\n\n"
        "**Auth**: Send `Authorization: Bearer <API_KEY>` header (preferred) or `?api_key=<API_KEY>` query param (deprecated — leaks into access logs)."
    ),
    version="2.1.0",
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
    # Prefer the Authorization: Bearer header
    if credentials and _safe_compare(credentials.credentials, API_KEY):
        return credentials.credentials
    # Legacy fallback: ?api_key=...  (kept for backwards compatibility but
    # strongly discouraged because the key ends up in access logs / Referer.)
    key_from_query = request.query_params.get('api_key')
    if key_from_query and _safe_compare(key_from_query, API_KEY):
        try:
            print(f"[API] DEPRECATED: api_key passed via query string from {request.client.host if request.client else '?'} (key={_mask_key(key_from_query)}). Use the Authorization header instead.")
        except Exception:
            pass
        return key_from_query
    raise HTTPException(
        status_code=401,
        detail="Invalid or missing API key. Send via 'Authorization: Bearer <key>' header (preferred) or '?api_key=<key>'.",
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
    job_profile: Optional[str] = "generalist"
    """Defines which scrapers run for this user. Must be one of the values
    returned by GET /api/v1/profiles. Unknown values fall back to 'generalist'."""
    callback_url: Optional[str] = None   # Webhook URL to push job results


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

    desc_col = ", descricao_completa" if include_description else ""

    query = f"""
        SELECT id, user_id, titulo, empresa, localizacao, plataforma, categoria, link,
               data_publicacao,
               COALESCE(data_scraped, '') AS data_scraped,
               status,
               recrutador_nome, recrutador_link, observacoes,
               COALESCE(salario, '')           AS salario,
               COALESCE(tipo_contrato, '')     AS tipo_contrato,
               COALESCE(nivel_experiencia, '') AS nivel_experiencia,
               relevance_score
               {desc_col}
        FROM vagas
        WHERE user_id = ?
    """
    params: list = [user_id]

    if run_date:
        # Sargable range — lets idx_vagas_user_scraped narrow the date band.
        day_start, day_end = _day_range(run_date)
        query += " AND data_scraped >= ? AND data_scraped < ?"
        params.extend([day_start, day_end])
    if status:
        query += " AND status = ?"
        params.append(status)
    if platform:
        query += " AND LOWER(plataforma) LIKE ?"
        params.append(f"%{platform.lower()}%")
    if salario_only:
        query += " AND salario IS NOT NULL AND salario != ''"
    if nivel:
        query += " AND LOWER(nivel_experiencia) LIKE ?"
        params.append(f"%{nivel.lower()}%")
    if min_score is not None:
        query += " AND COALESCE(relevance_score, 0) >= ?"
        params.append(min_score)

    if sort_by_relevance:
        query += " ORDER BY COALESCE(relevance_score, 0) DESC, data_scraped DESC LIMIT ?"
    else:
        query += " ORDER BY data_scraped DESC LIMIT ?"
    params.append(limit)

    with contextlib.closing(_get_conn()) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/v1/status", tags=["Health"])
def health_check():
    """Returns API health, DB status and total job count."""
    db_ok = _db_exists()
    job_count = 0
    platforms  = {}
    if db_ok:
        try:
            with contextlib.closing(_get_conn()) as conn:
                job_count = conn.execute("SELECT COUNT(*) FROM vagas").fetchone()[0]
                rows = conn.execute("SELECT plataforma, COUNT(*) FROM vagas GROUP BY plataforma").fetchall()
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

    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute(
            """
            SELECT id, user_id, titulo, empresa, localizacao, plataforma, categoria, link,
                   data_publicacao, data_scraped, status, descricao_completa,
                   recrutador_nome, recrutador_link, observacoes,
                   salario, tipo_contrato, nivel_experiencia, relevance_score
            FROM vagas WHERE id = ?
            """,
            (job_id,),
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
    if not _db_exists():
        raise HTTPException(status_code=503, detail="Database not found.")

    today_start, today_end = _day_range(datetime.now().strftime('%Y-%m-%d'))
    with contextlib.closing(_get_conn()) as conn:
        total      = conn.execute("SELECT COUNT(*) FROM vagas WHERE user_id=?", (user_id,)).fetchone()[0]
        active     = conn.execute("SELECT COUNT(*) FROM vagas WHERE user_id=? AND status='Ativa'", (user_id,)).fetchone()[0]
        expired    = conn.execute("SELECT COUNT(*) FROM vagas WHERE user_id=? AND status='Expirada'", (user_id,)).fetchone()[0]
        today_cnt  = conn.execute(
            "SELECT COUNT(*) FROM vagas WHERE user_id=? AND data_scraped >= ? AND data_scraped < ?",
            (user_id, today_start, today_end),
        ).fetchone()[0]
        by_plat    = conn.execute("SELECT plataforma, COUNT(*) FROM vagas WHERE user_id=? GROUP BY plataforma", (user_id,)).fetchall()

    return StatsResponse(
        total_jobs=total,
        active_jobs=active,
        expired_jobs=expired,
        jobs_by_platform={r[0]: r[1] for r in by_plat},
        jobs_today=today_cnt,
        generated_at=datetime.now().isoformat(),
    )


@app.get("/api/v1/profiles", tags=["Users"])
def list_job_profiles():
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

    # Normalise job_profile — unknown value → 'generalist'
    valid_profiles = _valid_job_profiles()
    raw_profile = (profile.job_profile or 'generalist').lower().strip()
    job_profile_norm = raw_profile if raw_profile in valid_profiles else 'generalist'
    if job_profile_norm != raw_profile:
        print(f"[sync] Unknown job_profile '{raw_profile}' → normalised to 'generalist'")

    # Convert lists to comma-separated strings for SQLite
    job_titles_str        = ", ".join(profile.job_titles)          if profile.job_titles          else ""
    locations_str         = ", ".join(profile.locations)           if profile.locations           else ""
    exp_levels_str        = ", ".join(profile.experience_levels)   if profile.experience_levels   else ""
    keywords_str          = ", ".join(profile.keywords)            if profile.keywords            else ""
    negative_keywords_str = ", ".join(profile.negative_keywords)   if profile.negative_keywords   else ""

    upsert_sql = '''
        INSERT INTO users_perfil (
            user_id, is_active, job_titles, locations, is_remote, min_salary,
            experience_levels, keywords, negative_keywords, job_profile, callback_url,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            is_active=excluded.is_active,
            job_titles=excluded.job_titles,
            locations=excluded.locations,
            is_remote=excluded.is_remote,
            min_salary=excluded.min_salary,
            experience_levels=excluded.experience_levels,
            keywords=excluded.keywords,
            negative_keywords=excluded.negative_keywords,
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
        job_profile_norm,
        profile.callback_url,
    )

    try:
        # Routed through db_helper so the UPSERT retries on `database is locked`
        # under contention (scrapers/scorer running concurrently).
        from automation.db_helper import execute_with_retry as _db_exec
        _db_exec(upsert_sql, params)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "success", "message": f"Profile for {profile.user_id} updated successfully."}
