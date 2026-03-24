# 💼 Searching Engine Platform

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![Docker](https://img.shields.io/badge/Docker-Supported-2496ED.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B.svg)
![SQLite](https://img.shields.io/badge/SQLite-003B57.svg)

An advanced, fully-automated system designed to scrape, aggregate, verify, and serve job listings from various Portuguese and international job boards (LinkedIn, Indeed, Sapo, Expresso, Net-Empregos). 

The platform operates autonomously via a 00:00 Cronjob, stores data locally in a thread-safe SQLite database, provides a beautiful **Streamlit dashboard** for manual filtering, and exposes a **FastAPI REST Endpoint** protected by an API Key for external software integration.

---

## ✨ Key Features

- **🤖 Automated Scraper Engine**: Supports both Static (Sapo, Expresso, Net-Empregos via RSS/HTML) and Dynamic sites (LinkedIn, Indeed via Undetected ChromeDriver & Selenium).
- **🧠 Dynamic Intelligence (Supabase API)**: The system fetches its scraping strategy (target roles, locations, remote filters, and negative keywords) dynamically from a live JSON API. No hardcoded search terms!
- **🌐 REST API Service**: Exposes scraped jobs securely on port `8080`. External applications can query jobs by `user_id`, `run_date`, `platform`, and `status`.
- **🛡️ Bullet-Proof SQLite**: Uses a centralized `db_helper.py` applying `WAL` mode and dynamic concurrency retries, completely avoiding "database is locked" errors across 5 simultaneous scrapers.
- **🧹 Auto-Cleanup & Verification**: Periodically checks if jobs have expired (404 links) and purges jobs older than your retention limit.
- **📈 Real-Time Dashboard**: Includes a sleek Streamlit web UI to monitor, filter, and apply to collected jobs easily.

---

## 🏗️ System Architecture

The platform is strictly containerized using Docker Compose:

1. **`job_api`**: FastAPI service running on port `8080` to serve results externally (ready to be exposed via Cloudflared).
2. **`streamlit_app`**: Python Streamlit dashboard running on port `8501` for human interaction.
3. **`python_scraper`**: The background worker that runs cron jobs, executes the headless Chrome browsers via Xvfb, and manages automated database writes.

---

## 🚀 Quick Setup (Docker)

The recommended way to run this system anywhere is using Docker.

### Prerequisites
*   [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) installed.

### Step-by-Step Guide

**1. Clone the repository**
```bash
git clone https://github.com/yourusername/searching-eng.git
cd searching-eng
```

**2. Configure Environment Variables**
Copy the example file and edit your settings:
```bash
cp .env.example .env
```
Key variables inside `.env`:
```env
# Limit the amount of jobs per scraping session
MAX_JOBS_PER_PLATFORM=5

# Key to protect your REST API
API_KEY=your_super_secret_key_here

# (Optional) Email settings via Resend
RESEND_API_KEY=re_your_api_key_here
```

**3. Build and Start the System**
```bash
docker-compose up -d --build
```

**4. Access Services**
- 📊 **Dashboard:** [http://localhost:8501](http://localhost:8501)
- 🔌 **REST API (Health Check):** [http://localhost:8080/api/v1/status](http://localhost:8080/api/v1/status)

---

## 📡 REST API Usage

External software can fetch the latest scraped jobs for processing.

**Endpoint:** `GET /api/v1/jobs`

**Headers / Query Params:**
- Authentication via Header: `Authorization: Bearer <API_KEY>`
- Or via Query Parameter: `?api_key=<API_KEY>`

**Query Parameters:**
- `user_id` (Required): The Supabase user ID mapped to the jobs.
- `run_date`: YYYY-MM-DD to filter by scrape date (Defaults to 'today'. Use 'all' for full history).
- `status`: 'Ativa' or 'Expirada'
- `limit`: Default 500 max jobs.

**Example Request:**
```bash
curl "http://localhost:8080/api/v1/jobs?user_id=12345&run_date=all&api_key=your_super_secret_key_here"
```

---

## 📁 Clean Directory Structure

```text
searching-eng/
├── .env                  # Environment configurations
├── docker-compose.yml    # Docker services definition
├── Dockerfile            # Python/Chrome/Xvfb environment setup
├── init_db.py            # SQLite Database creation script (Schema v3)
│
├── api/                  # FastAPI External Service
│   └── main.py
│
├── app/                  # Frontend Dashboard
│   └── job_dashboard.py
│
├── automation/           # Core Logic & Services
│   ├── clean_jobs.py     # Purge old DB entries
│   ├── db_helper.py      # Bullet-proof SQLite concurrency layer
│   ├── job_verifier.py   # Check if active URLs turned 404
│   ├── orchestrator.py   # Main Controller (with 1h freeze-protection)
│   ├── profile_fetcher.py# Connects to Supabase JSON Rules
│   └── send_email.py     # Notification Service (Resend)
│
├── config/               
│   └── crontab           # Automated schedules (00:00 Daily Run)
│
├── database/             # Persistent Storage (.db files ignored in Git)
├── logs/                 # Diagnostic Outputs & Error Screenshots
│
└── scrapers/             # The extraction engines
    ├── expresso_scraper.py
    ├── indeed_scraper.py
    ├── linkedin_scraper.py
    ├── net_jobs_scraper.py
    └── sapo_scraper.py
```

---

*Automatic Job Scraping System | Built for efficiency, scale, and clean database architectures.*
