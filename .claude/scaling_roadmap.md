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
| PB-1 | Add PocketBase service to docker-compose.yml | docker-compose.yml | 🔲 |
| PB-2 | Design PocketBase collections: `users`, `jobs`, `platforms` | (PB admin UI) | 🔲 |
| PB-3 | Create `automation/pb_client.py` — PocketBase REST client (auth, upsert jobs, sync profiles) | automation/pb_client.py | 🔲 |
| PB-4 | Modify `webhook_dispatcher.py` — after dispatch, push top jobs to PocketBase `jobs` collection | automation/webhook_dispatcher.py | 🔲 |
| PB-5 | Modify `profile_fetcher.py` — add PocketBase as profile source (replaces Supabase Edge Function) | automation/profile_fetcher.py | 🔲 |
| PB-6 | Update `api/main.py` — proxy profile sync to PocketBase instead of local SQLite | api/main.py | 🔲 |
| PB-7 | Update `app/job_dashboard.py` — read from PocketBase via REST instead of direct SQLite | app/job_dashboard.py | 🔲 |
| PB-8 | Migrate existing `users_perfil` rows to PocketBase | migration script | 🔲 |
| PB-9 | Remove `job_api` FastAPI service once PocketBase covers all endpoints | docker-compose.yml | 🔲 |

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
| GD-1 | Design new schema (jobs_global + jobs_users) | init_db.py | 🔲 |
| GD-2 | Add `save_job_global()` to db_helper.py — check global first, then insert+associate | automation/db_helper.py | 🔲 |
| GD-3 | Modify `job_exists()` — check global table by link | automation/db_helper.py | 🔲 |
| GD-4 | Update scorer — scores go to jobs_users, not vagas | automation/job_scorer.py | 🔲 |
| GD-5 | Update all scrapers — call `save_job_global()` instead of `save_job()` | all scrapers | 🔲 |
| GD-6 | Update API queries — JOIN jobs_global + jobs_users | api/main.py | 🔲 |
| GD-7 | Update dashboard — read from new schema | app/job_dashboard.py | 🔲 |
| GD-8 | Migration script — convert existing `vagas` to new tables | migration_v7.py | 🔲 |

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
| BP-1 | Evaluate Browserless vs Playwright — test with LinkedIn and Indeed | — | 🔲 |
| BP-2 | Add browser service to docker-compose.yml | docker-compose.yml | 🔲 |
| BP-3 | Rewrite `init_chrome_with_timeout()` in `_shared.py` to use pool connection | scrapers/_shared.py | 🔲 |
| BP-4 | Update LinkedIn scraper — use CDP/Playwright instead of uc.Chrome | scrapers/linkedin_scraper.py | 🔲 |
| BP-5 | Update Indeed scraper | scrapers/indeed_scraper.py | 🔲 |
| BP-6 | Update Sapo scraper | scrapers/sapo_scraper.py | 🔲 |
| BP-7 | Update Expresso scraper | scrapers/expresso_scraper.py | 🔲 |
| BP-8 | Remove `undetected-chromedriver` from requirements.txt | requirements.txt | 🔲 |

---

## Phase 5 — Observability
**Goal:** Replace `print()` logs with structured logging + alerts on failures.

| # | Task | File | Status |
|---|---|---|---|
| OB-1 | Add Sentry SDK — capture exceptions from scrapers and API | all | 🔲 |
| OB-2 | Add `structlog` — replace print() with structured JSON logs | all | 🔲 |
| OB-3 | Prometheus metrics endpoint on job_api — jobs/day, scraper success rates | api/main.py | 🔲 |
| OB-4 | Grafana dashboard — visualize scraper health, job yield per platform | docker-compose.yml | 🔲 |
| OB-5 | Celery Flower — already planned in TQ-8, real-time task monitoring | — | 🔲 |

---

## Execution order

**Start now:** Phase 1 (Task Queue) — no DB schema changes, self-contained, high impact.
**After Phase 1:** Phase 2 (PocketBase) — replace API layer while scrapers keep running.
**After Phase 2:** Phase 3 (Global dedup) — requires new schema, coordinate with PB migration.
**After Phase 3:** Phase 4 (Browser pool) — independent of DB, pure scraping infrastructure.
**Continuous:** Phase 5 (Observability) — can add incrementally.

---

## Dependencies map

```
Phase 1 (Queue)   →  Phase 3 (Dedup) can run in parallel with Phase 2
Phase 2 (PB)      →  Phase 3 (Dedup) uses PB as job storage backend
Phase 3 (Dedup)   →  Phase 4 (Browser) independent, can overlap
Phase 4 (Browser) →  Phase 5 (Observability) independent
```

---

*Created: 2026-05-22*
