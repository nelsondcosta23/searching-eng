"""Scraper anomaly detection — compares today's yield against a 7-day rolling average.

Called by orchestrator.run_scraper() after each scraper finishes. If a scraper
ran successfully but produced far fewer jobs than its historical baseline, a
warning is printed and (if Sentry is configured) a capture_message() is sent.

Environment variables:
  YIELD_ALERT_THRESHOLD  — ratio below which we alert (default: 0.20, i.e. <20% of average)
  YIELD_MIN_BASELINE     — minimum average required before alerting (default: 5 jobs/day)
  DB_PATH                — path to SQLite database
"""

import os
import sqlite3

DB_PATH             = os.environ.get('DB_PATH', '/app/database/vagas.db')
YIELD_ALERT_THRESHOLD = float(os.environ.get('YIELD_ALERT_THRESHOLD', '0.20'))
YIELD_MIN_BASELINE    = float(os.environ.get('YIELD_MIN_BASELINE',    '5'))


def get_7day_average(platform_prefix: str) -> float:
    """Return the daily average jobs added for a platform over the last 7 complete days.

    Uses jobs_global.data_scraped grouped by calendar day, excluding today so
    the in-progress day doesn't dilute the baseline.
    Returns 0.0 if the database is unreachable or no history exists.
    """
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            rows = conn.execute(
                """
                SELECT DATE(data_scraped) AS day, COUNT(*) AS cnt
                FROM jobs_global
                WHERE plataforma LIKE ?
                  AND DATE(data_scraped) >= DATE('now', '-7 days')
                  AND DATE(data_scraped) <  DATE('now')
                GROUP BY day
                """,
                (f'{platform_prefix}%',),
            ).fetchall()
        if not rows:
            return 0.0
        # Average over the days that actually had data (not padded zeros)
        return sum(r[1] for r in rows) / len(rows)
    except Exception:
        return 0.0


def check_scraper_yield(platform: str, today_count: int) -> None:
    """Warn if today's scraper yield is anomalously low vs the 7-day baseline.

    Prints a ⚠ warning to stdout (captured by cron log) and forwards to Sentry
    when configured. No-op if the baseline is too low to be meaningful.

    Args:
        platform:    Scraper name / platform prefix (e.g. "LinkedIn", "ITJobs").
        today_count: Jobs added to jobs_global for this platform in today's run
                     (from _count_global_since).
    """
    if today_count < 0:
        # -1 means _count_global_since itself failed — not an anomaly signal
        return

    avg = get_7day_average(platform)
    if avg < YIELD_MIN_BASELINE:
        # Not enough history or platform is too thin — skip noisy alerts
        return

    if avg > 0 and (today_count / avg) < YIELD_ALERT_THRESHOLD:
        msg = (
            f'[health] ⚠ LOW YIELD — {platform}: today={today_count} jobs '
            f'vs 7-day avg={avg:.1f} '
            f'(ratio={today_count/avg:.0%}, threshold={YIELD_ALERT_THRESHOLD:.0%})'
        )
        print(msg)
        try:
            from automation.monitoring import capture_message
            capture_message(
                msg,
                level='warning',
                context={
                    'platform':        platform,
                    'today_count':     today_count,
                    'avg_7day':        round(avg, 1),
                    'ratio':           round(today_count / avg, 3),
                    'threshold':       YIELD_ALERT_THRESHOLD,
                },
            )
        except Exception:
            pass
