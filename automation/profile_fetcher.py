import os
import json
import sqlite3
import urllib.request
import urllib.parse
import time
from urllib.error import URLError

PROFILE_URL = "https://jysdacsaobjulyzyzsdj.supabase.co/functions/v1/job-search-profile"

# Path to the local SQLite database
_DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))

# Cities that are subsets of a country (for remote_only de-duplication)
_CITY_TO_COUNTRY = {
    "Lisboa": "Portugal", "Porto": "Portugal", "Braga": "Portugal",
    "Aveiro": "Portugal", "Coimbra": "Portugal", "Setúbal": "Portugal",
    "London": "United Kingdom", "Manchester": "United Kingdom",
    "Berlin": "Germany", "Munich": "Germany", "Hamburg": "Germany",
    "Amsterdam": "Netherlands", "Rotterdam": "Netherlands",
    "Dublin": "Ireland", "Cork": "Ireland",
    "Madrid": "Spain", "Barcelona": "Spain",
    "Paris": "France", "Lyon": "France",
}

# Global cache — 1 HTTP call per orchestration run
_PROFILE_CACHE = None
CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tmp', 'profile_cache.json')
CACHE_TTL = 3600  # 1 hour


def get_raw_profile():
    """Fetches the job search profile from the Supabase Edge Function with file caching."""
    global _PROFILE_CACHE
    
    # 1. Check memory cache
    if _PROFILE_CACHE:
        return _PROFILE_CACHE
        
    # 2. Check file cache
    if os.path.exists(CACHE_FILE):
        try:
            mtime = os.path.getmtime(CACHE_FILE)
            if time.time() - mtime < CACHE_TTL:
                with open(CACHE_FILE, 'r') as f:
                    _PROFILE_CACHE = json.load(f)
                    # print(f"[profile_fetcher] Profile loaded from file cache.")
                    return _PROFILE_CACHE
        except Exception as e:
            print(f"[profile_fetcher] Error reading cache file: {e}")

    # 3. Fetch from API
    try:
        req = urllib.request.Request(PROFILE_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status == 200:
                content = response.read().decode('utf-8')
                _PROFILE_CACHE = json.loads(content)
                
                # Save to file cache
                try:
                    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
                    with open(CACHE_FILE, 'w') as f:
                        f.write(content)
                except Exception as e:
                    print(f"[profile_fetcher] Error saving cache file: {e}")
                
                print(f"[profile_fetcher] Profile loaded from API. user_id={_PROFILE_CACHE.get('user_id', 'N/A')}")
                return _PROFILE_CACHE
    except Exception as e:
        print(f"[profile_fetcher] API Error: {e}")
        
    # 4. Fallback to expired cache if API fails
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                _PROFILE_CACHE = json.load(f)
                print(f"[profile_fetcher] API failed. Using expired file cache.")
                return _PROFILE_CACHE
        except Exception:
            pass

    return {}


def get_local_profile(user_id=None):
    """
    Reads a specific user profile from the local users_perfil SQLite table.
    Priority: explicit user_id arg > TARGET_USER_ID env var > first active user.
    Returns a dict with: user_id, keywords (list), negative_keywords (list), job_titles (list), locations (string).
    """
    result = {'user_id': None, 'keywords': [], 'negative_keywords': [], 'job_titles': [], 'locations': ''}
    try:
        if not os.path.exists(_DB_PATH):
            return result

        # Determine which user to load
        target_uid = user_id or os.environ.get('TARGET_USER_ID')

        conn = sqlite3.connect(_DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row

        _cols = "user_id, keywords, negative_keywords, job_titles, locations, is_remote, min_salary, experience_levels, job_profile"
        if target_uid:
            row = conn.execute(
                f"SELECT {_cols} FROM users_perfil WHERE user_id = ? AND is_active = 1",
                (target_uid,)
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT {_cols} FROM users_perfil WHERE is_active = 1 LIMIT 1"
            ).fetchone()
        conn.close()

        if row:
            result['user_id'] = row['user_id']
            if row['locations']:
                result['locations'] = row['locations']
            if row['keywords']:
                result['keywords'] = [k.strip().lower() for k in row['keywords'].split(',') if k.strip()]
            if row['negative_keywords']:
                result['negative_keywords'] = [k.strip().lower() for k in row['negative_keywords'].split(',') if k.strip()]
            if row['job_titles']:
                result['job_titles'] = [k.strip().lower() for k in row['job_titles'].split(',') if k.strip()]

            # Extended fields (may not exist in older schema rows)
            try:
                result['is_remote']         = bool(row['is_remote'])
                result['min_salary']        = int(row['min_salary']) if row['min_salary'] else 0
                result['experience_levels'] = [e.strip() for e in (row['experience_levels'] or '').split(',') if e.strip()]
                result['job_profile']       = (row['job_profile'] or 'generalist').lower().strip()
            except Exception:
                result['is_remote']         = False
                result['min_salary']        = 0
                result['experience_levels'] = []
                result['job_profile']       = 'generalist'

            if any([result['keywords'], result['negative_keywords'], result['job_titles']]):
                print(f"[profile_fetcher] Local profile loaded for '{result['user_id']}': "
                      f"{len(result['keywords'])} keywords, "
                      f"{len(result['negative_keywords'])} negative keywords, "
                      f"{len(result['job_titles'])} job titles.")
    except Exception as e:
        print(f"[profile_fetcher] Could not read local users_perfil: {e}")
    return result


def _get_strategy():
    return get_raw_profile().get('job_search_strategy', {})


def _api_strategy_is_empty() -> bool:
    """True when the upstream Supabase profile is unavailable or empty.

    Used to trigger a local-DB fallback consistently across getters when the
    Edge Function is down (e.g. returning 404). Without this, the API path
    returned silently empty data while the local users_perfil table held
    valid info.
    """
    s = _get_strategy()
    if not s:
        return True
    queries = s.get('queries') or []
    return len(queries) == 0


def _build_local_queries(local: dict) -> list[dict]:
    """Generates queries from a local users_perfil row dict.

    Produces two sets:
    1. Priority queries  — each job_title × each location (original behaviour)
    2. Enriched queries  — top 2 job_titles × top 3 keywords (standard priority)
       These find niche matches like "CTO AI Portugal" or "Head of Technology SaaS"
       that pure title searches miss.
    """
    titles    = local.get('job_titles') or []
    keywords  = local.get('keywords')   or []
    locations = [l.strip() for l in (local.get('locations') or "Portugal").split(',') if l.strip()]
    if not locations:
        locations = ["Portugal"]

    # Read is_remote from profile, fallback to REMOTE_ONLY env var
    is_remote = bool(local.get('is_remote')) or (os.environ.get('REMOTE_ONLY', '0') == '1')

    queries = []

    # --- Base queries: job_title × location (priority) ---
    for title in titles:
        for loc in locations:
            queries.append({
                'search_string': title,
                'location':      loc,
                'is_priority':   True,
                'remote_only':   is_remote,
            })

    # --- Enriched queries: short job_title + keyword (standard priority) ---
    # Only combine the top 2 shortest job_titles (avoid "Chief Technology Officer AI"
    # which is too long to match anything), with the top 3 most useful keywords.
    short_titles = sorted(titles, key=len)[:2]   # CTO, Tech Lead
    # Limit enriched combos to 1 keyword (was 3). 2 titles × 1 kw = 2 per location
    # instead of 6 — reduces ~65% of enriched queries with minimal signal loss.
    useful_kw    = [kw for kw in keywords[:4]
                    if len(kw) <= 20 and kw.lower() not in ('bitrix24',)][:1]  # skip brand names

    for title in short_titles:
        for kw in useful_kw:
            combo = f"{title} {kw}"
            for loc in locations:
                queries.append({
                    'search_string': combo,
                    'location':      loc,
                    'is_priority':   False,
                    'remote_only':   is_remote,
                })

    return queries


def get_user_id():
    """Returns the active user_id: TARGET_USER_ID env var > API profile > local active user."""
    target = os.environ.get('TARGET_USER_ID')
    if target:
        return target
    api_uid = get_raw_profile().get('user_id')
    if api_uid:
        return api_uid
    # Final fallback: first active local user (when API is down).
    local_uid = get_local_profile().get('user_id')
    return local_uid or 'Unknown'


def get_negative_keywords():
    """Returns negative keywords. If TARGET_USER_ID is set, only uses local DB."""
    target_uid = os.environ.get('TARGET_USER_ID')
    if target_uid:
        return get_local_profile(target_uid)['negative_keywords']

    api_negatives = [kw.lower() for kw in _get_strategy().get('filters', {}).get('negative_keywords', [])]
    local_negatives = get_local_profile()['negative_keywords']
    # Union without duplicates, preserving API list first
    merged = list(dict.fromkeys(api_negatives + local_negatives))
    return merged


def get_search_description():
    """Returns the user's professional profile description (for TF-IDF scoring).

    Priority: TARGET_USER_ID env > API search_description > local users_perfil.
    """
    target_uid = os.environ.get('TARGET_USER_ID')
    if target_uid:
        local = get_local_profile(target_uid)
        return " ".join(local['keywords'] + local['job_titles'])
    api_desc = _get_strategy().get('search_description', '')
    if api_desc:
        return api_desc
    # Fallback: synthesize from local profile when API is unavailable.
    local = get_local_profile()
    return " ".join(local['keywords'] + local['job_titles'])


def get_profile_filters():
    """Returns the full filters dict (seniority_level, industries, company_stage, etc.)."""
    target_uid = os.environ.get('TARGET_USER_ID')
    if target_uid:
        local = get_local_profile(target_uid)
        filters = {}
        if local.get('experience_levels'):
            filters['seniority_level'] = local['experience_levels']
        if local.get('min_salary'):
            filters['min_salary'] = local['min_salary']
        return filters

    api_filters = _get_strategy().get('filters', {})

    # Merge local experience_levels when API doesn't provide seniority
    if not api_filters.get('seniority_level'):
        local = get_local_profile()
        if local.get('experience_levels'):
            api_filters = dict(api_filters)
            api_filters['seniority_level'] = local['experience_levels']
    if not api_filters.get('min_salary'):
        local = get_local_profile()
        if local.get('min_salary'):
            api_filters = dict(api_filters)
            api_filters['min_salary'] = local['min_salary']

    return api_filters


def get_preferences():
    return _get_strategy().get('preferences', {})


def _get_all_queries():
    """
    Returns the list of search queries.

    Priority:
      1. TARGET_USER_ID env  → generated from local users_perfil for that uid.
      2. API strategy queries → if upstream Supabase has them.
      3. Local fallback     → first active users_perfil row (when API is down).
    """
    target_uid = os.environ.get('TARGET_USER_ID')
    if target_uid:
        local = get_local_profile(target_uid)
        if local['user_id']:
            generated = _build_local_queries(local)
            if generated:
                return generated

    api_queries = _get_strategy().get('queries', [])
    if api_queries:
        return api_queries

    # Final fallback: API is empty/down → use first active local user.
    local = get_local_profile()
    if local['user_id']:
        generated = _build_local_queries(local)
        if generated:
            print(f"[profile_fetcher] API empty/down — using local fallback for user {local['user_id']}")
            return generated

    return []


def _deduplicate_queries(queries):
    """
    For remote_only queries: removes city-level entries when a country-level
    entry already exists for the same role. Reduces ~102 → ~42 queries.
    E.g. if 'Portugal' exists, removes 'Lisboa', 'Porto', 'Braga', 'Aveiro', 'Coimbra'.
    """
    # Build a set of (search_string, country) pairs that exist
    country_level = set()
    for q in queries:
        loc = q.get('location', '')
        if q.get('remote_only') and loc not in _CITY_TO_COUNTRY:
            country_level.add((q.get('search_string', ''), loc))

    deduped = []
    for q in queries:
        role = q.get('search_string', '')
        loc  = q.get('location', '')

        # If this is a city and its country already has an entry → skip
        if q.get('remote_only') and loc in _CITY_TO_COUNTRY:
            parent_country = _CITY_TO_COUNTRY[loc]
            if (role, parent_country) in country_level:
                continue  # Redundant city-level query

        deduped.append(q)
    return deduped


def get_queries(deduplicate=True):
    """Returns all queries, optionally de-duplicated."""
    queries = _get_all_queries()
    if deduplicate:
        before = len(queries)
        queries = _deduplicate_queries(queries)
        after = len(queries)
        if before != after:
            print(f"[profile_fetcher] De-duplicated: {before} → {after} queries (saved {before - after} redundant searches)")
    return queries


def get_priority_queries(deduplicate=True):
    """Returns only is_priority=true queries."""
    return [q for q in get_queries(deduplicate) if q.get('is_priority', False)]


def get_standard_queries(deduplicate=True):
    """Returns only is_priority=false queries."""
    return [q for q in get_queries(deduplicate) if not q.get('is_priority', False)]


def get_job_titles():
    """Returns ONLY job title names used for scraper title-level filtering.
    Intentionally excludes general keywords (cloud, automation, saas, etc.)
    to avoid false positives — e.g. 'Software Engineer' should NOT pass
    a filter targeting 'CTO' / 'Tech Lead' just because its description
    mentions 'technology' or 'product'.

    Sources: API query search_strings + local users_perfil.job_titles only.
    """
    titles = []
    for q in _get_all_queries():
        raw_string = q.get('search_string', '')
        parts = raw_string.replace('(', '').replace(')', '').split(' OR ')
        for p in parts:
            clean = p.replace('"', '').strip().lower()
            if clean and clean not in titles:
                titles.append(clean)

    local = get_local_profile()
    for title in local['job_titles']:
        if title and title not in titles:
            titles.append(title)

    return titles


def get_target_roles():
    """Extracts role keywords from all API queries + local users_perfil keywords.
    Result is a deduplicated union of both sources.
    NOTE: used by the scorer only — for scraper title filtering use get_job_titles()."""
    roles = list(get_job_titles())

    # --- Extra: Local users_perfil general keywords (for scorer context) ---
    local = get_local_profile()
    for kw in local['keywords']:
        if kw and kw not in roles:
            roles.append(kw)

    return roles


def strict_keyword_match(text, keywords):
    """
    Bulletproof keyword matching:
    1. Ignores 1-2 letter keywords unless in whitelist (IT, UX, UI, HR...)
       This prevents matching the Portuguese word "em" (in) when searching for "EM" (Engineering Manager).
    2. Uses word boundary matching so "cto" doesn't match "director".
    """
    import re
    whitelist = ['it', 'ux', 'ui', 'qa', 'hr', 'vp', 'ai', 'ml', 'bi', 'c#']
    text_lower = text.lower()
    
    for kw in keywords:
        kw_clean = kw.strip().lower()
        if len(kw_clean) < 3 and kw_clean not in whitelist:
            continue
            
        pattern = r'\b' + re.escape(kw_clean) + r'\b'
        if re.search(pattern, text_lower):
            return True
            
    return False

# ─────────────────────────────────────────────────────────────────────────────
# URL Generators
# ─────────────────────────────────────────────────────────────────────────────
def _clean_role_name(search_string):
    return search_string.replace('(', '').replace('"', '').split(' OR ')[0].strip()


def generate_linkedin_urls(priority_only=False, standard_only=False):
    if priority_only:
        queries = get_priority_queries()
    elif standard_only:
        queries = get_standard_queries()
    else:
        queries = get_queries()

    urls = {}
    for q in queries:
        loc = q.get('location', 'Worldwide')
        is_prio = q.get('is_priority', False)
        clean_name = _clean_role_name(q.get('search_string', ''))
        key = f"{'★ ' if is_prio else ''}LinkedIn: {clean_name} - {loc}"

        q_role = urllib.parse.quote(q.get('search_string', ''))
        q_loc  = urllib.parse.quote(loc)
        url = f"https://pt.linkedin.com/jobs/search?keywords={q_role}&location={q_loc}&f_TPR=r86400"

        if q.get('remote_only'):
            url += "&f_WT=2"

        urls[key] = {'url': url, 'is_priority': is_prio}
    return urls


def generate_indeed_urls(priority_only=False, standard_only=False):
    if priority_only:
        queries = get_priority_queries()
    elif standard_only:
        queries = get_standard_queries()
    else:
        queries = get_queries()

    urls = {}
    for q in queries:
        loc = q.get('location', 'Portugal')
        is_prio = q.get('is_priority', False)
        clean_name = _clean_role_name(q.get('search_string', ''))
        key = f"{'★ ' if is_prio else ''}Indeed: {clean_name} - {loc}"

        q_role = urllib.parse.quote(q.get('search_string', ''))
        q_loc  = urllib.parse.quote(loc)

        if q.get('remote_only'):
            url = f"https://pt.indeed.com/jobs?q={q_role}&l={q_loc}&sc=0kf%3Aattr%28DSQF7%29%3B&fromage=1"
        else:
            url = f"https://pt.indeed.com/jobs?q={q_role}&l={q_loc}&fromage=1"

        urls[key] = {'url': url, 'is_priority': is_prio}
    return urls


def generate_sapo_urls():
    """
    Generates Sapo search URLs. Uses country-level (Portugal) instead of per-city
    to reduce redundant searches (1 URL per role instead of 1 per role×city).
    When is_remote=1 in the user profile, also generates remote-specific searches.
    """
    queries = get_queries()
    local   = get_local_profile()
    is_remote = local.get('is_remote', False)
    urls = {}
    seen_roles  = set()
    seen_remote = set()

    for q in queries:
        loc = q.get('location', 'Portugal')
        # Sapo is PT only — only process PT-related queries
        if loc not in ["Portugal", "Lisboa", "Porto", "Braga", "Aveiro", "Coimbra"]:
            continue

        clean_name = _clean_role_name(q.get('search_string', ''))
        q_role = urllib.parse.quote(q.get('search_string', ''))

        # National search (one per role)
        if clean_name not in seen_roles:
            seen_roles.add(clean_name)
            key = f"Sapo: {clean_name} - Portugal"
            urls[key] = f"https://emprego.sapo.pt/offers?local=Portugal&pesquisa={q_role}"

        # Remote-specific search (one per role, when is_remote=1)
        if is_remote and clean_name not in seen_remote:
            seen_remote.add(clean_name)
            key_r = f"Sapo: {clean_name} - Remoto"
            urls[key_r] = f"https://emprego.sapo.pt/offers?local=Portugal&pesquisa={q_role}&remote_work=1"

    return urls

def generate_expresso_urls():
    queries = get_queries()
    urls = {}
    for q in queries:
        loc = q.get('location', 'Portugal')
        clean_name = _clean_role_name(q.get('search_string', ''))
        
        q_role = urllib.parse.quote(q.get('search_string', ''))
        q_loc = urllib.parse.quote(loc if loc.lower() != 'portugal' else '')
        
        key = f"Expresso: {clean_name} - {loc}"
        url = f"https://expressoemprego.pt/pesquisa?K={q_role}&L={q_loc}"
        urls[key] = url

    return urls
