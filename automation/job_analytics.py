import sqlite3
import os
import re
from datetime import datetime
from typing import List, Dict, Any

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))

def _get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.row_factory = sqlite3.Row
    return conn

def parse_posting_age(data_pub: str, data_scraped_str: str) -> int:
    """Estimates the posting age in days from a publication string and scrape timestamp."""
    if not data_pub:
        return 0
    data_pub_lower = data_pub.lower().strip()
    
    # Hour/minute/recent/today
    if any(w in data_pub_lower for w in ('hour', 'hora', 'minute', 'minuto', 'recent', 'hoje', 'today', 'recent')):
        return 0
    # Yesterday
    if any(w in data_pub_lower for w in ('yesterday', 'ontem')):
        return 1
    # Check for "N days" / "N dias" / "Nd" / "há N dias"
    m = re.search(r'(\d+)\s*(?:days?|dias?|d)\b', data_pub_lower)
    if m:
        return int(m.group(1))
    m = re.search(r'\bh[áa]\s+(\d+)\s+dias?', data_pub_lower)
    if m:
        return int(m.group(1))
    
    # Try direct date parsing
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            clean_pub = data_pub.split('T')[0] if 'T' in data_pub else data_pub
            pub_date = datetime.strptime(clean_pub.strip()[:10], fmt.split(' ')[0]).date()
            scraped_date = datetime.strptime(data_scraped_str[:10], '%Y-%m-%d').date()
            diff = (scraped_date - pub_date).days
            return max(0, diff)
        except Exception:
            continue
            
    return 0

def update_all_posting_ages():
    """Batch updates all posting_age_days that are currently NULL in the jobs table."""
    conn = None
    try:
        conn = _get_connection()
        rows = conn.execute("SELECT id, data_publicacao, data_scraped FROM jobs WHERE posting_age_days IS NULL").fetchall()
        if not rows:
            return
        
        updates = []
        for r in rows:
            age = parse_posting_age(r['data_publicacao'], r['data_scraped'])
            updates.append((age, r['id']))
            
        conn.executemany("UPDATE jobs SET posting_age_days = ? WHERE id = ?", updates)
        conn.commit()
        print(f"[analytics] Updated posting_age_days for {len(updates)} jobs.")
    except Exception as e:
        print(f"[analytics] Failed to batch update posting ages: {e}")
    finally:
        if conn:
            conn.close()

def get_company_rankings() -> List[Dict[str, Any]]:
    """Rank companies by their active tech job openings."""
    update_all_posting_ages()
    
    conn = None
    try:
        conn = _get_connection()
        query = """
            SELECT j.empresa, COUNT(*) as open_positions, c.inception_year, c.company_age
            FROM jobs j
            LEFT JOIN companies c ON LOWER(j.empresa) = LOWER(c.name)
            WHERE j.status = 'Ativa' AND j.job_type != 'Non-tech'
            GROUP BY LOWER(j.empresa)
            ORDER BY open_positions DESC, j.empresa ASC
        """
        rows = conn.execute(query).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[analytics] Error getting rankings: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_job_type_distribution() -> List[Dict[str, Any]]:
    """Gets distribution of job types across all active tech postings."""
    conn = None
    try:
        conn = _get_connection()
        query = """
            SELECT job_type, COUNT(*) as count
            FROM jobs
            WHERE status = 'Ativa' AND job_type != 'Non-tech'
            GROUP BY job_type
            ORDER BY count DESC
        """
        rows = conn.execute(query).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[analytics] Error getting job type distribution: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_company_job_types() -> List[Dict[str, Any]]:
    """Gets job type breakdown for each company."""
    conn = None
    try:
        conn = _get_connection()
        query = """
            SELECT j.empresa, j.job_type, COUNT(*) as count
            FROM jobs j
            WHERE j.status = 'Ativa' AND j.job_type != 'Non-tech'
            GROUP BY LOWER(j.empresa), j.job_type
            ORDER BY j.empresa ASC, count DESC
        """
        rows = conn.execute(query).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[analytics] Error getting company job types: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_company_stats(company_name: str) -> Dict[str, Any]:
    """Calculate stats for a single company: job types, vacancy ages, and company age."""
    conn = None
    try:
        conn = _get_connection()
        
        # Get active jobs
        jobs_query = """
            SELECT id, titulo, plataforma, localizacao, link, posting_age_days, salario, tipo_contrato, nivel_experiencia, job_type, data_scraped
            FROM jobs
            WHERE LOWER(empresa) = LOWER(?) AND status = 'Ativa' AND job_type != 'Non-tech'
            ORDER BY posting_age_days ASC, data_scraped DESC
        """
        jobs = [dict(r) for r in conn.execute(jobs_query, (company_name,)).fetchall()]
        
        # Get company age
        company_query = "SELECT inception_year, company_age, description FROM companies WHERE LOWER(name) = LOWER(?)"
        company_row = conn.execute(company_query, (company_name,)).fetchone()
        
        inception_year = company_row['inception_year'] if company_row else None
        company_age = company_row['company_age'] if company_row else None
        description = company_row['description'] if company_row else None
        
        # Age stats of postings
        ages = [j['posting_age_days'] for j in jobs if j['posting_age_days'] is not None]
        avg_age = sum(ages) / len(ages) if ages else 0
        min_age = min(ages) if ages else 0
        max_age = max(ages) if ages else 0
        
        # Job type breakdown
        breakdown = {}
        for j in jobs:
            jt = j['job_type']
            breakdown[jt] = breakdown.get(jt, 0) + 1
            
        return {
            'company': company_name,
            'inception_year': inception_year,
            'company_age': company_age,
            'description': description,
            'total_openings': len(jobs),
            'avg_posting_age_days': round(avg_age, 1),
            'min_posting_age_days': min_age,
            'max_posting_age_days': max_age,
            'job_types': breakdown,
            'jobs': jobs
        }
    except Exception as e:
        print(f"[analytics] Error compiling stats for '{company_name}': {e}")
        return {}
    finally:
        if conn:
            conn.close()

def generate_markdown_report() -> str:
    """Generates a text-based Markdown report of the tech market intelligence."""
    rankings = get_company_rankings()
    job_types = get_job_type_distribution()
    co_types = get_company_job_types()
    
    # Pivot co_types to company -> {job_type: count}
    co_breakdown: Dict[str, Dict[str, int]] = {}
    for r in co_types:
        co = r['empresa']
        co_breakdown.setdefault(co, {})[r['job_type']] = r['count']
        
    report = []
    report.append("# Tech Job-Market Intelligence Report")
    report.append(f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    report.append("## Overall Job Type Distribution")
    for jt in job_types:
        report.append(f"- **{jt['job_type']}**: {jt['count']} open positions")
    report.append("")
    
    report.append("## Top Hiring Companies")
    for i, r in enumerate(rankings, 1):
        co = r['empresa']
        age_str = f"({r['company_age']} years old)" if r['company_age'] else "(Age: Unknown)"
        report.append(f"{i}. **{co}** {age_str} — **{r['open_positions']}** open positions")
        
        # Add breakdown
        types_str = []
        for jt, cnt in sorted(co_breakdown.get(co, {}).items(), key=lambda x: x[1], reverse=True):
            types_str.append(f"{jt}: {cnt}")
        report.append(f"   *Breakdown: {', '.join(types_str)}*")
        
    return "\n".join(report)
