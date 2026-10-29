"""
tests/test_contradictions.py – contradiction finder (contradictions/), no API key needed.

All documents here are invented. They go through the real S0 splitter and copy detection,
so the detectors see the same passages they see in production.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

# Same test environment as test_smoke.py, set before anything reads app.config: this module
# is collected first, and settings are read once on import
os.environ.setdefault("LLM_FAKE", "true")
os.environ.setdefault("PATIENT_ID", "TEST")
os.environ.setdefault("CACHE_DIR", str(Path(__file__).parent / "fixtures" / "cache"))
os.environ.setdefault("GEMINI_API_KEY", "fake")

from app.llm import client as llm_client
from app.schemas import Contradiction, Document
from contradictions import llm
from contradictions.model import Corpus, Finding
from contradictions.rules import (
    age_vs_birth_date,
    bmi_mismatch,
    case_number,
    copied_text,
    finding_polarity,
    impossible_dates,
    lv_wall,
    procedure_dates,
    run_rules,
    stay_dates,
)
from contradictions.run import is_shown, run_async
from contradictions.text import find_dates, polarity, sides
from preprocess import s0_passages


def _corpus(*docs: tuple[str, str, str, str]) -> Corpus:
    """(doc_id, title, iso date, text) → Corpus, split exactly like S0 does."""
    documents, passages = [], []
    for doc_id, title, iso_date, raw in docs:
        text = s0_passages._normalize_text(raw)
        documents.append(Document(doc_id=doc_id, title=title, date=iso_date, text=text))
        passages += s0_passages._split_into_passages(text, doc_id, s0_passages._detect_sections(text))
    passages = s0_passages.detect_copies(passages, doc_dates={d.doc_id: d.date for d in documents})
    return Corpus(documents, passages)


def _text(c: Corpus, pid: str) -> str:
    return c.passage_map[pid].text


LETTER = (
    "ORT, am 09.02.2020\n\n"
    "Aufnahme am 03/02/2020 | Entlassen am 09/02/20200\n\n"
    "Wir berichten über Herrn Test, geb. am 01.01.1960, welcher zur Abklärung aufgenommen war.\n\n"
    "Therapie und Verlauf:\n"
    "Herr Test wurde am 05.02.2020 in gutem Allgemeinzustand aufgenommen.\n"
    "Er konnte am 09.02.2020 nach Hause entlassen werden.\n"
)


# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------

class TestText:
    def test_dates_valid_impossible_and_glued(self):
        hits = find_dates("am 03/02/2020, bis 09/02/20200, Beginn: 03.02.202011:02, vom 05. Februar 2020")
        assert [h.raw for h in hits] == ["03/02/2020", "09/02/20200", "03.02.2020", "05. Februar 2020"]
        assert [h.value is None for h in hits] == [False, True, False, False]
        assert hits[2].glued_time

    def test_sides_long_and_abbreviated(self):
        assert sides("Papillenödem li > re, A. carotis sin., rechte Seite, Linksherz") == ["L", "R", "L", "R"]

    def test_polarity(self):
        t = "Sonographisch zeigen sich axillär keine vergrößerten Lymphknoten."
        assert polarity(t, t.index("vergr"), t.index("Lymph") + 10) == "negative"
        t = "Knoten tastbar, sowie vergrößerte Lymphknoten links."
        assert polarity(t, t.index("vergr"), t.index("Lymph") + 10) == "positive"
        t = "Fragestellung: vergrößerte Lymphknoten?"
        assert polarity(t, t.index("vergr"), t.index("Lymph") + 10) == "question"


# ---------------------------------------------------------------------------
# detectors
# ---------------------------------------------------------------------------

class TestStayDates:
    def test_header_vs_narrative_and_impossible_date_reported_once(self):
        c = _corpus(("L1", "Entlassungsbrief", "2020-02-09", LETTER))
        findings, covered = stay_dates(c)
        by_key = {f.key: f for f in findings}
        assert set(by_key) == {"stay_dates:L1:admission", "stay_dates:L1:discharge"}
        assert "03.02.2020 in the header but as 05.02.2020 in the text" in by_key["stay_dates:L1:admission"].summary_en
        assert "09/02/20200 (not a valid date)" in by_key["stay_dates:L1:discharge"].summary_en
        # The birth date in "geb. am 01.01.1960, welcher ... aufgenommen war" is no admission date
        assert "1960" not in by_key["stay_dates:L1:admission"].summary_en
        # The impossible discharge date is explained above, not reported a second time
        assert impossible_dates(c, covered) == []

    def test_two_contradictions_sharing_the_header_both_survive(self):
        c = _corpus(("L1", "Entlassungsbrief", "2020-02-09", LETTER))
        keys = [f.key for f in run_rules(c)]
        assert "stay_dates:L1:admission" in keys and "stay_dates:L1:discharge" in keys

    def test_consistent_letter_is_quiet(self):
        text = LETTER.replace("05.02.2020", "03.02.2020").replace("20200\n", "2020\n")
        assert stay_dates(_corpus(("L1", "Brief", "2020-02-09", text)))[0] == []

    def test_planned_admission_is_ignored(self):
        text = "Stationäre Aufnahme für den 10.02.2020 terminisiert.\n\nAufnahme am 12/02/2020 | Entlassen am 14/02/2020\n"
        assert stay_dates(_corpus(("A1", "Ambulanzbrief", "2020-02-01", text)))[0] == []


class TestImpossibleDates:
    def test_reports_impossible_but_not_glued_time(self):
        c = _corpus(("B1", "Befund", "2020-05-01", "Kontrolle am 31.04.2020 geplant.\n\nBeginn: 03.02.202011:02\n"))
        [f] = impossible_dates(c)
        assert "31.04.2020" in f.summary_en and f.certainty == "definite"


class TestBMI:
    def test_mismatch_and_match(self):
        bad = _corpus(("B1", "Brief", "2020-02-09", "Größe: 180 cm\nGewicht: 90 kg\nBMI: 31,5\n"))
        [f] = bmi_mismatch(bad)
        assert "BMI 31.5" in f.summary_en and "27.8" in f.summary_en and len(f.passage_ids) == 3
        ok = _corpus(("B1", "Brief", "2020-02-09", "Größe: 180 cm\nGewicht: 90 kg\nBMI: 27,6\n"))
        assert bmi_mismatch(ok) == []


class TestCaseNumber:
    def test_number_shared_by_two_stays_links_the_same_stay_counterpart(self):
        c = _corpus(
            ("A", "Befund A", "2019-05-02", "Fall: 20190501\n\nBefund A.\n"),
            ("B", "Befund B", "2019-05-03", "Fall-ID: 20190501\n\nBefund B.\n"),
            ("E", "Ambulanzbrief", "2020-01-20", "Fall: 20200120\n\nAmbulant.\n"),
            ("D", "Histologie", "2020-02-06", "Fall: 20200203\n\nHistologie.\n"),
            ("C", "Entlassungsbrief", "2020-02-09", "Fall: 20190501\n\n" + LETTER),
        )
        [f] = case_number(c)
        docs = [pid.split(":")[0] for pid in f.passage_ids]
        assert docs[0] == "C" and set(docs) == {"A", "B", "C", "D"}  # outpatient visit E is another case
        assert "20200203" in f.summary_en

    def test_one_stay_one_number_is_quiet(self):
        c = _corpus(("A", "A", "2019-05-02", "Fall: 1234567\n"), ("B", "B", "2019-05-05", "Fall: 1234567\n"))
        assert case_number(c) == []


ORIGINAL = (
    "Links zeigt sich eine hochgradige Stenose der A. femoralis superficialis von etwa 70%.\n\n"
    "Rechts zeigen sich nur geringe Wandunregelmäßigkeiten ohne hämodynamische Relevanz.\n\n"
    "Gesamtbeurteilung\nHochgradige Stenose der A. femoralis superficialis links.\n"
)


class TestCopiedText:
    def test_swapped_sides_in_a_copy(self):
        swapped = (ORIGINAL.replace("Links zeigt", "XX").replace("Rechts zeigen", "Links zeigen").replace("XX", "Rechts zeigt"))
        c = _corpus(("R", "Radiologie", "2019-05-02", ORIGINAL), ("L", "Brief", "2019-05-10", "Befunde:\n" + swapped))
        [f] = copied_text(c)
        assert f.kind == "laterality" and f.certainty == "definite"
        linked = [_text(c, pid) for pid in f.passage_ids]
        assert any(t.startswith("Rechts zeigt sich eine hochgradige") for t in linked)       # the flipped copy
        assert any(t.startswith("Links zeigt sich eine hochgradige") for t in linked)        # its original
        assert sum("superficialis links" in t for t in linked) == 2                         # unflipped summary + original
        assert "still says" in f.summary_en

    def test_identical_copy_and_value_change_across_stays_are_quiet(self):
        later = "Entlassungsmedikation:\nTestopril 5mg oral 1 0 1 0 ST\n"
        c = _corpus(
            ("R", "Radiologie", "2019-05-02", ORIGINAL),
            ("L", "Brief", "2019-05-10", ORIGINAL + "\nEntlassungsmedikation:\nTestopril 5mg oral 1 0 1/2 0 ST\n"),
            ("M", "Brief 2020", "2020-03-01", later),
        )
        assert copied_text(c) == []


class TestFindingPolarity:
    def test_present_and_absent_in_one_episode(self):
        c = _corpus(
            ("A", "Ambulanzbrief", "2020-01-20", "Im Tastbefund ein Knoten, sowie vergrößerte Lymphknoten links inguinal.\n"),
            ("B", "Brief", "2020-01-25", "Fragestellung: vergrößerte Lymphknoten?\n\nSonographisch keine vergrößerten Lymphknoten inguinal.\n"),
        )
        [f] = finding_polarity(c)
        assert f.certainty == "definite" and len(f.passage_ids) == 2
        assert "vergrößerte Lymphknoten links inguinal" in f.summary_en

    def test_tnm_n0_counts_as_absent(self):
        c = _corpus(
            ("A", "Ambulanzbrief", "2020-01-20", "Klinisch vergrößerte Lymphknoten axillär.\n"),
            ("H", "Histologie", "2020-01-27", "Invasives Karzinom (T2 N0 M0 G2).\n"),
        )
        [f] = finding_polarity(c)
        assert "TNM N0" in f.summary_en

    def test_other_episode_and_negations_only_are_quiet(self):
        c = _corpus(
            ("A", "Brief 2019", "2019-01-20", "Vergrößerte Lymphknoten zervikal.\n\nKein Perikarderguss.\n"),
            ("B", "Brief 2020", "2020-01-25", "Keine vergrößerten Lymphknoten.\n\nKein Perikarderguss.\n"),
        )
        assert finding_polarity(c) == []


class TestProcedureDates:
    def test_sampling_date_vs_examination_block(self):
        c = _corpus(
            ("H", "Histologie", "2020-02-07", "Entnahmedatum: 04.02.2020\nLokalisation: Mamma rechts\nEntnahmetechnik: Stanzbiopsie\n"),
            ("L", "Brief", "2020-02-09",
             "Sonographie der rechten Mamma vom 05.02.2020:\nZusammenfassung:\nEs erfolgte die ultraschallgezielte Biopsie.\n\n"
             "Histologie vom 07.02.2020:\nStanzbiopsie mit Nachweis eines Karzinoms.\n"),
        )
        [f] = procedure_dates(c)
        assert "04.02.2020" in f.summary_en and "05.02.2020" in f.summary_en and "07.02.2020" not in f.summary_en

    def test_other_side_is_another_procedure(self):
        c = _corpus(
            ("H", "Histologie", "2020-02-07", "Entnahmedatum: 04.02.2020\nLokalisation: Mamma links\nEntnahmetechnik: Stanzbiopsie\n"),
            ("L", "Brief", "2020-02-09", "Sonographie der rechten Mamma vom 05.02.2020:\nEs erfolgte die Biopsie.\n"),
        )
        assert procedure_dates(c) == []


class TestAgeAndEcho:
    def test_age_vs_birth_date(self):
        c = _corpus(("L", "Brief", "2020-02-09", "Wir berichten über Herrn Test, geb. 01.01.1960.\n\n65-jähriger Pat. in gutem AZ.\n"))
        [f] = age_vs_birth_date(c)
        assert "Age 65" in f.summary_en and "gives 60" in f.summary_en
        ok = _corpus(("L", "Brief", "2020-02-09", "Herr Test, geb. 01.01.1960.\n\n60-jähriger Pat.\n"))
        assert age_vs_birth_date(ok) == []

    def test_lv_wall_is_only_possible(self):
        c = _corpus(("E", "Echo", "2020-02-09", "Diameter: leichte Verdickung des linken Ventrikels\nSeptum: 9mm, HW: 9mm.\n"))
        [f] = lv_wall(c)
        assert f.certainty == "possible"
        assert not is_shown(f, include_possible=False, threshold=0.8)


# ---------------------------------------------------------------------------
# LLM layer (scripted client)
# ---------------------------------------------------------------------------

class ScriptedClient:
    def __init__(self, responses: dict[str, list[dict]]):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[str] = []

    async def generate(self, prompt_name, messages, response_schema=None, temperature=0.0, model=None):
        self.calls.append(prompt_name)
        queue = self.responses.get(prompt_name, [])
        raw = queue.pop(0) if queue else {}
        return response_schema.model_validate(raw) if response_schema else raw


class TestLLM:
    def _corpus(self) -> Corpus:
        return _corpus(("L", "Brief", "2020-02-09", "RR bei Aufnahme: 140/90 mmHg\n\nZum Zeitpunkt der Aufnahme RR 165/95 mmHg.\n"))

    def test_sweep_validates_quotes_ids_and_duplicates(self, monkeypatch):
        c = self._corpus()
        p0, p1 = (p.passage_id for p in c.passages)
        known = [Finding(key="k", rule="x", kind="value", certainty="definite", summary_en="k", passage_ids=["L:p999", "L:p998"])]
        client = ScriptedClient({"cx_sweep": [{"contradictions": [
            {"summary_en": "BP at admission 140/90 vs 165/95.", "kind": "value",
             "passage_ids": [p0, p1, "L:p777"], "quotes": ["140/90 mmHg", "165/95"]},
            {"summary_en": "Invented.", "kind": "value", "passage_ids": [p0], "quotes": ["EF 45%"]},
            {"summary_en": "Repeat.", "kind": "nonsense", "passage_ids": [p0, p1], "quotes": ["140/90"]},
        ]}]})
        monkeypatch.setattr(llm_client, "_client_instance", client)
        [f] = asyncio.run(llm.sweep(c, known))
        assert f.passage_ids == [p0, p1] and f.certainty == "possible" and f.rule == "llm_sweep"

    def test_review_judges_only_possible_findings(self, monkeypatch):
        c = self._corpus()
        definite = Finding(key="d", rule="x", kind="date", certainty="definite", summary_en="d", passage_ids=[c.passages[0].passage_id])
        possible = Finding(key="p", rule="x", kind="value", certainty="possible", summary_en="p", passage_ids=[c.passages[1].passage_id])
        client = ScriptedClient({"cx_judge": [{"verdict": "contradiction", "confidence": 0.9, "rationale": "r"}]})
        monkeypatch.setattr(llm_client, "_client_instance", client)
        asyncio.run(llm.review(c, [definite, possible]))
        assert client.calls == ["cx_judge"] and definite.judge is None
        assert is_shown(possible, include_possible=False, threshold=0.8)

    def test_is_duplicate_needs_two_shared_passages_or_a_subset(self):
        assert not llm.is_duplicate(["H:p1", "T:p2"], ["H:p1", "T:p3"])   # admission vs discharge on one header
        assert llm.is_duplicate(["H:p1"], ["H:p1", "T:p3"])
        assert llm.is_duplicate(["A", "B", "C"], ["B", "C", "D"])


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class TestRun:
    def test_writes_contradictions_report_and_keeps_s3(self, tmp_path: Path):
        c = _corpus(("L1", "Entlassungsbrief", "2020-02-09", LETTER + "\nGröße: 180 cm\nGewicht: 90 kg\nBMI: 31,5\n"))
        out = tmp_path / "P1"
        out.mkdir()
        (out / "documents.json").write_text(json.dumps([d.model_dump() for d in c.documents.values()]), encoding="utf-8")
        (out / "passages.json").write_text(json.dumps([p.model_dump() for p in c.passages]), encoding="utf-8")
        header = next(p.passage_id for p in c.passages if "Aufnahme am" in p.text)
        s3 = [
            Contradiction(contradiction_id="X:s3_other", summary_en="other", kind="value", passage_ids=["L1:p900"]),
            Contradiction(contradiction_id="X:s3_dup", summary_en="dup", kind="date", passage_ids=[header]),
        ]
        (out / "contradictions.json").write_text(json.dumps([x.model_dump() for x in s3]), encoding="utf-8")
        (out / "manifest.json").write_text(json.dumps({"contradiction_count": 0}), encoding="utf-8")

        result = asyncio.run(run_async(tmp_path, "P1", keep_s3=True))

        ids = [cx.contradiction_id for cx in result]
        assert "X:cf:stay_dates_l1_admission" in ids and "X:s3_other" in ids and "X:s3_dup" not in ids
        written = [Contradiction.model_validate(x) for x in json.loads((out / "contradictions.json").read_text())]
        assert [cx.contradiction_id for cx in written] == ids
        assert all(cx.judge and cx.judge.verdict == "contradiction" for cx in written if cx.contradiction_id.startswith("X:cf:"))
        report = json.loads((out / "contradictions_report.json").read_text())
        assert {r["rule"] for r in report} >= {"stay_dates", "bmi_mismatch"} and all("shown" in r for r in report)
        assert json.loads((out / "manifest.json").read_text())["contradiction_count"] == len(ids)

        # Re-running without --keep-s3 replaces S3's entries and is stable
        again = asyncio.run(run_async(tmp_path, "P1"))
        assert [cx.contradiction_id for cx in again] == [i for i in ids if i.startswith("X:cf:")]
