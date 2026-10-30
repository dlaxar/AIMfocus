"""
tests/test_smoke.py – smoke test: start app on fixture cache, POST one situation,
assert schema-valid Screen is returned.

Requires LLM_FAKE=true (set via environment before running, or via conftest.py).
Uses a small in-memory fixture cache built from tests/fixtures/documents.json.
"""
from __future__ import annotations

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch

# Ensure LLM_FAKE is set before importing anything that reads settings
os.environ.setdefault("LLM_FAKE", "true")
os.environ.setdefault("PATIENT_ID", "TEST")
os.environ.setdefault("CACHE_DIR", str(Path(__file__).parent / "fixtures" / "cache"))
os.environ.setdefault("GEMINI_API_KEY", "fake")

from fastapi.testclient import TestClient


FIXTURE_DIR = Path(__file__).parent / "fixtures"
CACHE_DIR = FIXTURE_DIR / "cache" / "TEST"


def _build_fixture_cache():
    """Create a minimal cache from the fixture documents so the store can load."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    docs_src = FIXTURE_DIR / "documents.json"
    raw_docs = json.loads(docs_src.read_text(encoding="utf-8"))

    # Write documents.json
    (CACHE_DIR / "documents.json").write_text(
        json.dumps(raw_docs, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Build passages from S0
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from preprocess.s0_passages import _normalize_text, _detect_sections, _split_into_passages

    all_passages = []
    for doc in raw_docs:
        doc_id = doc["doc_id"]
        norm_text = _normalize_text(doc.get("text", ""))
        sections = _detect_sections(norm_text)
        passages = _split_into_passages(norm_text, doc_id, sections)
        # Update doc text to normalized
        doc["text"] = norm_text
        all_passages.extend(p.model_dump() for p in passages)

    (CACHE_DIR / "documents.json").write_text(
        json.dumps(raw_docs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (CACHE_DIR / "passages.json").write_text(
        json.dumps(all_passages, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Minimal facts (from fixture LLM response, adapted to actual passage IDs)
    # Use first passage IDs from TEST001
    test001_passages = [p for p in all_passages if p["doc_id"] == "TEST001"]
    pid0 = test001_passages[0]["passage_id"] if test001_passages else "TEST001:p000"
    pid1 = test001_passages[1]["passage_id"] if len(test001_passages) > 1 else "TEST001:p001"
    pid2 = test001_passages[2]["passage_id"] if len(test001_passages) > 2 else "TEST001:p002"

    facts = [
        {
            "fact_id": "F:TEST001:all:01",
            "type": "allergy",
            "passage_id": pid0,
            "span": test001_passages[0]["text"][:40] if test001_passages else "Kontrastmittel",
            "fields": {"substance": "contrast media", "reaction": ["nausea", "dizziness"]},
            "status": "positive",
            "laterality": None,
            "date": "2016-06-01",
            "date_precision": "day",
            "date_basis": "document_date",
            "concept_id": "C:allergy:contrast_media",
            "en": "Contrast media allergy (nausea, dizziness)",
            "de": "Kontrastmittel (Uebelkeit, Schwindel)",
        },
    ]
    (CACHE_DIR / "facts.raw.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (CACHE_DIR / "facts.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (CACHE_DIR / "derived.json").write_text("[]", encoding="utf-8")
    (CACHE_DIR / "concepts.json").write_text(
        json.dumps([
            {
                "concept_id": "C:allergy:contrast_media",
                "label_en": "Contrast media",
                "type": "allergy",
                "fact_ids": ["F:TEST001:all:01"],
                "series": [],
            }
        ], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (CACHE_DIR / "contradictions.json").write_text("[]", encoding="utf-8")
    (CACHE_DIR / "fact_sheet.txt").write_text(
        "F:TEST001:all:01 | allergy | Contrast media allergy (nausea, dizziness) | positive | 2016-06-01\n",
        encoding="utf-8",
    )

    from datetime import datetime, timezone
    manifest = {
        "patient_id": "TEST",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "preprocess_model": "fake",
        "prompt_versions": {},
        "document_count": len(raw_docs),
        "passage_count": len(all_passages),
        "fact_count": len(facts),
        "derived_fact_count": 0,
        "contradiction_count": 0,
        "coverage_pct": 100.0,
    }
    (CACHE_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@pytest.fixture(scope="session", autouse=True)
def fixture_cache():
    _build_fixture_cache()


@pytest.fixture()
def client():
    from app.store import load_store
    import importlib
    import app.store as store_mod

    # Reset singleton
    store_mod._store = None

    from app.main import app
    with TestClient(app) as c:
        yield c


class TestHealthz:
    def test_healthz_ok(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["cache_loaded"] is True
        assert isinstance(data["commit"]["tags"], list)


class TestScreenEndpoint:
    def test_post_screen_returns_valid_schema(self, client):
        resp = client.post(
            "/api/screen",
            json={"situation": "Anaesthetist, pre-op check before surgery"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Validate top-level schema fields
        assert "situation" in data
        assert "sections" in data
        assert "meta" in data
        assert isinstance(data["sections"], list)

    def test_screen_sections_have_items(self, client):
        resp = client.post(
            "/api/screen",
            json={"situation": "Anaesthetist, pre-op check before surgery"},
        )
        data = resp.json()
        for section in data["sections"]:
            assert "need_id" in section
            assert "label" in section
            assert "items" in section
            for item in section["items"]:
                assert item["kind"] in ("documented", "derived", "not_documented", "check")

    def test_documents_endpoint(self, client):
        resp = client.get("/api/documents")
        assert resp.status_code == 200
        docs = resp.json()
        assert len(docs) > 0
        assert all("doc_id" in d for d in docs)

    def test_contradictions_endpoint(self, client):
        resp = client.get("/api/contradictions")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
