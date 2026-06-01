.PHONY: test lint lint-fix build up down restart logs scrape rescore

# ── Testing ───────────────────────────────────────────────────────────────────
test:
	python -m pytest tests/ -v --tb=short

test-fast:
	python -m pytest tests/ --tb=short -q

# ── Linting ───────────────────────────────────────────────────────────────────
lint:
	ruff check .
	ruff format --check .

lint-fix:
	ruff check --fix .
	ruff format .

# ── Docker ────────────────────────────────────────────────────────────────────
build:
	docker-compose build

up:
	docker-compose up -d

down:
	docker-compose down

restart:
	docker-compose restart python_scraper job_api streamlit_app

# ── Logs ──────────────────────────────────────────────────────────────────────
logs:
	docker-compose logs -f python_scraper

logs-api:
	docker-compose logs -f job_api

logs-all:
	docker-compose logs -f

# ── Manual runs ───────────────────────────────────────────────────────────────
scrape:
	docker exec python_scraper python /app/automation/orchestrator.py

rescore:
	docker exec python_scraper python /app/automation/job_scorer.py --rescore-all

db:
	docker exec -it python_scraper sqlite3 /app/database/vagas.db
