# Plan: FHIR R4 as the only store

Status: implemented and merged into `main` (steps 1–3, 5–8; step 4 in a lighter form, see below).
The runtime reads `cache/<patient>/fhir.json` by default; imported FHIR patients
(`preprocess/import_fhir.py`) end in the same bundle format.
Prototype quality is the goal: valid FHIR R4 outside, nothing lost inside.

## What is implemented

| Step | State |
|---|---|
| 1. `app/fhir/` with `load` / `save`, round-trip test | done: `cache.py`, `mapping.py`, `ids.py`, `extensions.py`, `codes.py`; `tests/test_fhir.py` |
| 2. Converter | done: `python -m preprocess.convert_cache --patient <id>` (and `--to-json` back) |
| 3. `FactStore` reads `fhir.json` | done: `CACHE_FORMAT=auto` (default: `fhir.json` when present), `fhir`, `json` |
| 4. Stages on the bundle | lighter: stages still hand over through the JSON files; the pipeline ends with an `=== FHIR bundle ===` step that writes `fhir.json`, and `--from sN` restores the earlier stages' files from `fhir.json` when they are missing (stage tags). The contradiction finder (`contradictions/`) is untouched. |
| 5. ICD-10 / ATC / LOINC | done: IBM mentions matched to our facts and spread per concept; LOINC table checked on loinc.org |
| 6. Tests | done: round trip, R4 validation, references, status mapping, codes, store on `fhir.json`, held-back findings |
| 7. Real ingestion + eval | done, see `Results` |
| 8. Docs | this file, `core/README.md`, `docs/architecture.md` |

Deviations from the design below:
- The JSON stage files stay as the pipeline's working files; the runtime reads only `fhir.json`.
  A standalone `python -m contradictions` run writes `contradictions.json`: run
  `python -m preprocess.convert_cache` afterwards to put it into `fhir.json`.
- Raw S1 facts, S1 report, alias merges and the manifest are `Binary` resources (JSON), since
  `Bundle` has no extensions. Facts are tagged `s2` (they are the merged S2 output).
- Held-back findings without a finder ID get `X:report:<key>` as `DetectedIssue.id`.

## Results (patient 0000-0001)

See the section "Results" at the end.

## Goal

- Ingestion writes **one file**, `cache/<patient>/fhir.json`: an R4 `Bundle` (type `collection`).
- It replaces every cache file we have today: `documents.json`, `passages.json`, `facts.raw.json`,
  `facts.json`, `concepts.json`, `derived.json`, `contradictions.json`, `contradictions_report.json`,
  `s1_report.json`, `s1_aliases.json`, `fact_sheet.txt`, `manifest.json`.
- Pipeline stages hand their results to each other through this file, and the runtime reads only this file.
- Diagnoses carry **ICD-10** codes and medications **ATC** codes, taken from IBM's extraction
  (`data/<patient>/diagnoses.json`, `medication.json`). Measurements get **LOINC** codes from a fixed table.

## Design

### One conversion layer, everything else unchanged

The pipeline and the runtime keep working with today's Pydantic objects (`Document`, `Passage`, `Fact`,
`DerivedFact`, `Concept`, `Contradiction`). Only persistence changes:

```
core/app/fhir/
  cache.py      Cache dataclass (all objects of one patient) + load(path) / save(cache, path)
  mapping.py    object <-> FHIR resource, one pair of functions per fact type
  ids.py        our IDs <-> FHIR ids ("F:12340012:dia:01" -> "F-12340012-dia-01")
  extensions.py extension URLs and helpers
  codes.py      ICD-10 / ATC from IBM's files, LOINC table
```

- Every stage replaces its `json.loads(...)` / `write_text(...)` with `cache = fhir.load(path)` /
  `fhir.save(cache, path)`.
- `FactStore.load()` calls `fhir.load()` and builds its indices as today.
- Fast path, chat, validator, screens and eval only use the `FactStore` API, so they don't change.
- `fact_sheet` is not stored: `FactStore.load()` regenerates it with the same function S4 uses today.
- Resources are built with `fhir.resources` (R4B models), so an invalid resource fails during ingestion.

### Restarting from a stage (`--from sN`)

Every resource gets `meta.tag` with the stage that created it (`s0` … `s4`). `--from sN` loads the
bundle, drops all resources tagged `sN` or later, and runs from there. That gives the same behaviour as
today's separate files.

### Mapping

| Today | FHIR R4 | Notes |
|---|---|---|
| Patient (`data/<patient>/patient.json`) | `Patient` | synthetic; `birthDate` checked against the letters |
| `Document` | `DocumentReference` | normalized text as `content.attachment` (text/plain); date, type, title, department |
| `Passage` | extension on its `DocumentReference` | id, start, end, kind, section, copy_of; text = slice of the document text |
| S1 coverage per document (`s1_report`) | extension on the `DocumentReference` | |
| Fact `diagnosis` | `Condition` | ICD-10 coding |
| Fact `finding` | `Observation`, category `exam` | |
| Fact `measurement` | `Observation`, category `vital-signs` or `laboratory` | LOINC coding, UCUM unit; blood pressure as two components |
| Fact `medication` | `MedicationStatement` | ATC coding; dosage text from the schedule (1-0-1) |
| Fact `allergy` | `AllergyIntolerance` | |
| Fact `procedure` | `Procedure` | |
| Fact `recommendation`, `plan` | `ServiceRequest` | intent `proposal` / `plan` |
| Fact `event` (admission, discharge) | `Encounter` | |
| Fact `demographic` | `Observation` | |
| `DerivedFact` (BMI, weight change, current medication, follow-up status) | `Observation` with `derivedFrom` | |
| `Concept` | coding in our CodeSystem on every resource of the concept | label as `display`; timelines recomputed on load |
| Alias merges (`s1_aliases`) | extension on the `Bundle` | for review only |
| `Contradiction` (shown) | `DetectedIssue`, status `final` | `implicated` = the conflicting resources |
| Hidden finding (`contradictions_report`) | `DetectedIssue`, status `preliminary` | with rule, certainty and judge verdict |
| Manifest | `Bundle.meta` + extension | version, model, coverage, counts |

**Every fact resource** fills the standard FHIR fields (code text, status, date, value, body site for
laterality) and carries **one extension with our original values**: fact id, passage id, offsets,
verbatim span, our status, `fields`, `en`, `de`. Loading reads the extension, so the round trip is
lossless and doesn't depend on FHIR's status vocabulary.

### Status mapping (standard fields)

| Our status | Condition (clinical / verification) | MedicationStatement | Procedure | ServiceRequest |
|---|---|---|---|---|
| positive | active / confirmed | – | completed | – |
| suspected | active / provisional | – | – | – |
| negative | – / refuted | – | not-done | – |
| history | inactive / confirmed | – | completed | – |
| current, started, changed | – | active | – | – |
| discontinued, historical | – | stopped / completed | – | – |
| recommended, open | – | – | – | active |
| done | – | – | – | completed |
| unknown | – / unconfirmed | unknown | unknown | unknown |

### ICD-10 and ATC from IBM's extraction

A code-only step at the end of S1, after the alias merge. No LLM, so no invented codes.

1. Walk the mentions in `diagnoses.json` and `medication.json` (and optionally MEL codes from `procedures.json`).
2. Strip IBM's markup (`<SPAN …>`, entities, extra spaces) and find the passage in the mention's
   `documentIds` that contains the text. On patient 0000-0001, 187 of 189 mentions are found this way.
3. Take our fact on that passage whose span overlaps the mention, and whose type fits:
   - ICD-10 → `diagnosis` or `finding`
   - ATC → `medication`
4. Spread the code to every fact of the same concept, since a concept is one clinical thing. A concept
   with two different codes keeps both, and we log it.
5. Systems: `http://hl7.org/fhir/sid/icd-10`, `http://www.whocc.no/atc`, `http://loinc.org`.

Expected coverage from the earlier comparison:
- **Medication:** almost complete. All 22 IBM mentions sit on our medication facts, and 11 of 12 ingredients have an ATC code.
- **Diagnoses:** 75 IBM mentions match one of our diagnosis facts directly. Findings and our extra diagnoses stay without a code.

LOINC: a fixed table for our measurement keys (weight, height, BMI, blood pressure, heart rate, temperature,
HbA1c, LVEF, …). Check every code against loinc.org before committing.

Follow-up, not part of this plan: use the codes as a merge key in the alias merge. Same type and same
ICD-10 or ATC code means the same concept.

## Steps

1. **`app/fhir/` with `load` / `save` and a round-trip test.** Today's JSON cache → `Cache` → bundle →
   `Cache` must give identical objects. Also check that every resource validates as R4 and every
   reference resolves.
2. **One-off converter:** `python -m preprocess.convert_cache --patient 0000-0001` turns an existing JSON
   cache into `fhir.json`, so we can test the runtime without a new ingestion.
3. **`FactStore.load()` reads `fhir.json`.** Run the eval on the converted cache. It should give the same
   result as the JSON cache it came from: 62/69 on all 23 situations, 15/15 on the core 5, give or take
   the runtime's run-to-run variation.
4. **Stages switch to the bundle:**
   - S0 writes `DocumentReference`s with passages
   - S1 adds the fact resources and the codes
   - S2 adds concept codings and derived observations
   - S3 (the contradiction finder in `contradictions/`) adds `DetectedIssue`s
   - S4 writes the manifest

   `--from` works through the stage tags. The old JSON writing goes away.
5. **ICD-10 / ATC tagging and the LOINC table**, with unit tests on a small invented IBM-style file.
6. **Tests and fixtures:** `tests/test_smoke.py::_build_fixture_cache` writes `fhir.json` instead of the
   JSON files. Stage tests read and write through `app/fhir`.
7. **One real ingestion** (about 2–6 min of Gemini calls), then the eval, then compare with step 3.
8. **Docs:** `core/README.md` (cache layout, pipeline table, mapping) and the root README.

Steps 1–3 are safe on their own: the runtime runs on FHIR while the pipeline still writes JSON. That's
a good point to stop if time runs out.

## Dependencies

- `fhir.resources` (R4B models, Pydantic v2) in `core/requirements.txt`.

## Risks and open points

- **Our extensions aren't profiled.** That's fine for a prototype, but a strict validator will warn about
  unknown extensions.
- **The contradiction finder in `contradictions/`** is another team member's code. Step 4 changes how it
  reads and writes, so coordinate before touching it.
- **Rewriting the whole file after each stage:** about a few MB per patient, negligible.
- **IBM codes only exist for patients IBM processed.** For a new patient without these files, facts
  simply stay uncoded.

## Results

Measured on branch `fhir-store`, patient 0000-0001, runtime model Claude Haiku 5.5, all 31
eval situations (93 questions, `eval/score_runs.py`).

| Check | Result |
|---|---|
| Round trip JSON → bundle → JSON | all 11 artifacts identical |
| R4 validation (`fhir.resources` R4B models) | 386 resources valid, 0.2–0.4 s |
| References inside the bundle | 445, none dangling |
| Store from `fhir.json` vs. JSON files | identical in every field, fact sheet identical to the character; load 25 ms vs 7 ms |
| Codes | 117 of 120 IBM mentions matched to our facts; ICD-10 / ATC on 150–158 facts; LOINC table (10 codes) checked on loinc.org |
| `--from s3` with only `fhir.json` present | earlier stages restored, S3/S4 rerun, bundle rewritten |
| Eval, JSON store | 86/93 (mean 4.3 s, max 7.9 s) |
| Eval, same cache from `fhir.json` | 86/93 (mean 4.4 s, max 8.4 s) |
| Fresh ingestion straight into `fhir.json`, eval on it | 87/93 (mean 4.6 s, max 7.6 s); ingestion 5 min 15 s |
| After merging main (stricter prompt), `fhir.json` | 90/93 (mean 5.8 s); one screen at 10.9 s from a slow select call – the same situation repeated 4× gave 4.7–8.7 s on FHIR and 4.0–8.1 s on JSON |
| Chat stream with cards, FHIR mode, through the former Flask gateway | 6/6 questions; text 1.5–3.6 s, first cards 1.1–3.2 s |

### Official HL7 FHIR Validator

`python -m preprocess.validate_fhir` runs `validator_cli.jar` (HL7 / HAPI, Java 17+) against
R4 4.0.1 with our extension definitions (`app/fhir/ig/`) and the patient's concept CodeSystem.
Result for patient 0000-0001: **0 errors**, 233 warnings, 439 information messages.

| Warning | Count | Why it stays |
|---|---|---|
| Observations should have a performer | 201 | the letters do not say who measured |
| Code system definition not found | 31 | ICD-10-GM and ATC are not in the validator's terminology; it notes the CodeableConcept is still valid |
| Observation should have effective[x] | 1 | one finding without any date in the letter |

Fixed on the way (first run: 584 errors): our extensions now have StructureDefinitions;
vital signs carry UCUM units (kg, cm, kg/m2, Cel, /min, mm[Hg]) as the vital-signs profiles
require, and only single values get a vital-signs LOINC (a Holter range stays an exam
observation); HbA1c is a laboratory observation; IBM's diagnosis codes are ICD-10-GM
(`http://fhir.de/CodeSystem/bfarm/icd-10-gm` – M81.99 does not exist in WHO ICD-10); every
resource has a short narrative. `tests/test_fhir.py` runs the validator on the fixture bundle
when it is installed.
