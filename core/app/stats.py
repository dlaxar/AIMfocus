"""
app/stats.py – counts for the /stats page: patients in DATA_DIR and the FHIR bundles in
CACHE_DIR, each checked against the R4B models (same check as fhir.save) and remembered
until the file changes.
"""
from __future__ import annotations

import collections
import json
import logging
from pathlib import Path
from typing import Any

from app import patient
from app.config import settings
from app.fhir.cache import validate

logger = logging.getLogger(__name__)

# fhir.json path → (mtime, result); validating a bundle takes up to ~0.5 s
_checked: dict[Path, tuple[float, dict]] = {}


def _check_bundle(path: Path) -> dict[str, Any]:
    mtime = path.stat().st_mtime
    hit = _checked.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    bundle = json.loads(path.read_text(encoding="utf-8"))
    types = collections.Counter(e["resource"]["resourceType"] for e in bundle.get("entry", []))
    try:
        validate(bundle)
        error = None
    except Exception as exc:  # pydantic ValidationError, or fhir.resources missing
        error = str(exc).splitlines()[0][:300]
        logger.warning("FHIR bundle %s is not valid: %s", path, error)
    result = {"resources": sum(types.values()), "documents": types.get("DocumentReference", 0),
              "valid": error is None, "error": error}
    _checked[path] = (mtime, result)
    return result


def collect() -> dict[str, Any]:
    """{patients, patients_with_fhir, bundles, valid_bundles, documents, valid_documents, per_patient[]}."""
    data_patients = {p.get("id"): p for p in patient._all_patients(settings.DATA_DIR)}
    bundles = sorted(settings.CACHE_DIR.glob("*/fhir.json")) if settings.CACHE_DIR.exists() else []
    rows = []
    for path in bundles:
        pid = path.parent.name
        info = data_patients.get(pid) or patient.patient_json(pid)
        rows.append({"patient_id": pid, "name": info.get("name"), **_check_bundle(path)})
    valid = [r for r in rows if r["valid"]]
    return {
        "patients": len(data_patients),
        "patients_with_fhir": len(rows),
        "bundles": len(rows),
        "valid_bundles": len(valid),
        "documents": sum(r["documents"] for r in rows),
        "valid_documents": sum(r["documents"] for r in valid),
        "per_patient": rows,
    }
