"""
tests/test_fastpath.py – fast-path mockup (/api/v2): immediate documented part,
background derive calls, polling and validation of derive results.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.test_smoke import _build_fixture_cache


@pytest.fixture(scope="module", autouse=True)
def fixture_cache():
    _build_fixture_cache()


@pytest.fixture()
def client():
    import app.store as store_mod

    store_mod._store = None
    from app.main import app

    with TestClient(app) as c:
        yield c


def _by_name(screen: dict) -> dict:
    return {v["name"]: v for v in screen["variables"]}


class TestStoreEndpoints:
    def test_schema_lists_concepts(self, client):
        data = client.get("/api/v2/schema").json()
        assert any(c["concept_id"] == "C:allergy:contrast_media" for c in data["concepts"])
        assert "concept_id" in data["fact_fields"]

    def test_concept_and_fact(self, client):
        concept = client.get("/api/v2/concepts/C:allergy:contrast_media").json()
        assert concept["facts"][0]["fact_id"] == "F:TEST001:all:01"
        assert concept["facts"][0]["source"]["doc_id"] == "TEST001"
        assert client.get("/api/v2/facts/F:NOPE").status_code == 404


class TestScreens:
    def test_id_returned_at_once_then_screen_completes(self, client):
        resp = client.post("/api/v2/screens", json={"situation": "Anaesthetist, pre-op check"})
        assert resp.status_code == 202
        first = resp.json()
        assert resp.headers["Location"] == f"/api/v2/screens/{first['screen_id']}"
        # The ID comes back before any LLM call has answered
        assert first["status"] == "starting"
        assert first["variables"] == []

        final = client.get(f"/api/v2/screens/{first['screen_id']}?wait=true").json()
        assert final["status"] == "complete"
        assert final["role"] == "anaesthetist"
        v = _by_name(final)
        assert v["contrast_allergy"]["state"] == "documented"
        assert v["contrast_allergy"]["sources"][0]["doc_id"] == "TEST001"
        # Variables without facts are not guessed: the text search confirms them
        assert v["kidney_function"]["state"] == "not_documented"
        assert v["antithrombotic_medication"]["state"] == "not_documented"
        assert {"select", "first_response", "derive", "complete"} <= set(final["timings_ms"])

    def test_empty_value_falls_back_to_stored_fact_text(self, client):
        from app.fastpath.models import SelectedVariable
        from app.fastpath.service import _documented_variable
        from app.store import get_store

        v = SelectedVariable(name="allergies", label="Allergies", value="  ", fact_ids=["F:TEST001:all:01"])
        out = _documented_variable(v, get_store(), 0.0)
        assert out is not None and out.value == "Contrast media allergy (nausea, dizziness)"

    def test_select_failure_is_reported_on_the_screen(self, client, monkeypatch):
        from app.fastpath import service

        async def boom(*args, **kwargs):
            raise RuntimeError("quota")
        monkeypatch.setattr(service, "_select", boom)
        sid = client.post("/api/v2/screens", json={"situation": "x"}).json()["screen_id"]
        final = client.get(f"/api/v2/screens/{sid}?wait=true").json()
        assert final["status"] == "complete"
        assert final["error"] and "RuntimeError" in final["error"]

    def test_get_with_situation_query(self, client):
        resp = client.get("/api/v2/screens", params={"situation": "Anaesthetist, pre-op check"})
        assert resp.status_code in (200, 202)
        assert client.get(f"/api/v2/screens/{resp.json()['screen_id']}").status_code == 200

    def test_unknown_screen_404(self, client):
        assert client.get("/api/v2/screens/doesnotexist").status_code == 404

    def test_stream_ends_with_done(self, client):
        screen_id = client.post("/api/v2/screens", json={"situation": "x"}).json()["screen_id"]
        body = client.get(f"/api/v2/screens/{screen_id}/stream").text
        assert "event: screen" in body
        assert body.rstrip().endswith("data: {}")


class TestDeriveValidation:
    """The derive answer must quote the cited passages verbatim."""

    def _store(self):
        from app.store import get_store
        return get_store()

    def test_valid_quote_accepted(self, client):
        from app.fastpath.models import DeriveOutput
        from app.fastpath.service import _check_derive

        store = self._store()
        p = next(p for p in store.passages.values() if "Testopril" in p.text)
        out = DeriveOutput(found=True, value="Testopril 5 mg in the morning",
                           passage_ids=[p.passage_id], quotes=["Testopril 5mg"])
        assert _check_derive(out, store) == []

    def test_invented_quote_and_number_rejected(self, client):
        from app.fastpath.models import DeriveOutput
        from app.fastpath.service import _check_derive

        store = self._store()
        p = next(p for p in store.passages.values() if "Testopril" in p.text)
        out = DeriveOutput(found=True, value="Creatinine 1.4 mg/dl",
                           passage_ids=[p.passage_id], quotes=["Kreatinin 1,4 mg/dl"])
        errors = _check_derive(out, store)
        assert any("quote" in e for e in errors)
        assert any("1.4" in e for e in errors)

    def test_unknown_passage_rejected(self, client):
        from app.fastpath.models import DeriveOutput
        from app.fastpath.service import _check_derive

        out = DeriveOutput(found=True, value="x", passage_ids=["NOPE:p999"], quotes=["x"])
        assert _check_derive(out, self._store())
