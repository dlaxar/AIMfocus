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
        self.codes: dict[str, list[dict]] = {}   # fact_id → standard codings (ICD-10, ATC, LOINC)

        # Reverse indices
        self._passage_by_doc: dict[str, list[str]] = {}     # doc_id → [passage_id]
        self._facts_by_concept: dict[str, list[str]] = {}   # concept_id → [fact_id]
        self._facts_by_type: dict[str, list[str]] = {}      # type → [fact_id]

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, cache_dir: Path, patient_id: str) -> None:
        out_dir = cache_dir / patient_id
        fhir_path = out_dir / "fhir.json"
        self.source = "json"
        if settings.CACHE_FORMAT != "json" and fhir_path.exists():
            from app import fhir
            fhir_cache = fhir.load(fhir_path)
            artifacts = fhir_cache.data
            self.codes = fhir_cache.codes
            self.source = "fhir"

            def read(name: str, default=None):
                return artifacts.get(name, default)
        elif settings.CACHE_FORMAT == "fhir":
            raise RuntimeError(f"CACHE_FORMAT=fhir but {fhir_path} does not exist. Run: python -m preprocess.convert_cache")
        else:
            def read(name: str, default=None):
                path = out_dir / f"{name}.json"
                return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

        manifest = read("manifest")
        if manifest is None:
            raise RuntimeError(
                f"Cache manifest not found in {out_dir}. "
                "Run: python -m preprocess.run --patient " + patient_id
            )

        self.manifest = CacheManifest.model_validate(manifest)
        logger.info("Store: loading %s cache v%s (coverage %.1f%%)", self.source, self.manifest.completed_at, self.manifest.coverage_pct)

        # Documents
        raw_docs = read("documents", [])
        for d in raw_docs:
            doc = Document.model_validate(d)
            self.documents[doc.doc_id] = doc

        # Passages
        raw_passages = read("passages", [])
        for p_raw in raw_passages:
            p = Passage.model_validate(p_raw)
            self.passages[p.passage_id] = p
            self._passage_by_doc.setdefault(p.doc_id, []).append(p.passage_id)

        # Facts
        raw_facts = read("facts", [])
        for f_raw in raw_facts:
            f = Fact.model_validate(f_raw)
            self.facts[f.fact_id] = f
            self._facts_by_type.setdefault(f.type, []).append(f.fact_id)
            if f.concept_id:
                self._facts_by_concept.setdefault(f.concept_id, []).append(f.fact_id)

        # Derived facts
        raw_derived = read("derived", [])
        for d_raw in raw_derived:
            df = DerivedFact.model_validate(d_raw)
            self.derived[df.fact_id] = df

        age = self._age_fact()
        if age:
            self.derived[age.fact_id] = age

        # Concepts
        raw_concepts = read("concepts", [])
        for c_raw in raw_concepts:
            c = Concept.model_validate(c_raw)
            self.concepts[c.concept_id] = c

        # Contradictions
        raw_cx = read("contradictions")
        if raw_cx is not None:
            self.contradictions = [Contradiction.model_validate(x) for x in raw_cx]
            for cx in self.contradictions:
                cx.fact_ids = self._facts_in_passages(set(cx.passage_ids))

        if self.source == "json":   # same codes the FHIR bundle carries
            self.codes = self._standard_codes(raw_facts, raw_passages, patient_id)

        # Fact sheet (for the answerer prompt)
        # (fhir.json does not store it; it is rebuilt exactly as S4 writes it)
        fact_sheet_path = out_dir / "fact_sheet.txt"
        if self.source == "fhir":
            from preprocess.s4_export import build_fact_sheet
            self.fact_sheet = build_fact_sheet(
                patient_id, [Fact.model_validate(f) for f in raw_facts], [DerivedFact.model_validate(d) for d in raw_derived])
        elif fact_sheet_path.exists():
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

    @staticmethod
    def _standard_codes(raw_facts: list[dict], raw_passages: list[dict], patient_id: str) -> dict[str, list[dict]]:
        from app.fhir.codes import ibm_codes, loinc_coding
        codes, _stats = ibm_codes(settings.DATA_DIR, patient_id, raw_facts, raw_passages)
        for f in raw_facts:
            if f.get("type") == "measurement" and loinc_coding(f.get("concept_id")):
                codes[f["fact_id"]] = codes.get(f["fact_id"], []) + loinc_coding(f.get("concept_id"))
        return codes

    def _facts_in_passages(self, passage_ids: set[str]) -> list[str]:
        """Facts extracted from these passages or copies of them, plus derived facts built on them."""
        def linked(pid: str) -> bool:
            p = self.passages.get(pid)
            return pid in passage_ids or bool(p and p.copy_of in passage_ids)
        facts = [fid for fid, f in self.facts.items() if linked(f.passage_id)]
        hit = set(facts)
        return facts + [did for did, d in self.derived.items() if hit & set(d.inputs)]

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
