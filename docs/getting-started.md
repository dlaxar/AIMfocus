# Getting started

How to run AIMfocus locally, build the patient data, and run the tests. For what the app does,
see the [README](../README.md); for how it works, [architecture.md](architecture.md).

## Prerequisites

- Python 3.12 or newer, Node 20 or newer
- An **Anthropic API key** (runtime: screens and chat)
- To build the fact store from scratch: a key for `PREPROCESS_MODEL` (Gemini by default, so a
  **Google API key**)
- The challenge data. IBM's data is **not** in this repository (challenge rule): copy the
  challenge repository's `data/` folder to the repository root. It is gitignored.

```
IBM-AIM-Hackathon-Team-Oachkatzlschwoaf/
├── data/                    ← copied from the challenge repository (gitignored)
│   └── patient-0000-0001/
├── core/
├── frontend/
└── .env                     ← your keys (gitignored)
```

## 1. Secrets

At the repository root (the core reads this file; `core/.env` overrides it if present):

```bash
echo "CLAUDE_API_KEY=..." > .env
echo "GOOGLE_API_KEY=..." >> .env     # only for preprocessing with Gemini
```

`ANTHROPIC_API_KEY` and `GEMINI_API_KEY` are accepted as well. All other settings have working
defaults; see [core/README.md](../core/README.md#configuration-env) and
[core/.env.example](../core/.env.example).

## 2. Core service

```bash
cd core
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m preprocess.run --patient 0000-0001     # once, about 2 minutes
.venv/bin/python -m uvicorn app.main:app --port 8001
```

The API is then at `http://localhost:8001` (interactive docs at `/docs`, health at `/healthz`).

## 3. Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The Vite dev server forwards `/api/*` to the core on port 8001. To
point the frontend at another core, set `VITE_BASE_URL` in `frontend/.env.local`.

## 4. Try it

1. Type a role and task, for example *"I am the anaesthetist. She is scheduled for a
   quadrantectomy with intraoperative radiotherapy on 16 May 2016."* (or use the microphone).
2. Pick the patient: search for "Anna", "0000-0001" or "1959".
3. The screen fills in within a few seconds. Click a source icon to see the passage, a warning
   icon to see a contradiction, and ask follow-up questions in the chat bar.

## Optional

### More patients from FHIR records

Synthetic FHIR R4 records with DICOM images (Synthea / MITRE Coherent data set; each patient
folder holds `fhir.json` and `dicom/*.dcm`) are imported without an LLM:

```bash
cd core
.venv/bin/python -m preprocess.import_fhir --all ../data/coherent
.venv/bin/python -m preprocess.import_fhir --folder ../data/coherent/Wiley422_Hermiston71
```

Patient IDs are `coh-<first 8 characters of the FHIR id>`. The import writes
`data/patient-<id>/patient.json`, so the patient search finds them, and a PNG preview per
DICOM study to `core/cache/<id>/images/`.

### Re-running preprocessing

```bash
.venv/bin/python -m preprocess.run --patient 0000-0001 --from s3     # restart from a stage
.venv/bin/python -m preprocess.refresh_derived --patient 0000-0001   # code-only derived facts, no LLM
.venv/bin/python -m contradictions --patient 0000-0001               # contradiction rules only
.venv/bin/python -m preprocess.run --patient 0000-0001 --offline-llm DIR   # answer LLM prompts from files
```

With `--offline-llm DIR`, every prompt without an answer is written to `DIR/<key>.prompt.md`;
put the answer in `DIR/<key>.json` and run again. This lets you build the cache with any model,
or by hand.

### FHIR checks

```bash
.venv/bin/python -m preprocess.convert_cache --patient 0000-0001     # JSON files → fhir.json
.venv/bin/python -m preprocess.validate_fhir --patient 0000-0001     # official HL7 validator (Java 17+)
```

The `/stats` page in the app shows how many patients and validated FHIR documents the core holds.

### Demo without the LLM

Recorded screens in `streams/0.json`, `1.json`, … (gitignored) can be replayed as if they were
streamed live: open `http://localhost:5173/#/patient/dashboard?simulate`, or set
`VITE_SIMULATE_SCREEN=1` in `frontend/.env.local`.

## Tests

No API key needed; LLM calls return canned responses from `core/tests/fixtures/`.

```bash
cd core
LLM_FAKE=true .venv/bin/python -m pytest tests
```

The fixtures are a tiny **invented** patient, not IBM data. How to run the evaluation against
the challenge situations is in [evaluation.md](evaluation.md).

## Troubleshooting

| Symptom | Cause |
|---|---|
| Core fails at start with "Cache manifest not found" | Run `preprocess.run` for the patient first; `core/cache/<patient>/fhir.json` must exist |
| Patient search finds nothing | `data/patient-*/patient.json` missing: check that `data/` is at the repository root (or set `DATA_DIR`) |
| Screens stay at "starting" or show an error | `CLAUDE_API_KEY` missing or invalid; the core log shows the API error |
| Microphone button greyed out | The browser has no Web Speech API (use Chrome, Edge or Safari) |
