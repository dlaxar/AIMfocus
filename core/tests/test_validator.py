"""
tests/test_validator.py – unit tests for the validation rules (section 7).
"""
from __future__ import annotations

import pytest
from app.schemas import AnswererItem, Fact, Need
from app.runtime.validator import (
    ValidationResult,
    fact_matches_filter,
    facts_for_need,
    validate_all,
    validate_item,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fact(
    fact_id="F:TEST001:med:01",
    span="Testopril 5mg oral 1 0 0 0 ST",
    en="Testoprilat (Testopril) 5 mg, 1-0-0",
    fields=None,
    laterality=None,
    date="2016-06-01",
    ftype="medication",
) -> Fact:
    return Fact(
        fact_id=fact_id,
        type=ftype,
        passage_id="TEST001:p001",
        span=span,
        fields=fields or {"brand": "Testopril", "generic": "testoprilat", "strength": "5 mg", "schedule": "1-0-0"},
        status="current",
        laterality=laterality,
        date=date,
        en=en,
    )


def _fact_getter(fact_store: dict):
    def _get(fid):
        return fact_store.get(fid)
    return _get


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestValidatorPass:
    def test_valid_documented_item(self):
        fact = _make_fact()
        store = {fact.fact_id: fact}
        item = AnswererItem(
            need_id="N1",
            fact_ids=[fact.fact_id],
            text_en="Testoprilat 5 mg, 1-0-0",
            absent=False,
        )
        result = validate_item(item, _fact_getter(store), [])
        assert result.passed, result.errors

    def test_not_documented_no_facts(self):
        item = AnswererItem(need_id="N1", fact_ids=[], text_en="Not documented", absent=True)
        result = validate_item(item, _fact_getter({}), [])
        assert result.passed, result.errors


class TestValidatorFail:
    def test_missing_fact_id(self):
        item = AnswererItem(
            need_id="N1",
            fact_ids=["F:NONEXISTENT:med:99"],
            text_en="Something 5 mg",
            absent=False,
        )
        result = validate_item(item, _fact_getter({}), [])
        assert not result.passed
        assert any("not found" in e for e in result.errors)

    def test_number_not_in_fact(self):
        fact = _make_fact()
        store = {fact.fact_id: fact}
        item = AnswererItem(
            need_id="N1",
            fact_ids=[fact.fact_id],
            text_en="Testoprilat 999 mg",  # 999 not in fact
            absent=False,
        )
        result = validate_item(item, _fact_getter(store), [])
        assert not result.passed
        assert any("999" in e for e in result.errors)

    def test_laterality_mismatch(self):
        fact = _make_fact(laterality="right")
        store = {fact.fact_id: fact}
        item = AnswererItem(
            need_id="N1",
            fact_ids=[fact.fact_id],
            text_en="Left stenosis 5 mg",  # says "left" but fact is "right"
            absent=False,
        )
        result = validate_item(item, _fact_getter(store), [])
        assert not result.passed
        assert any("laterality" in e for e in result.errors)

    def test_date_not_in_fact(self):
        fact = _make_fact(date="2016-06-01")
        store = {fact.fact_id: fact}
        item = AnswererItem(
            need_id="N1",
            fact_ids=[fact.fact_id],
            text_en="Testoprilat from June 2015",  # 2015 not in fact
            absent=False,
        )
        result = validate_item(item, _fact_getter(store), [])
        assert not result.passed
        assert any("2015" in e for e in result.errors)

    def test_not_documented_with_cited_facts(self):
        item = AnswererItem(
            need_id="N1",
            fact_ids=["F:SOMETHING:med:01"],
            text_en="Not documented",
            absent=True,
        )
        result = validate_item(item, _fact_getter({}), [])
        assert not result.passed


def _meas(fact_id, name, value, unit, date, concept_id=None) -> Fact:
    return Fact(
        fact_id=fact_id, type="measurement", passage_id="TEST001:p010",
        span=f"{name}: {value} {unit}", fields={"name": name, "value": value, "unit": unit},
        status="positive", date=date, concept_id=concept_id, en=f"{name} {value} {unit}",
    )


class TestFilterMatching:
    def test_drug_class_list_any_of(self):
        f = _make_fact(fields={"brand": "Thrombo ASS", "generic": "acetylsalicylic acid", "drug_class": "antiplatelet"})
        assert fact_matches_filter(f, {"drug_class": ["antithrombotic", "antiplatelet"]})
        assert not fact_matches_filter(f, {"drug_class": ["biguanide"]})

    def test_name_matches_concept_slug(self):
        f = _meas("F:T:mea:01", "EF", 60, "%", "2015-06", concept_id="C:meas:lvef")
        assert fact_matches_filter(f, {"name": "lvef"})
        assert fact_matches_filter(f, {"name": "LVEF"})

    def test_unknown_key_does_not_match(self):
        f = _meas("F:T:mea:01", "Gewicht", 78, "kg", "2015-06")
        assert not fact_matches_filter(f, {"name": "creatinine"})

    def test_broad_category_without_filter_is_unspecific(self):
        need = Need(need_id="N1", label="Kidney function", category="measurement")
        assert facts_for_need(need, [_meas("F:T:mea:01", "Gewicht", 78, "kg", "2015-06")]) is None


class TestValidateAll:
    def _need(self, **kw):
        return Need(need_id="N1", label="x", category=kw.pop("category", "measurement"), **kw)

    def test_hallucinated_fact_id_never_shows_llm_text(self):
        item = AnswererItem(need_id="N1", fact_ids=["F:NOPE:mea:01"], text_en="Creatinine 1.1 mg/dl", absent=False)
        validated, dropped = validate_all([item], _fact_getter({}), lambda c: [], [self._need()])
        assert dropped == 1
        assert validated[0].screen_kind == "check"
        assert "1.1" not in validated[0].item.text_en
        assert validated[0].item.fact_ids == []

    def test_invalid_text_falls_back_to_stored_rendering(self):
        fact = _make_fact()
        item = AnswererItem(need_id="N1", fact_ids=[fact.fact_id, "F:NOPE:med:09"], text_en="Testoprilat 999 mg", absent=False)
        validated, _ = validate_all([item], _fact_getter({fact.fact_id: fact}), lambda c: [], [self._need(category="medication")])
        assert validated[0].screen_kind == "documented"
        assert validated[0].item.text_en == fact.en
        assert validated[0].item.fact_ids == [fact.fact_id]

    def test_absent_stays_not_documented_when_only_unrelated_facts_exist(self):
        weight = _meas("F:T:mea:01", "Gewicht", 78, "kg", "2015-06", concept_id="C:meas:body_weight")
        item = AnswererItem(need_id="N1", fact_ids=[], text_en="Not documented", absent=True)
        need = self._need(filter={"name": "creatinine"})
        validated, _ = validate_all([item], _fact_getter({}), lambda c: [weight], [need])
        assert validated[0].screen_kind == "not_documented"

    def test_absent_downgraded_to_check_when_matching_fact_exists(self):
        ef = _meas("F:T:mea:02", "EF", 60, "%", "2015-06", concept_id="C:meas:lvef")
        item = AnswererItem(need_id="N1", fact_ids=[], text_en="Not documented", absent=True)
        need = self._need(filter={"name": "lvef"})
        validated, _ = validate_all([item], _fact_getter({ef.fact_id: ef}), lambda c: [ef], [need])
        assert validated[0].screen_kind == "check"
        assert validated[0].item.fact_ids == [ef.fact_id]
