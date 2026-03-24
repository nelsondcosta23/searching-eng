import os
import json
import urllib.request
import urllib.parse
from urllib.error import URLError

PROFILE_URL = "https://jysdacsaobjulyzyzsdj.supabase.co/functions/v1/job-search-profile"

def get_raw_profile():
    """Fetches the full root JSON profile from the Supabase API."""
    try:
        req = urllib.request.Request(PROFILE_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                return json.loads(response.read().decode('utf-8'))
    except (URLError, json.JSONDecodeError) as e:
        print(f"Error fetching profile API: {e}")
    return {}

def get_job_profile_v2():
    data = get_raw_profile()
    return data.get('job_search_strategy', {})

def get_user_id():
    data = get_raw_profile()
    return data.get('user_id', 'Unknown')

def get_queries():
    strategy = get_job_profile_v2()
    return strategy.get('queries', [])

def get_negative_keywords():
    strategy = get_job_profile_v2()
    filters = strategy.get('filters', {})
    return [kw.lower() for kw in filters.get('negative_keywords', [])]

def generate_linkedin_urls():
    queries = get_queries()
    urls = {}
    for q in queries:
        loc = q.get('location', 'Worldwide')
        # Extract a clean name for the dictionary key from the boolean string
        # e.g. ("CTO" OR "Chief") -> CTO
        clean_name = q.get('search_string', '').replace('(', '').replace('"', '').split(' OR ')[0] 
        key = f"LinkedIn: {clean_name} - {loc}"
        
        q_role = urllib.parse.quote(q.get('search_string', ''))
        q_loc = urllib.parse.quote(loc)
        url = f"https://pt.linkedin.com/jobs/search?keywords={q_role}&location={q_loc}"
        
        if q.get('remote_only'):
            url += "&f_WT=2" # LinkedIn filter for Remote
            
        urls[key] = url
    return urls

def generate_indeed_urls():
    queries = get_queries()
    urls = {}
    for q in queries:
        loc = q.get('location', 'Portugal')
        clean_name = q.get('search_string', '').replace('(', '').replace('"', '').split(' OR ')[0] 
        key = f"Indeed: {clean_name} - {loc}"
        
        q_role = urllib.parse.quote(q.get('search_string', ''))
        q_loc = urllib.parse.quote(loc)
        
        # Indeed handles remote natively using the sc parameter
        if q.get('remote_only'):
             url = f"https://pt.indeed.com/jobs?q={q_role}&l={q_loc}&sc=0kf%3Aattr%28DSQF7%29%3B"
        else:
             url = f"https://pt.indeed.com/jobs?q={q_role}&l={q_loc}"
             
        urls[key] = url
    return urls

def generate_sapo_urls():
    queries = get_queries()
    urls = {}
    for q in queries:
        loc = q.get('location', 'Portugal')
        if loc not in ["Portugal", "Lisboa", "Porto", "Braga", "Aveiro", "Coimbra"]:
            continue # Sapo is PT only, skip foreign queries
            
        clean_name = q.get('search_string', '').replace('(', '').replace('"', '').split(' OR ')[0] 
        key = f"Sapo: {clean_name} - {loc}"
        
        q_role = urllib.parse.quote(q.get('search_string', ''))
        q_loc = urllib.parse.quote(loc)
        url = f"https://emprego.sapo.pt/offers?local={q_loc}&pesquisa={q_role}"
        
        urls[key] = url
    return urls

# For RSS feeds (Net-Empregos, Expresso) we need to extract raw titles to filter locally
def get_target_roles():
    queries = get_queries()
    roles = []
    for q in queries:
        # Simplistic parsing of boolean string ("CTO" OR "Chief Technology Officer")
        # to extract acceptable keywords for RSS filters
        raw_string = q.get('search_string', '')
        parts = raw_string.replace('(', '').replace(')', '').split(' OR ')
        for p in parts:
            clean = p.replace('"', '').strip()
            if clean and clean not in roles:
                roles.append(clean)
    return roles
