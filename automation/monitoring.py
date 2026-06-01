"""Observability helpers — Sentry, structlog, Prometheus.

All features are opt-in via environment variables and fail silently if the
package is not installed or the DSN/config is missing. The rest of the
pipeline is never blocked by a monitoring failure.

Environment variables:
  SENTRY_DSN        — enable Sentry (omit to disable)
  SENTRY_ENV        — environment tag (default: production)
  LOG_LEVEL         — structlog level: DEBUG | INFO | WARNING (default: INFO)
  METRICS_ENABLED   — expose Prometheus /metrics (default: 1)
"""

import os
import sys
import time
import traceback

# ─────────────────────────────────────────────────────────────────────────────
# OB-1  Sentry
# ─────────────────────────────────────────────────────────────────────────────

_sentry_ready = False


def init_sentry(dsn: str = '', env: str = '') -> bool:
    """Initialise Sentry SDK. Returns True if active, False if disabled/failed."""
    global _sentry_ready
    dsn = dsn or os.environ.get('SENTRY_DSN', '').strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            environment=env or os.environ.get('SENTRY_ENV', 'production'),
            traces_sample_rate=0.1,   # 10% of requests traced — keeps quota low
            send_default_pii=False,
        )
        _sentry_ready = True
        return True
    except Exception as exc:
        print(f'[monitoring] Sentry init failed (non-fatal): {exc}')
        return False


def capture_exception(exc: Exception, context: dict = None) -> None:
    """Send an exception to Sentry (no-op when Sentry is not configured)."""
    if not _sentry_ready:
        return
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            for k, v in (context or {}).items():
                scope.set_extra(k, v)
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass


def capture_message(msg: str, level: str = 'info', context: dict = None) -> None:
    """Send a message to Sentry (no-op when not configured)."""
    if not _sentry_ready:
        return
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            for k, v in (context or {}).items():
                scope.set_extra(k, v)
            sentry_sdk.capture_message(msg, level=level)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# OB-2  Structured logger (additive — never replaces existing print() calls)
# ─────────────────────────────────────────────────────────────────────────────

_LOG_LEVEL_ORDER = {'debug': 0, 'info': 1, 'warning': 2, 'error': 3, 'critical': 4}
_MIN_LEVEL = _LOG_LEVEL_ORDER.get(os.environ.get('LOG_LEVEL', 'info').lower(), 1)


def _emit(level: str, event: str, **kwargs) -> None:
    """Write one JSON log line to stdout (captured by cron just like print())."""
    if _LOG_LEVEL_ORDER.get(level, 1) < _MIN_LEVEL:
        return
    try:
        import json as _json
        record = {
            'ts':    time.strftime('%Y-%m-%dT%H:%M:%S'),
            'level': level,
            'event': event,
        }
        record.update(kwargs)
        print(_json.dumps(record, ensure_ascii=False, default=str), flush=True)
    except Exception:
        # Last resort: plain print so we never lose the message
        print(f'[{level.upper()}] {event} {kwargs}', flush=True)


class _Logger:
    """Minimal structured logger. Usage: log.info("event", key=value, ...)"""
    def debug(self, event: str, **kw):    _emit('debug',    event, **kw)
    def info(self, event: str, **kw):     _emit('info',     event, **kw)
    def warning(self, event: str, **kw):  _emit('warning',  event, **kw)
    def error(self, event: str, **kw):    _emit('error',    event, **kw)
    def critical(self, event: str, **kw): _emit('critical', event, **kw)

    def exception(self, event: str, exc: Exception = None, **kw):
        """Log an error with exception traceback, also forwarding to Sentry."""
        kw['traceback'] = traceback.format_exc()
        _emit('error', event, **kw)
        if exc is not None:
            capture_exception(exc, context=kw)


log = _Logger()


# ─────────────────────────────────────────────────────────────────────────────
# OB-3  Prometheus metrics registry
# ─────────────────────────────────────────────────────────────────────────────

_metrics_enabled = os.environ.get('METRICS_ENABLED', '1') == '1'
_prom_ready = False

# Metric objects — populated by init_prometheus()
jobs_scraped_total   = None
jobs_active          = None
scrape_duration      = None
api_requests_total   = None
api_errors_total     = None


def init_prometheus() -> bool:
    """Register Prometheus metrics. Returns True if successful."""
    global _prom_ready, jobs_scraped_total, jobs_active, scrape_duration
    global api_requests_total, api_errors_total

    if not _metrics_enabled:
        return False
    try:
        from prometheus_client import Counter, Gauge, Histogram, REGISTRY
        jobs_scraped_total = Counter(
            'searching_eng_jobs_scraped_total',
            'Total jobs saved to DB',
            ['platform'],
        )
        jobs_active = Gauge(
            'searching_eng_jobs_active',
            'Current active jobs in DB per user',
            ['user_id'],
        )
        scrape_duration = Histogram(
            'searching_eng_scrape_duration_seconds',
            'Time taken per scraper run',
            ['platform'],
            buckets=[30, 60, 120, 300, 600, 1200, 1800],
        )
        api_requests_total = Counter(
            'searching_eng_api_requests_total',
            'API requests received',
            ['endpoint', 'method', 'status'],
        )
        api_errors_total = Counter(
            'searching_eng_api_errors_total',
            'API 4xx/5xx responses',
            ['endpoint'],
        )
        _prom_ready = True
        return True
    except Exception as exc:
        print(f'[monitoring] Prometheus init failed (non-fatal): {exc}')
        return False


def record_job_scraped(platform: str, count: int = 1) -> None:
    """Increment the jobs_scraped counter for a platform."""
    if _prom_ready and jobs_scraped_total:
        try:
            jobs_scraped_total.labels(platform=platform).inc(count)
        except Exception:
            pass


def record_scrape_duration(platform: str, seconds: float) -> None:
    """Observe a scraper run duration."""
    if _prom_ready and scrape_duration:
        try:
            scrape_duration.labels(platform=platform).observe(seconds)
        except Exception:
            pass


def set_active_jobs(user_id: str, count: int) -> None:
    """Set the active-jobs gauge for one user.

    Called by the orchestrator after each user's pipeline completes so the
    Prometheus/Grafana dashboard reflects the current DB state.
    """
    if _prom_ready and jobs_active:
        try:
            jobs_active.labels(user_id=user_id).set(count)
        except Exception:
            pass


def generate_metrics_response() -> tuple:
    """Return (content, media_type) for the /metrics endpoint."""
    if not _prom_ready:
        return 'Prometheus not initialised', 'text/plain'
    try:
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
        return generate_latest(), CONTENT_TYPE_LATEST
    except Exception as exc:
        return f'Error generating metrics: {exc}', 'text/plain'


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: initialise everything at once
# ─────────────────────────────────────────────────────────────────────────────

def init_all() -> None:
    """Call once at application startup to enable all observability features."""
    sentry_ok  = init_sentry()
    prom_ok    = init_prometheus()
    log.info(
        'monitoring_init',
        sentry=sentry_ok,
        prometheus=prom_ok,
        log_level=os.environ.get('LOG_LEVEL', 'info'),
    )
