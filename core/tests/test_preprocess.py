"""
tests/test_preprocess.py – unit tests for S1–S4 and the eval helpers (no API key needed).

LLM calls are replaced by a scripted stub that returns queued responses and records prompts.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.schemas import Concept, DerivedFact, Document, Fact, Passage, S1Report
from preprocess import s1_extract, s2_merge, s3_contradictions
from preprocess.s4_export import coverage_pct
from preprocess.vocab import normalize_concept_key, render_registry


class ScriptedClient:
    """Stand-in LLM client: returns queued raw dicts per prompt_name, records every prompt."""

    def __init__(self, responses: dict[str, list[dict]]):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[tuple[str, list[dict]]] = []

    async def generate(self, prompt_name, messages, response_schema=None, temperature=0.0, model=None):
        self.calls.append((prompt_name, messages))
        queue = self.responses.get(prompt_name, [])
        raw = queue.pop(0) if queue else {}
        return response_schema.model_validate(raw) if response_schema else raw


def _passage(pid: str, text: str, kind: str = "sentence") -> Passage:
    return Passage(passage_id=pid, doc_id=pid.split(":")[0], kind=kind, start=0, end=len(text), text=text)


def _fact(fid: str, pid: str, ftype: str, concept_id: str | None = None, **kw) -> Fact:
    return Fact(fact_id=fid, type=ftype, passage_id=pid, span=kw.pop("span", "x"),
                concept_id=concept_id, **kw)


# ---------------------------------------------------------------------------
# vocab
# ---------------------------------------------------------------------------

class TestConceptKeys:
    def test_slugifies_and_prefixes(self):
        assert normalize_concept_key("dx:Diabetes Type 2", "diagnosis") == "C:dx:diabetes_type_2"

    def test_prefix_follows_type(self):
        assert normalize_concept_key("dx:carotid_ica:stenosis", "finding") == "C:finding:carotid_ica:stenosis"

    def test_laterality_never_in_key(self):
        assert normalize_concept_key("finding:carotid_ica:stenosis_left", "finding") == "C:finding:carotid_ica:stenosis"

    def test_measurement_aliases_and_other(self):
        assert normalize_concept_key("meas:EF", "measurement") == "C:meas:lvef"
        assert normalize_concept_key("Gewicht", "measurement") == "C:meas:body_weight"
        assert normalize_concept_key("meas:waist", "measurement") == "C:meas:other:waist"

    def test_empty_or_unknown_type(self):
        assert normalize_concept_key("", "diagnosis") is None
        assert normalize_concept_key("dx:x", "nonsense") is None

    def test_registry_rendering(self):
        assert render_registry({"C:dx:a": "A"}) == "dx:a · A"


# ---------------------------------------------------------------------------
# S1
# ---------------------------------------------------------------------------

class TestS1Validation:
    passages = {"D1:p001": _passage("D1:p001", "Gewicht: 66\nkg"), "D1:p002": _passage("D1:p002", "BMI: 22,8")}

    def _raw(self, **kw) -> s1_extract.RawFact:
        base = dict(passage_id="D1:p001", type="measurement", span="Gewicht: 66 kg", status="positive",
                    date_precision="day", date_basis="document_date")
        base.update(kw)
        return s1_extract.RawFact.model_validate(base)

    def test_span_matches_across_line_break(self):
        hard, soft = s1_extract._validate_raw_fact(self._raw(fields={"value": 66}), self.passages)
        assert hard == [] and soft == []

    def test_number_not_in_span_is_hard_error(self):
        hard, _ = s1_extract._validate_raw_fact(self._raw(fields={"value": 67}), self.passages)
        assert any("not in span" in e for e in hard)

    def test_decimal_comma_matches(self):
        raw = self._raw(passage_id="D1:p002", span="BMI: 22,8", fields={"value": "22.8"})
        assert s1_extract._validate_raw_fact(raw, self.passages)[0] == []

    def test_bad_enums_are_soft_and_coerced(self):
        raw = self._raw(status="present", date_precision="exact", date_basis="text", laterality="links",
                        concept_key="meas:Gewicht")
        hard, soft = s1_extract._validate_raw_fact(raw, self.passages)
        assert hard == [] and len(soft) == 4
        fact = s1_extract._raw_to_fact(raw, "D1", {})
        assert fact is not None
        assert (fact.status, fact.date_precision, fact.date_basis, fact.laterality) == ("unknown", "unknown", "unknown", None)
        assert fact.concept_id == "C:meas:body_weight"

    def test_unknown_type_does_not_crash(self):
        assert s1_extract._raw_to_fact(self._raw(type="vital_sign"), "D1", {}) is None


def _write_s0_cache(tmp_path: Path) -> Path:
    import json
    out = tmp_path / "cache" / "P1"
    out.mkdir(parents=True)
    docs = [
        Document(doc_id="LATE", title="Brief 2016", date="2016-04-15", text="Gewicht: 66 kg"),
        Document(doc_id="EARLY", title="Brief 2015", date="2015-06-28", text="Gewicht: 78 kg"),
    ]
    passages = [_passage("LATE:p000", "Gewicht: 66 kg"), _passage("EARLY:p000", "Gewicht: 78 kg")]
    (out / "documents.json").write_text(json.dumps([d.model_dump() for d in docs]), encoding="utf-8")
    (out / "passages.json").write_text(json.dumps([p.model_dump() for p in passages]), encoding="utf-8")
    return tmp_path / "cache"


def _weight_raw(doc: str, kg: int, key: str = "meas:body_weight") -> dict:
    return {"passage_id": f"{doc}:p000", "type": "measurement", "span": f"Gewicht: {kg} kg",
            "concept_key": key, "concept_label_en": "Body weight", "fields": {"name": "Gewicht", "value": kg, "unit": "kg"},
            "status": "positive", "date_precision": "day", "date_basis": "document_date", "en": f"Weight {kg} kg"}


class TestS1Run:
    def test_date_order_and_registry_reuse(self, tmp_path, monkeypatch):
        stub = ScriptedClient({"s1_extract": [
            {"facts": [_weight_raw("EARLY", 78)]},
            {"facts": [_weight_raw("LATE", 66)]},
        ]})
        monkeypatch.setattr(s1_extract, "get_client", lambda: stub)
        facts, reports = asyncio.run(s1_extract.run_async(Path("unused"), _write_s0_cache(tmp_path), "P1"))

        prompts = [msgs[-1]["content"] for name, msgs in stub.calls if name == "s1_extract"]
        assert "Document ID: EARLY" in prompts[0] and "Document ID: LATE" in prompts[1]
        assert "(empty – this is the first document)" in prompts[0]
        assert "meas:body_weight · Body weight" in prompts[1]
        assert {f.concept_id for f in facts} == {"C:meas:body_weight"}
        assert [r.facts_extracted for r in reports] == [1, 1]

    def test_one_batched_retry_fixes_bad_fact(self, tmp_path, monkeypatch):
        bad = _weight_raw("EARLY", 79)          # 79 is not in the span
        stub = ScriptedClient({"s1_extract": [
            {"facts": [bad]},
            {"facts": [_weight_raw("EARLY", 78)]},  # retry answer
            {"facts": [_weight_raw("LATE", 66)]},
        ]})
        monkeypatch.setattr(s1_extract, "get_client", lambda: stub)
        facts, reports = asyncio.run(s1_extract.run_async(Path("unused"), _write_s0_cache(tmp_path), "P1"))

        assert len([c for c in stub.calls if c[0] == "s1_extract"]) == 3
        assert sorted(f.fields["value"] for f in facts) == [66, 78]
        assert reports[0].facts_rejected == 0

    def test_alias_merge_only_within_prefix(self):
        registry = {"C:dx:hypertension": "HTN", "C:dx:arterial_hypertension": "Art. HTN", "C:med:candesartan": "C"}
        out = s1_extract.AliasOutput.model_validate({"groups": [
            {"canonical": "dx:arterial_hypertension", "aliases": ["dx:hypertension"]},
            {"canonical": "dx:arterial_hypertension", "aliases": ["med:candesartan"]},
            {"canonical": "dx:arterial_hypertension", "aliases": ["dx:not_in_registry"]},
        ]})
        assert s1_extract._alias_map(out, registry) == {"C:dx:hypertension": "C:dx:arterial_hypertension"}


# ---------------------------------------------------------------------------
# S2
# ---------------------------------------------------------------------------

class TestS2:
    def _vitals(self):
        return [
            _fact("F:A:mea:01", "A:p001", "measurement", "C:meas:body_weight", fields={"name": "Gewicht", "value": "78"}, date="2015-06-28"),
            _fact("F:A:mea:02", "A:p002", "measurement", "C:meas:body_height", fields={"name": "Größe", "value": "170 cm"}, date="2015-06-28"),
            _fact("F:B:mea:01", "B:p001", "measurement", "C:meas:body_weight", fields={"name": "Gewicht", "value": "66,0"}, date="2016-04-15"),
            _fact("F:B:mea:02", "B:p002", "measurement", "C:meas:body_height", fields={"name": "Größe", "value": "1,70"}, date="2016-04-15"),
        ]

    def test_bmi_and_weight_change_from_concept_ids(self):
        facts, concepts = s2_merge._resolve_concepts(self._vitals())
        concepts = s2_merge._build_timelines(facts, concepts)
        bmi = {d.fact_id: d.fields["bmi"] for d in s2_merge._derive_bmi(facts)}
        assert bmi == {"D:bmi:A": 27.0, "D:bmi:B": 22.8}
        [change] = s2_merge._derive_weight_change(facts, concepts)
        assert change.fields["delta_kg"] == -12.0

    def test_fallback_key_drops_laterality(self):
        f = _fact("F:A:fin:01", "A:p001", "finding", None, fields={"label": "carotid stenosis left"}, laterality="left")
        facts, concepts = s2_merge._resolve_concepts([f])
        assert facts[0].concept_id == "C:finding:carotid_stenosis"

    def _rec_facts(self):
        return [
            _fact("F:A:rec:01", "A:p009", "recommendation", "C:rec:carotid_duplex_followup", status="recommended",
                  en="Carotid duplex in 2 months"),
            _fact("F:B:pro:01", "B:p003", "procedure", "C:proc:breast_ultrasound", status="positive", en="Breast ultrasound"),
        ]

    def test_followup_no_record(self, monkeypatch):
        stub = ScriptedClient({"s2_followup": [{"status": "no_record", "evidence_fact_ids": []}]})
        monkeypatch.setattr(s2_merge, "get_client", lambda: stub)
        [d] = asyncio.run(s2_merge._derive_followups(self._rec_facts(), {"A": "2015-06-28", "B": "2016-04-15"}, "m"))
        assert d.fields["status"] == "no_record" and d.inputs == ["F:A:rec:01"]
        # The recommendation itself is never offered as evidence
        prompt = stub.calls[0][1][-1]["content"]
        assert "F:B:pro:01" in prompt and prompt.count("F:A:rec:01") == 1

    def test_followup_done_needs_valid_later_evidence(self, monkeypatch):
        stub = ScriptedClient({"s2_followup": [{"status": "done", "evidence_fact_ids": ["F:A:rec:01", "F:X:nope"]}]})
        monkeypatch.setattr(s2_merge, "get_client", lambda: stub)
        assert asyncio.run(s2_merge._derive_followups(self._rec_facts(), {"A": "2015-06-28", "B": "2016-04-15"}, "m")) == []

    def test_followup_without_later_documents_skips_llm(self, monkeypatch):
        stub = ScriptedClient({})
        monkeypatch.setattr(s2_merge, "get_client", lambda: stub)
        [d] = asyncio.run(s2_merge._derive_followups(self._rec_facts()[:1], {"A": "2015-06-28"}, "m"))
        assert d.fields["status"] == "no_record" and stub.calls == []

    def test_followup_without_answer_is_skipped(self, monkeypatch):
        stub = ScriptedClient({})
        monkeypatch.setattr(s2_merge, "get_client", lambda: stub)
        assert asyncio.run(s2_merge._derive_followups(self._rec_facts(), {"A": "2015-06-28", "B": "2016-04-15"}, "m")) == []


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------

DOCS = {"A": {"doc_id": "A", "title": "Radiologie", "date": "2015-06-11"},
        "B": {"doc_id": "B", "title": "Brief", "date": "2015-06-28"},
        "C": {"doc_id": "C", "title": "Brief 2016", "date": "2016-04-15"}}


def _concepts(facts: list[Fact]) -> dict[str, Concept]:
    return s2_merge._resolve_concepts(facts)[1]


class TestS3:
    def test_laterality_conflict_within_concept(self):
        facts = [
            _fact("F:A:fin:01", "A:p001", "finding", "C:finding:carotid_ica:stenosis", laterality="left", status="positive"),
            _fact("F:B:fin:01", "B:p005", "finding", "C:finding:carotid_ica:stenosis", laterality="right", status="positive"),
            _fact("F:B:fin:02", "B:p006", "finding", "C:finding:carotid_ica:stenosis", laterality="right", status="negative"),
        ]
        [cand] = s3_contradictions._candidates_laterality(facts, _concepts(facts), DOCS)
        summary, kind, pids, cids, _ = cand
        assert kind == "laterality" and set(pids) == {"A:p001", "B:p005"}
        assert "left in Radiologie (2015-06-11)" in summary and "right in Brief (2015-06-28)" in summary

    def test_negated_other_side_is_no_conflict(self):
        facts = [
            _fact("F:A:fin:01", "A:p001", "finding", "C:finding:carotid_ica:stenosis", laterality="left", status="positive"),
            _fact("F:A:fin:02", "A:p002", "finding", "C:finding:carotid_ica:stenosis", laterality="right", status="negative"),
        ]
        assert s3_contradictions._candidates_laterality(facts, _concepts(facts), DOCS) == []

    def test_admission_dates_close_together(self):
        facts = [
            _fact("F:C:eve:01", "C:p001", "event", "C:event:admission", date="2016-04-10"),
            _fact("F:C:eve:02", "C:p030", "event", "C:event:admission", date="2016-04-12"),
            _fact("F:B:eve:01", "B:p001", "event", "C:event:admission", date="2015-06-10"),  # other stay
        ]
        [cand] = s3_contradictions._candidates_event_dates(facts, _concepts(facts), DOCS)
        assert cand[1] == "date" and set(cand[2]) == {"C:p001", "C:p030"}
        assert "10.04.2016" in cand[0] and "12.04.2016" in cand[0]

    def test_bmi_candidate_carries_concepts(self):
        facts = [
            _fact("F:B:mea:01", "B:p001", "measurement", "C:meas:body_weight", fields={"value": 78}),
            _fact("F:B:mea:02", "B:p002", "measurement", "C:meas:body_height", fields={"value": 170}),
            _fact("F:B:mea:03", "B:p003", "measurement", "C:meas:bmi", fields={"value": "29,4"}),
        ]
        [cand] = s3_contradictions._candidates_bmi(s2_merge._derive_bmi(facts), facts, DOCS)
        assert cand[3] == ["C:meas:bmi", "C:meas:body_weight", "C:meas:body_height"]
        assert set(cand[2]) == {"B:p001", "B:p002", "B:p003"}
        assert "BMI 29.4" in cand[0] and "27.0" in cand[0]

    def test_copy_mismatch_gets_concepts_of_its_passages(self):
        orig = _passage("A:p014", "Links findet sich eine Stenose der A. carotis interna")
        copy = _passage("B:p077", "Rechts findet sich eine Stenose der A. carotis interna").model_copy(update={"copy_of": "A:p014"})
        facts = [_fact("F:A:fin:01", "A:p014", "finding", "C:finding:carotid_ica:stenosis")]
        [cand] = s3_contradictions._candidates_copy_mismatch([orig, copy], facts, DOCS)
        assert cand[1] == "copy_mismatch" and cand[3] == ["C:finding:carotid_ica:stenosis"]

    def test_overlapping_contradictions_merge(self):
        from app.schemas import Contradiction
        a = Contradiction(contradiction_id="X:a", summary_en="a", kind="laterality", passage_ids=["A:p1", "B:p2"], concept_ids=["C:x"])
        b = Contradiction(contradiction_id="X:b", summary_en="b", kind="copy_mismatch", passage_ids=["B:p2", "A:p3"], concept_ids=["C:y"])
        c = Contradiction(contradiction_id="X:c", summary_en="c", kind="date", passage_ids=["C:p9"])
        merged = s3_contradictions._merge_overlapping([a, b, c])
        assert [m.contradiction_id for m in merged] == ["X:a", "X:c"]
        assert merged[0].passage_ids == ["A:p1", "B:p2", "A:p3"] and merged[0].concept_ids == ["C:x", "C:y"]


# ---------------------------------------------------------------------------
# S4 + eval
# ---------------------------------------------------------------------------

def test_coverage_counts_passages_not_facts():
    reports = [
        S1Report(doc_id="A", total_passages=10, flagged_no_content=2, facts_extracted=25, facts_rejected=0,
                 uncovered_passages=["A:p1", "A:p2"]),
        S1Report(doc_id="B", total_passages=10, flagged_no_content=0, facts_extracted=3, facts_rejected=0),
    ]
    assert coverage_pct(reports) == pytest.approx(90.0)


class TestEvalHelpers:
    def test_situation_text_excludes_questions(self, tmp_path):
        from eval.run_eval import _parse_situations_md, situation_text
        md = tmp_path / "situations.md"
        md.write_text(
            "# Situations\n\nPatient: X.\n\n## 1. Pre-operative anaesthesia check\n\n"
            "You are the anaesthetist. She is scheduled\nfor surgery on 16 May 2016.\n\n"
            "1. Which antithrombotic drug does she take?\n2. Which allergy?\n\n"
            "## 2. Surgery planning\n\nYou are the surgeon.\n\n1. What is the histology?\n",
            encoding="utf-8",
        )
        sits = _parse_situations_md(md)
        assert [s["id"] for s in sits] == ["1", "2"]
        assert situation_text(sits[0]) == (
            "Pre-operative anaesthesia check. You are the anaesthetist. She is scheduled for surgery on 16 May 2016."
        )
        assert sits[0]["questions"] == ["Which antithrombotic drug does she take?", "Which allergy?"]
        assert "histology" not in situation_text(sits[1])

    def test_score_question_checks_sources(self):
        from eval.run_eval import QuestionVerdict, score_question
        items = [{"sources": [{"doc_id": "12340018"}]}, {"sources": []}]
        exp = {"id": "1.1", "sources": ["12340011", "12340018"]}
        assert score_question(QuestionVerdict(question_id="1.1", answered=True, item_indices=[0]), exp, items)[0]
        assert not score_question(QuestionVerdict(question_id="1.1", answered=True, item_indices=[1]), exp, items)[0]
        assert not score_question(QuestionVerdict(question_id="1.1", answered=False), exp, items)[0]
        not_doc = {"id": "5.2", "sources": []}
        assert score_question(QuestionVerdict(question_id="5.2", answered=True, item_indices=[1]), not_doc, items)[0]
