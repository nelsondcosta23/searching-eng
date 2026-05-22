"""
Job Relevance Scorer — TF-IDF inspired keyword matching.

Scores each job (0–100) based on how well it matches the user's professional
profile (search_description, target roles, industries, seniority preferences).

No external ML dependencies required — pure Python implementation.
"""
import re
import os
import sys
import sqlite3
import time
import random
from collections import Counter

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from automation.profile_fetcher import get_search_description, get_profile_filters, get_target_roles, get_user_id, get_negative_keywords
from automation.db_helper import transaction
from scrapers._shared import extract_seniority, extract_salary_from_text, negative_keyword_match

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))

# Commit every N rows during scoring. Keeps WAL small and lets readers run
# between batches (instead of starving them for the whole scoring run).
_SCORER_BATCH_SIZE = int(os.environ.get('SCORER_BATCH_SIZE', '200'))

# Portuguese and English stopwords to ignore in scoring
_STOPWORDS = {
    'de', 'da', 'do', 'em', 'com', 'para', 'por', 'que', 'uma', 'um', 'os', 'as',
    'e', 'o', 'a', 'se', 'na', 'no', 'mais', 'mas', 'ou', 'ao', 'são', 'ser',
    'the', 'and', 'or', 'in', 'of', 'to', 'a', 'for', 'with', 'on', 'at', 'by',
    'is', 'are', 'be', 'an', 'we', 'our', 'you', 'your', 'it', 'that', 'this',
    'will', 'can', 'have', 'has', 'from', 'as', 'all', 'its', '-', '–', '|',
}


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-word chars, remove stopwords and short tokens."""
    if not text:
        return []
    tokens = re.findall(r"[a-zA-ZÀ-ÿ0-9]+(?:['\-][a-zA-ZÀ-ÿ0-9]+)*", text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 2]


def _build_profile_terms() -> dict[str, float]:
    """
    Builds a weighted term dictionary from the user's profile.
    Returns {term: weight} where weight reflects importance.
    """
    weights: dict[str, float] = {}

    # Generic single-word tokens that appear in almost any tech job description.
    # They help rank CTO jobs above non-tech jobs, but should not inflate SE jobs
    # to the same score as CTO jobs when the job TITLE clearly matches.
    _GENERIC_TOKENS = {
        'tech', 'technology', 'technical', 'product', 'head', 'lead', 'leader',
        'leadership', 'development', 'officer', 'director', 'manager', 'engineering',
        'engineer', 'software', 'digital', 'data', 'cloud', 'platform', 'strategy',
        'strategic',
    }

    # Role titles — very high weight for discriminative tokens, low for generic ones
    for role in get_target_roles():
        for token in _tokenize(role):
            if token in _GENERIC_TOKENS:
                weights[token] = max(weights.get(token, 0), 2.0)
            else:
                weights[token] = max(weights.get(token, 0), 10.0)
        # Full role phrase match gives strong bonus regardless of individual tokens
        phrase_key = role.lower().strip()
        weights[f'__phrase__{phrase_key}'] = 20.0

    filters = get_profile_filters()

    # Seniority level terms — high weight
    for level in filters.get('seniority_level', []):
        for token in _tokenize(level):
            weights[token] = max(weights.get(token, 0), 8.0)

    # Industry terms — medium weight
    for industry in filters.get('industries', []):
        for token in _tokenize(industry):
            weights[token] = max(weights.get(token, 0), 5.0)

    # General profile description — base weight
    for token in _tokenize(get_search_description()):
        if token not in weights:
            weights[token] = 1.5

    return weights


_SALARY_NUMBER_RE = re.compile(
    r'[\$€£]?\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*[kK]?[\$€£]?'
)


def _parse_salary_value(text: str) -> int:
    """Parse a salary string to an integer annual value in the original currency.

    Returns 0 when no value can be extracted.
    Examples:
      'EUR 50.000–73.000' → 50000  (lower bound)
      '€50k'              → 50000
      '80k EUR'           → 80000
      '65.000€'           → 65000
    """
    if not text:
        return 0
    # Handle 'k' multiplier first (e.g. '50k', '80k EUR')
    k_match = re.search(r'(\d+)\s*[kK]', text)
    if k_match:
        return int(k_match.group(1)) * 1000
    # Strip thousand separators so "40.000–60.000" becomes "40000–60000"
    cleaned = re.sub(r'[.,]', '', text)
    # Detect salary range and return the midpoint. "EUR 40000–60000" → 50000
    # instead of 40000 (the lower bound), which avoids false salary penalties.
    range_match = re.search(r'(\d{4,})\s*[–\-]\s*(\d{4,})', cleaned)
    if range_match:
        low  = int(range_match.group(1))
        high = int(range_match.group(2))
        return (low + high) // 2
    m = re.search(r'\d{4,}', cleaned)  # require at least 4 digits (salary ≥ 1000)
    if m:
        try:
            return int(m.group(0))
        except ValueError:
            pass
    return 0


def _score_job(titulo: str, empresa: str, descricao: str, observacoes: str,
               profile_terms: dict[str, float],
               min_salary: int = 0,
               job_salary_text: str = '',
               profile_seniority: list = None,
               negative_keywords: list = None) -> int:
    """
    Computes a relevance score (0–100) for a single job.

    Scoring strategy:
      - Title match:         weight × 4  (most signal)
      - Observations match:  weight × 2  (structured metadata)
      - Description match:   weight × 1  (broad context)
      - Salary modifier:     ±10/−25 vs min_salary threshold
      - Seniority match:     +8 when job level matches profile preference
    """
    title_tokens = _tokenize(titulo)
    obs_tokens   = _tokenize(observacoes)
    desc_tokens  = _tokenize(descricao)

    raw_score = 0.0
    all_text_lower = f"{titulo} {observacoes} {descricao}".lower()

    for term, weight in profile_terms.items():
        if term.startswith('__phrase__'):
            phrase = term.replace('__phrase__', '')
            if re.search(r'\b' + re.escape(phrase) + r'\b', all_text_lower):
                raw_score += weight
        else:
            if term in title_tokens:
                raw_score += weight * 4
            if term in obs_tokens:
                raw_score += weight * 2
            if term in desc_tokens:
                raw_score += weight * 1

    # Normalize to 0–100. Divisor 175 keeps genuine title matches visible
    # (CTO exact match → raw≈60 → score≈34 before floor) while still
    # preventing keyword-spam descriptions from dominating.
    normalized = min(100, int((raw_score / 175.0) * 100))

    # --- Title-match floor: guarantee score ≥ 55 when a profile role term
    # appears in the job title — prevents sparse descriptions from burying
    # genuinely relevant roles. Checks both token matches (weight ≥ 8) and
    # phrase matches against the raw title text (catches generic-token roles
    # like "Head of Technology" whose tokens all have weight 2.0). ---
    if profile_terms:
        title_text_lower = titulo.lower()
        has_title_match = any(
            (not term.startswith('__phrase__') and term in title_tokens and weight >= 8.0)
            or (term.startswith('__phrase__') and weight >= 15.0 and
                re.search(r'\b' + re.escape(term.replace('__phrase__', '')) + r'\b', title_text_lower))
            for term, weight in profile_terms.items()
        )
        if has_title_match:
            normalized = max(normalized, 55)

    # --- Salary modifier ---
    if min_salary > 0:
        salary_val = _parse_salary_value(job_salary_text)
        if salary_val > 0:
            if salary_val >= min_salary:
                normalized = min(100, normalized + 10)   # meets expectation → boost
            elif salary_val < min_salary * 0.75:         # >25% below threshold → strong penalty
                normalized = max(0, normalized - 25)
            else:                                         # slightly below → mild penalty
                normalized = max(0, normalized - 10)

    # --- Seniority match modifier ---
    if profile_seniority:
        job_level = extract_seniority(titulo, descricao[:300])
        if job_level:
            norm_level = job_level.lower()
            matches = any(
                norm_level in ps.lower() or ps.lower() in norm_level
                for ps in profile_seniority
            )
            if matches:
                normalized = min(100, normalized + 8)

    # --- Negative keyword penalty (cap score at 15 for jobs that should be filtered) ---
    # Handles jobs saved BEFORE negative_keywords were added to the profile.
    if negative_keywords and negative_keyword_match(titulo.lower(), negative_keywords):
        normalized = min(normalized, 15)

    return normalized


def score_and_update_unscored_jobs():
    """
    Finds all jobs with NULL relevance_score, computes their score and saves it.
    Called at the end of each orchestration run.

    Reads the candidate list once, then writes scores in batches of
    `_SCORER_BATCH_SIZE` so readers (API/dashboard/webhook) aren't starved
    for the full scoring run. Each batch is its own retried transaction.
    """
    if not os.path.exists(DB_PATH):
        print("[scorer] Database not found. Skipping scoring.")
        return

    user_id = get_user_id()
    print(f"\n[scorer] Building profile term weights for user: {user_id}...")
    profile_terms = _build_profile_terms()
    print(f"[scorer] Profile terms loaded: {len(profile_terms)} unique weighted terms.")

    filters           = get_profile_filters()
    min_salary        = int(filters.get('min_salary') or 0)
    profile_seniority = list(filters.get('seniority_level') or [])
    neg_keywords      = get_negative_keywords()
    if min_salary:
        print(f"[scorer] Salary filter active: min_salary={min_salary}")
    if profile_seniority:
        print(f"[scorer] Seniority filter active: {profile_seniority}")
    if neg_keywords:
        print(f"[scorer] Negative keywords active: {len(neg_keywords)} terms")

    # Phase 1: read candidates (short-lived read connection)
    with transaction(row_factory=sqlite3.Row) as conn:
        rows = conn.execute("""
            SELECT id, titulo, empresa,
                   COALESCE(descricao_completa, '') AS descricao_completa,
                   COALESCE(observacoes, '')         AS observacoes,
                   COALESCE(salario, '')             AS salario
            FROM vagas
            WHERE relevance_score IS NULL AND user_id = ?
        """, (user_id,)).fetchall()

    if not rows:
        print("[scorer] No unscored jobs found. All up to date.")
        return

    print(f"[scorer] Scoring {len(rows)} unscored jobs (batch={_SCORER_BATCH_SIZE})...")

    # Phase 2: compute scores in memory first (CPU-only, no DB lock held)
    scored_pairs = []
    for row in rows:
        score = _score_job(
            titulo=row['titulo'] or '',
            empresa=row['empresa'] or '',
            descricao=row['descricao_completa'],
            observacoes=row['observacoes'],
            profile_terms=profile_terms,
            min_salary=min_salary,
            job_salary_text=row['salario'] or '',
            profile_seniority=profile_seniority,
            negative_keywords=neg_keywords,
        )
        scored_pairs.append((score, row['id']))

    # Phase 3: write in batches with retry
    total_written = 0
    for i in range(0, len(scored_pairs), _SCORER_BATCH_SIZE):
        batch = scored_pairs[i:i + _SCORER_BATCH_SIZE]
        try:
            with transaction() as conn:
                conn.executemany(
                    "UPDATE vagas SET relevance_score = ? WHERE id = ?",
                    batch,
                )
            total_written += len(batch)
        except Exception as e:
            print(f"[scorer] ⚠ Batch {i // _SCORER_BATCH_SIZE + 1} failed: {e}. Continuing...")

    print(f"[scorer] ✅ Scored {total_written}/{len(rows)} jobs successfully.")

    # Phase 4: show top 5 matches (separate short read)
    with transaction(row_factory=sqlite3.Row) as conn:
        top = conn.execute("""
            SELECT titulo, empresa, plataforma, relevance_score
            FROM vagas
            WHERE relevance_score IS NOT NULL AND user_id = ?
            ORDER BY relevance_score DESC
            LIMIT 5
        """, (user_id,)).fetchall()

    if top:
        print("\n[scorer] 🏆 Top 5 most relevant jobs in DB:")
        for i, job in enumerate(top, 1):
            print(f"  {i}. [{job['relevance_score']:3d}] {job['titulo']} @ {job['empresa']} ({job['plataforma']})")


def backfill_enrichment():
    """Backfill nivel_experiencia and salario for existing jobs that are missing them.

    Runs after scoring so it benefits from the same connection pattern.
    Processes vagas that have a description but still have empty enrichment fields.
    Safe to call repeatedly — only touches rows that need filling.
    """
    if not os.path.exists(DB_PATH):
        return

    user_id = get_user_id()

    with transaction(row_factory=sqlite3.Row) as conn:
        rows = conn.execute("""
            SELECT id, titulo, descricao_completa, salario, nivel_experiencia
            FROM vagas
            WHERE user_id = ?
              AND descricao_completa IS NOT NULL
              AND LENGTH(descricao_completa) > 100
              AND (
                  nivel_experiencia IS NULL OR nivel_experiencia = ''
                  OR salario IS NULL OR salario = ''
              )
        """, (user_id,)).fetchall()

    if not rows:
        print("[enrichment] All enrichable rows already filled. Nothing to do.")
        return

    print(f"[enrichment] Backfilling {len(rows)} rows with nivel_experiencia / salario...")

    updated_nivel = 0
    updated_sal = 0
    pairs = []
    for row in rows:
        new_nivel = row['nivel_experiencia'] or ''
        new_sal   = row['salario'] or ''

        if not new_nivel:
            new_nivel = extract_seniority(row['titulo'] or '', row['descricao_completa'] or '')
            if new_nivel:
                updated_nivel += 1

        if not new_sal:
            new_sal = extract_salary_from_text(row['descricao_completa'] or '')
            if new_sal:
                updated_sal += 1

        if new_nivel != (row['nivel_experiencia'] or '') or new_sal != (row['salario'] or ''):
            pairs.append((new_nivel or None, new_sal or None, row['id']))

    if pairs:
        with transaction() as conn:
            conn.executemany(
                "UPDATE vagas SET nivel_experiencia = ?, salario = ? WHERE id = ?",
                pairs,
            )

    print(f"[enrichment] ✅ nivel_experiencia filled: {updated_nivel} | salario filled: {updated_sal}")


def rescore_all_jobs():
    """Force-rescores ALL jobs in the database (useful after profile update).

    Atomically nullifies all scores then re-scores. If re-scoring fails the
    nullification still stands — but the next regular scorer run will pick up
    NULL rows and rescore them, so we never lose data permanently.
    """
    if not os.path.exists(DB_PATH):
        print("[scorer] Database not found.")
        return

    user_id = get_user_id()
    print(f"[scorer] Force-rescoring ALL jobs for user: {user_id}...")
    with transaction() as conn:
        conn.execute("UPDATE vagas SET relevance_score = NULL WHERE user_id = ?", (user_id,))

    score_and_update_unscored_jobs()


if __name__ == '__main__':
    if '--rescore-all' in sys.argv:
        rescore_all_jobs()
    elif '--backfill' in sys.argv:
        backfill_enrichment()
    else:
        score_and_update_unscored_jobs()
        backfill_enrichment()
