"""
tests/test_shape.py – output types of screen variables (value, trend, table, list), built in code.
"""
from __future__ import annotations

from app.fastpath import shape
from app.fastpath.models import ScreenVariable, SelectedVariable
from app.schemas import Concept, Contradiction, DerivedFact, Fact


class _Store:
    def __init__(self, facts, derived=(), contradictions=()):
        self.facts = {f.fact_id: f for f in facts}
        self.derived = {d.fact_id: d for d in derived}
        self.contradictions = list(contradictions)
        self.concepts = {"C:meas:body_weight": Concept(concept_id="C:meas:body_weight", type="measurement", label_en="Body weight"),
                         "C:meas:blood_pressure": Concept(concept_id="C:meas:blood_pressure", type="measurement", label_en="Blood pressure")}

    def get_fact(self, fid):
        return self.facts.get(fid)

    def facts_by_concept(self, cid):
        return [f for f in self.facts.values() if f.concept_id == cid]

    def get_passage(self, pid):
        return None

    def get_document(self, doc_id):
        return None


def _fact(fid, type_, concept, date, en, **fields):
    return Fact(fact_id=fid, type=type_, passage_id=f"{fid}:p1", span=en, concept_id=concept, date=date, en=en,
                fields=fields, status="current" if type_ == "medication" else "positive")


def _var(output):
    return ScreenVariable(name="v", label="V", state="documented", output=output)


def test_trend_gives_numbers_and_flags_the_contradicting_point():
    facts = [_fact("F:A:mea:01", "measurement", "C:meas:body_weight", "2015-06-28", "Weight 78 kg", value="78", unit="kg"),
             _fact("F:B:mea:01", "measurement", "C:meas:body_weight", "2016-04-15", "Weight 66 kg", value="66", unit="kg")]
    cx = Contradiction(contradiction_id="X:bmi", summary_en="BMI does not match", kind="calculation", fact_ids=["F:A:mea:01"])
    store = _Store(facts, contradictions=[cx])
    var = _var("trend")
    shape.shape_documented(var, SelectedVariable(name="v", label="V"), facts, store, "2016-04-15")
    (series,) = var.series
    assert [(p.date, p.value) for p in series.points] == [("2015-06-28", 78.0), ("2016-04-15", 66.0)]
    assert series.points[0].contradiction_ids == ["X:bmi"] and series.points[1].contradiction_ids == []
    assert var.headline.text == "66 kg" and var.headline.change == "-12 kg since 2015-06-28"


def test_blood_pressure_is_two_series_and_one_headline():
    facts = [_fact("F:A:mea:02", "measurement", "C:meas:blood_pressure", "2016-04-05", "BP 145/88", value="145/88", unit="mmHg")]
    var = _var("trend")
    shape.shape_documented(var, SelectedVariable(name="v", label="V"), facts, _Store(facts), "2016-04-15")
    assert [s.name for s in var.series] == ["Blood pressure – Systolic", "Blood pressure – Diastolic"]
    assert var.headline.text == "145/88 mmHg"


def test_trend_without_numbers_falls_back_to_value():
    facts = [_fact("F:A:fin:01", "finding", None, "2015-06-28", "Normal LV function")]
    var = _var("trend")
    shape.shape_documented(var, SelectedVariable(name="v", label="V"), facts, _Store(facts), "2016-04-15")
    assert var.output == "value" and var.latest[0].text == "Normal LV function"


def test_table_keeps_item_first_and_few_columns():
    med = _fact("F:A:med:01", "medication", "C:med:aspirin", "2016-04-15", "Aspirin 100 mg",
                generic="acetylsalicylic acid", brand="Thrombo ASS", strength="100 mg", schedule="1-0-0", drug_class="antiplatelet")
    curmed = DerivedFact(fact_id="D:curmed:aspirin", en="Current medication: Aspirin", inputs=["F:A:med:01"])
    sel = SelectedVariable(name="v", label="V", columns=["class", "schedule", "strength", "route", "since", "bogus"])
    var = _var("table")
    shape.shape_documented(var, sel, [curmed], _Store([med], [curmed]), "2016-04-15")
    assert var.table.columns == ["item", "class", "schedule", "strength"]          # item first, at most 4
    assert var.table.rows[0].cells == {"item": "Acetylsalicylic acid (Thrombo ASS)", "class": "antiplatelet",
                                       "schedule": "1-0-0", "strength": "100 mg"}
    assert var.table.rows[0].fact_id == "F:A:med:01"                               # the derived row stands for its input


def test_codes_come_separately_from_the_text():
    facts = [_fact("F:A:mea:01", "measurement", "C:meas:body_weight", "2016-04-15", "Weight 66 kg", value="66", unit="kg")]
    store = _Store(facts)
    store.codes = {"F:A:mea:01": [{"system": "http://loinc.org", "code": "29463-7", "display": "Body weight"}]}
    var = _var("trend")
    shape.shape_documented(var, SelectedVariable(name="v", label="V"), facts, store, "2016-04-15")
    point = var.series[0].points[0]
    assert [(c.system, c.code) for c in point.codes] == [("LOINC", "29463-7")]
    assert var.series[0].code.code == "29463-7" and "29463-7" not in var.series[0].name + var.headline.text


def test_list_shows_a_repeated_diagnosis_once():
    facts = [_fact("F:A:dia:01", "diagnosis", "C:dx:hypertension", "2015-06-28", "Arterial hypertension"),
             _fact("F:B:dia:01", "diagnosis", "C:dx:hypertension", "2016-04-15", "Arterial hypertension")]
    var = _var("list")
    shape.shape_documented(var, SelectedVariable(name="v", label="V"), facts, _Store(facts), "2016-04-15")
    assert [(e.fact_id, e.date) for e in var.items] == [("F:B:dia:01", "2016-04-15")]
