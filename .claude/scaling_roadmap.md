# Scaling Roadmap — searching-eng

Target: 1000 users. Current: ~20 users, single-machine, single SQLite, sequential scraping.

**Architecture decision — PocketBase:**
PocketBase replaces the `job_api` FastAPI service and `users_perfil` layer.
It runs as a lightweight Go binary (single file, zero dependencies) with SQLite underneath.
It gives us: auth, user management, real-time subscriptions, admin UI, REST API.

The internal scraper database stays as SQLite (write-heavy, isolated, no external access).
After scraping + scoring, top results are pushed to PocketBase via its REST API.
External software talks only to PocketBase — never to the scraper DB directly.

```
External Software  →  [PocketBase :8090]  ←  Streamlit Dashboard
                           ↑ push top jobs
                    [Post-processing]
                           ↑
                    [Internal SQLite]  (scraper write layer)
                           ↑
                    [Celery Workers]   (one task per user)
                           ↑
                    [Redis Queue]      (task broker)
                           ↑
                    [Cron / API]       (enqueues tasks)
```

---

## Phase 1 — Task Queue (Celery + Redis)
**Goal:** Stop running all users sequentially. Each user becomes an independent task.
**Impact:** 50 users done in parallel instead of 50 × 20min = 16h sequential.
**Files:** docker-compose.yml, requirements.txt, automation/tasks.py (new), automation/worker.py (new), automation/orchestrator.py

### Tasks

| # | Task | File | Status |
|---|---|---|---|
| TQ-1 | Add Redis service to docker-compose.yml | docker-compose.yml | ✅ |
| TQ-2 | Add Celery worker services to docker-compose.yml (selenium + api workers) | docker-compose.yml | ✅ |
| TQ-3 | Add `celery`, `redis`, `flower` to requirements.txt | requirements.txt | ✅ |
| TQ-4 | Create `automation/celery_app.py` — Celery app config + rate limiting per platform | automation/celery_app.py | ✅ |
| TQ-5 | Create `automation/tasks.py` — `scrape_user_task`, `dispatch_all_users`, `run_global_post_processing` | automation/tasks.py | ✅ |
| TQ-6 | Modify `orchestrator.py` — auto-detect Redis, dispatch tasks when available, sequential fallback | automation/orchestrator.py | ✅ |
| TQ-7 | Platform-level rate limits in celery_app.py (LinkedIn/Indeed 3/min, Sapo 5/min) | automation/celery_app.py | ✅ |
| TQ-8 | Flower monitoring UI on :5555 in docker-compose (profile: celery) | docker-compose.yml | ✅ |

**To activate Celery mode:**
```bash
# Add to .env:
REDIS_URL=redis://redis:6379/0

# Start Redis + workers + Flower:
docker-compose --profile celery up -d redis celery_selenium celery_api flower

# Monitor at http://localhost:5555
```

---

## Phase 2 — PocketBase Integration
**Goal:** Replace FastAPI `job_api` + `users_perfil` SQLite table with PocketBase.
**Impact:** Auth, user management, real-time dashboard, admin UI — all free.

### Tasks

| # | Task | File | Status |
|---|---|---|---|
| PB-1 | Add PocketBase service to docker-compose.yml + Dockerfile.pocketbase | docker-compose.yml | ✅ |
| PB-2 | Design PocketBase collections: `user_profiles`, `job_results` | automation/pb_client.py | ✅ |
| PB-3 | Create `automation/pb_client.py` — REST client (admin auth, upsert profiles, push jobs) | automation/pb_client.py | ✅ |
| PB-4 | Modify `webhook_dispatcher.py` — push top jobs to PocketBase `job_results` for ALL users | automation/webhook_dispatcher.py | ✅ |
| PB-5 | Modify `profile_fetcher.py` — PocketBase replaces Supabase Edge Function as primary source | automation/profile_fetcher.py | ✅ |
| PB-6 | Update `api/main.py` — mirror profile to PocketBase on POST /users/sync (non-fatal) | api/main.py | ✅ |
| PB-7 | Update `app/job_dashboard.py` — optional PocketBase source via `DASHBOARD_DATA_SOURCE=pocketbase`; auto-fallback to SQLite when PB unreachable | app/job_dashboard.py | ✅ |
| PB-8 | Create `automation/pb_setup.py` — migrate existing `users_perfil` rows to PocketBase | automation/pb_setup.py | ✅ |
| PB-9 | Remove `job_api` FastAPI service — **permanently deferred**: rate limiting, SSRF, audit_log, Prometheus /metrics, multi-key auth and complex JOINs cannot be replicated in PocketBase. Architecture decision: PocketBase = profile/results store; job_api = hardened external REST API. | docker-compose.yml | ❌ N/A |

**First-run instructions:**
```bash
# 1. Add to .env:
PB_URL=http://pocketbase:8090
PB_ADMIN_EMAIL=admin@searching-eng.local
PB_ADMIN_PASSWORD=<strong-password>

# 2. Start PocketBase:
docker-compose up -d pocketbase

# 3. Open admin UI and create admin account:
#    http://localhost:8090/_/

# 4. Migrate existing users:
docker exec python_scraper python /app/automation/pb_setup.py
```

---

## Phase 3 — Global Job Deduplication
**Goal:** Stop storing the same job N times (once per user). One global record, N user associations.
**Impact:** 1000× reduction in storage; scraper skips already-known jobs immediately.

### New schema

```sql
-- Global job registry (one row per unique job URL)
jobs_global (
  id          TEXT PRIMARY KEY,   -- hash(link)
  link        TEXT UNIQUE,
  titulo      TEXT,
  empresa     TEXT,
  plataforma  TEXT,
  localizacao TEXT,
  descricao   TEXT,
  salario     TEXT,
  tipo_contrato TEXT,
  data_publicacao TEXT,
  data_scraped TEXT,
  status      TEXT DEFAULT 'Ativa'
)

-- Per-user association + score
jobs_users (
  job_id      TEXT REFERENCES jobs_global(id),
  user_id     TEXT,
  relevance_score INTEGER,
  nivel_experiencia TEXT,
  viewed      BOOLEAN DEFAULT false,
  applied     BOOLEAN DEFAULT false,
  PRIMARY KEY (job_id, user_id)
)
```

### Tasks

| # | Task | File | Status |
|---|---|---|---|
| GD-1 | Design new schema (jobs_global + jobs_users) | init_db.py | ✅ |
| GD-2 | Add `save_job_global()` to db_helper.py — check global first, then insert+associate | automation/db_helper.py | ✅ |
| GD-3 | Modify `job_exists()` — check global table by link | automation/db_helper.py | ✅ |
| GD-4 | Update scorer — scores go to jobs_users, not vagas | automation/job_scorer.py | ✅ |
| GD-5 | Update all scrapers — `save_job()` is now a thin wrapper over `save_job_global()` | all scrapers | ✅ |
| GD-6 | Update API queries — JOIN jobs_global + jobs_users | api/main.py | ✅ |
| GD-7 | Update dashboard — read from new schema | app/job_dashboard.py | ✅ |
| GD-8 | Migration script — convert existing `vagas` to new tables | migration_v7.py | ✅ |

---

## Phase 4 — Browser Pool
**Goal:** Replace spawn-Chrome-per-scraper with shared pool of browser instances.
**Impact:** Faster startup, lower memory, reusable sessions (better anti-detection).

### Option A — Browserless (SaaS or self-hosted)
- Docker image: `ghcr.io/browserless/chrome`
- Scrapers connect via WebSocket CDP instead of launching Chrome locally
- Zero ChromeDriver management, auto-rotation, built-in stealth

### Option B — Playwright pool (self-hosted)
- Replace undetected-chromedriver with `playwright-python`
- `BrowserContext` pool managed in `scrapers/_shared.py`
- Better headless mode, faster, built-in network interception

### Tasks

| # | Task | File | Status |
|---|---|---|---|
| BP-1 | Chose Playwright over Browserless — self-hosted, no extra Docker service | — | ✅ |
| BP-2 | No extra Docker service needed — Playwright runs inside existing container | docker-compose.yml | ✅ |
| BP-3 | Add `get_pw_browser()`, `new_pw_context()`, `apply_stealth()` pool to `_shared.py` | scrapers/_shared.py | ✅ |
| BP-4 | Migrate LinkedIn scraper to Playwright (pool context, no temp dirs) | scrapers/linkedin_scraper.py | ✅ |
| BP-5 | Migrate Indeed scraper to Playwright | scrapers/indeed_scraper.py | ✅ |
| BP-6 | Migrate Sapo scraper to Playwright | scrapers/sapo_scraper.py | ✅ |
| BP-7 | Migrate Expresso scraper to Playwright | scrapers/expresso_scraper.py | ✅ |
| BP-8 | `undetected-chromedriver` kept in requirements.txt for rollback via selenium_backup/ | requirements.txt | ⏭️ deferred |

**Selenium backup:** `scrapers/selenium_backup/` — original files before migration.
**Rollback:** copy files back from `selenium_backup/` and revert `requirements.txt`.
**First run after rebuild:** `docker-compose up -d --build` (triggers `playwright install chromium`).

---

## Phase 5 — Observability
**Goal:** Replace `print()` logs with structured logging + alerts on failures.

| # | Task | File | Status |
|---|---|---|---|
| OB-1 | Add Sentry SDK — capture exceptions from scrapers and API | automation/monitoring.py | ✅ |
| OB-2 | Add structured logging — JSON lines via custom _Logger (cron-compatible, no logging module) | automation/monitoring.py | ✅ |
| OB-3 | Prometheus metrics endpoint on job_api — /metrics, jobs scraped, active, duration, API counters | api/main.py, automation/monitoring.py | ✅ |
| OB-4 | Grafana dashboard — scraper health, job yield, API requests | config/grafana/, config/prometheus.yml | ✅ |
| OB-5 | Celery Flower — real-time task monitoring UI on :5555 | docker-compose.yml | ✅ |

---

## Phase 6 — Security & Quality (Phases A–F)
**Goal:** Harden the API, add observability, remove Supabase, migrate to PocketBase primary, add tests.
**Status:** ✅ Complete (2026-06-01)

| # | Task | File | Status |
|---|---|---|---|
| A | API security hardening (rate limiting, SSRF, CORS, body size) | api/main.py | ✅ |
| B | Input validation (Pydantic field guards, user_id length) | api/main.py | ✅ |
| C | Scraper anomaly detection + PIPELINE_MODE env var | automation/scraper_health.py, orchestrator.py | ✅ |
| D | Supabase removal, Streamlit auth, audit_log table | profile_fetcher.py, job_dashboard.py, init_db.py, api/main.py | ✅ |
| E | pytest test suite — 104 tests (unit + integration) | tests/ | ✅ |
| F | SQLite PRAGMA tuning, multiple API keys, GitHub Actions CI, Makefile, ruff | api/main.py, db_helper.py, .github/workflows/, Makefile, ruff.toml | ✅ |

---

## Execution order

**All phases complete.** Current status as of 2026-06-01:
- Phases 1–6: ✅ Done
- PB-9 (remove job_api): ❌ Permanently deferred — see PB-9 rationale above

---

## Dependencies map

```
Phase 1 (Queue)   →  Phase 3 (Dedup) can run in parallel with Phase 2
Phase 2 (PB)      →  Phase 3 (Dedup) uses PB as job storage backend
Phase 3 (Dedup)   →  Phase 4 (Browser) independent, can overlap
Phase 4 (Browser) →  Phase 5 (Observability) independent
Phase 6 (Security/Quality) → orthogonal to all above
```

---

*Created: 2026-05-22 · Last updated: 2026-06-01*
