"""
app/fhir/cache.py – the patient cache as one FHIR R4 Bundle, and back.

`Cache.data` holds the same JSON artifacts the pipeline stages write (documents, passages,
facts, …) as plain lists / dicts, so stages and FactStore keep their code. `save` maps them
to resources and validates the Bundle with the R4B models; `load` reads the resources'
original-values extensions back, so `load(save(cache)) == cache`.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.fhir import mapping
from app.fhir.codes import ibm_codes
from app.fhir.extensions import ATC, ICD10, LOINC, RXNORM, SNOMED, ORIGINAL, PASSAGES, REPORT, read_json_extension, stage_of, stage_tag
from app.fhir.ids import fhir_id

logger = logging.getLogger(__name__)

# artifact → stage that writes it (file name = artifact + ".json")
ARTIFACTS: dict[str, str] = {
    "documents": "s0", "passages": "s0",
    "facts.raw": "s1", "s1_report": "s1", "s1_aliases": "s1",
    "facts": "s2", "concepts": "s2", "derived": "s2",
    "contradictions": "s3", "contradictions_report": "s3",
    "manifest": "s4",
}
_BINARY = ("facts.raw", "s1_report", "s1_aliases", "manifest")
# Binary resource id → artifact name ("s1_report" is stored as "artifact-s1-report")
_BINARY_NAMES = {fhir_id(f"artifact-{n}"): n for n in (*_BINARY, "empty-artifacts")}
STAGES = ["s0", "s1", "s2", "s3", "s4"]
STANDARD_SYSTEMS = (ICD10, ATC, LOINC, SNOMED, RXNORM)


@dataclass
class Cache:
    patient_id: str
    data: dict[str, Any] = field(default_factory=dict)   # artifact → parsed JSON
    patient: Optional[dict] = None                        # data/<patient>/patient.json
    codes: dict[str, list[dict]] = field(default_factory=dict)  # fact_id → ICD-10 / ATC (from IBM); after load also LOINC

    def drop_from(self, stage: str) -> None:
        """Forget what `stage` and later stages produced (for --from)."""
        later = set(STAGES[STAGES.index(stage):])
        for name, st in ARTIFACTS.items():
            if st in later:
                self.data.pop(name, None)


# ---------------------------------------------------------------------------
# JSON directory (today's cache layout)
# ---------------------------------------------------------------------------

def from_json_dir(out_dir: Path, patient_id: str, data_dir: Optional[Path] = None) -> Cache:
    cache = Cache(patient_id=patient_id)
    for name in ARTIFACTS:
        path = out_dir / f"{name}.json"
        if path.exists():
            cache.data[name] = json.loads(path.read_text(encoding="utf-8"))
    if data_dir is not None:
        folder = next((data_dir / n for n in (patient_id, f"patient-{patient_id}") if (data_dir / n).exists()), None)
        if folder and (folder / "patient.json").exists():
            cache.patient = json.loads((folder / "patient.json").read_text(encoding="utf-8"))
        if "facts" in cache.data and "passages" in cache.data:
            cache.codes, stats = ibm_codes(data_dir, patient_id, cache.data["facts"], cache.data["passages"])
            logger.info("FHIR codes from IBM: %s", stats)
    for f in cache.data.get("facts", []):   # imported FHIR facts carry their own codes
        own = source_codings(f)
        if own:
            cache.codes[f["fact_id"]] = cache.codes.get(f["fact_id"], []) + own
    return cache


def source_codings(fact: dict) -> list[dict]:
    """Standard codings a fact brought from its source (fields.codings of an imported FHIR resource).
    System and code only: Synthea's display texts are not the code systems' own (the HL7
    validator rejects them); the fact's text keeps the name."""
    return [{k: c[k] for k in ("system", "code") if k in c}
            for c in (fact.get("fields") or {}).get("codings", []) if c.get("system") in STANDARD_SYSTEMS]


def to_json_dir(cache: Cache, out_dir: Path) -> list[str]:
    """Write the artifacts back as today's JSON files (for stages restarted with --from)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in cache.data.items():
        (out_dir / f"{name}.json").write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(name)
    return written


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

def to_bundle(cache: Cache) -> dict:
    d = cache.data
    pref = {"reference": f"Patient/{fhir_id(cache.patient_id)}"}
    resources: list[dict] = [mapping.patient(cache.patient_id, cache.patient)]

    passages_by_doc: dict[str, list[dict]] = {}
    for p in d.get("passages", []):
        passages_by_doc.setdefault(p["doc_id"], []).append(p)
    docs = d.get("documents", [])
    resources += [mapping.document_reference(doc, passages_by_doc.pop(doc["doc_id"], []), pref) for doc in docs]
    if passages_by_doc:   # passages of a document that is not in documents.json – keep them anyway
        logger.warning("FHIR: passages without a document: %s", list(passages_by_doc)[:5])

    labels = {c["concept_id"]: c.get("label_en") for c in d.get("concepts", [])}
    type_of: dict[str, str] = {}
    for f in d.get("facts", []):
        r = mapping.fact_resource(f, pref, labels.get(f.get("concept_id")), cache.codes.get(f["fact_id"], []))
        r["meta"] = stage_tag("s2")    # facts.json is the merged S2 output; raw S1 facts are a Binary
        type_of[f["fact_id"]] = r["resourceType"]
        resources.append(r)
    for df in d.get("derived", []):
        type_of[df["fact_id"]] = "Observation"
    resources += [mapping.derived_resource(df, pref, type_of) for df in d.get("derived", [])]
    if "concepts" in d:
        resources.append(mapping.concept_system(d["concepts"]))

    # Contradictions: shown ones final, held-back report findings preliminary
    fact_by_passage: dict[str, list[str]] = {}
    for f in d.get("facts", []):
        fact_by_passage.setdefault(f["passage_id"], []).append(f["fact_id"])
    report = {r.get("contradiction_id") or f"report:{i}": (i, r) for i, r in enumerate(d.get("contradictions_report", []))}

    def implicated(item: dict) -> list[dict]:
        refs = [mapping.ref(fid, type_of[fid]) for pid in item.get("passage_ids", []) for fid in fact_by_passage.get(pid, [])]
        docs_hit = dict.fromkeys(pid.split(":")[0] for pid in item.get("passage_ids", []))
        return refs + [mapping.ref(doc_id, "DocumentReference") for doc_id in docs_hit]

    seen = set()
    for cx in d.get("contradictions", []):
        i_r = report.get(cx["contradiction_id"])
        resources.append(mapping.detected_issue(cx, {"index": i_r[0], "entry": i_r[1]} if i_r else None, pref, implicated(cx)))
        seen.add(cx["contradiction_id"])
    for cid, (i, r) in report.items():
        if cid not in seen:
            resources.append(mapping.detected_issue(None, {"index": i, "entry": r}, pref, implicated(r)))

    for name in _BINARY:
        if name in d:
            resources.append(mapping.binary(name, ARTIFACTS[name], d[name]))
    # An artifact that exists but is empty ("ran, found nothing") has no resources; record it
    empty = {name: content for name, content in d.items() if name not in _BINARY and not content}
    if empty:
        resources.append(mapping.binary("empty-artifacts", "s0", empty))

    resources = [mapping.with_narrative(r) for r in resources]
    ids = [f"{r['resourceType']}/{r['id']}" for r in resources]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"FHIR: duplicate resource ids {sorted(dupes)[:5]}")
    manifest = d.get("manifest") or {}
    bundle = {
        "resourceType": "Bundle", "id": fhir_id(f"patient-{cache.patient_id}"), "type": "collection",
        "meta": {"lastUpdated": manifest.get("completed_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        "entry": [{"fullUrl": f"{mapping_base()}/{rid}", "resource": r} for rid, r in zip(ids, resources)],
    }
    return bundle


def mapping_base() -> str:
    from app.fhir.extensions import BASE
    return BASE


def validate(bundle: dict) -> None:
    """R4 structure check of every resource (fhir.resources R4B models)."""
    from fhir.resources.R4B.bundle import Bundle
    Bundle.model_validate(bundle)


def save(cache: Cache, path: Path, check: bool = True) -> dict:
    bundle = to_bundle(cache)
    if check:
        t = time.perf_counter()
        validate(bundle)
        logger.info("FHIR: %d resources valid (R4B) in %.2f s", len(bundle["entry"]), time.perf_counter() - t)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    return bundle


def _attachment_text(r: dict) -> str:
    data = r["content"][0]["attachment"].get("data", "")
    return base64.b64decode(data).decode("utf-8") if data else ""


def load(path: Path) -> Cache:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    d: dict[str, Any] = {}
    cache = Cache(patient_id="", data=d)
    facts, derived, cxs, reports, docs, passages = [], [], [], [], [], []
    for entry in bundle.get("entry", []):
        r = entry["resource"]
        rt = r["resourceType"]
        if rt == "Patient":
            cache.patient_id = next((i["value"] for i in r.get("identifier", []) if i.get("system") == "urn:ibm-aim:patient"), r["id"])
        elif rt == "DocumentReference":
            docs.append({**read_json_extension(r, ORIGINAL), "text": _attachment_text(r)})
            passages += read_json_extension(r, PASSAGES) or []
        elif rt == "CodeSystem" and r.get("id") == "concepts":
            d["concepts"] = [read_json_extension(c, ORIGINAL) for c in r.get("concept", [])]
        elif rt == "DetectedIssue":
            cx, rep = read_json_extension(r, ORIGINAL), read_json_extension(r, REPORT)
            if cx is not None:
                cxs.append(cx)
            if rep is not None:
                reports.append(rep)
        elif rt == "Binary":
            name = _BINARY_NAMES.get(r["id"], r["id"])
            content = json.loads(base64.b64decode(r["data"]).decode("utf-8"))
            if name == "empty-artifacts":
                for empty_name, empty_value in content.items():
                    d.setdefault(empty_name, empty_value)
            elif name in ARTIFACTS:
                d[name] = content
        else:
            original = read_json_extension(r, ORIGINAL)
            if original is None:
                continue
            is_derived = stage_of(r) == "s2" and original.get("type") == "derived"
            (derived if is_derived else facts).append(original)
            if not is_derived:
                code = r.get("code") or r.get("medicationCodeableConcept") or (r.get("type") or [{}])[0]
                standard = [c for c in code.get("coding", []) if c.get("system") in STANDARD_SYSTEMS]
                if standard:
                    cache.codes[original["fact_id"]] = standard
    if docs:
        d["documents"], d["passages"] = docs, passages
    if facts:
        d["facts"] = facts
    if derived:
        d["derived"] = derived
    if cxs:
        d["contradictions"] = cxs
    if reports:
        d["contradictions_report"] = [x["entry"] for x in sorted(reports, key=lambda x: x["index"])]
    return cache
