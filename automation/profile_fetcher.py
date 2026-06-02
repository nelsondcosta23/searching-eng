import os
import json
import urllib.parse
from typing import Optional

_CONFIG_PATH = os.environ.get(
    'TECH_PROFILE_CONFIG',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'tech_profile.json')
)

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

_PROFILE_CACHE = None

def _load_profile() -> dict:
    global _PROFILE_CACHE
    if _PROFILE_CACHE is not None:
        return _PROFILE_CACHE
    if not os.path.exists(_CONFIG_PATH):
        print(f"[profile_fetcher] Config file not found at {_CONFIG_PATH}. Using defaults.")
        _PROFILE_CACHE = {
            "job_titles": ["software engineer"],
            "keywords": ["python"],
            "negative_keywords": [],
            "negative_companies": [],
            "locations": ["Portugal"],
            "is_remote": True,
            "min_salary": 0,
            "experience_levels": []
        }
        return _PROFILE_CACHE
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Normalize fields
            data['job_titles'] = [t.strip().lower() for t in data.get('job_titles', []) if t.strip()]
            data['keywords'] = [k.strip().lower() for k in data.get('keywords', []) if k.strip()]
            data['negative_keywords'] = [n.strip().lower() for n in data.get('negative_keywords', []) if n.strip()]
            data['negative_companies'] = [c.strip().lower() for c in data.get('negative_companies', []) if c.strip()]
            data['experience_levels'] = [e.strip() for e in data.get('experience_levels', []) if e.strip()]
            if 'locations' not in data or not data['locations']:
                data['locations'] = ["Portugal"]
            _PROFILE_CACHE = data
            return _PROFILE_CACHE
    except Exception as e:
        print(f"[profile_fetcher] Error loading tech_profile.json: {e}")
        return {}

def get_user_id() -> str:
    return "SingleUser"

def get_negative_keywords() -> list[str]:
    return _load_profile().get('negative_keywords', [])

def get_negative_companies() -> list[str]:
    return _load_profile().get('negative_companies', [])

def get_search_description() -> str:
    profile = _load_profile()
    return " ".join(profile.get('keywords', []) + profile.get('job_titles', []))

def get_profile_filters() -> dict:
    profile = _load_profile()
    return {
        'seniority_level': profile.get('experience_levels', []),
        'min_salary': profile.get('min_salary', 0)
    }

def _build_local_queries(profile: dict) -> list[dict]:
    titles = profile.get('job_titles') or []
    keywords = profile.get('keywords') or []
    locations = profile.get('locations') or ["Portugal"]
    is_remote = bool(profile.get('is_remote'))

    queries = []

    # Priority queries: job_title x location
    for title in titles:
        for loc in locations:
            queries.append({
                'search_string': title,
                'location': loc,
                'is_priority': True,
                'remote_only': is_remote,
            })

    # Enriched queries: short title + keyword
    short_titles = sorted(titles, key=len)[:2]
    useful_kw = [kw for kw in keywords[:4] if len(kw) <= 20][:1]

    for title in short_titles:
        for kw in useful_kw:
            combo = f"{title} {kw}"
            for loc in locations:
                queries.append({
                    'search_string': combo,
                    'location': loc,
                    'is_priority': False,
                    'remote_only': is_remote,
                })
    return queries

def _get_all_queries() -> list[dict]:
    return _build_local_queries(_load_profile())

def _deduplicate_queries(queries: list[dict]) -> list[dict]:
    country_level = set()
    for q in queries:
        loc = q.get('location', '')
        if q.get('remote_only') and loc not in _CITY_TO_COUNTRY:
            country_level.add((q.get('search_string', ''), loc))

    deduped = []
    for q in queries:
        role = q.get('search_string', '')
        loc = q.get('location', '')

        if q.get('remote_only') and loc in _CITY_TO_COUNTRY:
            parent_country = _CITY_TO_COUNTRY[loc]
            if (role, parent_country) in country_level:
                continue

        deduped.append(q)
    return deduped

def get_queries(deduplicate: bool = True) -> list[dict]:
    queries = _get_all_queries()
    if deduplicate:
        queries = _deduplicate_queries(queries)
    return queries

def get_priority_queries(deduplicate: bool = True) -> list[dict]:
    return [q for q in get_queries(deduplicate) if q.get('is_priority', False)]

def get_standard_queries(deduplicate: bool = True) -> list[dict]:
    return [q for q in get_queries(deduplicate) if not q.get('is_priority', False)]

def get_job_titles() -> list[str]:
    titles = []
    for q in _get_all_queries():
        raw_string = q.get('search_string', '')
        parts = raw_string.replace('(', '').replace(')', '').split(' OR ')
        for p in parts:
            clean = p.replace('"', '').strip().lower()
            if clean and clean not in titles:
                titles.append(clean)

    # Union with explicitly configured job titles
    for title in _load_profile().get('job_titles', []):
        if title and title not in titles:
            titles.append(title)
    return titles

def get_target_roles() -> list[str]:
    roles = list(get_job_titles())
    for kw in _load_profile().get('keywords', []):
        if kw and kw not in roles:
            roles.append(kw)
    return roles

def strict_keyword_match(text: str, keywords: list[str]) -> bool:
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

# URL Generators
def _clean_role_name(search_string: str) -> str:
    return search_string.replace('(', '').replace('"', '').split(' OR ')[0].strip()

def generate_linkedin_urls(priority_only=False, standard_only=False) -> dict:
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

_INDEED_DOMAIN_MAP = {
    'united kingdom': 'uk.indeed.com',
    'england':        'uk.indeed.com',
    'london':         'uk.indeed.com',
    'ireland':        'ie.indeed.com',
    'germany':        'de.indeed.com',
    'france':         'fr.indeed.com',
    'spain':          'es.indeed.com',
    'netherlands':    'www.indeed.nl',
}

def _indeed_domain(loc: str) -> str:
    loc_lower = loc.lower()
    for key, domain in _INDEED_DOMAIN_MAP.items():
        if key in loc_lower:
            return domain
    return 'pt.indeed.com'

def generate_indeed_urls(priority_only=False, standard_only=False) -> dict:
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

        domain = _indeed_domain(loc)
        q_role = urllib.parse.quote(q.get('search_string', ''))
        q_loc  = urllib.parse.quote(loc)

        if q.get('remote_only'):
            url = f"https://{domain}/jobs?q={q_role}&l={q_loc}&sc=0kf%3Aattr%28DSQF7%29%3B&fromage=1"
        else:
            url = f"https://{domain}/jobs?q={q_role}&l={q_loc}&fromage=1"

        urls[key] = {'url': url, 'is_priority': is_prio}
    return urls

def generate_sapo_urls() -> dict:
    queries = get_queries()
    profile = _load_profile()
    is_remote = profile.get('is_remote', False)
    urls = {}
    seen_roles = set()
    seen_remote = set()

    for q in queries:
        clean_name = _clean_role_name(q.get('search_string', ''))
        q_role = urllib.parse.quote(q.get('search_string', ''))

        if clean_name not in seen_roles:
            seen_roles.add(clean_name)
            key = f"Sapo: {clean_name} - Portugal"
            urls[key] = f"https://emprego.sapo.pt/offers?local=Portugal&pesquisa={q_role}"

        if is_remote and clean_name not in seen_remote:
            seen_remote.add(clean_name)
            key_r = f"Sapo: {clean_name} - Remoto"
            urls[key_r] = f"https://emprego.sapo.pt/offers?local=Portugal&pesquisa={q_role}&remote_work=1"

    return urls

def generate_expresso_urls() -> dict:
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

def get_local_profile() -> dict:
    return _load_profile()
