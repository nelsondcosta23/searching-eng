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
- **🧠 Dynamic Intelligence (Local + Sync)**: The system manages scraping strategies (roles, locations, keywords) via a local database, syncable with external software via API.
- **🌐 REST API Service**: Exposes scraped jobs securely on port `8080`. External applications can query jobs and sync user profiles.
- **🔔 Webhook Push**: Automatically pushes the top 5 most relevant jobs found today directly to your external software's callback URL.
- **🛡️ Bullet-Proof SQLite**: Uses a centralized `db_helper.py` applying `WAL` mode and dynamic concurrency retries (Schema v6).
- **🧹 Auto-Cleanup & Verification**: Periodically checks if jobs have expired (404 links) and purges old jobs (default 45 days).
- **📈 Real-Time Dashboard**: Includes a sleek Streamlit web UI to monitor, filter, and apply to collected jobs easily.

---

## 🏗️ System Architecture

The platform is strictly containerized using Docker Compose:

1. **`job_api`**: FastAPI service running on port `8080` for result serving and profile syncing.
2. **`streamlit_app`**: Python Streamlit dashboard running on port `8501`.
3. **`python_scraper`**: The background worker running daily tasks (00:00 Scrape, 13:15 Email, etc.).
4. **`cloudflare_tunnel`**: Automatically exposes the `job_api` to a public URL.

---

## 🚀 Quick Setup (Docker)

1. **Clone the repository**
2. **Configure `.env`**:
   ```bash
   cp .env.example .env
   # Edit .env with your RESEND_API_KEY, API_KEY, etc.
   ```
3. **Start the System**:
   ```bash
   docker-compose up -d --build
   ```
4. **Access**:
   - 📊 **Dashboard:** [http://localhost:8501](http://localhost:8501)
   - 🔌 **API Docs:** [http://localhost:8080/docs](http://localhost:8080/docs)

---

## 📡 REST API & Webhooks

### Fetch Jobs
`GET /api/v1/jobs?user_id=...&api_key=...`

### Sync Profile
`POST /api/v1/users/sync`
Allows external software to update search preferences and register a `callback_url` for webhooks.

### Webhook Push
If a `callback_url` is provided, the system sends a `POST` request every night with today's best jobs.
Includes security header: `X-Webhook-Secret`.

*Detailed documentation in: [API_INTEGRATION_GUIDE.md](API_INTEGRATION_GUIDE.md)*

---

## 📁 Directory Structure

```text
searching-eng/
├── api/                  # FastAPI Service (main.py)
├── app/                  # Streamlit Dashboard (job_dashboard.py)
├── automation/           # Core Logic (orchestrator, scorer, dispatcher...)
├── scrapers/             # Extraction engines (LinkedIn, Sapo, etc.)
├── config/               # Crontab definitions
├── database/             # Persistent SQLite storage
└── logs/                 # Screenshots & Error logs
```

---

*Automatic Job Scraping System | Built for efficiency, scale, and clean database architectures.*
