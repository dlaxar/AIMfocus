"""
tests/test_patient.py – the patient header (/api/v2/patients/{id}), patient search, passage context.
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


def test_patient_search(client, tmp_path, monkeypatch):
    import json
    from app.config import settings
    for pid, name in (("0000-0001", "Anna Musterfrau"), ("0000-0002", "Max Mustermann")):
        (tmp_path / f"patient-{pid}").mkdir()
        (tmp_path / f"patient-{pid}" / "patient.json").write_text(
            json.dumps({"id": pid, "name": name, "ward": "3B"}), encoding="utf-8")
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)

    assert [p["id"] for p in client.get("/api/v2/patients/search?q=muster").json()] == ["0000-0001", "0000-0002"]
    assert client.get("/api/v2/patients/search?q=0002").json() == [{"id": "0000-0002", "name": "Max Mustermann"}]
    assert len(client.get("/api/v2/patients/search?q=muster&limit=1").json()) == 1
    assert client.get("/api/v2/patients/search?q=").json() == []
    assert client.get("/api/v2/patients/search?q=x&limit=0").status_code == 422


def test_passage_context_is_its_section(client):
    r = client.get("/api/passages/TEST001:p003/context").json()
    assert r["doc"]["doc_id"] == "TEST001" and r["passage"]["passage_id"] == "TEST001:p003"
    sec = r["section"]
    assert sec["name"] == "Diagnosen"
    assert sec["text"].startswith("Diagnosen:") and "Diabetes mellitus" in sec["text"]
    assert "Allergien" not in sec["text"]
    assert sec["start"] <= r["passage"]["start"] < r["passage"]["end"] <= sec["end"]


def test_passage_context_of_a_header_opens_the_next_section(client):
    sec = client.get("/api/passages/TEST001:p005/context").json()["section"]
    assert sec["name"] == "Allergien" and sec["text"].startswith("Allergien:") and "Kontrastmittel" in sec["text"]


def test_passage_context_404(client):
    assert client.get("/api/passages/NOPE:p1/context").status_code == 404
