"""
app/fhir/extensions.py – extension URLs, code systems and small helpers.

The extensions are not profiled (prototype): a strict validator warns about them, the R4
models accept them.
"""
from __future__ import annotations

import json
from typing import Any, Optional

BASE = "https://oachkatzlschwoaf.example/fhir"
ORIGINAL = f"{BASE}/StructureDefinition/original-values"   # our object as JSON (lossless round trip)
PASSAGES = f"{BASE}/StructureDefinition/passages"          # a document's passages as JSON
REPORT = f"{BASE}/StructureDefinition/contradiction-report" # the finder's full record of a finding
STAGE_SYSTEM = f"{BASE}/CodeSystem/pipeline-stage"          # meta.tag: which stage created a resource
CONCEPT_SYSTEM = f"{BASE}/CodeSystem/concepts"              # our concept IDs

# IBM codes diagnoses with ICD-10-GM (German modification, e.g. M81.99, not in WHO ICD-10)
ICD10 = "http://fhir.de/CodeSystem/bfarm/icd-10-gm"
ATC = "http://www.whocc.no/atc"
LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"
UCUM = "http://unitsofmeasure.org"


def json_extension(url: str, value: Any) -> dict:
    return {"url": url, "valueString": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}


def read_json_extension(resource: dict, url: str) -> Optional[Any]:
    for ext in resource.get("extension", []) or []:
        if ext.get("url") == url:
            return json.loads(ext["valueString"])
    return None


def stage_tag(stage: str) -> dict:
    return {"tag": [{"system": STAGE_SYSTEM, "code": stage}]}


def stage_of(resource: dict) -> Optional[str]:
    for tag in (resource.get("meta") or {}).get("tag", []) or []:
        if tag.get("system") == STAGE_SYSTEM:
            return tag.get("code")
    return None
