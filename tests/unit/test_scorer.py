"""Unit tests for automation/job_scorer.py — pure functions only, no DB."""
import pytest
from automation.job_scorer import _tokenize, _parse_salary_value, _score_job


# ── _tokenize ────────────────────────────────────────────────────────────────

class TestTokenize:
    def test_empty_string(self):
        assert _tokenize("") == []

    def test_none(self):
        assert _tokenize(None) == []

    def test_stopwords_removed(self):
        tokens = _tokenize("the director of engineering and the team")
        assert "the" not in tokens
        assert "of" not in tokens
        assert "and" not in tokens
        assert "director" in tokens
        assert "engineering" in tokens

    def test_tokens_shorter_than_3_removed(self):
        # "AI" → "ai" (len 2) → removed;  "CTO" → "cto" (len 3) → kept
        tokens = _tokenize("CTO AI VP lead")
        assert "cto" in tokens
        assert "lead" in tokens
        assert "ai" not in tokens
        assert "vp" not in tokens

    def test_lowercased(self):
        tokens = _tokenize("Senior Software Engineer")
        assert "senior" in tokens
        assert "software" in tokens
        assert "engineer" in tokens

    def test_special_chars_split(self):
        tokens = _tokenize("Python/Django REST-API developer")
        assert "python" in tokens
        assert "django" in tokens
        assert "developer" in tokens

    def test_numbers_kept(self):
        tokens = _tokenize("Python3 AWS S3 infrastructure")
        assert "python3" in tokens
        assert "infrastructure" in tokens


# ── _parse_salary_value ──────────────────────────────────────────────────────

class TestParseSalaryValue:
    def test_empty_returns_zero(self):
        assert _parse_salary_value("") == 0

    def test_no_salary_returns_zero(self):
        assert _parse_salary_value("Competitive salary") == 0

    def test_k_notation_euro(self):
        assert _parse_salary_value("€50k") == 50000

    def test_k_notation_suffix_currency(self):
        assert _parse_salary_value("80k EUR") == 80000

    def test_european_dot_separator(self):
        assert _parse_salary_value("65.000€") == 65000

    def test_range_returns_midpoint(self):
        # "EUR 40.000–60.000" → midpoint 50000
        result = _parse_salary_value("EUR 40.000–60.000")
        assert result == 50000

    def test_range_dash_separator(self):
        result = _parse_salary_value("40000-60000")
        assert result == 50000

    def test_plain_number(self):
        assert _parse_salary_value("50000") == 50000


# ── _score_job ───────────────────────────────────────────────────────────────

class TestScoreJob:
    def test_no_profile_terms_gives_zero(self):
        score = _score_job("Software Engineer", "Acme", "We build software.", "", {})
        assert score == 0

    def test_empty_title_and_description(self):
        terms = {"cto": 10.0}
        score = _score_job("", "", "", "", terms)
        assert score == 0

    def test_title_match_applies_floor_55(self):
        # "cto" appears in title → weight ≥ 8 → floor at 55
        terms = {"cto": 10.0, "__phrase__cto": 20.0}
        score = _score_job("CTO", "Startup", "Lead the engineering org", "", terms)
        assert score >= 55

    def test_title_match_case_insensitive(self):
        terms = {"cto": 10.0, "__phrase__cto": 20.0}
        score = _score_job("Chief Technology Officer (CTO)", "Co", "tech", "", terms)
        assert score >= 55

    def test_description_only_match_no_floor(self):
        # Token only in description, not title — no floor applied
        terms = {"python": 10.0}
        score_title = _score_job("Python Developer", "Co", "desc", "", terms)
        score_desc  = _score_job("Software Engineer", "Co", "python backend expert", "", terms)
        # Title match should score higher (×4 vs ×1 weight)
        assert score_title > score_desc

    def test_negative_keyword_caps_score_at_15(self):
        terms = {"cto": 10.0, "__phrase__cto": 20.0}
        # "internship" triggers negative keyword → cap at 15
        score = _score_job(
            "CTO Internship", "Co", "Lead the company as CTO",
            "", terms, negative_keywords=["internship"]
        )
        assert score <= 15

    def test_negative_keyword_not_in_title_no_cap(self):
        terms = {"engineer": 10.0}
        score = _score_job(
            "Senior Engineer", "Co", "Python and AWS", "",
            terms, negative_keywords=["internship"]
        )
        assert score > 15

    def test_salary_above_min_boosts_score(self):
        terms = {"engineer": 10.0}
        score_ok  = _score_job("Engineer", "Co", "tech", "", terms,
                               min_salary=50_000, job_salary_text="€80k")
        score_low = _score_job("Engineer", "Co", "tech", "", terms,
                               min_salary=50_000, job_salary_text="€30k")
        # 80k ≥ 50k → +10; 30k < 37.5k (75%) → −25; difference ≥ 35
        assert score_ok > score_low

    def test_salary_modifier_only_when_min_salary_set(self):
        # No min_salary → salary text is ignored, scores are equal
        terms = {"engineer": 10.0}
        s1 = _score_job("Engineer", "Co", "tech", "", terms, job_salary_text="€80k")
        s2 = _score_job("Engineer", "Co", "tech", "", terms, job_salary_text="€20k")
        assert s1 == s2

    def test_phrase_match_bonus(self):
        # Phrase match gives extra weight beyond individual tokens
        terms = {"__phrase__chief technology officer": 20.0}
        score_phrase = _score_job(
            "Chief Technology Officer", "Corp", "managing engineering", "", terms
        )
        score_none = _score_job("Head of Product", "Corp", "managing product", "", terms)
        assert score_phrase > score_none

    def test_score_bounded_0_to_100(self):
        # Even with very heavy profile terms, score should not exceed 100
        terms = {f"term{i}": 100.0 for i in range(50)}
        title = " ".join(f"term{i}" for i in range(50))
        score = _score_job(title, "Co", title, title, terms)
        assert 0 <= score <= 100
