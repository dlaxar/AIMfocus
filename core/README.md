# Clinical Documentation Screen — Backend

Team **Oachkatzlschwoaf** · IBM AIM Hackathon · Track 1

---

## Overview

A clinician types a **role + task** in free text (e.g. *"Anaesthetist, pre-op check before quadrantectomy on 16 May 2016"*).
The backend returns **one screen** with only the patient facts that task needs. Each fact links to the exact sentence or table row it came from. Missing facts are shown as **"not documented"** and relevant **contradictions** are flagged.

**Core principle: slow and thorough offline, fast and constrained at runtime.**

---

## Quick start

### 1. Install dependencies

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env:
#   DATA_DIR  = path to the IBM data directory (never committed)
#   GEMINI_API_KEY = your key
#   PATIENT_ID = 0000-0001
```

### 3. Run offline preprocessing (once per patient)

```bash
python -m preprocess.run --patient 0000-0001
# Re-run from a specific stage (e.g. after editing a prompt):
python -m preprocess.run --patient 0000-0001 --from s3
```

Output is written to `cache/<patient-id>/`. That directory is gitignored.

### 4. Start the API server

```bash
uvicorn app.main:app --reload
# API docs: http://localhost:8000/docs
```

### 5. Run tests (no API key needed)

```bash
LLM_FAKE=true pytest tests/ -v
```

### 6. Run evaluation

```bash
uvicorn app.main:app &
python -m eval.run_eval          # 5 standard situations
python -m eval.run_eval --extra  # + 5 extra situations
```

---

## Repository layout

```
backend/
  app/
    main.py               FastAPI app, all routes
    config.py             Settings (pydantic-settings, .env)
    schemas.py            Pydantic data models – shared contract
    store.py              Loads cache into memory, lookup helpers
    runtime/
      planner.py          R1 – parse situation → Need list
      answerer.py         R2 – select fact IDs per need
      validator.py        R3 – deterministic validation rules
      screen.py           R4 – assemble final Screen
      templates.py        Role/task → hard-coded needs templates
    llm/
      client.py           Thin wrapper: Gemini (default) / Fake (CI)
      prompts/            Versioned prompt files (*.md)
  preprocess/
    run.py                CLI: python -m preprocess.run --patient ID
    s0_passages.py        Normalize + split into passages
    s1_extract.py         LLM fact extraction (one call per document)
    s2_merge.py           Concept resolution, timelines, derived facts
    s3_contradictions.py  Rule-based candidates + LLM judge
    s4_export.py          fact_sheet.txt + manifest.json
  tests/
    fixtures/             Tiny invented patient + LLM canned responses
    test_validator.py     Validator unit tests
    test_passages.py      S0 splitter unit tests
    test_smoke.py         End-to-end smoke test (LLM_FAKE=true)
  eval/
    run_eval.py           Evaluation harness → markdown table
    situations_extra.md   5 additional role situations
cache/                    gitignored – preprocessing output
data/                     gitignored – IBM patient data
.env.example              Template for secrets
```

---

## Pipeline stages

### Offline (once per patient, ~2 minutes)

| Stage | What it does | Output |
|-------|-------------|--------|
| **S0** | HTML unescape, section detection, sentence/table/list splitting, copy detection | `documents.json`, `passages.json` |
| **S1** | LLM (strong model) extracts atomic facts from all documents in parallel (long documents in parts); immediate code validation; alias merge links concept keys across documents; coverage report | `facts.raw.json`, `s1_report.json`, `s1_aliases.json` |
| **S2** | Concept resolution, timelines, derived facts (weight change, BMI, med changes) | `facts.json`, `concepts.json`, `derived.json` |
| **S3** | Rule-based candidates + LLM judge; keeps only `contradiction` with confidence ≥ threshold | `contradictions.json` |
| **S4** | Exports `fact_sheet.txt` (one line per fact) and `manifest.json` | `fact_sheet.txt`, `manifest.json` |

### Runtime (per request, target ≈ 5 s)

| Step | Budget | What it does |
|------|--------|-------------|
| **R1** Planner | 1.5 s | Gemini Flash: situation → role + Need list; merged with role templates |
| **R2** Answerer | 3.0 s | Gemini Flash: select fact IDs per need from `fact_sheet.txt`; deterministic resolvers first |
| **R3** Validator | < 50 ms | Code-only: numbers, drug names, laterality, dates must appear in cited facts |
| **R4** Screen assembly | < 50 ms | Resolve passages → sources with offsets; attach contradictions |

---

## API

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/screen` | `{"situation": str}` → `Screen` |
| `POST` | `/api/screen/stream` | Same, SSE: `plan` / `section` / `done` events |
| `GET` | `/api/documents` | Document list |
| `GET` | `/api/documents/{doc_id}` | Normalized text + passages |
| `GET` | `/api/passages/{passage_id}` | Single passage with char offsets |
| `GET` | `/api/contradictions` | All precomputed contradictions |
| `POST` | `/api/v2/chat` | `{"question", "chat_id"?, "screen_id"?}` → `{chat_id, answer, state, sources, fact_ids}`; pass `chat_id` back for follow-ups. `state`: `answered` / `not_documented` / `check` (failed validation, show with a warning). About 2 s with Haiku 5.5 |
| `GET` | `/api/v2/chat/{chat_id}` | The conversation so far |
| `GET` | `/healthz` | Cache loaded, manifest version |

---

## Configuration (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_DIR` | `../data` | IBM patient data root (gitignored) |
| `CACHE_DIR` | `./cache` | Preprocessing output (gitignored) |
| `PATIENT_ID` | `0000-0001` | Active patient |
| `GEMINI_API_KEY` | – | Your Google AI API key |
| `CLAUDE_API_KEY` | – | Anthropic API key, for `claude-` runtime models |
| `RUNTIME_MODEL` | `claude-haiku-5-5` | Runtime model (select/derive, R1/R2); `gemini-…` models go to Google |
| `RUNTIME_THINKING_BUDGET` | `0` | `0` = no thinking (Haiku 5.5, Gemini flash); `-1` = unset (needed for Opus 5.5) |
| `RUNTIME_EFFORT` | – | Claude only: `low` / `medium` / `high` |
| `RUNTIME_THINKING_LEVEL` | `minimal` | Gemini thinking level for runtime calls (`""` = model default) |
| `PREPROCESS_MODEL` | `gemini-4-argon` | Model for S1/S3 |
| `PREPROCESS_MAX_PARALLEL` | `6` | Max parallel LLM calls for S2 follow-ups |
| `S1_CHUNK_PASSAGES` | `30` | S1 splits longer documents into parts extracted in parallel (`0` = one call per document) |
| `S1_PARALLEL_DOCS` | `true` | S1 extracts all documents at once; the alias merge links their concept keys (`false` = oldest first, each document reuses earlier keys) |
| `LLM_FAKE` | `false` | `true` in CI – returns fixture responses |
| `CONTRADICTION_LLM_SWEEP` | `false` | Extra LLM sweep for S3 |
| `REFERENCE_DATE` | `auto` | `auto` = latest document date |

---

## Rules

- **No manual edits of the cache.** Read outputs to improve prompts and rules, then re-run.
- `cache/` and `data/` are gitignored. IBM patient files must never enter the repo or CI.
- `tests/fixtures/` contains a tiny **invented** patient only. No real data.
- New features land behind flags until the eval shows they help.
