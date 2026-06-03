"""Shared helpers used across scrapers.

Phase 4 update: Playwright browser pool replaces undetected_chromedriver.
  - get_pw_browser()  — shared Chromium instance (one per process/worker)
  - new_pw_context()  — fresh incognito context from the pool (<100ms)
  - apply_stealth()   — anti-bot patches via playwright-stealth

Legacy Selenium helpers (init_chrome_with_timeout, get_chrome_major_version)
are kept for the job_verifier.py (which uses undetected_chromedriver for LinkedIn/Indeed).
"""
from __future__ import annotations

import re
import threading
import random
from typing import Iterable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ─────────────────────────────────────────────────────────────────────────────
# Playwright browser pool — Phase 4
# ─────────────────────────────────────────────────────────────────────────────

_PW_LOCK     = threading.Lock()
_pw_instance = None   # playwright SyncPlaywright handle
_pw_browser  = None   # shared Chromium browser (kept alive across scrapes)

_POOL_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

_PW_LAUNCH_ARGS = [
    '--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
    '--disable-gpu', '--disable-background-networking', '--disable-extensions',
    '--disable-translate', '--disable-sync',
]


def get_pw_browser():
    """Return the shared Playwright Chromium browser (singleton per process).

    Launches on first call. Thread-safe — concurrent scrapers in the same
    Celery worker share one browser instance and each get their own context.
    """
    global _pw_instance, _pw_browser
    with _PW_LOCK:
        if _pw_browser is None or not _pw_browser.is_connected():
            try:
                if _pw_instance is not None:
                    try:
                        _pw_instance.stop()
                    except Exception:
                        pass
                from playwright.sync_api import sync_playwright
                _pw_instance = sync_playwright().start()
                _pw_browser = _pw_instance.chromium.launch(
                    headless=True,
                    args=_PW_LAUNCH_ARGS,
                )
            except Exception as exc:
                raise RuntimeError(f"Playwright browser launch failed: {exc}") from exc
    return _pw_browser


def new_pw_context(user_agent: Optional[str] = None, block_images: bool = True):
    """Create a fresh browser context from the shared pool.

    Each context is isolated (own cookies, cache, storage) — equivalent to
    a new incognito window. Caller MUST call context.close() when done.

    Returns the context. Get a page with: page = context.new_page()
    """
    browser = get_pw_browser()
    ua = user_agent or random.choice(_POOL_USER_AGENTS)
    ctx = browser.new_context(
        user_agent=ua,
        viewport={'width': 1920, 'height': 1080},
        locale='pt-PT',
        extra_http_headers={'Accept-Language': 'pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7'},
    )
    if block_images:
        # Block images/fonts/media to speed up page loads significantly
        ctx.route(
            '**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,otf,mp4,mp3}',
            lambda route: route.abort(),
        )
    return ctx


def apply_stealth(page) -> None:
    """Apply anti-bot detection patches to a Playwright page.

    Uses playwright-stealth if installed; falls back to a minimal manual patch
    that hides the webdriver property.
    """
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
        return
    except ImportError:
        pass
    # Minimal fallback — hides navigator.webdriver
    page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Negative keyword matching (word-boundary)
# ─────────────────────────────────────────────────────────────────────────────
def negative_keyword_match(text: str, negatives: Iterable[str]) -> Optional[str]:
    """Returns the first matching negative keyword (with word boundaries), or None.

    Uses ``\\b`` so 'intern' does NOT match 'international', 'manager' does NOT
    match 'managers', etc. Compare to the previous inline behaviour
    `nkw in text` which produced false positives.

    Keywords that start with a non-word character (e.g. '.net') use
    lookahead/lookbehind instead of \\b, since \\b does not anchor against
    punctuation. This allows '.net' to match '(.NET)' and 'Tech Lead .NET'
    without false-positives on 'internet'.
    """
    if not text or not negatives:
        return None
    text_lower = text.lower()
    for kw in negatives:
        kw_clean = (kw or '').strip().lower()
        if not kw_clean:
            continue
        # \b only works between word/non-word transitions. For keywords starting
        # with punctuation (e.g. '.net'), use lookahead/behind instead.
        if kw_clean[0].isalnum() or kw_clean[0] == '_':
            pattern = r'\b' + re.escape(kw_clean) + r'\b'
        else:
            pattern = r'(?<!\w)' + re.escape(kw_clean) + r'(?!\w)'
        if re.search(pattern, text_lower):
            return kw_clean
    return None


# ─────────────────────────────────────────────────────────────────────────────
# HTTP session factory
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}


# ─────────────────────────────────────────────────────────────────────────────
# Seniority extraction (from title, falling back to first 500 chars of desc)
# ─────────────────────────────────────────────────────────────────────────────
_SENIORITY_MAP = [
    # Most specific / senior first so they short-circuit before generic ones.
    # Patterns cover both English and Portuguese job title conventions.
    (re.compile(r'\b(cto|ceo|coo|cpo|ciso|chief\s+\w+\s+officer)\b', re.I),                          'C-Level'),
    (re.compile(r'\b(vp|vice.?president|vice.?presidente)\b', re.I),                                   'VP'),
    (re.compile(r'\b(director|diretor[a]?|diretora|head\s+of|responsável)\b', re.I),                   'Director'),
    (re.compile(r'\b(staff\s+engineer|principal\s+engineer|principal)\b', re.I),                       'Staff / Principal'),
    # Manager: EN + PT (gestor, gestora, coordenador, coordenadora, chefe de)
    (re.compile(r'\b(engineering\s+manager|tech(?:nical)?\s+manager|gestor[a]?|coordenador[a]?|chefe\s+de)\b', re.I), 'Manager'),
    # Lead: EN + PT (líder, responsável técnico)
    (re.compile(r'\b(tech(?:nical)?\s+lead|team\s+lead|lead\s+\w+|l[íi]der|responsável\s+t[eé]cnico)\b', re.I), 'Lead'),
    # Senior: EN + PT variants (sénior, snr)
    (re.compile(r'\b(senior|s[eé]nior|sr\.?|snr\.?)\b', re.I),                                        'Sénior'),
    # Mid-level: EN + PT (pleno, intermédio)
    (re.compile(r'\b(mid.?level|pleno|middle|interm[eé]dio)\b', re.I),                                 'Mid-Level'),
    # Junior: EN + PT (júnior, estagiário, trainee, estágio)
    (re.compile(r'\b(junior|j[uú]nior|jr\.?|entry.?level|associate|estagi[áa]rio|trainee|est[áa]gio)\b', re.I), 'Júnior'),
]


def extract_seniority(titulo: str, descricao: str = '') -> str:
    """Infer seniority level from job title (primary) then description (fallback).

    Returns a normalised label like 'Sénior', 'Lead', 'Júnior', etc., or ''
    when nothing can be inferred.
    """
    for pattern, label in _SENIORITY_MAP:
        if pattern.search(titulo or ''):
            return label
    for pattern, label in _SENIORITY_MAP:
        if pattern.search((descricao or '')[:500]):
            return label
    return ''


# ─────────────────────────────────────────────────────────────────────────────
# Salary extraction from free text (descriptions)
# ─────────────────────────────────────────────────────────────────────────────
_SALARY_RE = re.compile(
    r'(?:'
    # Symbol+amount+k  must come before plain symbol+amount to avoid truncation
    r'[\$€£]\s*\d+[kK]'                                              # €50k / $120k
    r'|\d+[kK]\s*[\$€£]'                                             # 50k€
    r'|\d{2,3}\s*[kK]\s*(?:EUR|USD|GBP|por\s+ano|anuais|gross|/year|per\s+year)'  # 80k EUR
    r'|[\$€£]\s*\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?'              # €50,000 / €50.000,00
    r'|\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?\s*[\$€£]'              # 50.000€
    r')',
    re.I,
)


def extract_salary_from_text(text: str) -> str:
    """Return the first salary-looking pattern found in the first 1 000 chars of text,
    or '' if none found.
    """
    if not text:
        return ''
    m = _SALARY_RE.search(text[:1000])
    return m.group(0).strip() if m else ''


def get_chrome_major_version() -> Optional[int]:
    """Returns installed Chrome major version.

    Reads CHROME_VERSION env var (set by orchestrator at startup) to avoid a
    subprocess call on every scraper. Falls back to subprocess detection.
    """
    import subprocess as _sp
    cached = __import__('os').environ.get('CHROME_VERSION', '')
    if cached:
        try:
            return int(cached)
        except ValueError:
            pass
    try:
        r = _sp.run(['google-chrome', '--version'], capture_output=True, text=True, timeout=5)
        m = re.search(r'(\d+)\.', r.stdout)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def strip_html(html: str) -> str:
    """Convert HTML to clean plain text — preserves list items and paragraphs as newlines."""
    if not html:
        return ''
    text = re.sub(r'<br\s*/?>', '\n', html, flags=re.I)
    text = re.sub(r'</(p|li|h\d|div|tr)\s*>', '\n', text, flags=re.I)
    text = re.sub(r'<li[^>]*>', '• ', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


def init_chrome_with_timeout(options, *, headless: bool = True, version_main=None, timeout_s: int = 90):
    """Initialize an undetected Chrome driver with a hard timeout.

    Uses SIGALRM on Linux (Docker container) so the blocking uc.Chrome() call
    is actually interrupted — not just abandoned. Calls sys.exit(1) on timeout
    so the scraper subprocess exits cleanly instead of hanging for 2 hours.

    Args:
        options:      uc.ChromeOptions instance already configured.
        headless:     Pass through to uc.Chrome(headless=...).
        version_main: Chrome major version (None = auto-detect).
        timeout_s:    Seconds before giving up (default 90).
    """
    import signal
    import sys
    import undetected_chromedriver as uc

    def _on_timeout(signum, frame):
        raise OSError(f"Chrome driver init timed out after {timeout_s}s")

    kwargs: dict = {"options": options, "headless": headless}
    if version_main is not None:
        kwargs["version_main"] = version_main

    if hasattr(signal, "SIGALRM"):  # Linux / macOS (not Windows)
        old_handler = signal.signal(signal.SIGALRM, _on_timeout)
        signal.alarm(timeout_s)
    try:
        driver = uc.Chrome(**kwargs)
        return driver
    except OSError as exc:
        # Raise RuntimeError so callers' `except Exception` blocks can catch it
        # and implement fallback logic (e.g. HTTP-only mode in Sapo scraper).
        # Previously called sys.exit(1) which raises SystemExit(BaseException),
        # bypassing all `except Exception` handlers in the call stack.
        raise RuntimeError(f"Chrome driver init failed: {exc}") from exc
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)


def make_session(
    *,
    retries: int = 3,
    backoff: float = 1.5,
    status_forcelist=(500, 502, 503, 504, 429),
    headers: Optional[dict] = None,
) -> requests.Session:
    """Returns a `requests.Session` with retries + default headers configured.

    The session retries on transient 5xx/429 with exponential backoff,
    handles both http and https, and ships with PT/EN Accept-Language plus
    a desktop Chrome User-Agent. Override `headers` to replace the defaults
    or pass `User-Agent` to override just the UA.

    backoff_factor=1.5 → waits 1.5s, 3s, 6s between retries (aggressive enough
    to clear rate limits without hanging the scraper for minutes).
    """
    sess = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=list(status_forcelist),
        allowed_methods=['GET', 'POST', 'HEAD'],
    )
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    sess.headers.update(headers or DEFAULT_HEADERS)
    return sess
