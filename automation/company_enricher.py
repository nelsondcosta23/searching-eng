import sqlite3
import os
import re
import requests
import time
from datetime import datetime
from typing import Optional, Tuple

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))

def _get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn

def _clean_company_name(name: str) -> str:
    """Removes common suffixes, country names, and extra spacing to improve matching on Wikidata."""
    if not name:
        return ""
    # Remove parenthetical details like "(BMW)" or "(BMW Group)"
    cleaned = re.sub(r'\(.*?\)', '', name)
    cleaned = re.sub(r'\b(portugal|spain|uk|usa|emea|germany|france|belgium|netherlands|brasil|brazil)\b', '', cleaned, flags=re.I)
    cleaned = re.sub(r'\b(lda\.?|s\.?a\.?|ltd\.?|inc\.?|co\.?|corp\.?|corporation|solutions|technologies|technology|systems|group|grupo)\b', '', cleaned, flags=re.I)
    cleaned = re.sub(r'[^a-zA-Z0-9\s\-&]', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned if cleaned else name

def fetch_inception_year(company_name: str) -> Tuple[Optional[int], Optional[str]]:
    """Query the public Wikidata API for the company's inception year and description.
    
    Returns (inception_year, description).
    """
    headers = {
        'User-Agent': 'TechJobIntelligenceTool/1.0 (nelsonfilipecosta@gmail.com) Requests/2.0'
    }
    
    # Try searching with cleaned name first, fallback to original if empty or no results
    names_to_try = [company_name]
    cleaned = _clean_company_name(company_name)
    if cleaned and cleaned.lower() != company_name.lower():
        names_to_try.insert(0, cleaned)

    for term in names_to_try:
        try:
            # Step 1: Search for entity matching term
            search_url = "https://www.wikidata.org/w/api.php"
            search_params = {
                "action": "wbsearchentities",
                "search": term,
                "language": "en",
                "format": "json",
                "type": "item",
                "limit": 1
            }
            res = requests.get(search_url, params=search_params, headers=headers, timeout=8)
            if res.status_code != 200:
                continue
            search_data = res.json()
            search_results = search_data.get("search", [])
            if not search_results:
                continue
            
            entity_id = search_results[0].get("id")
            description = search_results[0].get("description")
            if not entity_id:
                continue
                
            # Step 2: Get claims for property P571 (inception)
            claims_url = "https://www.wikidata.org/w/api.php"
            claims_params = {
                "action": "wbgetclaims",
                "entity": entity_id,
                "property": "P571",
                "format": "json"
            }
            res = requests.get(claims_url, params=claims_params, headers=headers, timeout=8)
            if res.status_code != 200:
                continue
            claims_data = res.json()
            claims = claims_data.get("claims", {}).get("P571", [])
            if not claims:
                # Cache description even if inception year is missing
                return None, description
                
            time_val = claims[0].get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("time")
            if time_val:
                m = re.match(r'^[+-](\d{4})', time_val)
                if m:
                    return int(m.group(1)), description
        except Exception as e:
            print(f"[company_enricher] API lookup failed for '{term}': {e}")
            
    return None, None

def get_or_enrich_company(company_name: str) -> Tuple[Optional[int], Optional[int]]:
    """Fetch company details. First checks the local SQLite cache, then falls back to Wikidata.
    
    Returns (inception_year, company_age).
    """
    if not company_name:
        return None, None

    normalized_name = company_name.strip()
    current_year = datetime.now().year
    
    conn = None
    try:
        conn = _get_connection()
        # 1. Check local cache
        row = conn.execute(
            "SELECT inception_year, company_age FROM companies WHERE LOWER(name) = LOWER(?)",
            (normalized_name,)
        ).fetchone()
        
        if row:
            inception_year, company_age = row[0], row[1]
            # Recalculate age if year is available but age is stale/null
            if inception_year is not None:
                calculated_age = current_year - inception_year
                if company_age != calculated_age:
                    conn.execute(
                        "UPDATE companies SET company_age = ?, last_updated = CURRENT_TIMESTAMP WHERE LOWER(name) = LOWER(?)",
                        (calculated_age, normalized_name)
                    )
                    conn.commit()
                    company_age = calculated_age
            return inception_year, company_age
            
        # 2. Call Wikidata API (uncached)
        # Apply slight rate-limiting delay for external requests
        time.sleep(0.5)
        inception_year, description = fetch_inception_year(normalized_name)
        
        company_age = None
        if inception_year is not None:
            company_age = current_year - inception_year
            
        # 3. Save to local cache
        conn.execute(
            "INSERT OR REPLACE INTO companies (name, inception_year, company_age, description) VALUES (?, ?, ?, ?)",
            (normalized_name, inception_year, company_age, description)
        )
        conn.commit()
        return inception_year, company_age
        
    except Exception as e:
        print(f"[company_enricher] Error caching/querying company '{company_name}': {e}")
        return None, None
    finally:
        if conn:
            conn.close()
