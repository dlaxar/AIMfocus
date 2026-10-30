"""
tests/test_patient.py – the patient header (/api/v2/patients/{id}).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.test_smoke import _build_fixture_cache


@pytest.fixture()
def client():
    import app.store as store_mod

    _build_fixture_cache()
    store_mod._store = None
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_profile_has_cave_with_source(client):
    r = client.get("/api/v2/patients/TEST").json()
    assert r["patient_id"] == "TEST"
    assert r["allergies"] and r["allergies"][0]["fact_id"] == "F:TEST001:all:01"
    assert r["allergies"][0]["source"]["doc_id"] == "TEST001"
    assert r["record"]["documents"] >= 1
    assert set(r) >= {"name", "sex", "birth_date", "age", "reference_date", "height", "weight", "bmi", "latest_stay"}


def test_other_patient_is_404(client):
    assert client.get("/api/v2/patients/NOPE").status_code == 404


def test_age_on_the_reference_date():
    from app.patient import _age
    assert _age("1959-03-15", "2016-04-15") == 57
    assert _age("1959-03-15", "2016-03-14") == 56
    assert _age(None, "2016-04-15") is None
