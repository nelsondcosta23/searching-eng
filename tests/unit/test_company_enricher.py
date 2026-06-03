"""Unit tests for automation/company_enricher.py."""
import pytest
import sqlite3
import datetime
from unittest.mock import MagicMock
import automation.company_enricher as enricher_mod

@pytest.fixture(autouse=True)
def patch_db_path(tmp_db, monkeypatch):
    """Redirect all db_helper/enricher operations to the test DB."""
    monkeypatch.setattr(enricher_mod, "DB_PATH", tmp_db)

def test_clean_company_name():
    assert enricher_mod._clean_company_name("Google Portugal") == "Google"
    assert enricher_mod._clean_company_name("Altice S.A.") == "Altice"
    assert enricher_mod._clean_company_name("BMW Group (BMW)") == "BMW"
    assert enricher_mod._clean_company_name("Siemens Technologies") == "Siemens"
    assert enricher_mod._clean_company_name("Acme Solutions Ltd.") == "Acme"

def test_fetch_inception_year(monkeypatch):
    mock_get = MagicMock()
    monkeypatch.setattr("requests.get", mock_get)

    # Mock response 1: wbsearchentities
    resp1 = MagicMock()
    resp1.status_code = 200
    resp1.json.return_value = {
        "search": [
            {
                "id": "Q95",
                "description": "American technology company"
            }
        ]
    }

    # Mock response 2: wbgetclaims for P31
    resp_p31 = MagicMock()
    resp_p31.status_code = 200
    resp_p31.json.return_value = {
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "datavalue": {
                            "value": {
                                "id": "Q4830453"
                            }
                        }
                    }
                }
            ]
        }
    }

    # Mock response 3: wbgetclaims
    resp2 = MagicMock()
    resp2.status_code = 200
    resp2.json.return_value = {
        "claims": {
            "P571": [
                {
                    "mainsnak": {
                        "datavalue": {
                            "value": {
                                "time": "+1998-09-04T00:00:00Z"
                            }
                        }
                    }
                }
            ]
        }
    }

    mock_get.side_effect = [resp1, resp_p31, resp2]

    year, desc = enricher_mod.fetch_inception_year("Google")
    assert year == 1998
    assert desc == "American technology company"

def test_get_or_enrich_company_cached(tmp_db):
    # Seed cache
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        "INSERT INTO companies (name, inception_year, company_age, description) VALUES (?, ?, ?, ?)",
        ("TestCorp", 2010, 16, "A test corporation")
    )
    conn.commit()
    conn.close()

    # Call get_or_enrich_company
    year, age = enricher_mod.get_or_enrich_company("TestCorp")
    assert year == 2010
    
    current_year = datetime.datetime.now().year
    assert age == current_year - 2010
