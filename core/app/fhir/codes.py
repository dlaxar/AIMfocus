"""
app/fhir/codes.py – standard codes for our facts, without an LLM.

- ICD-10 (diagnoses, findings) and ATC (medication) from IBM's extraction
  (data/<patient>/diagnoses.json, medication.json): each IBM mention is found in a passage
  of its document, matched to our fact on that passage, and the code spread to every fact
  of the same concept.
- LOINC from a fixed table of our measurement concepts.
"""
from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from typing import Iterable, Optional

from app.fhir.extensions import ATC, ICD10, LOINC

logger = logging.getLogger(__name__)

# Our measurement concepts → LOINC (code, display). Blood pressure also has components.
LOINC_TABLE: dict[str, tuple[str, str]] = {
    "C:meas:body_weight": ("29463-7", "Body weight"),
    "C:meas:body_height": ("8302-2", "Body height"),
    "C:meas:bmi": ("39156-5", "Body mass index (BMI) [Ratio]"),
    "C:meas:blood_pressure": ("85354-9", "Blood pressure panel with all children optional"),
    "C:meas:heart_rate": ("8867-4", "Heart rate"),
    "C:meas:hba1c": ("4548-4", "Hemoglobin A1c/Hemoglobin.total in Blood"),
    "C:meas:lvef": ("10230-1", "Left ventricular Ejection fraction"),
    "C:meas:temperature": ("8310-5", "Body temperature"),
}
LOINC_SYSTOLIC = ("8480-6", "Systolic blood pressure")
LOINC_DIASTOLIC = ("8462-4", "Diastolic blood pressure")

_TYPES_FOR = {"ICD10": ("diagnosis", "finding"), "ATC": ("medication",)}
_SYSTEM_FOR = {"ICD10": ICD10, "ATC": ATC}


def loinc_coding(concept_id: Optional[str]) -> list[dict]:
    hit = LOINC_TABLE.get(concept_id or "")
    return [{"system": LOINC, "code": hit[0], "display": hit[1]}] if hit else []


def _clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def ibm_mentions(tree: dict) -> Iterable[tuple[str, str, str, list[str]]]:
    """(catalog, code, mention text, document IDs) for every mention under a coded node."""
    def walk(node: dict, code: Optional[dict]):
        for child in node.get("values", []) or []:
            child_code = child.get("code") or code
            if child.get("values"):
                yield from walk(child, child_code)
            elif child_code and child.get("documentIds"):
                yield child_code["catalog"], child_code["value"], child.get("text") or child.get("name", ""), child["documentIds"]
    yield from walk(tree, None)


def _overlap(ptext: str, mention: str, span: str) -> bool:
    m = ptext.find(mention)
    s = ptext.find(_clean(span))
    if m < 0 or s < 0:
        return False
    return m < s + len(_clean(span)) and s < m + len(mention)


def ibm_codes(data_dir: Path, patient_id: str, facts: list[dict], passages: list[dict]) -> tuple[dict[str, list[dict]], dict]:
    """fact_id → codings from IBM's files, and match statistics. Empty when the files are missing."""
    folder = next((data_dir / n for n in (patient_id, f"patient-{patient_id}") if (data_dir / n).exists()), None)
    if folder is None:
        return {}, {"mentions": 0}
    by_doc: dict[str, list[dict]] = {}
    for p in passages:
        by_doc.setdefault(p["doc_id"], []).append(p)
    facts_by_passage: dict[str, list[dict]] = {}
    for f in facts:
        facts_by_passage.setdefault(f["passage_id"], []).append(f)

    concept_codes: dict[str, dict[tuple[str, str], str]] = {}   # concept_id → {(system, code): display}
    stats = {"mentions": 0, "passage_found": 0, "fact_matched": 0}
    for name in ("diagnoses.json", "medication.json"):
        path = folder / name
        if not path.exists():
            continue
        tree = json.loads(path.read_text(encoding="utf-8"))
        for catalog, code, text, doc_ids in ibm_mentions(tree):
            if catalog not in _SYSTEM_FOR:
                continue
            stats["mentions"] += 1
            mention = _clean(text)
            hits = [p for d in doc_ids for p in by_doc.get(str(d), []) if mention and mention in _clean(p["text"])]
            if not hits:
                continue
            stats["passage_found"] += 1
            matched = False
            for p in hits:
                candidates = [f for f in facts_by_passage.get(p["passage_id"], []) if f["type"] in _TYPES_FOR[catalog]]
                chosen = [f for f in candidates if _overlap(_clean(p["text"]), mention, f.get("span", ""))] or (candidates if len(candidates) == 1 else [])
                for f in chosen:
                    key = f.get("concept_id") or f["fact_id"]
                    concept_codes.setdefault(key, {})[(_SYSTEM_FOR[catalog], code)] = _clean(text)[:80]
                    matched = True
            stats["fact_matched"] += matched

    result: dict[str, list[dict]] = {}
    for f in facts:
        codes = concept_codes.get(f.get("concept_id") or f["fact_id"], {})
        if codes:
            result[f["fact_id"]] = [{"system": s, "code": c} for (s, c) in sorted(codes)]
    multi = {k: v for k, v in concept_codes.items() if len({c for (_s, c) in v}) > 1}
    if multi:
        logger.info("FHIR codes: %d concept(s) with more than one code, kept all: %s", len(multi), list(multi)[:5])
    stats["coded_facts"] = len(result)
    return result, stats
