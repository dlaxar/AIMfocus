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
        # Every contradiction of the record is on the screen: relevant ones plus the rest
        assert isinstance(final["contradictions"], list) and isinstance(final["other_contradictions"], list)

    def test_value_is_pulled_from_the_store_not_written_by_the_llm(self, client):
        from app.fastpath.models import SelectedVariable
        from app.fastpath.service import _documented_variable
        from app.store import get_store

        v = SelectedVariable(name="allergies", label="Allergies", fact_ids=["F:TEST001:all:01", "F:NOPE:all:09"])
        out = _documented_variable(v, get_store(), "2016-06-01", 0.0)
        assert out is not None
        assert out.value == "Contrast media allergy (nausea, dizziness) (2016-06-01)"
        assert out.fact_ids == ["F:TEST001:all:01"]          # unknown ID dropped
        assert out.sources[0].doc_id == "TEST001"

    def test_select_handles_map_to_full_ids(self, client):
        from app.fastpath.service import catalogue
        from app.store import get_store

        cat = catalogue(get_store())
        handle = next(h for h, full in cat.handles.items() if full == "C:allergy:contrast_media")
        assert handle.startswith("c") and f"\n{handle} | " in cat.text
        assert cat.resolve(handle) == "C:allergy:contrast_media"
        assert cat.resolve("F:TEST001:all:01") == "F:TEST001:all:01"   # a full ID passes through

    def test_unknown_ids_only_means_not_in_store(self, client):
        from app.fastpath.models import SelectedVariable
        from app.fastpath.service import _documented_variable
        from app.store import get_store

        v = SelectedVariable(name="kidney", label="Kidney", concept_ids=["C:meas:creatinine"], fact_ids=["F:NOPE"])
        assert _documented_variable(v, get_store(), "2016-06-01", 0.0) is None

    def test_wait_returns_current_state_on_timeout(self, client, monkeypatch):
        import asyncio
        from app.fastpath import service

        async def slow(*args, **kwargs):
            await asyncio.sleep(1)
            raise RuntimeError("slow model")
        monkeypatch.setattr(service, "_select", slow)
        sid = client.post("/api/v2/screens", json={"situation": "x"}).json()["screen_id"]
        screen = client.portal.call(service.wait_for, sid, 0.05)
        assert screen is not None and screen.status == "starting"

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


class TestResolveFacts:
    """Time scope and de-duplication of the analytic fact pull (no LLM involved)."""

    class _Store:
        def __init__(self, facts, derived=()):
            self._facts = {f.fact_id: f for f in facts}
            self.derived = {d.fact_id: d for d in derived}
            self.concepts = {f.concept_id: None for f in facts if f.concept_id}

        def get_any_fact(self, fid):
            return self._facts.get(fid)

        def facts_by_concept(self, cid):
            return [f for f in self._facts.values() if f.concept_id == cid]

        def get_document(self, doc_id):
            from app.schemas import Document
            return Document(doc_id=doc_id, title="Letter", text="", date="2016-04-15") if doc_id == "LETTER" else None

    @staticmethod
    def _weight(fid, kg, date):
        from app.schemas import Fact
        return Fact(fact_id=fid, type="measurement", passage_id=f"{fid}:p1", span=f"Gewicht: {kg} kg",
                    concept_id="C:meas:body_weight", date=date, en=f"Body weight {kg} kg")

    def _store(self):
        return self._Store([
            self._weight("F:B:mea:01", 67, "2016-04-04"),
            self._weight("F:A:mea:01", 78, "2015-06-28"),
            self._weight("F:C:mea:01", 66, "2016-04-15"),
            self._weight("F:D:mea:01", 60, "2016-09-01"),   # after the reference date
        ])

    def test_latest_takes_newest_up_to_reference_date(self):
        from app.fastpath.models import SelectedVariable
        from app.fastpath.service import resolve_facts, render_value
        v = SelectedVariable(name="w", label="Weight", concept_ids=["C:meas:body_weight"])
        facts = resolve_facts(v, self._store(), "2016-04-15")
        assert [f.fact_id for f in facts] == ["F:C:mea:01"]
        assert render_value(facts) == "Body weight 66 kg (2016-04-15)"

    def test_all_gives_history_oldest_first(self):
        from app.fastpath.models import SelectedVariable
        from app.fastpath.service import resolve_facts, render_value
        v = SelectedVariable(name="w", label="Weight", concept_ids=["C:meas:body_weight"], time_scope="all")
        facts = resolve_facts(v, self._store(), "2016-04-15")
        assert [f.fact_id for f in facts] == ["F:A:mea:01", "F:B:mea:01", "F:C:mea:01"]
        assert render_value(facts).startswith("Body weight 78 kg (2015-06-28); Body weight 67 kg")

    def test_plan_counts_from_its_letter_date(self):
        """A surgery scheduled for May in the April letter is known in April."""
        from app.fastpath.models import SelectedVariable
        from app.fastpath.service import resolve_facts
        from app.schemas import Fact
        plan = Fact(fact_id="F:LETTER:pla:01", type="plan", passage_id="LETTER:p3", span="OP am 16.05.2016",
                    concept_id="C:plan:surgery", date="2016-05-16", en="Surgery scheduled for 16 May 2016")
        v = SelectedVariable(name="p", label="Planned surgery", concept_ids=["C:plan:surgery"])
        assert [f.fact_id for f in resolve_facts(v, self._Store([plan]), "2016-04-15")] == ["F:LETTER:pla:01"]

    def test_recommendation_shown_with_its_follow_up_status(self):
        from app.fastpath.models import SelectedVariable
        from app.fastpath.service import resolve_facts
        from app.schemas import DerivedFact, Fact
        rec = Fact(fact_id="F:A:rec:01", type="recommendation", passage_id="A:p9", span="Duplex in 2 Monaten",
                   concept_id="C:rec:duplex", date="2015-06-28", en="Next duplex in 2 months")
        fu = DerivedFact(fact_id="D:followup:a", en="Follow-up open: Next duplex in 2 months – no record",
                         inputs=["F:A:rec:01"], formula="followup_status", fields={"rec_fact_id": "F:A:rec:01"})
        v = SelectedVariable(name="r", label="Recommendations", concept_ids=["C:rec:duplex"], time_scope="all")
        assert [f.fact_id for f in resolve_facts(v, self._Store([rec], [fu]), "2016-04-15")] == ["D:followup:a"]

    def test_same_text_in_two_letters_shown_once(self):
        from app.fastpath.service import render_value
        a, b = self._weight("F:A:mea:01", 78, "2015-06-28"), self._weight("F:B:mea:01", 78, "2015-06-28")
        assert render_value([a, b]) == "Body weight 78 kg (2015-06-28)"
