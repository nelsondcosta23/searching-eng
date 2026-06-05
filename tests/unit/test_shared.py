"""Unit tests for scrapers/_shared.py helper functions."""
import pytest
from scrapers._shared import (
    negative_keyword_match,
    extract_seniority,
    extract_salary_from_text,
    extract_work_mode,
    extract_skills,
)


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


# ── extract_work_mode ────────────────────────────────────────────────────────

class TestExtractWorkMode:
    def test_remote_from_location(self):
        assert extract_work_mode("Remote, Portugal", "Software Engineer", "") == "Remote"
        assert extract_work_mode("remoto", "Developer", "") == "Remote"

    def test_remote_from_title(self):
        assert extract_work_mode("Lisboa", "Remote Python Developer", "") == "Remote"

    def test_remote_from_description(self):
        desc = "This is a teletrabalho position."
        assert extract_work_mode("Lisbon", "Developer", desc) == "Remote"

    def test_hybrid_from_location(self):
        assert extract_work_mode("Híbrido - Porto", "Engineer", "") == "Hybrid"
        assert extract_work_mode("hybrid", "Dev", "") == "Hybrid"

    def test_hybrid_from_description(self):
        desc = "We follow a regime misto work policy."
        assert extract_work_mode("Lisbon", "Dev", desc) == "Hybrid"

    def test_onsite_from_title(self):
        assert extract_work_mode("Lisbon", "Presencial C# Developer", "") == "On-site"

    def test_onsite_from_description(self):
        desc = "This role requires being onsite at our office."
        assert extract_work_mode("Lisbon", "Dev", desc) == "On-site"

    def test_not_specified(self):
        assert extract_work_mode("Lisbon", "Python Developer", "Join our team!") == ""


# ── extract_skills ───────────────────────────────────────────────────────────

class TestExtractSkills:
    def test_basic_skills_extraction(self):
        desc = "We need a Python developer who knows React and SQL."
        skills = extract_skills("Backend Developer", desc)
        assert "PYTHON" in skills
        assert "REACT" in skills
        assert "SQL" in skills
        assert len(skills) == 3

    def test_dotnet_isolation(self):
        desc = "Looking for a .NET developer. Should not match internet."
        skills = extract_skills("Dev", desc)
        assert ".NET" in skills
        assert len(skills) == 1

    def test_golang_safeguard(self):
        # Should match Golang
        assert "GOLANG" in extract_skills("Golang Developer", "")
        # Should not match common "go" verb
        assert "GOLANG" not in extract_skills("Go to school", "we will go above and beyond")

    def test_case_insensitivity(self):
        assert "TYPESCRIPT" in extract_skills("typescript dev", "")
        assert "TYPESCRIPT" in extract_skills("TYPESCRIPT DEV", "")

    def test_c_sharp_and_c_plus_plus_extraction(self):
        # Verify C# is extracted correctly (even with trailing punctuation or spaces)
        assert "C#" in extract_skills("C# Developer", "experience in C#.")
        assert "C#" in extract_skills("Looking for a C# developer", "")
        
        # Verify C++ is extracted correctly
        assert "C++" in extract_skills("C++ Developer", "must know C++")
        assert "C++" in extract_skills("We use C++, Python, and Java", "")
        
        # Verify negative isolation (no collision with other words)
        assert "C#" not in extract_skills("Topic #1", "No C language mentioned here")
        assert "C++" not in extract_skills("Topic ++", "C language")

