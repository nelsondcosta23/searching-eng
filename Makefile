.PHONY: test test-fast lint lint-fix scrape verify dashboard db init-db

# ── Testing & Linting ─────────────────────────────────────────────────────────
test:
	python -m pytest tests/ -v --tb=short

test-fast:
	python -m pytest tests/ --tb=short -q

lint:
	ruff check .
	ruff format --check .

lint-fix:
	ruff check --fix .
	ruff format .

# ── Local Execution ───────────────────────────────────────────────────────────
init-db:
	python init_db.py

scrape:
	python automation/orchestrator.py

verify:
	python automation/job_verifier.py

dashboard:
	streamlit run app/job_dashboard.py

db:
	sqlite3 database/vagas.db
