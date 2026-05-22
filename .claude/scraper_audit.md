# Scraper Deep Audit — Tasks & Improvements

Generated: 2026-05-22. Full read of all 7 scrapers + `_shared.py` + `db_helper.py` + `companies.json`.
**Last updated: 2026-05-22 — Groups 1, 2, 3 implemented (commit 04997f7). Groups 4, 5 deferred.**

Priority: 🔴 Bug · 🟡 Improvement · 🟢 Nice-to-have
Status: ✅ Done · 🔲 Pending · ⏭️ Deferred · ❌ Not a bug (investigation)

---

## _shared.py

| # | Status | Priority | Task |
|---|---|---|---|
| S-1 | ✅ | 🔴 | `init_chrome_with_timeout` `sys.exit(1)` bypasses `except Exception` — fixed: now raises `RuntimeError` |
| S-2 | ⏭️ | 🟡 | `get_chrome_major_version` subprocess timeout hardcoded at 5s — add env var `CHROME_VERSION_DETECT_TIMEOUT` |
| S-3 | ⏭️ | 🟡 | `extract_seniority` only reads first 500 chars of description — increase to 1000 |
| S-4 | ⏭️ | 🟢 | `negative_keyword_match` doesn't log which keyword triggered — add optional `log=True` param |

---

## linkedin_scraper.py

| # | Status | Priority | Task |
|---|---|---|---|
| L-1 | ✅ | 🔴 | Duplicate parsing code in HTTP+Selenium extraction — consolidated into `_parse_linkedin_soup()` |
| L-2 | ✅ | 🔴 | HTTP extraction almost always empty (JS-rendered) — kept HTTP but clearly documented; Selenium fallback is the real path |
| L-3 | ✅ | 🔴 | `seen_jobs` dedup breaks when `empresa_raw = "Not specified"` — fixed: only dedup when empresa is known |
| L-4 | ⏭️ | 🟡 | Guest API `f_TPR=r86400` may miss results for narrow time windows — add `LINKEDIN_FALLBACK_NO_TPR` env var |
| L-5 | ⏭️ | 🟡 | `_normalize_linkedin_url` regex `[a-z]{2}` doesn't handle `www.` — fix to `(?:[a-z]{2}\|m)` |
| L-6 | ⏭️ | 🟡 | No sleep between HTTP job detail extractions — could trigger rate limiting |
| L-7 | ⏭️ | 🟡 | Selenium listing fallback `&start=` append could duplicate if URL already has it |
| L-8 | ⏭️ | 🟡 | Selenium listing fallback CSS selectors — LinkedIn A/B tests new class names |
| L-9 | ✅ | 🟢 | `subprocess` import removed when `_get_chrome_major_version` was centralised |
| L-10 | ⏭️ | 🟢 | Recruiter extraction only in Selenium path — acceptable |

---

## itjobs_scraper.py

| # | Status | Priority | Task |
|---|---|---|---|
| I-1 | ⏭️ | 🔴 | `int(j['id_externo'])` in `_fetch_location` throws `ValueError` for non-numeric id — add try/except |
| I-2 | ✅ | 🔴 | `future.result(timeout=8)` < request `timeout=10` causes thread leaks — fixed to `timeout=12` |
| I-3 | ⏭️ | 🟡 | API key wrong → 401 on every request silently — add abort on 401 |
| I-4 | ✅ | 🟡 | `categoria='IT'` hardcoded — fixed: now uses `categoria=q` (search query) |
| I-5 | ⏭️ | 🟡 | `_format_salary` doesn't guard non-numeric values from API |
| I-6 | ✅ | 🟡 | No date filter — added `publishedAt.from` for last 7 days |
| I-7 | ⏭️ | 🟢 | Early-stop triggers on `page > 1` — could also trigger page 1 when all results are known |
| I-8 | ⏭️ | 🟢 | `KEYWORDS` only uses job_titles — could include `get_target_roles()` for richer search |

---

## companies_scraper.py

| # | Status | Priority | Task |
|---|---|---|---|
| C-1 | ✅ | 🔴 | Lever `data = resp.json() or []` keeps error dicts — fixed: `isinstance(data, list)` guard |
| C-2 | ✅ | 🔴 | Ashby `isListed is False` misses `None` — fixed: `not raw.get('isListed', True)` |
| C-3 | ✅ | 🔴 | Gympass + Wellhub same `greenhouse_board: "gympass"` — Gympass deactivated in companies.json |
| C-4 | ✅ | 🔴 | Canonical active but blocked — deactivated in companies.json (saves 100+ wasted API calls) |
| C-5 | ⏭️ | 🟡 | `ThreadPoolExecutor(max_workers=8)` could hit same-domain rate limits — reduce to 5, env var |
| C-6 | ⏭️ | 🟡 | Per-company title dedup too strict — strip location suffix before comparing |
| C-7 | ⏭️ | 🟡 | `_NON_EU_EXCLUDE` doesn't block "Worldwide" or "Global Remote" |
| C-8 | ⏭️ | 🟡 | HTML fallback selector too broad — matches nav links |
| C-9 | ⏭️ | 🟡 | Results dict keyed by `company['name']` — collision risk if two companies share a name |
| C-10 | ⏭️ | 🟡 | No per-company yield tracking — `[ZERO YIELD]` tag for consistent zero-yielders |
| C-11 | ⏭️ | 🟢 | `_pick_strategy` priority order undocumented |
| C-12 | ⏭️ | 🟢 | ~15 SPA companies (Workday, Oracle Cloud) always yield 0 — mark `is_active: false` |

---

## landing_scraper.py

| # | Status | Priority | Task |
|---|---|---|---|
| LJ-1 | ⏭️ | 🔴 | `if len(results) < 50` hardcoded page size assumption — use named constant |
| LJ-2 | ⏭️ | 🟡 | `min(KEYWORDS, key=len)` could pick "vp" (2 chars) — add minimum length guard |
| LJ-3 | ⏭️ | 🟡 | `country_code == 'PT'` doesn't cover 'PRT' (ISO alpha-3) |
| LJ-4 | ⏭️ | 🟡 | `broad_count >= 2` accepts generic terms — require title match as prerequisite |
| LJ-5 | ⏭️ | 🟡 | Company name cache resets every run — persist to `tmp/landing_company_cache.json` (7d TTL) |
| LJ-6 | ⏭️ | 🟢 | No date filter — Landing.jobs API supports `published_after` |

---

## indeed_scraper.py

| # | Status | Priority | Task |
|---|---|---|---|
| IN-1 | ❌ | 🔴 | Sleep reported inside job loop — **verified: sleep IS between pages**, not between cards. Not a bug. |
| IN-2 | ✅ | 🔴 | Bare `except:` in WebDriverWait loop — fixed to `except Exception:` |
| IN-3 | ✅ | 🔴 | No warm-up after driver restart — fixed: adds homepage visit after restart |
| IN-4 | ✅ | 🔴 | Absolute URL concatenation risk — verified: guard already exists (`startswith('http')`) |
| IN-5 | ✅ | 🟡 | `class_='date'` stale selector — added `data-testid='myJobsStateDate'` + regex fallbacks |
| IN-6 | ⏭️ | 🟡 | Tab accumulation on Chrome crash — add `len(window_handles) > 3` cleanup check |
| IN-7 | ✅ | 🟡 | `fromage=1` filter — confirmed present in `generate_indeed_urls()` in profile_fetcher |
| IN-8 | ⏭️ | 🟢 | `INDEED_ZERO_ABORT=3` — consider reducing to 2 |
| IN-9 | ✅ | 🟢 | `subprocess` import removed when `get_chrome_major_version` was centralised |

---

## sapo_scraper.py

| # | Status | Priority | Task |
|---|---|---|---|
| SA-1 | ✅ | 🔴 | Bare `except:` swallows SystemExit/KeyboardInterrupt — fixed to `except Exception:` |
| SA-2 | ⏭️ | 🔴 | `total_novas_global` in `processar_pesquisa` is local copy — MAX_JOBS check uses stale count. Works accidentally but is misleading. Clean up. |
| SA-3 | ✅ | 🔴 | `vue_component_tag.get(':offers')` misses `v-bind:offers` — added fallback |
| SA-4 | ✅ | 🟡 | `sys.exit(1)` bypass (fixed via S-1 — now raises RuntimeError caught by HTTP-only fallback) |
| SA-5 | ⏭️ | 🟡 | 429 from Sapo not logged explicitly |
| SA-6 | ⏭️ | 🟡 | `.half-horizontal-padding` utility class used as fallback selector — fragile |
| SA-7 | ⏭️ | 🟡 | MAX_JOBS break inside `processar_pesquisa` only breaks inner loop — code smell |
| SA-8 | ⏭️ | 🟢 | Verify `&remote_work=1` Sapo parameter still works |
| SA-9 | ⏭️ | 🟢 | `json.loads()` should catch `json.JSONDecodeError` specifically |

---

## expresso_scraper.py

| # | Status | Priority | Task |
|---|---|---|---|
| E-1 | ✅ | 🔴 | No pagination — added `EXPRESSO_MAX_PAGES=5` loop with per-page dedup |
| E-2 | ✅ | 🔴 | Regex required exact 3 URL segments — fixed to `(?:[^/]+/)+` (any depth) |
| E-3 | ⏭️ | 🟡 | Session cookies may expire during long runs — add re-warm every 10 searches |
| E-4 | ⏭️ | 🟡 | `og:description \|` split fragile for company name extraction |
| E-5 | ⏭️ | 🟡 | `max(desc_candidates, key=len)` could pick cookie/nav HTML |
| E-6 | ✅ | 🟡 | No run-level dedup — added `seen_links: set` across all queries+pages |
| E-7 | ⏭️ | 🟢 | Sleep duration conservative (4-6s) — could reduce once pagination is stable |
| E-8 | ⏭️ | 🟢 | `seen_ids` in `_extract_job_links` handles per-page dedup — documented |

---

## db_helper.py

| # | Status | Priority | Task |
|---|---|---|---|
| DB-1 | ⏭️ | 🟡 | `LOWER(TRIM(titulo))` secondary check has no functional index — add to `init_db.py` |
| DB-2 | ⏭️ | 🟡 | `job_exists` uses fixed 1s sleep vs `execute_with_retry` exponential backoff — unify |
| DB-3 | ⏭️ | 🟡 | `PRAGMA journal_mode=WAL` on every connection — cache check |
| DB-4 | ⏭️ | 🟢 | `save_job` docstring says "Schema v5" — update to v6 |
| DB-5 | ⏭️ | 🟢 | `save_job` doesn't accept `relevance_score` — not needed now |

---

## companies.json

| # | Status | Priority | Task |
|---|---|---|---|
| CJ-1 | ✅ | 🔴 | Gympass duplicate of Wellhub — deactivated |
| CJ-2 | ✅ | 🔴 | Canonical active but universally blocked — deactivated |
| CJ-3 | ⏭️ | 🟡 | ~15 SPA companies (Workday, Oracle Cloud, etc.) always yield 0 — audit + deactivate |
| CJ-4 | ⏭️ | 🟡 | Several `careers_url` point to homepages/about pages, not job listings |
| CJ-5 | ⏭️ | 🟡 | No `last_verified` field — add for quarterly audit |
| CJ-6 | ⏭️ | 🟢 | Bank/telco HTML companies likely SPAs — audit zero-yield entries |
| CJ-7 | ⏭️ | 🟢 | Foursys uses Rankdone ATS — check for public JSON API |

---

## Cross-Scraper

| # | Status | Priority | Task |
|---|---|---|---|
| X-1 | ✅ | 🔴 | `sys.exit(1)` in `_shared.py` bypassed all fallbacks — fixed to `RuntimeError` |
| X-2 | ✅ | 🔴 | Double enrichment (orchestrator called `--backfill` + scorer ran it internally) — removed duplicate call |
| X-3 | ⏭️ | 🟡 | `db_helper` import not guarded in scrapers — unhandled ImportError if SQLite unavailable |
| X-4 | ✅ | 🟡 | Chrome version detection: confirmed `CHROME_VERSION` env var is passed via `run_scraper()` |
| X-5 | ⏭️ | 🟡 | `make_session()` retry profile same for all scrapers — acceptable |
| X-6 | ⏭️ | 🟢 | `_LOCAL_PROFILE_CACHE` invariant — document that TARGET_USER_ID must not change mid-process |
| X-7 | ⏭️ | 🟢 | Inconsistent timestamp usage in print statements |

---

## Summary

| Group | Tasks | Done | Deferred |
|---|---|---|---|
| Group 1 — Critical bugs | 7 | 7 ✅ | 0 |
| Group 2 — Data quality | 7 | 6 ✅ | 1 (LJ-6 — Landing date filter, param unverified) |
| Group 3 — Reliability | 5 | 5 ✅ | 0 |
| Group 4 — Performance | 4 | 0 | 4 ⏭️ |
| Group 5 — Cleanup | 9 | 0 | 9 ⏭️ |
| **Total tracked** | **32** | **18 ✅** | **14 ⏭️** |

Note: IN-1 (sleep in job loop) was reported by agent analysis but **verified as not a bug** — sleep is correctly between pages.
