# AIMfocus – Core

Team **Oachkatzlschwoaf** · IBM AIM Hackathon · Track 1

The backend of AIMfocus: a FastAPI service that builds role-specific patient screens and
answers follow-up questions, plus the offline pipeline that prepares each patient's fact store.
What the product does: [README](../README.md). How the pieces fit together:
[docs/architecture.md](../docs/architecture.md).

**Core principle: slow and thorough offline, fast and constrained at runtime.**

---

## Quick start

Full instructions (data, keys, frontend, troubleshooting): [docs/getting-started.md](../docs/getting-started.md).

```bash
cd core
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Keys: repo-root .env (core/.env overrides it), see .env.example
#   CLAUDE_API_KEY=...   runtime (screens, chat)
#   GOOGLE_API_KEY=...   preprocessing with Gemini

.venv/bin/python -m preprocess.run --patient 0000-0001     # once per patient, about 2 minutes
.venv/bin/python -m uvicorn app.main:app --reload --port 8001   # API docs: http://localhost:8001/docs

LLM_FAKE=true .venv/bin/python -m pytest tests             # no API key needed
```

Preprocessing writes to `cache/<patient-id>/` (gitignored). More commands:

```bash
python -m preprocess.run --patient 0000-0001 --from s3      # restart from a stage
python -m preprocess.run --patient 0000-0001 --offline-llm DIR   # answer LLM prompts from files
python -m preprocess.refresh_derived --patient 0000-0001    # code-only derived facts, no LLM
python -m preprocess.import_fhir --all ../data/coherent     # FHIR R4 patients (Synthea / Coherent), no LLM
python -m preprocess.make_demo_patient                      # demo copy of 0000-0001 with a brain MRI (0000-0101), no LLM
python -m preprocess.convert_cache --patient 0000-0001      # JSON files ↔ fhir.json (--to-json)
python -m preprocess.validate_fhir --patient 0000-0001      # official HL7 validator (Java 17+)
python -m contradictions --patient 0000-0001                # contradiction rules only (--llm: + sweep/judge)
```

Evaluation (core running): see [docs/evaluation.md](../docs/evaluation.md).

```bash
python -m eval.run_eval --v2 --extra --cases-dir <challenge>/cases   # 5 + 26 situations
python eval/score_runs.py <run-output>
python -m eval.run_additional                                        # 72 team questions, Opus judge
```

---

## Layout

```
core/
  app/
    main.py               FastAPI app: documents, passages, originals, patients, images, stats; mounts the routers
    config.py             Settings (pydantic-settings, .env); measured model choices as comments
    schemas.py            Pydantic data models: Document, Passage, Fact, DerivedFact, Contradiction, Source
    store.py              FactStore: loads cache/<patient>/ (fhir.json or JSON files), lookup helpers
    fastpath/             Screens (/api/v2/screens)
      service.py            create, select, derive (text search), upgrade, SSE state
      plan.py               screen planned before the patient is known, applied in code
      shape.py              facts → value / trend / table / list / image, with headline and sources
      models.py, router.py
    chat/                 Follow-up questions (/api/v2/chat), cached-prefix LLM call + cards
    fhir/                 FHIR R4 bundle: mapping, codes (ICD-10-GM, ATC, LOINC), load/save, ig/ extensions
    llm/
      client.py           Routes calls by model name: Anthropic (claude-*), Google Gemini, Fake (tests), Offline (files)
      prompts/            Versioned prompt files (*.md)
    runtime/
      validator.py        Code-only checks: numbers, drug names, laterality, dates must be in the cited facts
      screen.py           Source building (passage, offsets, quote) used by screens and chat
      planner.py, answerer.py, templates.py   earlier runtime (R1/R2, POST /api/screen), kept for comparison
    patient.py            Patient header and patient search (no LLM)
    originals.py          Maps passages back into the original delivered files
    images.py             DICOM → PNG previews and slices
    stats.py              Patient and FHIR-bundle counts for /stats
  preprocess/
    run.py                CLI: python -m preprocess.run --patient ID [--from sN]
    s0_passages.py        Normalize + split into passages, copy detection
    s1_extract.py         LLM fact extraction (parallel, long documents in parts), alias merge
    s2_merge.py           Concepts, timelines, derived facts
    s3_contradictions.py  Earlier S3 (rule candidates + LLM judge); the pipeline now uses contradictions/
    s4_export.py          fact_sheet.txt + manifest.json
    vocab.py              Controlled vocabulary for concept keys and measurement keys
    import_fhir.py        FHIR R4 bundles + DICOM → cache, no LLM
    make_demo_patient.py  Demo patient 0000-0101 (Eva Mustermann): copy of 0000-0001 + MRI report and image
    convert_cache.py      JSON files ↔ fhir.json
    validate_fhir.py      Official HL7 validator run
    refresh_derived.py    Recompute code-only derived facts
  contradictions/         Rule-based contradiction finder (S3), optional LLM sweep and judge – see its README
  docs/
    screen-api.md         JSON reference for the frontend
    fhir-plan.md          FHIR R4 store: design, mapping, validation results
  tests/                  pytest; fixtures/ is a tiny invented patient + canned LLM responses
  eval/                   Evaluation harness, team situations and questions (docs/evaluation.md)
cache/                    gitignored – preprocessing output
```

---

## Pipeline

### Offline (once per patient, about 2 minutes)

| Stage | What it does | Output |
|-------|-------------|--------|
| **S0** | HTML unescape, section detection, sentence/table/list splitting, copy detection | `documents.json`, `passages.json` |
| **S1** | `PREPROCESS_MODEL` extracts atomic facts from all documents in parallel (long documents in parts); immediate code validation; alias merge links concept keys across documents; coverage report | `facts.raw.json`, `s1_report.json`, `s1_aliases.json` |
| **S2** | Concept resolution, timelines, derived facts (current medication, daily doses, dose changes, weight change, BMI, age, follow-up status) | `facts.json`, `concepts.json`, `derived.json` |
| **S3** | `contradictions/`: rule detectors (the optional LLM sweep + judge runs with `python -m contradictions --llm`) | `contradictions.json`, `contradictions_report.json` |
| **S4** | Exports `fact_sheet.txt` (one line per fact) and `manifest.json` | `fact_sheet.txt`, `manifest.json` |
| **FHIR** | The whole cache as one validated R4 Bundle; the runtime reads this file (`CACHE_FORMAT=auto`) | `fhir.json` |

### Runtime (per request)

| Step | What it does |
|------|-------------|
| Plan + select | On `POST /api/v2/screens`, both models plan the screen without the patient (`fp_plan`) and select cards for the default patient (`fp_select`). The LLM picks facts; it writes no values |
| Patient picked | `PUT /api/v2/screens/<id>/patient` applies the plan to that patient's facts in code (no LLM call); values and sources come from the store |
| Derive | One `fp_derive` call per card the fact store cannot fill, in parallel over the raw passages; verbatim quotes required, else "not documented" |
| Validate | Code only: IDs exist, quotes verbatim, numbers, drugs, sides and dates backed by the cited facts |
| Upgrade | The same screen with `UPGRADE_MODEL` (Opus 5.5) replaces the fast one under the same ID |
| Chat | One cached-prefix call per question (`chat`), same validation, up to 2 cards via the screen builder |

Typical timings with Claude Haiku 5.5: documented cards after 2–4 s, complete screen after
3–7 s, chat answer about 2 s.

---

## API

JSON reference for the frontend (screen, card types, contradictions, sources, chat, patient
header, images): [docs/screen-api.md](docs/screen-api.md). OpenAPI: `http://localhost:8001/docs`.

Every patient-specific route takes `patient_id` (query, or in the POST body of screens and
chat); without it the default patient (`PATIENT_ID`) is used. Stores load on first use.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v2/screens` | `{"situation", "patient_id"?, "upgrade"?}` → screen at once (`202`), filled in the background |
| `GET` | `/api/v2/screens?situation=…` | Same as the POST, for quick tests in a browser |
| `GET` | `/api/v2/screens/{id}` | Current screen; `?wait=true` blocks until complete, `&final=true` also until the upgrade |
| `PUT` | `/api/v2/screens/{id}/patient` | `{"patient_id"}` → the same screen rebuilt for that patient |
| `GET` | `/api/v2/screens/{id}/stream` | SSE: `screen` event on every change, then `done` |
| `GET` | `/api/v2/screens/{id}/summary` | Short "Role, Situation" label and a role icon URL |
| `GET` | `/api/v2/simulated/stream` | SSE replay of `streams/<n>.json` (demo without the LLM) |
| `POST` | `/api/v2/chat` | `{"question", "chat_id"?, "screen_id"?}` → `{chat_id, answer, state, sources, fact_ids}` |
| `POST` | `/api/v2/chat/stream` | Same, SSE: `answer`, then `cards`, then `done` |
| `GET` | `/api/v2/chat/{chat_id}` | The conversation so far |
| `GET` | `/api/v2/patients` | Every patient with a cache (IBM + imported FHIR), with image count |
| `GET` | `/api/v2/patients/search?q=` | Patients whose name, ID or date of birth contains `q` |
| `GET` | `/api/v2/patients/{id}` | Patient header: name, age, CAVE, height / weight / BMI, latest stay |
| `GET` | `/api/v2/patients/{id}/images` | The patient's images (`ImageRef`: preview / slice / DICOM links) |
| `GET` | `/api/v2/patients/{id}/images/{image_id}/preview.png` · `slice.png?axis=&index=` · `dicom` | Quick PNG views and the original file |
| `GET` | `/api/v2/stats` | Patient count, FHIR bundles and documents, each validated |
| `GET` | `/api/v2/schema` · `/api/v2/concepts` · `/api/v2/concepts/{id}` · `/api/v2/facts/{id}` | Fact store inspection (debugging) |
| `GET` | `/api/documents` · `/api/documents/{doc_id}` | Document list; normalized text + passages |
| `GET` | `/api/documents/{doc_id}/original` · `/original/file` | The original file's text and metadata; the file itself |
| `GET` | `/api/passages/{id}` · `/context` · `/original` | One passage; inside its document section; its span in the original file |
| `GET` | `/api/contradictions` | All precomputed contradictions |
| `GET` | `/healthz` | Cache loaded, manifest version |
| `POST` | `/api/screen` · `/api/screen/stream` | Earlier runtime (R1 planner → R2 answerer → R3 validator → R4 assembly), kept for comparison; not used by the frontend |

Screens and chats live in memory (single process).

---

## Configuration (`.env`)

The repo-root `.env` is read first, `core/.env` overrides it, real environment variables
override both. Template: [.env.example](.env.example).

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_DIR` | `<repo>/data` | Challenge patient data root (gitignored) |
| `CACHE_DIR` | `core/cache` | Preprocessing output (gitignored) |
| `STREAMS_DIR` | `<repo>/streams` | Recorded screens for `/api/v2/simulated/stream` |
| `PATIENT_ID` | `0000-0001` | Default patient |
| `CLAUDE_API_KEY` | – | Anthropic API key (also `ANTHROPIC_API_KEY`), for `claude-*` models |
| `GOOGLE_API_KEY` | – | Google AI key (also `GEMINI_API_KEY`), for `gemini-*` models |
| `RUNTIME_MODEL` | `claude-haiku-5-5` | Screens (plan, select, derive) and chat; `gemini-…` models go to Google |
| `RUNTIME_THINKING_BUDGET` | `0` | `0` = no thinking (Haiku 5.5, Gemini flash); `-1` = unset (needed for Opus 5.5) |
| `RUNTIME_EFFORT` | – | Claude only: `low` / `medium` / `high` |
| `RUNTIME_THINKING_LEVEL` | `minimal` | Gemini thinking level for runtime calls (`""` = model default) |
| `UPGRADE_MODEL` | `claude-opus-5-5` | Second model per screen, replaces the fast screen when done; empty = off |
| `UPGRADE_EFFORT` | `low` | Effort for the upgrade model |
| `SCREEN_PLAN` | `true` | Plan the screen with the situation, so picking the patient needs no LLM call |
| `FASTPATH_MAX_PARALLEL_DERIVE` | `6` | Max parallel text-search calls per screen |
| `PREPROCESS_MODEL` | `gemini-4-argon` | Model for S1 and the optional contradiction sweep/judge |
| `PREPROCESS_MAX_PARALLEL` | `6` | Max parallel LLM calls for S2 follow-ups |
| `S1_CHUNK_PASSAGES` | `30` | S1 splits longer documents into parts extracted in parallel (`0` = one call per document) |
| `S1_PARALLEL_DOCS` | `true` | S1 extracts all documents at once; the alias merge links their concept keys |
| `CACHE_FORMAT` | `auto` | `auto` = `fhir.json` when present, else JSON files; `fhir`; `json` |
| `CONTRADICTION_CONFIDENCE_THRESHOLD` | `0.80` | Judge confidence needed to show a *possible* contradiction |
| `CONTRADICTION_LLM_SWEEP`, `COPY_SIMILARITY_THRESHOLD` | `false`, `0.85` | Not read by the current pipeline: S3 runs the rules only (LLM layer: `python -m contradictions --llm`), S0 uses a fixed copy threshold of 0.85 |
| `REFERENCE_DATE` | `auto` | "Now" for the record; `auto` = latest document date |
| `LLM_FAKE` | `false` | `true` in tests – canned responses from `tests/fixtures/llm_responses/` |
| `FAKE_LLM_DELAYS_MS` | `{}` | Fake-mode latency per prompt, e.g. `{"fp_derive": 3000}` |

---

## Rules

- **No manual edits of the cache.** Read outputs to improve prompts and rules, then re-run.
- `cache/` and `data/` are gitignored. IBM patient files must never enter the repo or CI.
- `tests/fixtures/` contains a tiny **invented** patient only. No real data.
- New features land behind flags until the eval shows they help.
