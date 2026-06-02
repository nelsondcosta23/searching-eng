"""Unit tests for automation/job_analytics.py."""
import pytest
import sqlite3
from datetime import datetime
import automation.job_analytics as analytics_mod
import automation.db_helper as db_mod

@pytest.fixture(autouse=True)
def patch_db_path(tmp_db, monkeypatch):
    """Redirect all db_helper/analytics operations to the test DB."""
    monkeypatch.setattr(analytics_mod, "DB_PATH", tmp_db)
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_db)

def test_parse_posting_age():
    # Hour / minute / recent / today / hoje
    assert analytics_mod.parse_posting_age("3 hours ago", "2026-06-02 20:00:00") == 0
    assert analytics_mod.parse_posting_age("hoje", "2026-06-02 20:00:00") == 0
    assert analytics_mod.parse_posting_age("recent", "2026-06-02 20:00:00") == 0

    # Yesterday / ontem
    assert analytics_mod.parse_posting_age("yesterday", "2026-06-02 20:00:00") == 1
    assert analytics_mod.parse_posting_age("ontem", "2026-06-02 20:00:00") == 1

    # N days / dias / d / há N dias
    assert analytics_mod.parse_posting_age("5 days ago", "2026-06-02 20:00:00") == 5
    assert analytics_mod.parse_posting_age("há 12 dias", "2026-06-15 20:00:00") == 12
    assert analytics_mod.parse_posting_age("3d ago", "2026-06-02 20:00:00") == 3

    # Direct date parsing
    assert analytics_mod.parse_posting_age("2026-05-30", "2026-06-02 20:00:00") == 3
    assert analytics_mod.parse_posting_age("30/05/2026", "2026-06-02 20:00:00") == 3

def test_update_all_posting_ages(tmp_db):
    # Insert jobs with NULL posting_age_days
    db_mod.save_job("u", "P", "e1", "Job 1", "Co", "Loc", "https://example.com/1", data_pub="3 days ago")
    db_mod.save_job("u", "P", "e2", "Job 2", "Co", "Loc", "https://example.com/2", data_pub="ontem")

    # Manually force scraped time for reproducible assertions
    conn = sqlite3.connect(tmp_db)
    conn.execute("UPDATE jobs SET data_scraped = '2026-06-02 20:00:00'")
    conn.commit()
    conn.close()

    analytics_mod.update_all_posting_ages()

    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    j1 = conn.execute("SELECT posting_age_days FROM jobs WHERE link = 'https://example.com/1'").fetchone()
    j2 = conn.execute("SELECT posting_age_days FROM jobs WHERE link = 'https://example.com/2'").fetchone()
    conn.close()

    assert j1['posting_age_days'] == 3
    assert j2['posting_age_days'] == 1

def test_rankings_and_distributions(tmp_db):
    # Seed jobs
    db_mod.save_job("u", "P", "e1", "Backend Developer", "Acme", "Loc", "https://example.com/1", data_pub="recent")
    db_mod.save_job("u", "P", "e2", "Frontend Developer", "Acme", "Loc", "https://example.com/2", data_pub="recent")
    db_mod.save_job("u", "P", "e3", "Fullstack Developer", "Corp", "Loc", "https://example.com/3", data_pub="recent")
    # Mark as tech
    conn = sqlite3.connect(tmp_db)
    conn.execute("UPDATE jobs SET job_type = 'Backend' WHERE link = 'https://example.com/1'")
    conn.execute("UPDATE jobs SET job_type = 'Frontend' WHERE link = 'https://example.com/2'")
    conn.execute("UPDATE jobs SET job_type = 'Full-stack' WHERE link = 'https://example.com/3'")
    conn.commit()
    conn.close()

    # Seed companies cache
    conn = sqlite3.connect(tmp_db)
    conn.execute("INSERT INTO companies (name, inception_year, company_age) VALUES (?, ?, ?)", ("Acme", 2000, 26))
    conn.commit()
    conn.close()

    rankings = analytics_mod.get_company_rankings()
    assert len(rankings) == 2
    assert rankings[0]['empresa'] == "Acme"
    assert rankings[0]['open_positions'] == 2
    assert rankings[0]['company_age'] == 26

    assert rankings[1]['empresa'] == "Corp"
    assert rankings[1]['open_positions'] == 1

    dist = analytics_mod.get_job_type_distribution()
    # Should contain Backend, Frontend, Full-stack
    types = [d['job_type'] for d in dist]
    assert "Backend" in types
    assert "Frontend" in types
    assert "Full-stack" in types

    report = analytics_mod.generate_markdown_report()
    assert "Tech Job-Market Intelligence Report" in report
    assert "Acme" in report
    assert "Corp" in report
