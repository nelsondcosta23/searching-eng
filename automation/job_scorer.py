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
from automation.profile_fetcher import get_search_description, get_profile_filters, get_target_roles, get_user_id
from automation.db_helper import transaction

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

    # Role titles — very high weight (exact match = most relevant)
    for role in get_target_roles():
        for token in _tokenize(role):
            weights[token] = max(weights.get(token, 0), 10.0)
        # Also add the full role as a phrase-key for exact phrase bonus
        phrase_key = role.lower().strip()
        weights[f'__phrase__{phrase_key}'] = 15.0

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


def _score_job(titulo: str, empresa: str, descricao: str, observacoes: str,
               profile_terms: dict[str, float]) -> int:
    """
    Computes a relevance score (0–100) for a single job.

    Scoring strategy:
      - Title match:       weight × 4  (most signal)
      - Observations match: weight × 2  (structured metadata)
      - Description match: weight × 1  (broad context)
    """
    # Combine all text fields
    title_tokens   = _tokenize(titulo)
    obs_tokens     = _tokenize(observacoes)
    desc_tokens    = _tokenize(descricao)

    raw_score = 0.0

    # Token-level scoring
    all_text_lower = f"{titulo} {observacoes} {descricao}".lower()

    for term, weight in profile_terms.items():
        if term.startswith('__phrase__'):
            # Exact phrase match bonus with word boundaries
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

    # Normalize to 0–100 (cap at 100)
    # Increased scale base from 50 to 90 to make 100% harder to reach
    normalized = min(100, int((raw_score / 90.0) * 100))
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

    # Phase 1: read candidates (short-lived read connection)
    with transaction(row_factory=sqlite3.Row) as conn:
        rows = conn.execute("""
            SELECT id, titulo, empresa,
                   COALESCE(descricao_completa, '') AS descricao_completa,
                   COALESCE(observacoes, '')         AS observacoes
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
    import sys
    if '--rescore-all' in sys.argv:
        rescore_all_jobs()
    else:
        score_and_update_unscored_jobs()
