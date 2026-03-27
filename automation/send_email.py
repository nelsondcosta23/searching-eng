import sqlite3
import os
import requests
from datetime import datetime, timedelta

DB_PATH          = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
RESEND_API_KEY   = os.environ.get('RESEND_API_KEY', '')
EMAIL_DESTINO    = os.environ.get('EMAIL_DESTINO', '')
EMAIL_REMETENTE  = os.environ.get('EMAIL_REMETENTE', 'jobs@resend.dev')

if not RESEND_API_KEY:
    raise EnvironmentError("RESEND_API_KEY is not defined! Check your .env file")
if not EMAIL_DESTINO:
    raise EnvironmentError("EMAIL_DESTINO is not defined! Check your .env file")

def obtain_recent_jobs():
    """Gets jobs inserted in the last 24 hours."""
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute('PRAGMA journal_mode=WAL;')
    cursor = conn.cursor()

    limit = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        SELECT titulo, empresa, localizacao, plataforma, link, data_scraped
        FROM vagas
        WHERE data_scraped >= ?
        ORDER BY plataforma, data_scraped DESC
    ''', (limit,))

    jobs = cursor.fetchall()
    conn.close()
    return jobs

def build_html_email(jobs, today_date):
    """Builds the HTML body of the email."""
    if not jobs:
        jobs_body = "<p>No new jobs were imported today. Try running the scrapers!</p>"
    else:
        rows = ""
        for title, company, location, platform, link, _ in jobs:
            rows += f"""
            <tr>
                <td style="padding:10px;border-bottom:1px solid #f0f0f0;">
                    <a href="{link}" style="color:#4F46E5;font-weight:600;text-decoration:none;">{title}</a>
                </td>
                <td style="padding:10px;border-bottom:1px solid #f0f0f0;color:#555;">{company or 'N/A'}</td>
                <td style="padding:10px;border-bottom:1px solid #f0f0f0;color:#555;">{location or 'N/A'}</td>
                <td style="padding:10px;border-bottom:1px solid #f0f0f0;">
                    <span style="background:#EEF2FF;color:#4F46E5;padding:3px 8px;border-radius:12px;font-size:12px;">{platform}</span>
                </td>
            </tr>"""

        jobs_body = f"""
        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
            <thead>
                <tr style="background:#4F46E5;color:white;">
                    <th style="padding:12px;text-align:left;">Title</th>
                    <th style="padding:12px;text-align:left;">Company</th>
                    <th style="padding:12px;text-align:left;">Location</th>
                    <th style="padding:12px;text-align:left;">Platform</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>"""

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="margin:0;padding:0;background:#f8f9fa;font-family:Arial,sans-serif;">
        <div style="max-width:700px;margin:30px auto;background:white;border-radius:12px;overflow:hidden;box-shadow:0 2px 16px rgba(0,0,0,0.08);">
            <div style="background:#4F46E5;padding:30px;text-align:center;">
                <h1 style="color:white;margin:0;font-size:24px;">🔍 Daily Jobs Summary</h1>
                <p style="color:#C7D2FE;margin:8px 0 0;">{today_date}</p>
            </div>
            <div style="padding:30px;">
                <p style="color:#374151;margin:0 0 20px;font-size:16px;">
                    <strong>{len(jobs)} new jobs</strong> were found in the last 24 hours:
                </p>
                {jobs_body}
                <p style="color:#9CA3AF;font-size:13px;margin:24px 0 0;">
                    This email is automatically sent by your job scraping system.
                </p>
            </div>
        </div>
    </body>
    </html>
    """

def send_email(html, num_jobs, today_date):
    """Sends the email via Resend API."""
    subject = f"📋 {num_jobs} New Job Openings — {today_date}"

    payload = {
        "from": EMAIL_REMETENTE,
        "to": [EMAIL_DESTINO],
        "subject": subject,
        "html": html,
    }

    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.post("https://api.resend.com/emails", json=payload, headers=headers, timeout=15)

    if response.status_code in (200, 201):
        data = response.json()
        print(f"✅ Email sent successfully! ID: {data.get('id')}")
    else:
        print(f"❌ Error sending email: {response.status_code} — {response.text}")

def main():
    today_date = datetime.now().strftime('%B %d, %Y')
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Preparing daily email...")

    jobs = obtain_recent_jobs()
    print(f"  → {len(jobs)} new jobs found in the last 24h.")

    html = build_html_email(jobs, today_date)
    send_email(html, len(jobs), today_date)

if __name__ == '__main__':
    main()
