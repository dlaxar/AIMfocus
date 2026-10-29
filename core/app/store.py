"""
app/store.py – loads precomputed cache into memory and provides lookup helpers.

The runtime refuses to start if the manifest is missing.
Call load_store() once at startup (FastAPI lifespan).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from app.config import settings
from app.schemas import (
    CacheManifest,
    Concept,
    Contradiction,
    DerivedFact,
    Document,
    Fact,
    Passage,
)

logger = logging.getLogger(__name__)


class FactStore:
    """In-memory index of all precomputed artifacts for one patient."""

    def __init__(self) -> None:
        self.documents: dict[str, Document] = {}
        self.passages: dict[str, Passage] = {}
        self.facts: dict[str, Fact] = {}
        self.derived: dict[str, DerivedFact] = {}
        self.concepts: dict[str, Concept] = {}
        self.contradictions: list[Contradiction] = []
        self.fact_sheet: str = ""
        self.manifest: Optional[CacheManifest] = None

        # Reverse indices
        self._passage_by_doc: dict[str, list[str]] = {}     # doc_id → [passage_id]
        self._facts_by_concept: dict[str, list[str]] = {}   # concept_id → [fact_id]
        self._facts_by_type: dict[str, list[str]] = {}      # type → [fact_id]

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, cache_dir: Path, patient_id: str) -> None:
        out_dir = cache_dir / patient_id
        manifest_path = out_dir / "manifest.json"

        if not manifest_path.exists():
            raise RuntimeError(
                f"Cache manifest not found at {manifest_path}. "
                "Run: python -m preprocess.run --patient " + patient_id
            )

        self.manifest = CacheManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        logger.info("Store: loading cache v%s (coverage %.1f%%)", self.manifest.completed_at, self.manifest.coverage_pct)

        # Documents
        raw_docs = json.loads((out_dir / "documents.json").read_text(encoding="utf-8"))
        for d in raw_docs:
            doc = Document.model_validate(d)
            self.documents[doc.doc_id] = doc

        # Passages
        raw_passages = json.loads((out_dir / "passages.json").read_text(encoding="utf-8"))
        for p_raw in raw_passages:
            p = Passage.model_validate(p_raw)
            self.passages[p.passage_id] = p
            self._passage_by_doc.setdefault(p.doc_id, []).append(p.passage_id)

        # Facts
        raw_facts = json.loads((out_dir / "facts.json").read_text(encoding="utf-8"))
        for f_raw in raw_facts:
            f = Fact.model_validate(f_raw)
            self.facts[f.fact_id] = f
            self._facts_by_type.setdefault(f.type, []).append(f.fact_id)
            if f.concept_id:
                self._facts_by_concept.setdefault(f.concept_id, []).append(f.fact_id)

        # Derived facts
        raw_derived = json.loads((out_dir / "derived.json").read_text(encoding="utf-8"))
        for d_raw in raw_derived:
            df = DerivedFact.model_validate(d_raw)
            self.derived[df.fact_id] = df

        age = self._age_fact()
        if age:
            self.derived[age.fact_id] = age

        # Concepts
        raw_concepts = json.loads((out_dir / "concepts.json").read_text(encoding="utf-8"))
        for c_raw in raw_concepts:
            c = Concept.model_validate(c_raw)
            self.concepts[c.concept_id] = c

        # Contradictions
        contradictions_path = out_dir / "contradictions.json"
        if contradictions_path.exists():
            raw_cx = json.loads(contradictions_path.read_text(encoding="utf-8"))
            self.contradictions = [Contradiction.model_validate(x) for x in raw_cx]

        # Fact sheet (for the answerer prompt)
        fact_sheet_path = out_dir / "fact_sheet.txt"
        if fact_sheet_path.exists():
            self.fact_sheet = fact_sheet_path.read_text(encoding="utf-8")

        logger.info(
            "Store loaded: %d docs, %d passages, %d facts, %d derived, %d contradictions",
            len(self.documents), len(self.passages), len(self.facts),
            len(self.derived), len(self.contradictions),
        )

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get_fact(self, fact_id: str) -> Optional[Fact]:
        return self.facts.get(fact_id) or None  # type: ignore[return-value]

    def get_any_fact(self, fact_id: str) -> Optional[Fact | DerivedFact]:
        return self.facts.get(fact_id) or self.derived.get(fact_id)  # type: ignore[return-value]

    def get_passage(self, passage_id: str) -> Optional[Passage]:
        return self.passages.get(passage_id)

    def get_document(self, doc_id: str) -> Optional[Document]:
        return self.documents.get(doc_id)

    def facts_by_type(self, fact_type: str) -> list[Fact]:
        return [self.facts[fid] for fid in self._facts_by_type.get(fact_type, []) if fid in self.facts]

    def facts_by_concept(self, concept_id: str) -> list[Fact]:
        return [self.facts[fid] for fid in self._facts_by_concept.get(concept_id, []) if fid in self.facts]

    def passages_for_doc(self, doc_id: str) -> list[Passage]:
        return [self.passages[pid] for pid in self._passage_by_doc.get(doc_id, []) if pid in self.passages]

    def available_types(self) -> list[str]:
        return list(self._facts_by_type.keys())

    def concept_labels_sample(self, n: int = 30) -> list[str]:
        return [c.label_en for c in list(self.concepts.values())[:n]]

    def contradictions_for_concepts(self, concept_ids: set[str]) -> list[Contradiction]:
        return [cx for cx in self.contradictions if set(cx.concept_ids) & concept_ids]

    def _age_fact(self) -> Optional[DerivedFact]:
        """Age on the reference date, from the date of birth in a letter header ("geb. 15.03.1959")."""
        ref = self.reference_date()
        for p in self.passages.values():
            m = re.search(r"\bgeb(?:oren|\.)?\s*(?:am\s*)?(\d{1,2})\.(\d{1,2})\.(\d{4})", p.text)
            if not m or not ref:
                continue
            d, mo, y = int(m[1]), int(m[2]), int(m[3])
            ry, rm, rd = (int(x) for x in ref[:10].split("-"))
            age = ry - y - ((rm, rd) < (mo, d))
            return DerivedFact(
                fact_id="D:age",
                en=f"Age {age} years (born {y:04d}-{mo:02d}-{d:02d})",
                formula="age_at_reference_date",
                fields={"passage_id": p.passage_id, "birth_date": f"{y:04d}-{mo:02d}-{d:02d}"},
                date=ref[:10],
            )
        return None

    def reference_date(self) -> Optional[str]:
        from app.config import settings as _settings
        if _settings.REFERENCE_DATE != "auto":
            return _settings.REFERENCE_DATE
        dates = [d.date for d in self.documents.values() if d.date]
        return max(dates) if dates else None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[FactStore] = None


def get_store() -> FactStore:
    global _store
    if _store is None:
        raise RuntimeError("FactStore not initialised. Call load_store() first.")
    return _store


def load_store() -> FactStore:
    global _store
    _store = FactStore()
    _store.load(settings.CACHE_DIR, settings.PATIENT_ID)
    return _store
