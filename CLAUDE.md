# CLAUDE.md — Project Guide

Ferramenta local de inteligência de mercado de trabalho de tecnologia (PT + internacional). Stack: Python 3.12 · Streamlit · SQLite (WAL) · Playwright.

## Big picture

A aplicação é executada localmente on-demand.
Os componentes principais são executados através da Streamlit app ou via scripts Python no terminal.

## Componentes-chave

- [automation/orchestrator.py](automation/orchestrator.py) — coordena scrapers e pós-processamento sequencial (scrape → classify → enrich → analyze).
- [automation/db_helper.py](automation/db_helper.py) — única porta de escrita de jobs. Tem retry com backoff para concorrência de SQLite.
- [automation/profile_fetcher.py](automation/profile_fetcher.py) — lê preferências e keywords de [config/tech_profile.json](config/tech_profile.json).
- [automation/job_classifier.py](automation/job_classifier.py) — classificador de IT robusto baseado em tokenização e normalização de aliases de tecnologia.
- [automation/company_enricher.py](automation/company_enricher.py) — consulta Wikidata para buscar o ano de fundação de empresas, verificando contra whitelist de organização/empresa (P31).
- [automation/job_analytics.py](automation/job_analytics.py) — gera estatísticas de idade de vagas e rankings de contratação.
- [app/job_dashboard.py](app/job_dashboard.py) — interface Streamlit para visualizar e filtrar vagas, ver relatórios de mercado, e iniciar scrapers on-demand.

## Scrapers (`scrapers/`)

- [scrapers/itjobs_scraper.py](scrapers/itjobs_scraper.py) — usa a API oficial JSON de itjobs.pt (auth via `ITJOBS_API_KEY`).
- [scrapers/companies_scraper.py](scrapers/companies_scraper.py) — scraper unificado para portais diretos das empresas (Ashby, Lever, Greenhouse, HTML).
- [scrapers/linkedin_scraper.py](scrapers/linkedin_scraper.py) — Guest API para listing + Playwright para deep extraction.
- [scrapers/sapo_scraper.py](scrapers/sapo_scraper.py) — extração via Playwright.
- [scrapers/expresso_scraper.py](scrapers/expresso_scraper.py) — Playwright com URL `/emprego/pesquisa/` (bypassa WAF).
- [scrapers/indeed_scraper.py](scrapers/indeed_scraper.py) — marcado como quebrado na fonte devido ao bloqueio de headless.
- [scrapers/landing_scraper.py](scrapers/landing_scraper.py) — usa API JSON pública.

## Convenções importantes

- **Línguas mistas**: banco de dados em português (`vagas`, `titulo`, `empresa`, `localizacao`); código/comentários em inglês.
- **SQLite WAL + retry**: conexões usam WAL. Para escritas, use o helper com retry em `db_helper.py`.
- **Logs**: capturados e impressos no stdout.

## Comandos comuns

```bash
# Executar a Streamlit Dashboard
streamlit run app/job_dashboard.py

# Rodar todos os scrapers e pós-processamento
python automation/orchestrator.py

# Rodar scraper específico (ex: itjobs)
python automation/orchestrator.py --scrapers itjobs

# Executar testes unitários
python -m pytest tests/unit/
```
