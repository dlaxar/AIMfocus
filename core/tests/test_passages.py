"""
tests/test_passages.py – unit tests for S0 passage splitting and copy detection.
"""
from __future__ import annotations

import pytest
from preprocess.s0_passages import (
    _normalize_text,
    _extract_date_from_body,
    _detect_sections,
    _split_into_passages,
    _token_set_ratio,
    detect_copies,
)
from app.schemas import Passage


SAMPLE_DOC = """\
Sehr geehrter Herr Kollege,

Diagnosen:
- Arterielle Hypertonie
- Diabetes mellitus Typ 2

Entlassungsmedikation:
Blopress 16mg oral 1 0 1 0 ST
Thrombo ASS 100mg oral 1 0 0 0 ST

Gewicht: 66 kg

Wir bitten um weitere ambulante Betreuung.
"""


class TestNormalize:
    def test_html_unescape(self):
        assert "&amp;" not in _normalize_text("Herr &amp; Frau")
        assert "&" in _normalize_text("Herr &amp; Frau")

    def test_br_to_newline(self):
        result = _normalize_text("Zeile1<BR>Zeile2")
        assert "\n" in result

    def test_collapse_spaces(self):
        result = _normalize_text("too   many   spaces")
        assert "  " not in result


class TestDateExtraction:
    def test_extracts_date(self):
        date = _extract_date_from_body("Brief vom 15.04.2016, Patient")
        assert date == "2016-04-15"

    def test_no_date_returns_none(self):
        assert _extract_date_from_body("Kein Datum vorhanden") is None

    def test_impossible_date_skipped(self):
        # day 99 should not be returned
        date = _extract_date_from_body("99.13.2016")
        assert date is None


class TestSectionDetection:
    def test_finds_diagnosen(self):
        sections = _detect_sections(SAMPLE_DOC)
        labels = [s[2] for s in sections]
        assert "Diagnosen" in labels

    def test_finds_medication(self):
        sections = _detect_sections(SAMPLE_DOC)
        labels = [s[2] for s in sections]
        assert any("Medikation" in l or "Entlassung" in l for l in labels)


class TestPassageSplit:
    def test_table_row_detected(self):
        doc = "Entlassungsmedikation:\nBlopress 16mg oral 1 0 1 0 ST\n"
        sections = _detect_sections(doc)
        passages = _split_into_passages(doc, "TEST001", sections)
        kinds = [p.kind for p in passages]
        assert "table_row" in kinds

    def test_list_item_detected(self):
        doc = "Diagnosen:\n- Arterielle Hypertonie\n- Diabetes mellitus\n"
        sections = _detect_sections(doc)
        passages = _split_into_passages(doc, "TEST001", sections)
        kinds = [p.kind for p in passages]
        assert "list_item" in kinds

    def test_passage_ids_unique(self):
        sections = _detect_sections(SAMPLE_DOC)
        passages = _split_into_passages(SAMPLE_DOC, "TEST001", sections)
        ids = [p.passage_id for p in passages]
        assert len(ids) == len(set(ids))

    def test_span_within_text(self):
        """Every passage span must lie within the document text."""
        sections = _detect_sections(SAMPLE_DOC)
        passages = _split_into_passages(SAMPLE_DOC, "TEST001", sections)
        for p in passages:
            assert p.text in SAMPLE_DOC or len(p.text) == 0


class TestCopyDetection:
    def test_copies_marked(self):
        p1 = Passage(passage_id="DOC1:p000", doc_id="DOC1", kind="sentence",
                     start=0, end=50,
                     text="A. carotis interna links: Stenose ca. 60 Prozent.")
        p2 = Passage(passage_id="DOC2:p000", doc_id="DOC2", kind="sentence",
                     start=0, end=50,
                     text="A. carotis interna links: Stenose ca. 60 Prozent.")
        result = detect_copies([p1, p2], threshold=0.85)
        copy_flags = [p.copy_of for p in result]
        assert any(c is not None for c in copy_flags)

    def test_different_passages_not_copies(self):
        p1 = Passage(passage_id="DOC1:p000", doc_id="DOC1", kind="sentence",
                     start=0, end=30, text="Blopress 16mg oral 1 0 1 0 ST")
        p2 = Passage(passage_id="DOC2:p001", doc_id="DOC2", kind="sentence",
                     start=0, end=30, text="Ganz anderer Text ohne Bezug.")
        result = detect_copies([p1, p2], threshold=0.85)
        copy_flags = [p.copy_of for p in result]
        assert all(c is None for c in copy_flags)


class TestTokenSetRatio:
    def test_identical(self):
        assert _token_set_ratio("hello world", "hello world") == 1.0

    def test_disjoint(self):
        assert _token_set_ratio("foo bar", "baz qux") == 0.0

    def test_partial(self):
        r = _token_set_ratio("hello world foo", "hello world bar")
        assert 0.4 < r < 1.0


class TestDocumentMetadata:
    def test_ibm_layout_reads_nested_metadata(self, tmp_path):
        """IBM files keep id/title/date under "metadata" (regression: every doc became "unknown")."""
        import json
        from preprocess.s0_passages import run

        doc_dir = tmp_path / "data" / "P1" / "documents"
        doc_dir.mkdir(parents=True)
        for doc_id, title in (("DOC1", "Brief A"), ("DOC2", "Brief B")):
            (doc_dir / f"{doc_id}.json").write_text(json.dumps({
                "metadata": {"id": doc_id, "title": title, "date": "2016-04-15", "department": "Interne"},
                "text": "OrtXX, am 15.04.2016<BR><BR>Diagnosen:<BR>Diabetes Mellitus Typ II",
            }), encoding="utf-8")

        docs, passages = run(tmp_path / "data", tmp_path / "cache", "P1")

        assert [d.doc_id for d in docs] == ["DOC1", "DOC2"]
        assert [d.title for d in docs] == ["Brief A", "Brief B"]
        assert {p.doc_id for p in passages} == {"DOC1", "DOC2"}
        assert len({p.passage_id for p in passages}) == len(passages)

    def test_file_name_is_fallback_id(self, tmp_path):
        import json
        from preprocess.s0_passages import run

        doc_dir = tmp_path / "data" / "P1" / "documents"
        doc_dir.mkdir(parents=True)
        (doc_dir / "12340008.json").write_text(json.dumps({"text": "Befund am 11.06.2015"}), encoding="utf-8")

        docs, _ = run(tmp_path / "data", tmp_path / "cache", "P1")
        assert docs[0].doc_id == "12340008"
