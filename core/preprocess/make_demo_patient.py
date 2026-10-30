"""
preprocess/make_demo_patient.py – a demo patient with a brain MRI, made from the challenge patient.

The challenge patient (0000-0001) has no imaging files, and her letters mention no MRI, so she
stays as she is (the jury tests with her). This makes a separate synthetic patient for the
demo: her record copied with another name, ID and date of birth, plus one added radiology
report (cranial MRI, June 2015) whose image is a DICOM volume from the Coherent data set.

Everything is replaced with strings of the same length, so passage offsets, sources and
contradictions of the copy stay valid. No LLM call: the copied cache is reused, the MRI
report's facts are written here.

    python -m preprocess.make_demo_patient [--dicom ../data/coherent/<patient>/dicom/<file>.dcm]

Writes $DATA_DIR/patient-0000-0101/ and cache/0000-0101/ (both gitignored).
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from app.config import settings

SOURCE = "0000-0001"
TARGET = "0000-0101"
NAME = "Eva Huber"
BIRTH = "1962-09-22"
MRI_DOC = "12340020"
MRI_DATE = "2015-06-15"
IMAGE_ID = f"mri-{TARGET}-{MRI_DATE}"

# Same length on both sides: every offset into the texts stays right
REPLACE = [
    (SOURCE, TARGET),
    ("15.03.1959", "22.09.1962"), ("15/03/1959", "22/09/1962"), ("1959-03-15", "1962-09-22"),
    ("56-jährige", "52-jährige"), ("56-j\\u00e4hrige", "52-j\\u00e4hrige"), ("56-year-old", "52-year-old"),
]

MRI_TEXT = (
    "Institut für Radiologie<BR><BR>"
    f"Patient(in): NN_{TARGET} VN_{TARGET} | geb. 22.09.1962<BR><BR>"
    "MRT Schädel vom 15.06.2015<BR><BR>"
    "Fragestellung: Amaurosis fugax links, Schwindel. Ischämie? Blutung?<BR><BR>"
    "Technik: MRT des Neurocraniums nativ (bekannte KM-Allergie), T1, T2, FLAIR, DWI.<BR><BR>"
    "Befund:<BR>"
    "Kein Nachweis einer akuten Diffusionsstörung im Sinne einer frischen Ischämie. "
    "Kein Nachweis einer intrakraniellen Blutung. "
    "Einzelne unspezifische punktförmige Marklagerhyperintensitäten in FLAIR, vereinbar mit einer beginnenden Mikroangiopathie. "
    "Ventrikelsystem normal weit, Mittellinie nicht verlagert.<BR><BR>"
    "Beurteilung:<BR>"
    "Kein Nachweis einer frischen Ischämie oder Blutung. Beginnende zerebrale Mikroangiopathie."
)
MRI_META = {"id": MRI_DOC, "title": "Radiologischer Befund", "version": "1", "date": MRI_DATE, "type": "MRT",
            "group": "Befund", "division": "Radiologie", "hospital": "Beispielkrankenhaus",
            "department": "Institut für Radiologie"}
# (span in the report, type, concept, status, English text, extra fields)
MRI_FACTS = [
    ("MRT Schädel vom 15.06.2015", "procedure", "C:proc:cranial_mri", "positive",
     f"MRI of the head (without contrast) on {MRI_DATE} – image available",
     {"name": "MRT Schädel", "modality": "MR", "body_site": "Head", "image_id": IMAGE_ID}),
    ("Kein Nachweis einer frischen Ischämie oder Blutung.", "finding", "C:finding:brain:acute_ischemia", "negative",
     "No acute ischaemia or intracranial haemorrhage on cranial MRI", {}),
    ("Beginnende zerebrale Mikroangiopathie.", "diagnosis", "C:dx:cerebral_microangiopathy", "positive",
     "Early cerebral microangiopathy (nonspecific white matter hyperintensities on MRI)", {}),
]


def _replace(text: str) -> str:
    for a, b in REPLACE:
        assert len(a) == len(b), (a, b)
        text = text.replace(a, b)
    return text


def _copy_tree(src: Path, dst: Path, skip: set[str] = frozenset()) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    for f in src.rglob("*"):
        rel = f.relative_to(src)
        if f.is_dir() or rel.parts[0] in skip:
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if f.suffix in (".json", ".txt"):
            target.write_text(_replace(f.read_text(encoding="utf-8")), encoding="utf-8")
        else:
            shutil.copy2(f, target)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make(dicom: Path) -> None:
    from preprocess.s0_passages import _detect_sections, _normalize_text, _split_into_passages

    data_src, data_dst = settings.DATA_DIR / f"patient-{SOURCE}", settings.DATA_DIR / f"patient-{TARGET}"
    cache_src, cache_dst = settings.CACHE_DIR / SOURCE, settings.CACHE_DIR / TARGET
    _copy_tree(data_src, data_dst, skip={"dicom"})
    _copy_tree(cache_src, cache_dst, skip={"fhir.json", "offline_llm", "images", "images.json"})
    # Only the challenge's own letters: anything added to the source patient later is left out
    letters = {f.stem for f in (data_src / "documents").glob("*.json")}
    _save(cache_dst / "documents.json", [d for d in _load(cache_dst / "documents.json") if d["doc_id"] in letters])
    _save(cache_dst / "passages.json", [p for p in _load(cache_dst / "passages.json") if p["doc_id"] in letters])
    kept = [f for f in _load(cache_dst / "facts.json") if f["passage_id"].split(":")[0] in letters]
    _save(cache_dst / "facts.json", kept)
    kept_ids = {f["fact_id"] for f in kept}
    _save(cache_dst / "concepts.json", [{**c, "fact_ids": [i for i in c["fact_ids"] if i in kept_ids]}
                                        for c in _load(cache_dst / "concepts.json")
                                        if any(i in kept_ids for i in c["fact_ids"])])
    _save(cache_dst / "s1_report.json", [r for r in _load(cache_dst / "s1_report.json") if r["doc_id"] in letters])

    info = _load(data_dst / "patient.json")
    info.update({"id": TARGET, "name": NAME, "dateOfBirth": BIRTH, "age": 64, "source": "demo",
                 "note": f"Synthetic demo patient: the record of {SOURCE} with another name and date of birth, plus a cranial MRI report"})
    _save(data_dst / "patient.json", info)

    # The MRI report: original file, normalized document, passages, facts, concepts
    _save(data_dst / "documents" / f"{MRI_DOC}.json", {"metadata": MRI_META, "text": MRI_TEXT})
    norm = _normalize_text(MRI_TEXT)
    passages = [p.model_dump() for p in _split_into_passages(norm, MRI_DOC, _detect_sections(norm))]
    docs = [d for d in _load(cache_dst / "documents.json") if d["doc_id"] != MRI_DOC]
    docs.append({"doc_id": MRI_DOC, "title": MRI_META["title"], "date": MRI_DATE, "date_source": "body",
                 "date_meta": MRI_DATE, "type": "MRT", "department": MRI_META["department"], "text": norm})
    _save(cache_dst / "documents.json", sorted(docs, key=lambda d: d["doc_id"]))
    _save(cache_dst / "passages.json", [p for p in _load(cache_dst / "passages.json") if p["doc_id"] != MRI_DOC] + passages)

    facts = [f for f in _load(cache_dst / "facts.json") if not f["fact_id"].startswith(f"F:{MRI_DOC}:")]
    concepts = {c["concept_id"]: c for c in _load(cache_dst / "concepts.json")}
    abbr = {"procedure": "pro", "finding": "fin", "diagnosis": "dia"}
    new_facts = []
    for i, (span, ftype, concept, status, en, fields) in enumerate(MRI_FACTS, 1):
        passage = next(p for p in passages if span in p["text"])
        fact = {"fact_id": f"F:{MRI_DOC}:{abbr[ftype]}:{i:02d}", "type": ftype, "passage_id": passage["passage_id"],
                "span": span, "fields": fields, "status": status, "laterality": None, "date": MRI_DATE,
                "date_precision": "day", "date_basis": "text_date", "concept_id": concept, "en": en, "de": span}
        new_facts.append(fact)
        concepts.setdefault(concept, {"concept_id": concept, "label_en": en.split(" (")[0].split(" on ")[0],
                                      "type": ftype, "fact_ids": []})["fact_ids"].append(fact["fact_id"])
    facts += new_facts
    _save(cache_dst / "facts.json", facts)
    _save(cache_dst / "concepts.json", list(concepts.values()))
    report = [r for r in _load(cache_dst / "s1_report.json") if r["doc_id"] != MRI_DOC]
    report.append({"doc_id": MRI_DOC, "total_passages": len(passages), "flagged_no_content": 0,
                   "facts_extracted": len(new_facts), "facts_rejected": 0, "uncovered_passages": []})
    _save(cache_dst / "s1_report.json", report)

    # The image: the DICOM with this patient's name, ID and study date, and its preview
    import pydicom
    from app import images
    dst_dicom = data_dst / "dicom" / f"{IMAGE_ID}.dcm"
    dst_dicom.parent.mkdir(parents=True, exist_ok=True)
    ds = pydicom.dcmread(dicom)
    ds.PatientName, ds.PatientID = "Huber^Eva", TARGET
    ds.PatientBirthDate, ds.StudyDate, ds.StudyDescription = BIRTH.replace("-", ""), MRI_DATE.replace("-", ""), "MRT Schaedel nativ"
    ds.save_as(dst_dicom)
    preview = images.render_preview(dst_dicom, cache_dst / "images", IMAGE_ID)
    _save(cache_dst / "images.json", [{**preview, "image_id": IMAGE_ID, "dicom": images.relative_to_data(dst_dicom),
                                       "modality": "MR", "body_site": "Head", "date": MRI_DATE, "dicom_modality": "MR"}])

    manifest = _load(cache_dst / "manifest.json")
    manifest.update({"patient_id": TARGET, "document_count": len(docs), "passage_count": len(_load(cache_dst / "passages.json")),
                     "fact_count": len(facts)})
    _save(cache_dst / "manifest.json", manifest)

    from app.schemas import DerivedFact, Fact
    from preprocess.s4_export import build_fact_sheet
    derived = [DerivedFact.model_validate(d) for d in _load(cache_dst / "derived.json")]
    (cache_dst / "fact_sheet.txt").write_text(
        build_fact_sheet(TARGET, [Fact.model_validate(f) for f in facts], derived), encoding="utf-8")
    from preprocess import convert_cache
    convert_cache.to_fhir(TARGET)
    print(f"{TARGET} ({NAME}, born {BIRTH}): {len(docs)} documents, {len(facts)} facts, MRI {IMAGE_ID}")


def main() -> None:
    default = next(iter(sorted((settings.DATA_DIR / "coherent").glob("*/dicom/*.dcm"))), None)
    ap = argparse.ArgumentParser(description="Demo patient with a brain MRI, made from the challenge patient")
    ap.add_argument("--dicom", type=Path, default=default, help="DICOM volume to use as the MRI")
    args = ap.parse_args()
    if args.dicom is None:
        ap.error("no DICOM found in $DATA_DIR/coherent – pass --dicom")
    make(args.dicom)


if __name__ == "__main__":
    main()
