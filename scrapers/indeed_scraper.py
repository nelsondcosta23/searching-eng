import time
import random
import requests
import re
import json
from datetime import datetime
from bs4 import BeautifulSoup
import os
import sys
# Playwright browser pool (Phase 4)

# Global Configurations
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
PLATAFORMA = 'Indeed PT (Selenium)'

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from automation.profile_fetcher import generate_indeed_urls, strict_keyword_match, get_user_id, get_job_titles, get_negative_keywords, get_negative_companies
    from automation.db_helper import save_job, job_exists
    PESQUISAS = generate_indeed_urls()
    USER_ID = get_user_id()
    KEYWORDS = get_job_titles()
    NEGATIVE_KEYWORDS  = get_negative_keywords()
    NEGATIVE_COMPANIES = get_negative_companies()
except ImportError as _e:
    print(f"FATAL: profile_fetcher import failed: {_e}. Aborting indeed_scraper.", file=__import__('sys').stderr)
    __import__('sys').exit(1)
    KEYWORDS = []
    NEGATIVE_KEYWORDS = []

from scrapers._shared import negative_keyword_match, new_pw_context, apply_stealth

MAX_JOBS = int(os.environ.get('MAX_JOBS_PER_PLATFORM', '0'))   # 0 = unlimited
MAX_PAGES = int(os.environ.get('INDEED_MAX_PAGES', '2'))        # Pages per search — reduced from 3; Indeed bot-detection means page 3 rarely yields new jobs
# Abort the entire scraper after this many consecutive zero-yield searches.
# Consistent zeros = bot-detection / CAPTCHA — no point burning more Selenium time.
INDEED_ZERO_ABORT = int(os.environ.get('INDEED_ZERO_ABORT', '3'))

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

def configurar_contexto():
    """Create a Playwright browser context + main page for Indeed scraping."""
    ctx = new_pw_context(block_images=True)
    page = ctx.new_page()
    apply_stealth(page)
    return ctx, page

def extrair_detalhes_vaga(ctx, link_absoluto, titulo):
    """
    Opens a job detail page in a new Playwright page and performs deep extraction.
    Returns a dict with: descricao_completa, salario, tipo_contrato, observacoes.
    """
    detalhes = {
        'descricao_completa': '',
        'salario': '',
        'tipo_contrato': '',
        'nivel_experiencia': '',
        'observacoes': '',
    }
    page = None
    try:
        page = ctx.new_page()
        apply_stealth(page)
        page.goto(link_absoluto, wait_until='domcontentloaded', timeout=30000)
        time.sleep(1.5 + random.uniform(0.5, 1.5))

        deep_soup = BeautifulSoup(page.content(), 'html.parser')

        # --- Full Job Description ---
        desc_tag = (
            deep_soup.find('div', attrs={'id': 'jobDescriptionText'}) or
            deep_soup.find(class_='jobsearch-JobComponent-description') or
            deep_soup.find('div', attrs={'data-testid': 'jobsearch-JobComponent-description'})
        )
        if desc_tag:
            detalhes['descricao_completa'] = desc_tag.get_text(separator='\n').strip()

        # --- Salary ---
        # Use specific salary test-IDs first; fall back to attribute snippets only
        # when the text actually contains a currency/numeric pattern — prevents
        # "Período Integral" (contract type chip) from being stored as salary.
        salary_tag = (
            deep_soup.find(attrs={'data-testid': 'salaryInfoAndJobType'}) or
            deep_soup.find(attrs={'data-testid': 'jobsearch-SalaryInfoSection'}) or
            deep_soup.find(class_=re.compile(r'salary-snippet|SalaryInfo|compensation', re.I))
        )
        if not salary_tag:
            for tag in deep_soup.find_all(attrs={'data-testid': 'attribute_snippet_testid'}):
                text = tag.get_text(strip=True)
                if re.search(r'[€$£₹¥]|\d[\d.,\s]*[kKmM]?\s*/\s*(ano|m[eê]s|hora|year|month|hour)', text, re.I):
                    salary_tag = tag
                    break
        if salary_tag:
            salary_text = salary_tag.get_text(strip=True)
            if salary_text and re.search(r'[€$£₹¥]|\d[\d.,\s]+', salary_text):
                detalhes['salario'] = salary_text

        # --- Job Type & Observations (chips/attributes) ---
        obs_list = []
        attribute_items = deep_soup.find_all(attrs={'data-testid': re.compile(r'attribute|jobType|workType', re.I)})
        for item in attribute_items:
            t = item.get_text(strip=True)
            if t and len(t) < 80:
                obs_list.append(t)

        # Also try the "job details" chips
        detail_chips = deep_soup.find_all(class_=re.compile(r'jobDetail|JobDetailsTable|metadata', re.I))
        for chip in detail_chips:
            t = chip.get_text(strip=True)
            if t and len(t) < 80 and t not in obs_list:
                obs_list.append(t)

        if obs_list:
            detalhes['observacoes'] = " | ".join(obs_list[:8])

        # Determine contract type and experience level from observations
        for obs in obs_list:
            obs_lower = obs.lower()
            if 'full-time' in obs_lower or 'permanente' in obs_lower or 'efetivo' in obs_lower:
                detalhes['tipo_contrato'] = obs
            elif 'part-time' in obs_lower or 'parcial' in obs_lower:
                detalhes['tipo_contrato'] = obs
            if any(kw in obs_lower for kw in ('senior', 'sénior', 'junior', 'júnior', 'mid-level', 'entry', 'lead', 'manager', 'director')):
                if not detalhes['nivel_experiencia']:
                    detalhes['nivel_experiencia'] = obs

    except Exception as e:
        print(f"  [Deep Extraction] Error for '{titulo}': {e}")
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass

    return detalhes

def processar_uma_pesquisa(ctx, main_page, categoria_nome, url_info, vagas_ja_inseridas=0):
    print(f"\n[Indeed] Search: {categoria_nome}")

    url_base = url_info['url'] if isinstance(url_info, dict) else url_info
    novas_vagas_cont = 0

    for page_num in range(MAX_PAGES):
        offset = page_num * 10
        url_pagina = f"{url_base}&start={offset}"

        print(f"  → Page {page_num + 1}/{MAX_PAGES} (offset={offset})")

        try:
            main_page.goto(url_pagina, wait_until='domcontentloaded', timeout=30000)
            time.sleep(random.uniform(4.0, 7.0))

            # --- Wait for job cards ---
            cards_found = False
            for selector in ['.jcs-JobTitle', '.cardOutline', '[data-testid="jobCard"]']:
                try:
                    main_page.wait_for_selector(selector, timeout=10000)
                    cards_found = True
                    break
                except Exception:
                    continue

            if not cards_found:
                print(f"  → No job cards detected on page {page_num + 1}. Stopping pagination.")
                break

            soup = BeautifulSoup(main_page.content(), 'html.parser')
            
            # --- Extract Job Cards ---
            # Try to find the common parent of job title and company
            vagas_html = (
                soup.find_all(class_='cardOutline') or 
                soup.find_all(attrs={'data-testid': 'jobCard'}) or
                soup.find_all(class_=re.compile(r'job_seen_beacon|result', re.I))
            )

            if not vagas_html:
                # Last resort: just find all job title links and walk up to a container
                title_links = soup.find_all(class_='jcs-JobTitle')
                vagas_html = [link.find_parent(['div', 'li', 'td']) for link in title_links if link.find_parent(['div', 'li', 'td'])]

            if not vagas_html:
                print(f"  → Empty page {page_num + 1}. Done.")
                break

            print(f"  → Found {len(vagas_html)} potential job cards on page {page_num + 1}.")

            for vaga in vagas_html:
                try:
                    link_tag = vaga.find(attrs={'data-jk': True})
                    if not link_tag:
                        continue

                    titulo = link_tag.get_text().strip()
                    if not strict_keyword_match(titulo, KEYWORDS):
                        continue

                    blocked_kw = negative_keyword_match(titulo, NEGATIVE_KEYWORDS)
                    if blocked_kw:
                        print(f"  [BLOCKED] '{titulo}' contains a negative keyword ({blocked_kw}).")
                        continue

                    link_relativo = link_tag.get('href', '')
                    link_absoluto = link_relativo if link_relativo.startswith('http') else f"https://pt.indeed.com{link_relativo}"

                    empresa = "Not specified"
                    empresa_tag = vaga.find(attrs={'data-testid': 'company-name'})
                    if empresa_tag:
                        empresa = empresa_tag.get_text().strip()

                    localizacao = "Not specified"
                    localizacao_tag = vaga.find(attrs={'data-testid': 'text-location'})
                    if localizacao_tag:
                        localizacao = localizacao_tag.get_text().strip()

                    data_pub = "Recent"
                    # Try multiple selectors — Indeed migrates class names frequently
                    data_pub_tag = (
                        vaga.find(attrs={'data-testid': 'myJobsStateDate'}) or
                        vaga.find(class_='date') or
                        vaga.find('span', class_=re.compile(r'date|posted', re.I))
                    )
                    if data_pub_tag:
                        data_pub = data_pub_tag.get_text().replace('Posted', '').strip()

                    # Salary preview from listing card
                    salario_preview = ""
                    salary_card_tag = vaga.find(attrs={'data-testid': 'attribute_snippet_testid'})
                    if salary_card_tag:
                        salario_preview = salary_card_tag.get_text(strip=True)

                    id_externo = link_tag.get('data-jk')

                    if NEGATIVE_COMPANIES and any(nc in empresa.lower() for nc in NEGATIVE_COMPANIES):
                        continue

                    if not job_exists(link_absoluto):
                        detalhes = extrair_detalhes_vaga(ctx, link_absoluto, titulo)

                        # Prefer deep-extracted salary, fallback to card preview
                        salario_final = detalhes['salario'] or salario_preview

                        if save_job(
                            user_id=USER_ID, plataforma=PLATAFORMA, id_externo=id_externo,
                            titulo=titulo, empresa=empresa, localizacao=localizacao,
                            link=link_absoluto, data_pub=data_pub, categoria=categoria_nome,
                            descricao_completa=detalhes['descricao_completa'],
                            observacoes=detalhes['observacoes'],
                            salario=salario_final or None,
                            tipo_contrato=detalhes.get('tipo_contrato') or None,
                            nivel_experiencia=detalhes.get('nivel_experiencia') or None,
                        ):
                            novas_vagas_cont += 1
                            total_agora = vagas_ja_inseridas + novas_vagas_cont
                            print(f"    ✅ Saved: {titulo} @ {empresa} [{total_agora} total]")
                            if MAX_JOBS > 0 and total_agora >= MAX_JOBS:
                                print(f"  [LIMIT REACHED] Max {MAX_JOBS} jobs. Stopping.")
                                return novas_vagas_cont, ctx, main_page
                except Exception:
                    continue

            # Gentle inter-page delay
            if page_num < MAX_PAGES - 1:
                time.sleep(random.uniform(4.0, 7.0))

        except Exception as e:
            print(f"  [Indeed] Error on page {page_num + 1} for '{categoria_nome}': {e}")
            # Recovery: close current context and create a fresh one
            try:
                ctx.close()
            except Exception:
                pass
            ctx, main_page = configurar_contexto()
            try:
                main_page.goto("https://pt.indeed.com/", wait_until='domcontentloaded', timeout=20000)
                time.sleep(random.uniform(3.0, 5.0))
            except Exception:
                pass
            break

    print(f"  → Finished '{categoria_nome}': {novas_vagas_cont} new jobs indexed.")
    return novas_vagas_cont, ctx, main_page

def iniciar_scraper_indeed():
    print(f"\n{'='*50}")
    print(f"  Indeed PT Scraper: QUEBRADO NA FONTE (Broken at source)")
    print("  Reason: Playwright headless execution is blocked by Indeed security/Cloudflare checks.")
    print("  Exiting early to prevent IP ban risks.")
    print(f"{'='*50}")
    return

if __name__ == '__main__':
    iniciar_scraper_indeed()