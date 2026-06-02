# scrapper_tcc

Automated job scraping platform for Portugal and remote-EMEA roles. Runs daily, multi-user, self-contained via Docker Compose.

![Python](https://img.shields.io/badge/Python-3.9-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green) ![SQLite](https://img.shields.io/badge/SQLite-WAL-lightgrey) ![Docker](https://img.shields.io/badge/Docker-Compose-2496ED)

---

## What it does

Every day at 00:00 the platform runs a full scrape across 7 sources, scores every job against each user's profile, verifies expired links, and pushes the day's top results to each user's webhook endpoint.

**Sources:**
- LinkedIn PT — Guest API for listings + Selenium for job detail pages
- ITJobs — Official JSON API (no Selenium)
- Companies — Direct ATS APIs: Greenhouse, Lever, Ashby (85+ companies, no Selenium)
- Sapo Jobs — HTTP + Selenium for deep extraction
- Expresso Jobs — Selenium with direct URL navigation
- Indeed PT — Selenium (bot-detection aware, aborts early if blocked)
- Landing.jobs — Public JSON API (no Selenium)

**Per-user pipeline:**
1. Tiered scraping: runs top-quality sources first, skips lower tiers if enough jobs found
2. TF-IDF scorer (0–100): weights title×4, metadata×2, description×1 + salary/seniority modifiers
3. Negative keyword and negative company filtering
4. Webhook dispatch: POSTs top-5 jobs to `callback_url` with `X-Webhook-Secret`

---

## Architecture

5 Docker services:

| Service | Port | Role |
|---|---|---|
| `python_scraper` | — | Daily worker (cron: scraping, scoring, verification, email) |
| `job_api` | 8080 | FastAPI REST — profile sync + job queries for external apps |
| `streamlit_app` | 8501 | Internal dashboard for monitoring and filtering |
| `cloudflare_tunnel` | — | Exposes `job_api` on a public `*.trycloudflare.com` URL |
| `tunnel_notifier` | — | Watches tunnel URL changes and notifies external software |

Cron schedule (UTC):

| Time | Task |
|---|---|
| 00:00 | Full scrape + score + webhook |
| 13:15 | Daily email summary (Resend) |
| 21:00 | Expired link verification |
| Sun 03:00 | DB cleanup (jobs older than `DIAS_RETENCAO` days, default 45) |

---

## Quick start

```bash
cp .env.example .env
# Fill in: API_KEY, RESEND_API_KEY, ITJOBS_API_KEY, EXTERNAL_WEBHOOK_SECRET

docker-compose up -d --build
```

- Dashboard: http://localhost:8501
- API docs: http://localhost:8080/docs

**Manual scrape (Windows):**
```bat
Procurar_Vagas_Agora.bat
```

**Manual scrape (any OS):**
```bash
docker exec python_scraper python /app/automation/orchestrator.py
```

**Force rescore after profile update:**
```bash
docker exec python_scraper python /app/automation/job_scorer.py --rescore-all
```

---

## Key environment variables

| Variable | Required | Description |
|---|---|---|
| `API_KEY` | Yes | Bearer token for REST API auth |
| `RESEND_API_KEY` | Yes | Resend.com API key for daily email |
| `ITJOBS_API_KEY` | Yes | ITJobs public API key |
| `EXTERNAL_WEBHOOK_SECRET` | Yes | Header secret sent in webhook `X-Webhook-Secret` |
| `OWNER_USER_ID` | Recommended | Restricts public GET endpoints to this user's data |
| `MAX_JOBS_PER_PLATFORM` | No | Cap new saves per source per run (0 = unlimited) |
| `MIN_TIER_YIELD` | No | New jobs required from Tier 1 before skipping Tier 2+3 (default 5) |
| `UVICORN_WORKERS` | No | API worker count (default 2) |

---

## External software integration

See [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md) for:
- How to sync a user profile (`POST /api/v1/users/sync`)
- Full payload reference (job titles, keywords, negative filters, callback URL)
- Webhook payload format

---

## Project layout

```
scrapper_tcc/
├── api/                    FastAPI app (main.py)
├── app/                    Streamlit dashboard
├── automation/             Core logic
│   ├── orchestrator.py     Multi-user tiered scrape runner
│   ├── job_scorer.py       TF-IDF relevance scorer (0–100)
│   ├── job_verifier.py     Expired link checker
│   ├── webhook_dispatcher.py
│   ├── profile_fetcher.py  Profile loading (Supabase API + local SQLite fallback)
│   ├── send_email.py
│   ├── db_helper.py        SQLite WAL + retry helper (single write path)
│   └── tunnel_update_notifier.py
├── scrapers/
│   ├── _shared.py          Shared helpers (strip_html, chrome version, keyword match)
│   ├── linkedin_scraper.py
│   ├── itjobs_scraper.py
│   ├── companies_scraper.py
│   ├── sapo_scraper.py
│   ├── expresso_scraper.py
│   ├── indeed_scraper.py
│   └── landing_scraper.py
├── config/
│   ├── companies.json      85+ companies with ATS board config
│   └── crontab
├── init_db.py              Idempotent DB migrations (Schema v6)
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## Schema

SQLite (WAL mode), Schema v6. Two main tables:

- `vagas` — jobs: `titulo`, `empresa`, `plataforma`, `link`, `relevance_score`, `status`, `salario`, `tipo_contrato`, `nivel_experiencia`
- `users_perfil` — per-user config: `job_titles`, `keywords`, `negative_keywords`, `negative_companies`, `locations`, `callback_url`, `search_description`, `min_salary`, `experience_levels`

All schema migrations are inline in `init_db.py` (idempotent, runs on container start).
