"""
Per-scraper run history logger.

Appends one JSON line per run to logs/scrapers/<platform_slug>.jsonl.
Each record: timestamp, jobs_added, success, duration_s, [notes].

Usage:
  from automation.run_logger import log_run, print_summary
  log_run("LinkedIn PT", jobs_added=12, success=True, duration_s=145)

  # CLI — print a summary table of all scraper history:
  python automation/run_logger.py [--last N]
"""
import json
import os
import re
import sys
from datetime import datetime

BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR  = os.path.join(BASE_DIR, 'logs', 'scrapers')


def _slug(platform: str) -> str:
    """'LinkedIn PT' → 'linkedin_pt'"""
    return re.sub(r'[^a-z0-9]+', '_', platform.lower()).strip('_')


def log_run(platform: str, jobs_added: int, success: bool, duration_s: int, notes: str = '') -> None:
    """Append a run record for the given platform."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    record: dict = {
        'ts':          datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'jobs_added':  jobs_added,
        'success':     success,
        'duration_s':  duration_s,
    }
    if notes:
        record['notes'] = notes

    path = os.path.join(LOGS_DIR, f'{_slug(platform)}.jsonl')
    try:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record) + '\n')
    except Exception as e:
        print(f'[run_logger] Could not write log for {platform}: {e}', file=sys.stderr)


def read_history(platform: str, last_n: int = 0) -> list[dict]:
    """Returns run records for a platform (all, or last N)."""
    path = os.path.join(LOGS_DIR, f'{_slug(platform)}.jsonl')
    if not os.path.exists(path):
        return []
    records = []
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        return []
    return records[-last_n:] if last_n else records


def _all_platforms() -> list[str]:
    """Returns platform slugs that have a log file."""
    if not os.path.isdir(LOGS_DIR):
        return []
    return sorted(
        f[:-6] for f in os.listdir(LOGS_DIR) if f.endswith('.jsonl')
    )


def print_summary(last_n: int = 10) -> None:
    """Prints a per-scraper summary table (last_n runs each)."""
    platforms = _all_platforms()
    if not platforms:
        print('No scraper history yet. Run the orchestrator first.')
        return

    print(f'\n{"Scraper":<30} {"Runs":>4} {"Last run":<19} {"Jobs (last)":>11} {"Jobs (total)":>12} {"Avg jobs":>8} {"Fail%":>6} {"Avg dur":>8}')
    print('─' * 100)

    for slug in platforms:
        history = read_history(slug)
        if not history:
            continue

        recent  = history[-last_n:]
        total_jobs  = sum(r.get('jobs_added', 0) for r in history if r.get('jobs_added', 0) >= 0)
        avg_jobs    = total_jobs / len(history) if history else 0
        fail_count  = sum(1 for r in history if not r.get('success', True))
        fail_pct    = 100 * fail_count // len(history) if history else 0
        avg_dur     = sum(r.get('duration_s', 0) for r in history) / len(history) if history else 0
        last_ts     = history[-1].get('ts', '')
        last_jobs   = history[-1].get('jobs_added', '?')
        name        = slug.replace('_', ' ').title()

        print(
            f'{name:<30} {len(history):>4} {last_ts:<19} {str(last_jobs):>11} '
            f'{total_jobs:>12} {avg_jobs:>8.1f} {fail_pct:>5}% {int(avg_dur):>6}s'
        )

    print(f'\n(showing totals across all runs; last {last_n} used for "last run" column)')
    print(f'Log files: {LOGS_DIR}')


def print_detail(platform: str, last_n: int = 20) -> None:
    """Prints per-run detail for one platform."""
    history = read_history(platform, last_n=last_n)
    if not history:
        print(f'No history for "{platform}"')
        return

    slug = _slug(platform)
    print(f'\n=== {slug} — last {len(history)} runs ===')
    print(f'{"Timestamp":<19}  {"Jobs":>5}  {"OK":>3}  {"Dur":>6}  Notes')
    print('─' * 60)
    for r in history:
        ok  = '✅' if r.get('success', True) else '❌'
        job = r.get('jobs_added', '?')
        dur = f'{r.get("duration_s", 0)}s'
        ts  = r.get('ts', '')
        notes = r.get('notes', '')
        print(f'{ts:<19}  {str(job):>5}  {ok:>3}  {dur:>6}  {notes}')


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='Scraper run history viewer')
    p.add_argument('--last',     type=int, default=10, help='Show last N runs per scraper (default 10)')
    p.add_argument('--platform', type=str, default='',  help='Show detail for one platform')
    args = p.parse_args()

    if args.platform:
        print_detail(args.platform, last_n=args.last)
    else:
        print_summary(last_n=args.last)
