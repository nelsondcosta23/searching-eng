"""Shared helpers used across scrapers.

Centralizes patterns that were duplicated across most scrapers (audit Fase G):
  - Word-boundary negative-keyword matching (also fixes a substring bug
    where 'intern' would block 'international').
  - HTTP session with retries + sane headers.

The Chrome driver factory and per-scraper base class are deliberately not
unified yet — each Selenium scraper has subtle UA / option / lock-cleanup
differences and consolidating them is higher-risk than the value warrants
for now.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ─────────────────────────────────────────────────────────────────────────────
# Negative keyword matching (word-boundary)
# ─────────────────────────────────────────────────────────────────────────────
def negative_keyword_match(text: str, negatives: Iterable[str]) -> Optional[str]:
    """Returns the first matching negative keyword (with word boundaries), or None.

    Uses ``\\b`` so 'intern' does NOT match 'international', 'manager' does NOT
    match 'managers', etc. Compare to the previous inline behaviour
    `nkw in text` which produced false positives.
    """
    if not text or not negatives:
        return None
    text_lower = text.lower()
    for kw in negatives:
        kw_clean = (kw or '').strip().lower()
        if not kw_clean:
            continue
        if re.search(r'\b' + re.escape(kw_clean) + r'\b', text_lower):
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


def make_session(
    *,
    retries: int = 3,
    backoff: float = 0.5,
    status_forcelist=(500, 502, 503, 504, 429),
    headers: Optional[dict] = None,
) -> requests.Session:
    """Returns a `requests.Session` with retries + default headers configured.

    The session retries on transient 5xx/429 with exponential backoff,
    handles both http and https, and ships with PT/EN Accept-Language plus
    a desktop Chrome User-Agent. Override `headers` to replace the defaults
    or pass `User-Agent` to override just the UA.
    """
    sess = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=list(status_forcelist),
    )
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    sess.headers.update(headers or DEFAULT_HEADERS)
    return sess
