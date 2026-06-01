"""Unit tests for scrapers/_shared.py helper functions."""
import pytest
from scrapers._shared import negative_keyword_match, extract_seniority, extract_salary_from_text


# ── negative_keyword_match ───────────────────────────────────────────────────

class TestNegativeKeywordMatch:
    """The main regression these tests guard against: word-boundary matching.

    Old behaviour (`kw in text`) caused 'intern' to match 'international',
    'manager' to match 'managers', etc. The word-boundary fix corrected this.
    """

    def test_exact_match_returns_keyword(self):
        result = negative_keyword_match("software intern position", ["intern"])
        assert result == "intern"

    def test_intern_does_not_match_international(self):
        result = negative_keyword_match("international software company", ["intern"])
        assert result is None

    def test_manager_does_not_match_managers(self):
        # "managers" has trailing 's' — word boundary should not match "manager"
        # (actually \bmanager\b WOULD match "managers" because \b is before 's'
        # Actually no: \bmanager\b — the \b after "r" is between "r" and "s" which
        # is NOT a word boundary because both are word chars. So \bmanager\b does
        # NOT match "managers". Let me verify...)
        # \bmanager\b: word boundary after "r" requires non-word char after "r".
        # In "managers" the char after "r" is "s" (word char) → no boundary → no match.
        result = negative_keyword_match("looking for senior managers here", ["manager"])
        assert result is None

    def test_manager_matches_exact(self):
        result = negative_keyword_match("looking for a product manager role", ["manager"])
        assert result == "manager"

    def test_multiple_keywords_returns_first_match(self):
        result = negative_keyword_match("junior analyst internship", ["internship", "junior"])
        assert result in ("internship", "junior")

    def test_no_match_returns_none(self):
        result = negative_keyword_match("senior backend engineer", ["internship", "junior"])
        assert result is None

    def test_empty_text_returns_none(self):
        assert negative_keyword_match("", ["intern"]) is None

    def test_empty_negatives_returns_none(self):
        assert negative_keyword_match("senior intern role", []) is None

    def test_case_insensitive(self):
        # negative_keyword_match lowercases both sides
        result = negative_keyword_match("Software INTERN Position", ["intern"])
        assert result == "intern"

    def test_dotnet_special_case(self):
        # Keywords starting with non-word chars use lookahead/lookbehind
        result = negative_keyword_match("C# and .NET developer", [".net"])
        assert result == ".net"

    def test_dotnet_does_not_match_internet(self):
        result = negative_keyword_match("internet of things developer", [".net"])
        assert result is None


# ── extract_seniority ────────────────────────────────────────────────────────

class TestExtractSeniority:
    def test_senior_title(self):
        assert extract_seniority("Senior Software Engineer") == "Sénior"

    def test_senior_abbreviation(self):
        assert extract_seniority("Sr. Backend Developer") == "Sénior"

    def test_junior_title(self):
        assert extract_seniority("Junior Python Developer") == "Júnior"

    def test_junior_abbreviation(self):
        assert extract_seniority("Jr. Frontend Dev") == "Júnior"

    def test_lead_title(self):
        assert extract_seniority("Technical Lead") == "Lead"

    def test_manager_title(self):
        assert extract_seniority("Engineering Manager") == "Manager"

    def test_director_title(self):
        assert extract_seniority("Director of Engineering") == "Director"

    def test_no_match_returns_empty(self):
        assert extract_seniority("Software Engineer") == ""

    def test_title_takes_priority_over_description(self):
        # "Senior" in title wins over "Junior" in description
        result = extract_seniority("Senior Developer", "junior preferred")
        assert result == "Sénior"

    def test_falls_back_to_description(self):
        result = extract_seniority("Backend Engineer", "we are looking for a senior developer")
        assert result == "Sénior"

    def test_portuguese_senior(self):
        assert extract_seniority("Desenvolvedor Sénior Python") == "Sénior"

    def test_portuguese_junior(self):
        assert extract_seniority("Desenvolvedor Júnior") == "Júnior"


# ── extract_salary_from_text ─────────────────────────────────────────────────

class TestExtractSalaryFromText:
    def test_euro_k_notation(self):
        result = extract_salary_from_text("We offer €50k per year")
        assert result == "€50k"

    def test_k_suffix_currency(self):
        result = extract_salary_from_text("Salary: 80k EUR per annum")
        assert "80k" in result

    def test_european_dot_separator(self):
        result = extract_salary_from_text("Remuneração: 45.000€")
        assert "45.000€" in result

    def test_no_salary_returns_empty(self):
        assert extract_salary_from_text("Competitive salary offered") == ""

    def test_empty_returns_empty(self):
        assert extract_salary_from_text("") == ""

    def test_only_first_1000_chars_searched(self):
        # Salary buried deep in text beyond 1000 chars should NOT be found
        long_prefix = "x" * 1001
        result = extract_salary_from_text(long_prefix + " €50k bonus")
        assert result == ""

    def test_salary_in_first_1000_chars_found(self):
        text = "We offer €60k per year. " + "description " * 50
        result = extract_salary_from_text(text)
        assert result != ""
