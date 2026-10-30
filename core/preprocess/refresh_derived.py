"""
preprocess/refresh_derived.py – recompute the code-only derived facts of an existing cache
(medication changes with daily doses) without running the LLM steps of S2/S3 again, then
rebuild fact_sheet.txt and fhir.json.

Use it after a change to a code-only derivation, instead of `preprocess.run --from s2`
(which also re-runs the LLM follow-up and contradiction steps):

    python -m preprocess.refresh_derived --patient 0000-0001
"""
from __future__ import annotations

import argparse
import json
import logging

from app.config import settings
from app.schemas import Concept, DerivedFact, Fact
from preprocess.s2_merge import _derive_med_changes

logger = logging.getLogger(__name__)


def refresh(patient_id: str) -> int:
    out = settings.CACHE_DIR / patient_id
    facts = [Fact.model_validate(f) for f in json.loads((out / "facts.json").read_text(encoding="utf-8"))]
    concepts = {c["concept_id"]: Concept.model_validate(c)
                for c in json.loads((out / "concepts.json").read_text(encoding="utf-8"))}
    derived = json.loads((out / "derived.json").read_text(encoding="utf-8"))
    new = {d.fact_id: d.model_dump() for d in _derive_med_changes(facts, concepts)}
    # Medication changes replaced in place (new ones appended); every other derived fact
    # (follow-up status, age, BMI, …) stays as it is
    kept = [new.pop(d["fact_id"], None) or d for d in derived
            if not d["fact_id"].startswith("D:medchange:") or d["fact_id"] in new]
    derived = kept + list(new.values())
    (out / "derived.json").write_text(json.dumps(derived, ensure_ascii=False, indent=2), encoding="utf-8")

    from preprocess.s4_export import build_fact_sheet
    (out / "fact_sheet.txt").write_text(
        build_fact_sheet(patient_id, facts, [DerivedFact.model_validate(d) for d in derived]), encoding="utf-8")
    from preprocess import convert_cache
    convert_cache.to_fhir(patient_id)
    count = sum(d["fact_id"].startswith("D:medchange:") for d in derived)
    logger.info("%s: %d medication-change facts recomputed", patient_id, count)
    return count


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description="Recompute code-only derived facts of a cache (no LLM)")
    ap.add_argument("--patient", default=settings.PATIENT_ID)
    refresh(ap.parse_args().patient)


if __name__ == "__main__":
    main()
