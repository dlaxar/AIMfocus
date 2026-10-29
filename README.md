# Clinical Documentation Screen – Team Oachkatzlschwoaf

IBM AIM Hackathon · Track 1. A clinician types their role and task ("Anaesthetist, pre-op check before quadrantectomy") and gets **one screen** with only the patient facts that task needs. Every fact links to the sentence or table row it comes from, missing facts are shown as "not documented", and contradictions between documents are flagged.

## Architecture

```
frontend/ (Vue + Vite, :5173)
   │  /api/* (Vite dev proxy)
   ▼
backend/ (Flask gateway, :8000)          patients, sessions, documents
   │  HTTP
   ▼
core/ (FastAPI, :8001)                   fact store + LLM pipeline
   ├─ offline:  S0 passages → S1 LLM fact extraction → S2 concepts/timelines/derived facts
   │            → S3 contradictions (rules + LLM judge) → S4 fact sheet      → core/cache/
   └─ runtime:  /api/v2/screens
                1. select call (Gemini Flash): knows the fact-store structure, returns the
                   task's variables with values + fact IDs → validated → returned at once
                2. one derive call per missing variable, in parallel, over the raw passages
                   (finds derived concepts, e.g. "CT done without contrast due to allergy")
                   → validated (verbatim quotes, numbers backed by passages) → pushed via SSE
```

Validation is code, not LLM: every number, drug name, side and date shown must appear in the cited facts or passages; anything else is dropped or marked "check".

### Flow in the app
1. "What are you looking for?" → `GET /api/session/get?situation=<base64>` starts the screen in the core. The documented part is computed right away, the text search keeps running in the background.
2. Patient selection.
3. Result screen: `GET /api/session/<id>` and live updates from `GET /api/session/<id>/stream` (SSE).

### Models
- Runtime: `gemini-2.5-flash` (select + derive calls)
- Preprocessing: `gemini-2.5-pro` (fact extraction, contradiction judge)

Configurable in `.env` (`RUNTIME_MODEL`, `PREPROCESS_MODEL`).

## Run it

Prerequisites: Python 3.12, Node 20+, a Google AI API key. The IBM data is **not** in this repo: copy the challenge's `data/` folder to the repo root (it is gitignored).

```bash
# 0. Secrets (repo root, gitignored)
echo "GOOGLE_API_KEY=..." > .env

# 1. Core service
cd core && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m preprocess.run --patient 0000-0001     # once, about 2 minutes
.venv/bin/python -m uvicorn app.main:app --port 8001

# 2. Flask gateway (new terminal)
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python app.py

# 3. Frontend (new terminal)
cd frontend && npm install && npm run dev                   # http://localhost:5173
```

Tests (no API key needed): `cd core && LLM_FAKE=true .venv/bin/python -m pytest tests`.

## Results on the 5 situations (2026-10-29)

Fast path via `python -m eval.run_eval --v2`, answers checked by hand against `cases/expected.json`.
`GET /api/session/get` returns the session ID at once; the times below run from that call until the screen is complete.

| Metric | gemini-3.5-flash-lite, thinking minimal (default) | gemini-3.8-flash, thinking low | Target |
|---|---|---|---|
| Correct answers | 13–14 of 15 (3 runs) | 15 of 15 | 15 of 15 |
| Traceability | 100% of linked sources | 100% (116/116) | 100% |
| Invented facts | 0 validator drops | 0 validator drops | 0 |
| Contradictions | not implemented yet (in progress) | – | 5 or more |
| Load time, complete screen | 1.2–4.9 s | 9.9–14.3 s | under 10 s |

With flash-lite the missing answer changes between runs (e.g. the native head CT for 4.2, the stenosis side for 5.3). For the highest answer quality set `RUNTIME_MODEL=gemini-3.8-flash` and `RUNTIME_THINKING_LEVEL=low` in `.env`.

The fact store was extracted offline (`preprocess.run --offline-llm`) with Claude using the same S1 prompt and validation as the Gemini path; the app itself only uses Gemini at runtime.

## Repository layout
| Path | What |
|---|---|
| `frontend/` | Vue app: situation input (text or voice), patient search, result screen |
| `backend/` | Flask gateway the frontend talks to; forwards screen requests to the core |
| `core/` | Fact store, preprocessing pipeline, runtime API, validator, tests, eval (see `core/README.md`) |
