"""
preprocess/import_fhir.py – import a patient from a FHIR R4 Bundle (e.g. Synthea / MITRE
Coherent) into the cache, with DICOM images.

    python -m preprocess.import_fhir --folder ../data/coherent/Wiley422_Hermiston71
    python -m preprocess.import_fhir --all ../data/coherent

The bundle's resources are coded already, so they become facts directly (no LLM S1):
Condition, MedicationRequest, Observation, Procedure, Immunization, AllergyIntolerance,
CarePlan and ImagingStudy. Each fact cites a readable line in a "FHIR record" document, so
every value on a screen has a source like a letter passage. The newest clinical notes
(DocumentReference text) become documents too. Then the usual stages run: S2 (concepts,
current medication, weight change, BMI), S3 (contradiction rules), S4, FHIR bundle.

Writes data/patient-<id>/patient.json (so the patient search lists the patient) and
cache/<id>/…; DICOM previews go to cache/<id>/images/ (see app/images.py).
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("preprocess.import_fhir")

MAX_NOTES = 40   # newest clinical notes kept as documents (Synthea notes repeat a template)
SKIP_OBSERVATION_CATEGORIES = {"survey"}   # questionnaires (PHQ, GAD, …) add noise, not facts
SNOMED_SYSTEM, RXNORM_SYSTEM, LOINC_SYSTEM = "http://snomed.info/sct", "http://www.nlm.nih.gov/research/umls/rxnorm", "http://loinc.org"

# LOINC → our measurement concepts, so derived facts (BMI, weight change) and the LOINC table apply
LOINC_CONCEPT = {
    "29463-7": "C:meas:body_weight", "8302-2": "C:meas:body_height", "39156-5": "C:meas:bmi",
    "85354-9": "C:meas:blood_pressure", "55284-4": "C:meas:blood_pressure", "8867-4": "C:meas:heart_rate",
    "8310-5": "C:meas:temperature", "9279-1": "C:meas:respiratory_rate", "4548-4": "C:meas:hba1c", "10230-1": "C:meas:lvef",
}
TYPE_ABBR = {"diagnosis": "dia", "finding": "fin", "medication": "med", "measurement": "mea", "procedure": "pro",
             "allergy": "all", "plan": "pla", "event": "eve"}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60] or "x"


def _date(r: dict, *keys: str) -> Optional[str]:
    for k in keys:
        v = r.get(k)
        if isinstance(v, dict):
            v = v.get("start")
        if isinstance(v, str) and len(v) >= 10:
            return v[:10]
    return None


def _coding(cc: Optional[dict]) -> tuple[str, list[dict]]:
    cc = cc or {}
    codings = [{k: c[k] for k in ("system", "code", "display") if k in c} for c in cc.get("coding", [])]
    text = cc.get("text") or (codings[0].get("display") if codings else "") or ""
    return text, codings


def _status(cc: Optional[dict]) -> str:
    return ((cc or {}).get("coding") or [{}])[0].get("code", "")


def _clean_display(text: str) -> tuple[str, str]:
    """'Hypertension (disorder)' → ('Hypertension', 'disorder')."""
    m = re.match(r"^(.*?)\s*\((disorder|finding|situation|procedure|observable entity|regime/therapy|person)\)$", text)
    return (m.group(1), m.group(2)) if m else (text, "")


class Builder:
    """Collects documents, passages and facts for one patient."""

    def __init__(self, pid: str) -> None:
        self.pid = pid
        self.documents: list[dict] = []
        self.passages: list[dict] = []
        self.facts: list[dict] = []
        self.sections: dict[str, list[tuple[str, Optional[dict]]]] = defaultdict(list)   # section → (line, fact)

    def add(self, section: str, line: str, fact: Optional[dict] = None) -> None:
        self.sections[section].append((line, fact))

    def record_documents(self) -> None:
        """One 'FHIR record' document per section; each line is a passage that its fact cites."""
        for section, items in self.sections.items():
            doc_id = f"{self.pid}-{slug(section)}"
            text, counts = "", defaultdict(int)
            dates = [f["date"] for _l, f in items if f and f.get("date")]
            for n, (line, fact) in enumerate(items):
                start = len(text)
                text += line + "\n"
                pid = f"{doc_id}:p{n:03d}"
                self.passages.append({"passage_id": pid, "doc_id": doc_id, "section": section, "kind": "list_item",
                                      "start": start, "end": start + len(line), "text": line, "copy_of": None})
                if fact:
                    abbr = TYPE_ABBR[fact["type"]]
                    counts[abbr] += 1
                    self.facts.append({**fact, "fact_id": f"F:{doc_id}:{abbr}:{counts[abbr]:03d}", "passage_id": pid, "span": line})
            self.documents.append({"doc_id": doc_id, "title": f"FHIR record – {section}", "date": max(dates) if dates else None,
                                   "date_source": "fhir", "date_meta": None, "type": "FHIR resources",
                                   "department": None, "text": text.rstrip("\n")})

    def note_document(self, r: dict, k: int) -> None:
        data = (r.get("content") or [{}])[0].get("attachment", {}).get("data")
        if not data:
            return
        text = base64.b64decode(data).decode("utf-8", "replace").strip()
        date = (r.get("date") or "")[:10] or None
        doc_id = f"{self.pid}-note-{k:03d}"
        title, _ = _coding(r.get("type"))
        self.documents.append({"doc_id": doc_id, "title": f"{title or 'Clinical note'} ({date})", "date": date,
                               "date_source": "fhir", "date_meta": date, "type": "Clinical note", "department": None, "text": text})
        section, n = None, 0
        for m in re.finditer(r"[^\n]+", text):
            line = m.group(0).strip()
            if not line:
                continue
            if line.startswith("#"):
                section = line.lstrip("# ").strip()
                kind = "header"
            else:
                kind = "list_item" if line.startswith("- ") else "sentence"
            start = m.start() + (len(m.group(0)) - len(m.group(0).lstrip()))
            self.passages.append({"passage_id": f"{doc_id}:p{n:03d}", "doc_id": doc_id, "section": section, "kind": kind,
                                  "start": start, "end": start + len(line), "text": line, "copy_of": None})
            n += 1


def _fact(ftype: str, concept: str, en: str, status: str, date: Optional[str], codings: list[dict], **fields: Any) -> dict:
    return {"type": ftype, "fields": {**{k: v for k, v in fields.items() if v not in (None, "")}, "codings": codings},
            "status": status, "laterality": None, "date": date, "date_precision": "day" if date else "unknown",
            "date_basis": "text_date" if date else "unknown", "concept_id": concept, "en": en, "de": ""}


def _display_name(patient: dict) -> str:
    name = (patient.get("name") or [{}])[0]
    full = " ".join(name.get("given", []) + [name.get("family", "")]).strip()
    return re.sub(r"\d+", "", full).strip() or full


def import_bundle(bundle: dict, pid: str, dicom_dir: Optional[Path]) -> tuple[Builder, dict, list[dict]]:
    resources = [e["resource"] for e in bundle.get("entry", [])]
    patient = next(r for r in resources if r["resourceType"] == "Patient")
    b = Builder(pid)
    images: list[dict] = []

    for r in resources:
        rt = r["resourceType"]
        if rt == "Condition":
            text, codings = _coding(r.get("code"))
            name, tag = _clean_display(text)
            ftype = "finding" if tag in ("finding", "situation") else "diagnosis"
            clinical, verification = _status(r.get("clinicalStatus")), _status(r.get("verificationStatus"))
            status = ("negative" if verification == "refuted" else "suspected" if verification in ("provisional", "differential")
                      else "history" if clinical in ("resolved", "inactive", "remission") else "positive")
            onset, end = _date(r, "onsetDateTime", "recordedDate"), _date(r, "abatementDateTime")
            en = name + (f" (resolved {end})" if end else "")
            b.add("Conditions", f"{name} – {status}, onset {onset or '?'}" + (f", resolved {end}" if end else ""),
                  _fact(ftype, f"C:{'dx' if ftype == 'diagnosis' else 'finding'}:{slug(name)}", en, status, onset, codings,
                        snomed_tag=tag, onset=onset, abatement=end))
        elif rt == "MedicationRequest":
            text, codings = _coding(r.get("medicationCodeableConcept"))
            if not text and r.get("medicationReference"):
                med = next((x for x in resources if x["resourceType"] == "Medication"
                            and r["medicationReference"]["reference"].endswith(x.get("id", "?"))), None)
                text, codings = _coding((med or {}).get("code"))
            status = {"active": "current", "on-hold": "current", "stopped": "discontinued", "completed": "historical",
                      "cancelled": "discontinued"}.get(r.get("status", ""), "historical")
            m = re.match(r"^(.*?)\s+(\d[\d.,]*\s*(?:MG|MCG|UNT|ML|MG/ML|%)[^ ]*)\s*(.*)$", text, re.I)
            generic, strength, form = (m.group(1), m.group(2), m.group(3)) if m else (text, "", "")
            timing = ((r.get("dosageInstruction") or [{}])[0].get("timing") or {}).get("repeat") or {}
            schedule = f"{timing['frequency']}x/{timing.get('period', 1):g}{timing.get('periodUnit', '')}" if timing.get("frequency") else ""
            date = _date(r, "authoredOn")
            b.add("Medications", f"{text} – {r.get('status', '')}, {date}" + (f", {schedule}" if schedule else ""),
                  _fact("medication", f"C:med:{slug(generic)}", f"{text}" + (f", {schedule}" if schedule else ""), status, date,
                        codings, generic=generic.lower(), strength=strength, schedule=schedule,
                        route="oral" if "oral" in form.lower() else "", form=form))
        elif rt == "Observation":
            cats = {c.get("code") for cat in r.get("category", []) for c in cat.get("coding", [])}
            if cats & SKIP_OBSERVATION_CATEGORIES:
                continue
            text, codings = _coding(r.get("code"))
            loinc = next((c["code"] for c in codings if c.get("system") == LOINC_SYSTEM), "")
            date = _date(r, "effectiveDateTime", "issued")
            value, unit = "", ""
            if "valueQuantity" in r:
                value, unit = f"{r['valueQuantity'].get('value'):g}", r["valueQuantity"].get("unit", "")
            elif "valueCodeableConcept" in r:
                value = _coding(r["valueCodeableConcept"])[0]
            elif r.get("component"):
                parts = {_coding(c.get("code"))[0].lower(): c.get("valueQuantity", {}) for c in r["component"]}
                sys_ = next((q for k, q in parts.items() if "systolic" in k), None)
                dia = next((q for k, q in parts.items() if "diastolic" in k), None)
                if sys_ and dia:
                    value, unit = f"{sys_.get('value'):g}/{dia.get('value'):g}", sys_.get("unit", "mm[Hg]")
            if value == "":
                continue
            unit = {"mm[Hg]": "mmHg", "kg/m2": "kg/m²", "{score}": "", "/min": "/min"}.get(unit, unit)
            concept = LOINC_CONCEPT.get(loinc) or f"C:meas:{slug(text)}"
            ftype = "measurement" if "valueQuantity" in r or r.get("component") else "finding"
            en = f"{text}: {value} {unit}".strip()
            b.add("Observations", f"{en} ({date})",
                  _fact(ftype, concept if ftype == "measurement" else f"C:finding:{slug(text)}", en, "positive", date, codings,
                        name=text, value=value, unit=unit, category=",".join(sorted(cats))))
        elif rt in ("Procedure", "Immunization"):
            text, codings = _coding(r.get("code") or r.get("vaccineCode"))
            name, _tag = _clean_display(text)
            date = _date(r, "performedDateTime", "performedPeriod", "occurrenceDateTime")
            en = f"Vaccination: {name}" if rt == "Immunization" else name
            status = {"completed": "positive", "not-done": "negative"}.get(r.get("status", ""), "positive")
            b.add("Procedures" if rt == "Procedure" else "Immunizations", f"{en} – {date}",
                  _fact("procedure", f"C:proc:{slug(name)}", en, status, date, codings))
        elif rt == "AllergyIntolerance":
            text, codings = _coding(r.get("code"))
            name, _tag = _clean_display(text)
            reactions = sorted({_clean_display(_coding(m)[0])[0] for x in r.get("reaction", []) for m in x.get("manifestation", [])})
            en = name + (f" ({', '.join(reactions)})" if reactions else "") + (" allergy" if "allerg" not in name.lower() else "")
            date = _date(r, "recordedDate", "onsetDateTime")
            b.add("Allergies", f"{en} – {r.get('criticality', '')} criticality, recorded {date}",
                  _fact("allergy", f"C:allergy:{slug(name)}", en, "positive", date, codings, criticality=r.get("criticality")))
        elif rt == "CarePlan":
            cats = [_clean_display(_coding(c)[0])[0] for c in r.get("category", []) if _coding(c)[0]]
            acts = [_clean_display(_coding((a.get("detail") or {}).get("code"))[0])[0] for a in r.get("activity", [])]
            name = next((c for c in cats if c and "plan" in c.lower()), cats[-1] if cats else "Care plan")
            date = _date(r, "period")
            en = name + (f": {', '.join(a for a in acts if a)}" if acts else "")
            b.add("Care plans", f"{en} – {r.get('status', '')}, from {date}",
                  _fact("plan", f"C:plan:{slug(name)}", en, "open" if r.get("status") == "active" else "done", date, []))
        elif rt == "ImagingStudy":
            series = (r.get("series") or [{}])[0]
            modality = (series.get("modality") or {}).get("code", "")
            site = re.sub(r"\s*\((?:body )?structure\)", "", (series.get("bodySite") or {}).get("display", "")).strip()
            site = re.sub(r"\s+structure$", "", site, flags=re.I)   # "Head structure (body structure)" → "Head"
            date = _date(r, "started")
            uid = next((i["value"].removeprefix("urn:oid:") for i in r.get("identifier", [])), r.get("id"))
            dicom = next(iter(sorted(dicom_dir.glob(f"*{uid}*.dcm"))), None) if dicom_dir and dicom_dir.exists() else None
            modality_name = {"MR": "MRI", "CT": "CT", "DX": "X-ray", "CR": "X-ray", "US": "Ultrasound"}.get(modality, modality)
            en = f"{modality_name} of the {site.lower() or 'body'}" + (" – image available" if dicom else "")
            b.add("Imaging", f"{en} ({date}), study {uid}",
                  _fact("procedure", f"C:proc:{slug(modality_name + ' ' + (site or 'imaging'))}", en, "positive", date, [],
                        modality=modality, body_site=site, image_id=uid if dicom else None,
                        instances=r.get("numberOfInstances")))
            if dicom:
                images.append({"image_id": uid, "dicom": str(dicom), "modality": modality, "body_site": site, "date": date})

    born, dead = patient.get("birthDate"), (patient.get("deceasedDateTime") or "")[:10]
    b.add("Patient", f"Patient: {_display_name(patient)}, {patient.get('gender', 'unknown')}, born {born}"
                     + (f", died {dead}" if dead else ""))
    b.record_documents()
    notes = sorted((r for r in resources if r["resourceType"] == "DocumentReference"), key=lambda r: r.get("date", ""))[-MAX_NOTES:]
    for k, r in enumerate(notes):
        b.note_document(r, k)

    info = {"id": pid, "name": _display_name(patient), "sex": patient.get("gender"),
            "dateOfBirth": patient.get("birthDate"), "deceased": (patient.get("deceasedDateTime") or "")[:10] or None,
            "source": "fhir", "fhir_patient_id": patient.get("id")}
    return b, info, images


def run(folder: Path, pid: Optional[str] = None, max_notes: int = MAX_NOTES) -> str:
    global MAX_NOTES
    MAX_NOTES = max_notes
    folder = folder.resolve()
    bundle = json.loads((folder / "fhir.json").read_text(encoding="utf-8"))
    patient = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient")
    pid = pid or f"coh-{patient['id'][:8]}"
    b, info, images = import_bundle(bundle, pid, folder / "dicom")
    from app import images as image_store
    info["source_folder"] = image_store.relative_to_data(folder)   # relative to DATA_DIR: works on every machine

    data_dir = settings.DATA_DIR / f"patient-{pid}"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "patient.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    out = settings.CACHE_DIR / pid
    out.mkdir(parents=True, exist_ok=True)
    for name, content in (("documents", b.documents), ("passages", b.passages), ("facts.raw", b.facts)):
        (out / f"{name}.json").write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    reports = defaultdict(lambda: {"total_passages": 0, "facts": 0})
    for p in b.passages:
        reports[p["doc_id"]]["total_passages"] += 1
    (out / "s1_report.json").write_text(json.dumps([
        {"doc_id": d, "total_passages": r["total_passages"], "flagged_no_content": 0,
         "facts_extracted": sum(1 for f in b.facts if f["passage_id"].startswith(d + ":")), "facts_rejected": 0,
         "uncovered_passages": []} for d, r in reports.items()], indent=2), encoding="utf-8")

    previews = [image_store.render_preview(Path(i["dicom"]), out / "images", i["image_id"]) for i in images]
    images = [{**i, "dicom": image_store.relative_to_data(Path(i["dicom"]))} for i in images]
    # FHIR says what the study is (MR, head); the DICOM header only adds the volume size
    (out / "images.json").write_text(json.dumps([{**p, **i, "dicom_modality": p.get("modality")} for i, p in zip(images, previews)], indent=2), encoding="utf-8")
    logger.info("%s (%s): %d documents, %d passages, %d facts, %d image(s)", pid, info["name"], len(b.documents),
                len(b.passages), len(b.facts), len(images))

    from preprocess.run import run_pipeline
    run_pipeline(pid, "s2", None)
    return pid


def main() -> None:
    ap = argparse.ArgumentParser(description="Import patients from FHIR bundles (with DICOM images)")
    ap.add_argument("--folder", type=Path, help="folder with fhir.json and dicom/")
    ap.add_argument("--all", type=Path, help="import every subfolder of this folder")
    ap.add_argument("--patient-id", default=None)
    ap.add_argument("--notes", type=int, default=MAX_NOTES, help="newest clinical notes to keep as documents")
    args = ap.parse_args()
    folders = sorted(p for p in args.all.iterdir() if (p / "fhir.json").exists()) if args.all else [args.folder]
    done = [run(f, args.patient_id if len(folders) == 1 else None, args.notes) for f in folders]
    print("imported:", ", ".join(done))


if __name__ == "__main__":
    main()
