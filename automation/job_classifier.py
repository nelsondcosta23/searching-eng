import re

# Token sets for each tech category
NON_TECH_TOKENS = {
    'marketing', 'sales', 'hr', 'recruiter', 'recruiters', 'cook', 'cooks', 'driver', 'drivers',
    'doctor', 'doctors', 'nurse', 'nurses', 'waiter', 'waiters', 'accountant', 'accountants',
    'lawyer', 'lawyers', 'designer', 'designers', 'productmanager', 'productmanagers',
    'scrummaster', 'scrummasters', 'agilecoach', 'agilecoaches', 'vendedor', 'vendedores',
    'comercial', 'comerciais', 'contabilista', 'contabilistas', 'advogado', 'advogados',
    'rececionista', 'rececionistas'
}

TECH_ANCHOR_TOKENS = {
    'developer', 'developers', 'engineer', 'engineers', 'programador', 'programadores',
    'engenheiro', 'engenheiros', 'software', 'tech'
}

FULLSTACK_TOKENS = {'fullstack'}

FRONTEND_TOKENS = {
    'frontend', 'react', 'angular', 'vue', 'javascript', 'typescript', 'js', 'ts', 'webdeveloper'
}

DATA_ML_TOKENS = {
    'datascientist', 'datascience', 'dataengineer', 'bigdataengineer', 'machinelearning', 'ml', 'ai',
    'artificialintelligence', 'deeplearning', 'nlp', 'computervision', 'dataanalyst',
    'bi', 'dba', 'bigdata'
}

# Tokens that represent an explicit ROLE label in the title (not just a tech stack).
# When a title contains one of these, it takes priority over infra-tech (AWS/Azure/GCP etc.)
# so that 'Backend Engineer (AWS)' classifies as Backend, not DevOps/Cloud.
ROLE_OVERRIDE_TOKENS = {
    'backend', 'frontend', 'fullstack', 'webdeveloper', 'mobile', 'ios', 'android',
    'datascientist', 'datascience', 'dataengineer', 'bigdataengineer', 'dataanalyst',
    'bi', 'dba', 'bigdata', 'qa', 'sdet', 'tester', 'testers', 'testing',
    'security', 'cybersecurity', 'pentest', 'infosec', 'appsec', 'secops',
    'support', 'helpdesk', 'suporte', 'itsupport', 'itadmin', 'sysadmin', 'sysadmins',
    'network', 'networks', 'redes', 'infrastructure', 'infraestrutura', 'systemsengineer',
}

# Infra-tech tokens that should NOT override an explicit role label.
# i.e. these are cloud/infra technologies used by many roles, not role labels themselves.
INFRA_TECH_ONLY_TOKENS = {'aws', 'azure', 'gcp', 'kubernetes', 'docker', 'terraform', 'jenkins', 'cicd'}

DEVOPS_CLOUD_TOKENS = {
    'devops', 'cloud', 'sre', 'platformengineer', 'kubernetes', 'docker', 'aws',
    'azure', 'gcp', 'cicd', 'jenkins', 'terraform'
}

QA_TOKENS = {
    'qa', 'test', 'tests', 'testing', 'sdet', 'sdets', 'qc', 'automatizador',
    'automatizadores', 'tester', 'testers', 'testes'
}

MOBILE_TOKENS = {
    'mobile', 'ios', 'android', 'swift', 'kotlin', 'flutter', 'reactnative', 'objectivec'
}

SECURITY_TOKENS = {
    'security', 'cybersecurity', 'segurança', 'pentest', 'pentests', 'infosec', 'appsec',
    'cyber', 'secops'
}

IT_INFRA_TOKENS = {
    'support', 'helpdesk', 'suporte', 'itsupport', 'itadmin', 'sysadmin', 'sysadmins',
    'network', 'networks', 'redes', 'infrastructure', 'infraestrutura', 'systemsengineer'
}

BACKEND_TOKENS = {
    'backend', 'python', 'java', 'csharp', 'golang', 'go', 'php', 'rust', 'node', 'nodejs',
    'ruby', 'rails', 'spring', 'laravel', 'django', 'dotnet', 'cpp', 'c', 'r'
}

OTHER_TECH_TOKENS = {
    'software', 'engineer', 'engineers', 'developer', 'developers', 'programador',
    'programadores', 'engenheiro', 'engenheiros', 'tech', 'cto', 'ctos', 'architect',
    'architects', 'arquiteto', 'technology', 'it', 'informática', 'informatico',
    'desenvolvedor', 'desenvolvedores', 'computadores'
}

def normalize_and_tokenize(text: str) -> list[str]:
    if not text:
        return []
    
    text = text.lower()
    
    replacements = {
        # Combinations first to avoid partial replacement issues
        "c/c++": "c cpp",
        "c / c++": "c cpp",
        "c/c#": "c csharp",
        "c / c#": "c csharp",
        "c++": "cpp",
        "c#": "csharp",
        
        # Abbreviation "c/" in Portuguese (meaning "with")
        " c/": " com ",
        " c /": " com ",
        
        # Full-stack
        "programadores full": "fullstack",
        "programador full": "fullstack",
        "developers full": "fullstack",
        "developer full": "fullstack",
        "full-stack": "fullstack",
        "full stack": "fullstack",
        
        # Frontend
        "front-end": "frontend",
        "front end": "frontend",
        "web developers": "webdeveloper",
        "web developer": "webdeveloper",
        
        # Backend
        "back-end": "backend",
        "back end": "backend",
        
        # Mobile
        "react native": "reactnative",
        "react-native": "reactnative",
        "objective-c": "objectivec",
        "objective c": "objectivec",
        
        # Data / ML
        # NOTE: compound aliases that START with 'big data' MUST come before the plain
        # 'data engineer' alias, otherwise str.replace processes 'data engineer' first and
        # turns 'big data engineer' into 'big dataengineer', making 'big data' unreachable.
        # Likewise, ALL 'big data X' compounds must come before 'big data' itself to avoid
        # 'big data analyst' collapsing into garbage token 'bigdataanalyst'.
        "big data engineers": "bigdataengineer",
        "big data engineer": "bigdataengineer",
        "big data analysts": "bigdata",
        "big data analyst": "bigdata",
        "big data scientists": "bigdata",
        "big data scientist": "bigdata",
        "big data developers": "bigdata",
        "big data developer": "bigdata",
        "big data": "bigdata",
        "data scientists": "datascientist",
        "data scientist": "datascientist",
        "data science": "datascience",
        "data engineers": "dataengineer",
        "data engineer": "dataengineer",
        "machine learning": "machinelearning",
        "artificial intelligence": "artificialintelligence",
        "deep learning": "deeplearning",
        "computer vision": "computervision",
        "data analysts": "dataanalyst",
        "data analyst": "dataanalyst",
        "analystas de dados": "dataanalyst",
        "analysta de dados": "dataanalyst",
        "analistas de dados": "dataanalyst",
        "analista de dados": "dataanalyst",
        "business intelligence": "bi",
        "intelligence de données": "datascience",
        "engenheiros de dados": "dataengineer",
        "engenheiro de dados": "dataengineer",
        "database administrators": "dba",
        "database administrator": "dba",
        
        # DevOps / Cloud
        "platform engineers": "platformengineer",
        "platform engineer": "platformengineer",
        "ci/cd": "cicd",
        "ci-cd": "cicd",
        
        # QA
        "quality assurance": "qa",
        "quality control": "qc",
        
        # IT / Infra
        "it support": "itsupport",
        "it administrators": "itadmin",
        "it administrator": "itadmin",
        "systems administrators": "sysadmin",
        "systems administrator": "sysadmin",
        "system administrators": "sysadmin",
        "system administrator": "sysadmin",
        "systems engineers": "systemsengineer",
        "systems engineer": "systemsengineer",
        "system engineers": "systemsengineer",
        "system engineer": "systemsengineer",
        "administradores de sistemas": "sysadmin",
        "administrador de sistemas": "sysadmin",
        
        # Specific languages/runtimes
        "node.js": "nodejs",
        "node-js": "nodejs",
        "node js": "nodejs",
        "nodejs": "nodejs",
        ".net": "dotnet",
        
        # Go-to-market & R&D
        "go-to-market": "gotomarket",
        "go to market": "gotomarket",
        "r & d": "rnd",
        "r&d": "rnd",
    }
    
    for phrase, replacement in replacements.items():
        text = text.replace(phrase, replacement)
        
    text = re.sub(r'[^\w\s]|_', ' ', text)
    return text.split()

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
    title_tokens = normalize_and_tokenize(title)
    title_token_set = set(title_tokens)
    
    # 1. Non-tech Filter (skip if title matches non-tech roles, unless it explicitly contains tech words)
    if any(tok in NON_TECH_TOKENS for tok in title_tokens):
        if not any(tok in TECH_ANCHOR_TOKENS for tok in title_tokens):
            return 'Non-tech'
            
    # 2. Full-stack
    if any(tok in FULLSTACK_TOKENS for tok in title_token_set):
        return 'Full-stack'

    # 3. Frontend
    if any(tok in FRONTEND_TOKENS for tok in title_token_set):
        return 'Frontend'

    # 4. Data/ML
    if any(tok in DATA_ML_TOKENS for tok in title_token_set):
        return 'Data/ML'

    # 5. DevOps/Cloud — but only if there's no explicit role label in the title that
    #    merely *uses* an infra technology. Rule: if every DevOps/Cloud match is an
    #    infra-tech-only token (aws/azure/gcp/k8s/docker/terraform/jenkins) AND the
    #    title contains a role-override token, skip DevOps/Cloud here and let the role
    #    category win further down.
    devops_hits = title_token_set & DEVOPS_CLOUD_TOKENS
    if devops_hits:
        infra_tech_hits = devops_hits & INFRA_TECH_ONLY_TOKENS
        has_role_label = bool(title_token_set & ROLE_OVERRIDE_TOKENS)
        # Only classify as DevOps/Cloud if there are DevOps *role* tokens (sre, devops,
        # platformengineer, cloud) OR there is no competing role label
        role_devops_tokens = devops_hits - INFRA_TECH_ONLY_TOKENS  # e.g. devops, sre, cloud
        if role_devops_tokens or not has_role_label:
            return 'DevOps/Cloud'

    # 6. QA
    if any(tok in QA_TOKENS for tok in title_token_set):
        return 'QA'

    # 7. Mobile
    if any(tok in MOBILE_TOKENS for tok in title_token_set):
        return 'Mobile'

    # 8. Security
    if any(tok in SECURITY_TOKENS for tok in title_token_set):
        return 'Security'

    # 9. IT/Infra
    if any(tok in IT_INFRA_TOKENS for tok in title_token_set):
        return 'IT/Infra'

    # 10. Backend
    if any(tok in BACKEND_TOKENS for tok in title_token_set):
        return 'Backend'

    # 11. Other-tech (fallback for remaining IT/tech titles)
    if any(tok in OTHER_TECH_TOKENS for tok in title_token_set):
        return 'Other-tech'

    # Description search as a last resort
    if description:
        desc_first_part = description.lower()[:300]
        desc_tokens = set(normalize_and_tokenize(desc_first_part))
        
        if any(tok in FULLSTACK_TOKENS for tok in desc_tokens):
            return 'Full-stack'
        if any(tok in FRONTEND_TOKENS for tok in desc_tokens):
            return 'Frontend'
        # Exclude highly ambiguous single-letter/general tokens (go, c, r) from description checks
        if any(tok in BACKEND_TOKENS and tok not in {'go', 'c', 'r'} for tok in desc_tokens):
            return 'Backend'
        if any(tok in DATA_ML_TOKENS for tok in desc_tokens):
            return 'Data/ML'
        if any(tok in DEVOPS_CLOUD_TOKENS for tok in desc_tokens):
            return 'DevOps/Cloud'
        if any(tok in QA_TOKENS for tok in desc_tokens):
            return 'QA'
        if any(tok in MOBILE_TOKENS for tok in desc_tokens):
            return 'Mobile'
        if any(tok in SECURITY_TOKENS for tok in desc_tokens):
            return 'Security'
        if any(tok in IT_INFRA_TOKENS for tok in desc_tokens):
            return 'IT/Infra'
        if any(tok in OTHER_TECH_TOKENS for tok in desc_tokens):
            return 'Other-tech'

    return 'Non-tech'
