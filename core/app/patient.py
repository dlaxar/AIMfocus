"""
app/patient.py – the patient header the frontend always shows: who she is, CAVE, current
body measurements, latest stay, and what the record holds. Built in code, no LLM.
Also the patient search over every data/*/patient.json.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app.config import settings
from app.runtime.screen import _best_passage_id, _build_source
from app.store import FactStore


def patient_json(patient_id: str) -> dict:
    for name in (patient_id, f"patient-{patient_id}"):
        path = settings.DATA_DIR / name / "patient.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


@lru_cache(maxsize=4)
def _all_patients(data_dir: Path) -> tuple[dict, ...]:
    patients = []
    for path in sorted(data_dir.glob("*/patient.json")):
        info = json.loads(path.read_text(encoding="utf-8"))
        info.pop("ward", None)
        patients.append(info)
    return tuple(patients)


def search_patients(q: str, limit: int = 10) -> list[dict]:
    """Patients whose name or ID contains q (case-insensitive), from every patient.json in DATA_DIR."""
    q = q.strip().lower()
    if not q:
        return []
    return [p for p in _all_patients(settings.DATA_DIR)
            if q in str(p.get("name", "")).lower() or q in str(p.get("id", "")).lower()][:limit]


def _age(birth: Optional[str], on: Optional[str]) -> Optional[int]:
    if not birth or not on or len(birth) < 10 or len(on) < 10:
        return None
    by, bm, bd = (int(x) for x in birth[:10].split("-"))
    y, m, d = (int(x) for x in on[:10].split("-"))
    return y - by - ((m, d) < (bm, bd))


def _source(fact_id: str, store: FactStore) -> Optional[dict]:
    fact = store.get_fact(fact_id)
    pid = (fact.passage_id if fact else None) or _best_passage_id(fact_id, store)
    src = _build_source(pid, store) if pid else None
    return src.model_dump() if src else None


def _latest(store: FactStore, concept_id: str, reference_date: str) -> Optional[dict]:
    facts = [f for f in store.facts_by_concept(concept_id)
             if f.date and (not reference_date or f.date <= reference_date) and str(f.fields.get("value", "")).strip()]
    if not facts:
        return None
    f = max(facts, key=lambda f: f.date)
    return {"value": f.fields.get("value"), "unit": f.fields.get("unit"), "date": f.date,
            "fact_id": f.fact_id, "source": _source(f.fact_id, store)}


def build_profile(store: FactStore) -> dict[str, Any]:
    info = patient_json(store.patient_id)
    ref = store.reference_date() or ""
    birth = info.get("dateOfBirth") or (store.derived.get("D:age").fields.get("birth_date") if store.derived.get("D:age") else None)

    # CAVE: every documented allergy, newest mention per allergen, with its source
    allergies: dict[str, Any] = {}
    for f in sorted(store.facts_by_type("allergy"), key=lambda f: f.date or ""):
        if f.status == "negative":
            continue
        allergies[f.concept_id or f.fact_id] = {"text": (f.en or f.span).strip().rstrip("."), "date": f.date,
                                               "fact_id": f.fact_id, "source": _source(f.fact_id, store)}

    admission = info.get("latestAdmission") or {}
    letter_dates = sorted(d.date for d in store.documents.values() if d.date)
    bmi = next((d for d in sorted(store.derived.values(), key=lambda d: d.date or "", reverse=True)
                if d.formula and "height" in d.formula and (not ref or (d.date or "") <= ref)), None)
    return {
        "patient_id": store.patient_id,
        "name": info.get("name"),
        "sex": info.get("sex"),
        "birth_date": birth,
        "age": _age(birth, ref),                       # on the reference date ("now" for the record)
        "reference_date": ref or None,
        "deceased": info.get("deceased"),
        "source": info.get("source", "ibm"),
        "images": len(store.images),
        "allergies": list(allergies.values()),
        "height": _latest(store, "C:meas:body_height", ref),
        "weight": _latest(store, "C:meas:body_weight", ref),
        "bmi": {"value": bmi.fields.get("bmi"), "date": bmi.date, "fact_id": bmi.fact_id,
                "text": bmi.en} if bmi else None,
        "latest_stay": {
            "id": admission.get("id"), "type": admission.get("type"),
            "admission": (admission.get("admission") or {}).get("date"),
            "discharge": (admission.get("discharge") or {}).get("date"),
        } if admission else None,
        "record": {
            "documents": len(store.documents), "first_letter": letter_dates[0] if letter_dates else None,
            "last_letter": letter_dates[-1] if letter_dates else None, "contradictions": len(store.contradictions),
        },
    }
