"""Job Search Results API — v2.0"""
from fastapi import FastAPI, Query, HTTPException, Depends, Security, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import os
import json
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
API_KEY  = os.environ.get('API_KEY', 'changeme-please')

app = FastAPI(
    title="Job Search Results API",
    description=(
        "Access scraped job results per user_id.\n\n"
        "Populated automatically by the scraper engine (Expresso, Sapo, Net-Empregos, Indeed, LinkedIn).\n\n"
        "**Auth**: Send `Authorization: Bearer <API_KEY>` header or `?api_key=<API_KEY>` query param."
    ),
    version="2.0.0",
)

# CORS — allow external apps (React dashboards, N8N, Zapier, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────
security = HTTPBearer(auto_error=False)

def verify_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
):
    if credentials and credentials.credentials == API_KEY:
        return credentials.credentials
    key_from_query = request.query_params.get('api_key')
    if key_from_query == API_KEY:
        return key_from_query
    raise HTTPException(
        status_code=401,
        detail="Invalid or missing API key. Send via 'Authorization: Bearer <key>' header or '?api_key=<key>'.",
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
        query += " AND DATE(data_scraped) = ?"
        params.append(run_date)
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

    if sort_by_relevance:
        query += " ORDER BY COALESCE(relevance_score, 0) DESC, data_scraped DESC LIMIT ?"
    else:
        query += " ORDER BY data_scraped DESC LIMIT ?"
    params.append(limit)

    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


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
            conn = _get_conn()
            job_count = conn.execute("SELECT COUNT(*) FROM vagas").fetchone()[0]
            rows = conn.execute("SELECT plataforma, COUNT(*) FROM vagas GROUP BY plataforma").fetchall()
            platforms = {r[0]: r[1] for r in rows}
            conn.close()
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

    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, user_id, titulo, empresa, localizacao, plataforma, categoria, link,
               data_publicacao, data_scraped, status, descricao_completa,
               recrutador_nome, recrutador_link, observacoes,
               salario, tipo_contrato, nivel_experiencia
        FROM vagas WHERE id = ?
        """,
        (job_id,),
    )
    row = cursor.fetchone()
    conn.close()

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

    conn = _get_conn()
    today = datetime.now().strftime('%Y-%m-%d')

    total      = conn.execute("SELECT COUNT(*) FROM vagas WHERE user_id=?", (user_id,)).fetchone()[0]
    active     = conn.execute("SELECT COUNT(*) FROM vagas WHERE user_id=? AND status='Ativa'", (user_id,)).fetchone()[0]
    expired    = conn.execute("SELECT COUNT(*) FROM vagas WHERE user_id=? AND status='Expirada'", (user_id,)).fetchone()[0]
    today_cnt  = conn.execute("SELECT COUNT(*) FROM vagas WHERE user_id=? AND DATE(data_scraped)=?", (user_id, today)).fetchone()[0]
    by_plat    = conn.execute("SELECT plataforma, COUNT(*) FROM vagas WHERE user_id=? GROUP BY plataforma", (user_id,)).fetchall()

    conn.close()

    return StatsResponse(
        total_jobs=total,
        active_jobs=active,
        expired_jobs=expired,
        jobs_by_platform={r[0]: r[1] for r in by_plat},
        jobs_today=today_cnt,
        generated_at=datetime.now().isoformat(),
    )


@app.post("/api/v1/users/sync", tags=["Users"])
def sync_user_profile(profile: UserProfile, api_key: str = Depends(verify_api_key)):
    """Upsert user scraping profile preferences."""
    if not _db_exists():
        raise HTTPException(status_code=503, detail="Database not found.")
        
    conn = _get_conn()
    cursor = conn.cursor()
    
    # Convert lists to comma-separated strings for SQLite
    job_titles_str = ", ".join(profile.job_titles) if profile.job_titles else ""
    locations_str = ", ".join(profile.locations) if profile.locations else ""
    exp_levels_str = ", ".join(profile.experience_levels) if profile.experience_levels else ""
    keywords_str = ", ".join(profile.keywords) if profile.keywords else ""
    negative_keywords_str = ", ".join(profile.negative_keywords) if profile.negative_keywords else ""
    
    try:
        cursor.execute('''
            INSERT INTO users_perfil (user_id, is_active, job_titles, locations, is_remote, min_salary, experience_levels, keywords, negative_keywords, callback_url, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                is_active=excluded.is_active,
                job_titles=excluded.job_titles,
                locations=excluded.locations,
                is_remote=excluded.is_remote,
                min_salary=excluded.min_salary,
                experience_levels=excluded.experience_levels,
                keywords=excluded.keywords,
                negative_keywords=excluded.negative_keywords,
                callback_url=excluded.callback_url,
                updated_at=CURRENT_TIMESTAMP
        ''', (
            profile.user_id, 
            1 if profile.is_active else 0, 
            job_titles_str, 
            locations_str, 
            1 if profile.is_remote else 0, 
            profile.min_salary, 
            exp_levels_str,
            keywords_str,
            negative_keywords_str,
            profile.callback_url,
        ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
        
    return {"status": "success", "message": f"Profile for {profile.user_id} updated successfully."}
