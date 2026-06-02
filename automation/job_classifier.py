import re

def classify_job(title: str, description: str = "") -> str:
    """Classifies a job title and description into a tech job type.
    
    Returns one of:
      - Backend
      - Frontend
      - Full-stack
      - Data/ML
      - DevOps/Cloud
      - QA
      - Mobile
      - Security
      - IT/Infra
      - Other-tech
      - Non-tech
    """
    title_lower = f" {title.lower()} "
    desc_first_part = description.lower()[:300] if description else ""
    
    # 1. Non-tech Filter (skip if title matches non-tech roles, unless it explicitly contains tech words)
    non_tech_keywords = [
        r'\bmarketing\b', r'\bsales\b', r'\bhr\b', r'\brecruiters?\b', r'\bcooks?\b', r'\bdrivers?\b',
        r'\bdoctors?\b', r'\bnurses?\b', r'\bwaiters?\b', r'\baccountants?\b', r'\blawyers?\b',
        r'\bdesigners?\b', r'\bproduct\s+managers?\b', r'\bscrum\s+masters?\b', r'\bagile\s+coach(?:es)?\b',
        r'\bvendedores?\b', r'\bcomercia(?:l|is)\b', r'\bcontabilistas?\b', r'\badvogados?\b', r'\brececionistas?\b'
    ]
    is_non_tech = False
    for pattern in non_tech_keywords:
        if re.search(pattern, title_lower):
            # Exception: check if it contains clear tech indicators
            tech_anchors = [r'\bdevelopers?\b', r'\bengineers?\b', r'\bprogramadores?\b', r'\bengenheiros?\b', r'\bsoftware\b', r'\btech\b']
            if not any(re.search(anchor, title_lower) for anchor in tech_anchors):
                is_non_tech = True
                break
                
    if is_non_tech:
        return 'Non-tech'

    # 2. Full-stack
    fullstack_patterns = [
        r'\bfullstack\b', r'\bfull-stack\b', r'\bfull\s+stack\b', r'\bprogramadores?\s+full\b', r'\bdevelopers?\s+full\b'
    ]
    if any(re.search(p, title_lower) for p in fullstack_patterns):
        return 'Full-stack'

    # 3. Frontend
    frontend_patterns = [
        r'\bfrontend\b', r'\bfront-end\b', r'\bfront\s+end\b', r'\breact\b', r'\bangular\b',
        r'\bvue\b', r'\bjavascript\b', r'\btypescript\b', r'\bjs\b', r'\bts\b', r'\bweb\s+developers?\b'
    ]
    if any(re.search(p, title_lower) for p in frontend_patterns):
        return 'Frontend'

    # 4. Data/ML
    data_ml_patterns = [
        r'\bdata\s+scientists?\b', r'\bdata\s+science\b', r'\bdata\s+engineers?\b', r'\bmachine\s+learning\b',
        r'\bml\b', r'\bai\b', r'\bartificial\s+intelligence\b', r'\bdeep\s+learning\b', r'\bnlp\b',
        r'\bcomputer\s+vision\b', r'\bdata\s+analysts?\b', r'\banalystas?\s+de\s+dados\b', r'\bbi\b',
        r'\bbusiness\s+intelligence\b', r'\bintelligence\s+de\s+données\b', r'\bengenheiros?\s+de\s+dados\b',
        r'\bdatabase\s+administrators?\b', r'\bdbas?\b', r'\bbig\s+data\b'
    ]
    if any(re.search(p, title_lower) for p in data_ml_patterns):
        return 'Data/ML'

    # 5. DevOps/Cloud
    devops_cloud_patterns = [
        r'\bdevops\b', r'\bcloud\b', r'\bsre\b', r'\bplatform\s+engineers?\b', r'\bkubernetes\b',
        r'\bdocker\b', r'\baws\b', r'\bazure\b', r'\bgcp\b', r'\bci/cd\b', r'\bjenkins\b',
        r'\bterraform\b'
    ]
    if any(re.search(p, title_lower) for p in devops_cloud_patterns):
        return 'DevOps/Cloud'

    # 6. QA
    qa_patterns = [
        r'\bqa\b', r'\btests?\b', r'\btesting\b', r'\bsdets?\b', r'\bquality\s+assurance\b',
        r'\bquality\s+control\b', r'\bautomatizadores?\b', r'\btesters?\b', r'\btestes\b'
    ]
    if any(re.search(p, title_lower) for p in qa_patterns):
        return 'QA'

    # 7. Mobile
    mobile_patterns = [
        r'\bmobile\b', r'\bios\b', r'\bandroid\b', r'\bswift\b', r'\bkotlin\b', r'\bflutter\b',
        r'\breact\s+native\b'
    ]
    if any(re.search(p, title_lower) for p in mobile_patterns):
        return 'Mobile'

    # 8. Security
    security_patterns = [
        r'\bsecurity\b', r'\bcybersecurity\b', r'\bsegurança\b', r'\bpentests?\b', r'\binfosec\b',
        r'\bappsec\b', r'\bcyber\b', r'\bsecops\b'
    ]
    if any(re.search(p, title_lower) for p in security_patterns):
        return 'Security'

    # 9. IT/Infra
    it_infra_patterns = [
        r'\bsupport\b', r'\bhelpdesk\b', r'\bsuporte\b', r'\bit\s+support\b', r'\bit\s+administrators?\b',
        r'\bsystems?\s+administrators?\b', r'\bsysadmins?\b', r'\bnetworks?\b', r'\bredes\b', r'\binfrastructure\b',
        r'\binfraestrutura\b', r'\bsystems?\s+engineers?\b', r'\badministradores?\s+de\s+sistemas\b'
    ]
    if any(re.search(p, title_lower) for p in it_infra_patterns):
        return 'IT/Infra'

    # 10. Backend
    backend_patterns = [
        r'\bbackend\b', r'\bback-end\b', r'\bback\s+end\b', r'\bpython\b', r'\bjava\b', r'(?:^|(?<=\s))c#(?:\b|(?=\s))',
        r'\bgolang\b', r'\bphp\b', r'\brust\b', r'\bnode\b', r'\bruby\b', r'\brails\b', r'\bspring\b',
        r'\blaravel\b', r'\bdjango\b', r'(?:^|(?<=\s))\.net\b', r'\bdotnet\b', r'(?:^|(?<=\s))c\+\+(?:\b|(?=\s))'
    ]
    if any(re.search(p, title_lower) for p in backend_patterns):
        return 'Backend'

    # 11. Other-tech (fallback for remaining IT/tech titles)
    tech_keywords = [
        r'\bsoftware\b', r'\bengineers?\b', r'\bdevelopers?\b', r'\bprogramadores?\b', r'\bengenheiros?\b',
        r'\btech\b', r'\bctos?\b', r'\barchitects?\b', r'\barquiteto\b', r'\btechnology\b', r'\bit\b',
        r'\binformática\b', r'\binformatico\b', r'\bdesenvolvedores?\b', r'\bcomputadores\b'
    ]
    if any(re.search(p, title_lower) for p in tech_keywords):
        return 'Other-tech'

    # Description search as a last resort
    if desc_first_part:
        if any(re.search(p, desc_first_part) for p in fullstack_patterns):
            return 'Full-stack'
        if any(re.search(p, desc_first_part) for p in frontend_patterns):
            return 'Frontend'
        if any(re.search(p, desc_first_part) for p in backend_patterns):
            return 'Backend'
        if any(re.search(p, desc_first_part) for p in data_ml_patterns):
            return 'Data/ML'
        if any(re.search(p, desc_first_part) for p in devops_cloud_patterns):
            return 'DevOps/Cloud'
        if any(re.search(p, desc_first_part) for p in qa_patterns):
            return 'QA'
        if any(re.search(p, desc_first_part) for p in mobile_patterns):
            return 'Mobile'
        if any(re.search(p, desc_first_part) for p in security_patterns):
            return 'Security'
        if any(re.search(p, desc_first_part) for p in it_infra_patterns):
            return 'IT/Infra'
        if any(re.search(p, desc_first_part) for p in tech_keywords):
            return 'Other-tech'

    return 'Non-tech'
