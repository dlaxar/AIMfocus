"""
app/fhir/mapping.py – our cache objects as FHIR R4 resources.

Every resource fills the standard fields (code, status, date, value, body site) and carries
the original object in the original-values extension; loading reads that extension, so the
round trip is lossless and independent of FHIR's status vocabularies.
"""
from __future__ import annotations

import base64
import re
from typing import Any, Optional

from app.fhir.codes import LOINC_DIASTOLIC, LOINC_SYSTOLIC, loinc_coding
from app.fhir.extensions import (
    CONCEPT_SYSTEM, LOINC, ORIGINAL, PASSAGES, REPORT, SNOMED, UCUM, json_extension, stage_tag,
)
from app.fhir.ids import fhir_id

_DATE = re.compile(r"^\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?$")
_NUMBER = re.compile(r"^-?\d+(?:[.,]\d+)?$")
_LATERALITY = {"left": ("7771000", "Left"), "right": ("24028007", "Right"), "bilateral": ("51440002", "Bilateral")}

RESOURCE_FOR_TYPE = {
    "diagnosis": "Condition", "finding": "Observation", "measurement": "Observation",
    "demographic": "Observation", "medication": "MedicationStatement", "allergy": "AllergyIntolerance",
    "procedure": "Procedure", "recommendation": "ServiceRequest", "plan": "ServiceRequest", "event": "Encounter",
}


def _date(value: Optional[str]) -> Optional[str]:
    return value if value and _DATE.match(value) else None


def _cc(text: str, codings: list[dict] = ()) -> dict:
    cc: dict[str, Any] = {"text": text}
    if codings:
        cc["coding"] = list(codings)
    return cc


def _status_cc(system: str, code: str) -> dict:
    return {"coding": [{"system": f"http://terminology.hl7.org/CodeSystem/{system}", "code": code}]}


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def ref(our_id: str, resource_type: str) -> dict:
    return {"reference": f"{resource_type}/{fhir_id(our_id)}"}


# ---------------------------------------------------------------------------
# Patient, documents, artifacts
# ---------------------------------------------------------------------------

def patient(patient_id: str, info: Optional[dict]) -> dict:
    r: dict[str, Any] = {"resourceType": "Patient", "id": fhir_id(patient_id), "meta": stage_tag("s0"),
                         "identifier": [{"system": "urn:ibm-aim:patient", "value": patient_id}]}
    if info:
        if info.get("name"):
            r["name"] = [{"text": info["name"]}]
        if info.get("sex") in ("female", "male", "other", "unknown"):
            r["gender"] = info["sex"]
        if _date(info.get("dateOfBirth")):
            r["birthDate"] = info["dateOfBirth"]
    return r


def document_reference(doc: dict, passages: list[dict], patient_ref: dict) -> dict:
    meta = {k: v for k, v in doc.items() if k != "text"}
    r: dict[str, Any] = {
        "resourceType": "DocumentReference", "id": fhir_id(doc["doc_id"]), "meta": stage_tag("s0"),
        "extension": [json_extension(ORIGINAL, meta), json_extension(PASSAGES, passages)],
        "identifier": [{"system": "urn:ibm-aim:document", "value": doc["doc_id"]}],
        "status": "current", "subject": patient_ref, "description": doc.get("title") or doc["doc_id"],
        "content": [{"attachment": {"contentType": "text/plain; charset=utf-8", "data": _b64(doc.get("text", "")),
                                    "title": doc.get("title") or doc["doc_id"]}}],
    }
    if doc.get("type"):
        r["type"] = _cc(doc["type"])
    if _date(doc.get("date")) and len(doc["date"]) == 10:
        r["date"] = f"{doc['date']}T00:00:00Z"
    return r


def binary(artifact: str, stage: str, content: Any) -> dict:
    import json
    return {"resourceType": "Binary", "id": fhir_id(f"artifact-{artifact}"), "meta": stage_tag(stage),
            "contentType": "application/json", "data": _b64(json.dumps(content, ensure_ascii=False))}


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

def _code(fact: dict, concept_label: Optional[str], extra: list[dict]) -> dict:
    codings = list(extra)
    if fact.get("concept_id"):
        codings.append({"system": CONCEPT_SYSTEM, "code": fact["concept_id"], **({"display": concept_label} if concept_label else {})})
    return _cc(fact.get("en") or fact.get("span") or fact["fact_id"], codings)


def _body_site(fact: dict) -> Optional[dict]:
    hit = _LATERALITY.get(fact.get("laterality") or "")
    return {"coding": [{"system": SNOMED, "code": hit[0], "display": hit[1]}], "text": hit[1].lower()} if hit else None


def _value(fact: dict) -> dict:
    """valueQuantity for a number, components for blood pressure, else valueString."""
    fields = fact.get("fields") or {}
    raw = str(fields.get("value", "")).strip()
    unit = str(fields.get("unit", "") or "")
    if "/" in raw:
        parts = [p.strip() for p in raw.split("/")]
        if len(parts) == 2 and all(_NUMBER.match(p) for p in parts):
            return {"component": [
                {"code": _cc(LOINC_SYSTOLIC[1], [{"system": LOINC, "code": LOINC_SYSTOLIC[0], "display": LOINC_SYSTOLIC[1]}]),
                 "valueQuantity": {"value": float(parts[0].replace(",", ".")), "unit": unit or "mmHg", "system": UCUM, "code": "mm[Hg]"}},
                {"code": _cc(LOINC_DIASTOLIC[1], [{"system": LOINC, "code": LOINC_DIASTOLIC[0], "display": LOINC_DIASTOLIC[1]}]),
                 "valueQuantity": {"value": float(parts[1].replace(",", ".")), "unit": unit or "mmHg", "system": UCUM, "code": "mm[Hg]"}},
            ]}
    if _NUMBER.match(raw):
        q: dict[str, Any] = {"value": float(raw.replace(",", "."))}
        if unit:
            q["unit"] = unit
        return {"valueQuantity": q}
    return {"valueString": fact.get("en") or fact.get("span") or ""}


def fact_resource(fact: dict, patient_ref: dict, concept_label: Optional[str], codes: list[dict]) -> dict:
    ftype, status, date = fact["type"], fact.get("status", "unknown"), _date(fact.get("date"))
    rtype = RESOURCE_FOR_TYPE.get(ftype, "Observation")
    extra = list(codes) + (loinc_coding(fact.get("concept_id")) if ftype == "measurement" else [])
    r: dict[str, Any] = {"resourceType": rtype, "id": fhir_id(fact["fact_id"]), "meta": stage_tag("s1"),
                         "extension": [json_extension(ORIGINAL, fact)]}
    code = _code(fact, concept_label, extra)
    site = _body_site(fact)

    if rtype == "Condition":
        r.update(subject=patient_ref, code=code)
        clinical, verification = {
            "positive": ("active", "confirmed"), "suspected": ("active", "provisional"), "negative": (None, "refuted"),
            "history": ("inactive", "confirmed"),
        }.get(status, (None, "unconfirmed"))
        if clinical:
            r["clinicalStatus"] = _status_cc("condition-clinical", clinical)
        r["verificationStatus"] = _status_cc("condition-ver-status", verification)
        if date:
            r["recordedDate"] = date
        if site:
            r["bodySite"] = [site]
    elif rtype == "Observation":
        category = {"measurement": "vital-signs", "finding": "exam", "demographic": "social-history"}.get(ftype, "exam")
        r.update(status="final", subject=patient_ref, code=code,
                 category=[_status_cc("observation-category", category)])
        if date:
            r["effectiveDateTime"] = date
        if site:
            r["bodySite"] = site
        if status == "negative":
            r["interpretation"] = [_status_cc("v3-ObservationInterpretation", "NEG")]
        r.update(_value(fact) if ftype == "measurement" else {"valueString": fact.get("en") or fact.get("span") or ""})
    elif rtype == "MedicationStatement":
        r.update(subject=patient_ref, medicationCodeableConcept=code, status={
            "current": "active", "started": "active", "changed": "active", "discontinued": "stopped", "historical": "completed",
        }.get(status, "unknown"))
        if date:
            r["effectiveDateTime"] = date
        fields = fact.get("fields") or {}
        dosage = {k: v for k, v in {"text": " ".join(str(fields.get(k, "")) for k in ("strength", "schedule")).strip(),
                                    "route": _cc(fields["route"]) if fields.get("route") else None}.items() if v}
        if dosage:
            r["dosage"] = [dosage]
    elif rtype == "AllergyIntolerance":
        r.update(patient=patient_ref, code=code,
                 clinicalStatus=_status_cc("allergyintolerance-clinical", "active"),
                 verificationStatus=_status_cc("allergyintolerance-verification",
                                               "refuted" if status == "negative" else "confirmed" if status == "positive" else "unconfirmed"))
        if date:
            r["recordedDate"] = date
    elif rtype == "Procedure":
        r.update(subject=patient_ref, code=code,
                 status={"positive": "completed", "history": "completed", "negative": "not-done"}.get(status, "unknown"))
        if date:
            r["performedDateTime"] = date
        if site:
            r["bodySite"] = [site]
    elif rtype == "ServiceRequest":
        r.update(subject=patient_ref, code=code, intent="proposal" if ftype == "recommendation" else "plan",
                 status={"recommended": "active", "open": "active", "done": "completed"}.get(status, "unknown"))
        if date:
            r["occurrenceDateTime"] = date
    elif rtype == "Encounter":
        r.update(subject=patient_ref, type=[code], status="finished",
                 **{"class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "IMP", "display": "inpatient encounter"}})
        if date:
            r["period"] = {"start": date}
    return r


# Observation.derivedFrom may only point at these resource types
_DERIVED_FROM_OK = {"Observation", "DocumentReference"}


def derived_resource(df: dict, patient_ref: dict, type_of: dict[str, str]) -> dict:
    r: dict[str, Any] = {
        "resourceType": "Observation", "id": fhir_id(df["fact_id"]), "meta": stage_tag("s2"),
        "extension": [json_extension(ORIGINAL, df)], "status": "final", "subject": patient_ref,
        "code": _cc(df.get("formula") or df["fact_id"]), "valueString": df.get("en", ""),
    }
    if _date(df.get("date")):
        r["effectiveDateTime"] = df["date"]
    sources = [ref(i, type_of[i]) for i in df.get("inputs", []) if type_of.get(i) in _DERIVED_FROM_OK]
    if sources:
        r["derivedFrom"] = sources
    return r


# ---------------------------------------------------------------------------
# Concepts, contradictions
# ---------------------------------------------------------------------------

def concept_system(concepts: list[dict]) -> dict:
    return {
        "resourceType": "CodeSystem", "id": "concepts", "meta": stage_tag("s2"), "url": CONCEPT_SYSTEM,
        "name": "PatientConcepts", "status": "active", "content": "complete", "caseSensitive": True,
        "concept": [{"code": c["concept_id"], "display": c.get("label_en") or c["concept_id"],
                     "extension": [json_extension(ORIGINAL, c)]} for c in concepts],
    }


def detected_issue(cx: Optional[dict], report: Optional[dict], patient_ref: dict, implicated: list[dict]) -> dict:
    """A contradiction: final when shown, preliminary when the finder held it back."""
    base = cx or (report or {}).get("entry") or {}
    # A held-back finding may have no contradiction ID, only the finder's key
    issue_id = base.get("contradiction_id") or f"X:report:{base.get('key') or (report or {}).get('index')}"
    r: dict[str, Any] = {
        "resourceType": "DetectedIssue", "id": fhir_id(issue_id), "meta": stage_tag("s3"),
        "extension": [json_extension(ORIGINAL, cx)] if cx else [],
        "status": "final" if cx else "preliminary", "patient": patient_ref,
        "code": _cc(base.get("kind", "contradiction")), "detail": base.get("summary_en", ""),
    }
    if report:
        r["extension"].append(json_extension(REPORT, report))
    if implicated:
        r["implicated"] = implicated
    return r
