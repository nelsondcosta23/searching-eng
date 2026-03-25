"""Job Search API Service"""
from fastapi import FastAPI, Query, HTTPException, Depends, Security, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import os
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

DB_PATH = os.environ.get('DB_PATH', '/app/database/vagas.db')
API_KEY = os.environ.get('API_KEY', 'changeme-please')

app = FastAPI(
    title="Job Search Results API",
    description="Access scraped job results per user_id. Populated nightly by the automated scraper.",
    version="1.0.0",
)

# Allow CORS for external software
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)
# Accepts key via: Authorization: Bearer <key>  OR  ?api_key=<key>
def verify_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
):
    # Try header first
    if credentials and credentials.credentials == API_KEY:
        return credentials.credentials
    # Fallback to query param
    key_from_query = request.query_params.get('api_key')
    if key_from_query == API_KEY:
        return key_from_query
    raise HTTPException(status_code=401, detail="Invalid or missing API key. Send via 'Authorization: Bearer <key>' header or '?api_key=<key>' query param.")

class Job(BaseModel):
    id: int
    user_id: Optional[str]
    titulo: Optional[str]
    empresa: Optional[str]
    localizacao: Optional[str]
    plataforma: Optional[str]
    categoria: Optional[str]
    link: Optional[str]
    data_scraped: Optional[str]
    status: Optional[str]
    descricao_completa: Optional[str] = None
    recrutador_nome: Optional[str] = None
    recrutador_link: Optional[str] = None
    observacoes: Optional[str] = None

class JobsResponse(BaseModel):
    user_id: str
    total: int
    generated_at: str
    filters: dict
    jobs: List[Job]

def get_jobs_from_db(user_id: str, status: Optional[str], platform: Optional[str], limit: int, include_description: bool, run_date: Optional[str]) -> List[dict]:
    if not os.path.exists(DB_PATH):
        return []
    
    desc_col = ", descricao_completa" if include_description else ""
    
    query = f"""
        SELECT id, user_id, titulo, empresa, localizacao, plataforma, categoria, link,
               COALESCE(data_scraped, '') AS data_scraped, status, recrutador_nome, recrutador_link, observacoes{desc_col}
        FROM vagas
        WHERE user_id = ?
    """
    params = [user_id]

    if run_date:
        query += " AND DATE(data_scraped) = ?"
        params.append(run_date)

    if status:
        query += " AND status = ?"
        params.append(status)

    if platform:
        query += " AND plataforma = ?"
        params.append(platform)

    query += " ORDER BY data_scraped DESC LIMIT ?"
    params.append(limit)

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

@app.get("/api/v1/status", tags=["Health"])
def health_check():
    """Check if the API is running and the database is accessible."""
    db_exists = os.path.exists(DB_PATH)
    job_count = 0
    if db_exists:
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            job_count = conn.execute("SELECT COUNT(*) FROM vagas").fetchone()[0]
            conn.close()
        except Exception:
            pass
    return {
        "status": "ok",
        "database": "connected" if db_exists else "not found",
        "total_jobs_in_db": job_count,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/v1/jobs", response_model=JobsResponse, tags=["Jobs"])
def get_jobs(
    user_id: str = Query(..., description="The user ID to filter results for."),
    run_date: Optional[str] = Query(None, description="Date to filter scraping results (YYYY-MM-DD). Defaults to today."),
    status: Optional[str] = Query(None, description="Filter by status: 'Ativa' or 'Expirada'. Default: all."),
    platform: Optional[str] = Query(None, description="Filter by platform, e.g. 'LinkedIn', 'Indeed PT'."),
    limit: int = Query(500, ge=1, le=1000, description="Max number of results (1-1000). Default: 500."),
    include_description: bool = Query(False, description="Include full job description text. Default: false."),
    api_key: str = Depends(verify_api_key),
):
    """
    Retrieve jobs scraped for a given `user_id`.
    
    By default, returns **only today's scraping results** (based on `data_scraped` date).
    Use `run_date=YYYY-MM-DD` to query a specific past date, or `run_date=all` to return the full history.
    """
    # Default to today's date unless 'all' or explicit date provided
    effective_date: Optional[str]
    if run_date is None:
        effective_date = datetime.now().strftime('%Y-%m-%d')  # Default: today
    elif run_date.lower() == 'all':
        effective_date = None  # No date filter = full history
    else:
        effective_date = run_date
    
    jobs = get_jobs_from_db(user_id, status, platform, limit, include_description, effective_date)
    
    return JobsResponse(
        user_id=user_id,
        total=len(jobs),
        generated_at=datetime.now().isoformat(),
        filters={"run_date": effective_date or "all", "status": status, "platform": platform, "limit": limit},
        jobs=jobs,
    )

@app.get("/api/v1/jobs/{job_id}", response_model=Job, tags=["Jobs"])
def get_single_job(
    job_id: int,
    api_key: str = Depends(verify_api_key),
):
    """Retrieve the full detail of a single job by its database ID."""
    if not os.path.exists(DB_PATH):
        raise HTTPException(status_code=503, detail="Database not found.")
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, titulo, empresa, localizacao, plataforma, categoria, link, data_scraped, status, descricao_completa, recrutador_nome, recrutador_link, observacoes FROM vagas WHERE id = ?",
        (job_id,)
    )
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail=f"Job with id={job_id} not found.")
    
    return dict(row)
