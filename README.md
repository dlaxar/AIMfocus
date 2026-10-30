# Clinical Documentation Screen – Team Oachkatzlschwoaf

IBM AIM Hackathon · Track 1. A clinician types their role and task ("Anaesthetist, pre-op check before quadrantectomy") and gets **one screen** with only the patient facts that task needs. Every fact links to the sentence or table row it comes from, missing facts are shown as "not documented", and contradictions between documents are flagged. Follow-up questions go to a chat that answers from the same sources and adds one or two cards to the screen.

## Architecture

```
frontend/ (Vue + Vite, :5173)
   │  /api/* (Vite dev proxy)
   ▼
core/ (FastAPI, :8001)
   ├─ offline, once per patient                                         → core/cache/<patient>/
   │    S0 split the letters into passages (sentences, table rows) with character offsets
   │    S1 LLM fact extraction per passage → validated (span must be in the passage)
   │    S2 concepts, timelines and derived facts in code (current medication, daily doses,
   │       dose changes, weight change, BMI, age on the reference date, follow-up status)
   │    S3 contradictions: rules (dates, BMI arithmetic, sides, copied text, case numbers); optional LLM sweep + judge
   │    S4 fact sheet, stored as one validated FHIR R4 bundle (ICD-10, ATC, LOINC codes)
   │
   └─ runtime, per request
        POST /api/v2/screens   → screen ID at once, then filled in the background:
          1. select call: the model sees the fact store under short handles and picks the
             cards the role needs (priority, tier, output type: value / trend / table / list /
             image). Values are pulled from the fact store by code, never written by the model.
          2. derive calls, in parallel, one per card the fact store cannot answer: a search of
             the raw passages; the answer needs verbatim quotes, else "not documented".
          3. upgrade: the same screen built with a stronger model in parallel; when it is
             done it replaces the fast screen under the same ID.
        Results reach the frontend as whole screens over SSE (/stream): documented cards
        first, text-search results and the upgraded screen as they arrive.
        POST /api/v2/chat/stream → text answer (~2 s) plus at most 2 extra cards, same validation.
```

Validation is code, not LLM: every number, drug name, side and date shown must appear in the cited facts or passages; anything else is dropped or marked "check". "Now" is the date of the latest document (15 April 2016 for the challenge patient); the app always searches all documents.

### Models (Anthropic API)
| Step | Model | Setting |
|---|---|---|
| Screen select + derive, chat | Claude Haiku 5.5, no thinking | `RUNTIME_MODEL`, `RUNTIME_THINKING_BUDGET=0` |
| Screen upgrade (streams in later) | Claude Opus 5.5, effort low | `UPGRADE_MODEL`, `UPGRADE_EFFORT`; empty = off |
| Offline fact extraction (and the optional contradiction judge) | `PREPROCESS_MODEL` (Gemini by default; Claude via `--offline-llm`) | once per patient |

### Beyond the challenge
- **More patients**: synthetic FHIR R4 records (Synthea / MITRE Coherent) are imported without LLM (`python -m preprocess.import_fhir --all ../data/coherent`); every route takes a `patient_id`.
- **Images**: DICOM studies get a PNG preview at import; image cards and sources link preview, single slices and the DICOM file.
- **Patient picked after the situation**: `PUT /api/v2/screens/<id>/patient` rebuilds the screen for that patient under the same ID.

JSON reference for the frontend: [core/docs/screen-api.md](core/docs/screen-api.md). Backend details: [core/README.md](core/README.md).

### Flow in the app
1. "What are you looking for?" → `POST /api/v2/screens` with `{"situation": ...}` starts the screen in the core while the clinician picks the patient.
2. Patient selection (`GET /api/v2/patients/search?q=`), then `PUT /api/v2/screens/<id>/patient`.
3. Result screen: `GET /api/v2/screens/<id>` and live updates from `GET /api/v2/screens/<id>/stream` (SSE); questions via `POST /api/v2/chat/stream`.

## Run it

Prerequisites: Python 3.12, Node 20+, an Anthropic API key (runtime) and, to build the fact store from scratch, a key for `PREPROCESS_MODEL`. The IBM data is **not** in this repo: copy the challenge's `data/` folder to the repo root (it is gitignored).

```bash
# 0. Secrets (repo root, gitignored)
echo "CLAUDE_API_KEY=..." > .env          # plus GOOGLE_API_KEY=... for preprocessing with Gemini

# 1. Core service
cd core && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m preprocess.run --patient 0000-0001     # once, about 2 minutes
.venv/bin/python -m uvicorn app.main:app --port 8001

# 2. Frontend (new terminal)
cd frontend && npm install && npm run dev                   # http://localhost:5173
```

Tests (no API key needed): `cd core && LLM_FAKE=true .venv/bin/python -m pytest tests`.

## Results on the 5 situations (2026-10-30)

`python -m eval.run_eval --v2 --extra` with Claude Haiku 5.5, two runs; answers scored by `eval/score_runs.py` against `cases/expected.json`. Load time runs from `POST /api/v2/screens` until the screen is complete (`status: "complete"`: documented cards and text search done). The Opus upgrade streams in afterwards (7–12 s) and is not part of this time.

| Metric | Result | Target |
|---|---|---|
| Correct answers | 15 of 15 (both runs) | 15 of 15 |
| Traceability | 100% of linked sources (211/211 and 234/234) | 100% |
| Invented facts | 0 shown; the validator held back 1 item per run as "check" | 0 |
| Contradictions | 8 shown, all checked by hand against the letters, no false alarms | 5 or more |
| Load time, slowest of the 5 | 5.2 s and 5.0 s | under 10 s |

On all 31 situations (5 challenge + 26 team situations, 93 questions): 88–90 of 93, mean 4.5 s, slowest 7.2 s. On the team's 72 additional questions (`python -m eval.run_additional`, Opus as judge): chat 66, screen 42–43 (fast) and 51–52 (after the upgrade).

## Repository layout
| Path | What |
|---|---|
| `frontend/` | Vue app: situation input (text or voice), patient search, result screen, chat |
| `core/` | API the frontend talks to, fact store, preprocessing pipeline, contradictions, validator, tests, eval (see `core/README.md`) |
| `streams/` | Recorded screens for the demo stream without the LLM (`/api/v2/simulated/stream`) |
