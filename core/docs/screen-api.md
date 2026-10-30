# Screen and chat API – JSON reference for the frontend

Everything the frontend needs to render a patient screen, flag contradictions, open the
source letter, and ask follow-up questions. All routes are served by the core (FastAPI,
`:8001`); the frontend reaches them through the Vite dev proxy under `/api`. The
machine-readable schema is the core's OpenAPI document: `http://127.0.0.1:8001/openapi.json`
(browsable at `/docs`).

## 1. Flow

| Step | Call | Returns |
|---|---|---|
| Clinician enters the situation | `POST /api/v2/screens` with `{"situation": "…"}` | the screen at once (`202`); `screen.screen_id` is the session ID, `screen.status = "starting"` |
| Screen fills in (while the patient is picked) | `GET /api/v2/screens/<id>/stream` (SSE) | an `event: screen` with the full screen on every change, then `event: done` |
| … or poll | `GET /api/v2/screens/<id>` · `?wait=true` blocks until complete | the screen |
| Short card title | `GET /api/v2/screens/<id>/summary` | `{"summary": "Anaesthetist, PreOp Info"}` |
| Patient search | `GET /api/v2/patients/search?q=<text>&limit=10` | patients whose name or ID contains the text (section 9) |
| Show a source in its section | `GET /api/passages/<passage_id>/context` | the passage and the document section around it (section 6) |
| Open a source letter | `GET /api/documents/<doc_id>` | the letter's text and passages (section 6) |
| Ask about the patient | `POST /api/v2/chat` with `screen_id` | an answer with sources (section 7) |
| Ask, with cards, streamed | `POST /api/v2/chat/stream` with `screen_id` (SSE) | `answer` at once, then `cards` as they come, then `done` (section 7) |
| Patient header (always shown) | `GET /api/v2/patients/<patient_id>` | name, birth date, age, CAVE, height / weight / BMI, latest stay (section 9) |

`screen.status`: `starting` (select call running, `variables` empty) → `partial`
(documented items there, text search running) → `complete`. Every update raises
`screen.version`.

Typical timings with the default model (Claude Haiku 5.5): first items after 2–4 s, complete
screen after 3–7 s.

## 2. Screen

```jsonc
{
  "screen_id": "a1b2c3d4e5f6",            // = session_id
  "situation": "Ward nurse admitting her on 10 April 2016.",
  "role": "Ward nurse",
  "task": "Admission 10 Apr 2016: safety, meds, follow-up",
  "reference_date": "2016-04-15",          // "now" for the record; present tense means this date
  "status": "complete",
  "variables": [ /* ScreenVariable, sorted by priority (section 3) */ ],
  "contradictions": [ /* Contradiction touching this screen's items (section 5) */ ],
  "other_contradictions": [ /* every other Contradiction in the record */ ],
  "timings_ms": {"select": 2800, "first_response": 2810, "derive": 1500, "complete": 4330},
  "error": null,                           // set when the screen could not be prepared
  "version": 4
}
```

## 3. ScreenVariable

One card on the screen. The model decides **what** to show, its **priority**, **tier** and
**output type**; the values are always pulled from the documents by code.

### Fields every variable has

| Field | Type | Meaning |
|---|---|---|
| `name` | string | stable key, snake_case (`current_medication`) |
| `label` | string | card title (`Current medication`) |
| `priority` | int | unique rank 1, 2, 3 … – render (and load) in this order |
| `tier` | `critical` · `important` · `background` | `critical`: must be seen before acting – first, prominent. `background`: context – collapsed |
| `output` | `value` · `trend` · `table` · `list` · `image` | which renderer to use (below) |
| `state` | see below | where the value comes from |
| `headline` | Headline \| null | **detail level 1**: what the collapsed card shows |
| `contradictions` | ContradictionRef[] | every contradiction touching this card (empty list if none) |
| `sources` | Source[] | all source passages of the card |
| `resolved_ms` | number \| null | time from the request until this card was ready |

| `state` | Meaning | Suggested UI |
|---|---|---|
| `documented` | from the structured fact store | normal |
| `pending` | text search still running | skeleton |
| `derived` | found by the text search in a letter (quote checked) | normal, maybe a "from text" hint |
| `not_documented` | the letters do not contain it – a valid, often important answer | "Not documented" |
| `check` | an answer existed but failed verification | show with a warning |
| `not_available` | `output: image` – the documents contain no images | placeholder |

```jsonc
// Headline – detail level 1
{"text": "66 kg", "date": "2016-04-15", "change": "-12 kg since 2015-06-28"}   // change: trends only
```

### Detail level 2, by `output`

Exactly one of these is filled; the others are empty. **Every entry, point and row carries its
own `source` and `contradiction_ids`** (detail level 3).

#### `value` – current value(s) and their history

`latest`: the newest entry per concept (e.g. one per drug). `history`: older entries, newest first.

```jsonc
{
  "label": "Allergies", "priority": 1, "tier": "critical", "output": "value", "state": "documented",
  "headline": {"text": "Contrast media allergy (nausea, dizziness, vomiting)", "date": "2016-04-15"},
  "latest": [
    {"fact_id": "F:12340018:all:01", "text": "Contrast media allergy (nausea, dizziness, vomiting)",
     "date": "2016-04-15", "status": "positive",
     "source": {"doc_id": "12340018", "doc_title": "Stat. Patientenbrief (Interne)", "doc_date": "2016-04-15",
                "passage_id": "12340018:p012", "start": 812, "end": 880, "quote_de": "Allergien: Kontrastmittel …"},
     "contradiction_ids": []}
  ],
  "history": [ /* Entry, older */ ],
  "contradictions": []
}
```

A value found by the text search (`state: "derived"`) is one `latest` entry with `fact_id: null`
and the quoted passage as `source`.

#### `trend` – numbers for a chart

One `Series` per measured quantity; blood pressure comes as two series (systolic, diastolic).
Points are oldest first. `label` tells readings on the same day apart (`24h mean`, `admission`).

```jsonc
{
  "label": "Weight trend", "priority": 4, "tier": "important", "output": "trend", "state": "documented",
  "headline": {"text": "66 kg", "date": "2016-04-15", "change": "-12 kg since 2015-06-28"},
  "series": [
    {"name": "Body weight", "unit": "kg", "points": [
      {"date": "2015-06-28", "value": 78, "label": "Weight", "fact_id": "F:12340012:mea:04",
       "source": {…}, "contradiction_ids": ["X:cf:bmi_12340012_p038"]},
      {"date": "2016-04-05", "value": 67, "label": "Weight", "fact_id": "F:12340014:mea:02",
       "source": {…}, "contradiction_ids": ["X:cf:bmi_12340014_p023"]},
      {"date": "2016-04-15", "value": 66, "label": "Weight", "fact_id": "F:12340018:mea:02",
       "source": {…}, "contradiction_ids": []}
    ]}
  ],
  "contradictions": [
    {"contradiction_id": "X:cf:bmi_12340014_p023", "kind": "calculation",
     "summary_en": "BMI 29 in Ambulanter Patientenbrief (2016-04-05) does not match the height (171 cm) and weight (67 kg) …"}
  ]
}
```

Blood pressure headline: `{"text": "145/88 mmHg", …}`. If a requested trend has no numbers,
the backend falls back to `output: "value"` – always render by the `output` you receive.

#### `table` – one row per item, few columns

The model picks 2–4 columns for the role (a nurse gets `item, strength, schedule`; a
pharmacist also `last_change`). `item` is always first. Possible columns:
`item, strength, schedule, route, class, status, since, last_change, value, date`.

```jsonc
{
  "label": "Current medication", "priority": 1, "tier": "critical", "output": "table", "state": "documented",
  "headline": {"text": "8 item(s): Acetylsalicylic acid (Thrombo ASS), Alendronic acid (Alendronat), …", "date": "2016-04-15"},
  "table": {
    "columns": ["item", "strength", "schedule", "last_change"],
    "rows": [
      {"cells": {"item": "Candesartan (Blopress)", "strength": "16 mg", "schedule": "1-0-1",
                 "last_change": "16 mg 1-0-1/2 (2015-06-28) → 16 mg 1-0-1 (2016-04-15)"},
       "fact_id": "F:12340018:med:04", "date": "2016-04-15", "source": {…}, "contradiction_ids": []}
    ]
  },
  "contradictions": []
}
```

An empty cell is `""`.

#### `list` – findings, diagnoses, recommendations

Newest first. `status` is the fact's status (`positive`, `negative`, `current` …) or, for
recommendations, `open` / `done` (follow-up checked against all later letters).

```jsonc
{
  "label": "Open follow-up items", "priority": 6, "tier": "important", "output": "list", "state": "documented",
  "headline": {"text": "Follow-up open: Next carotid duplex ultrasound planned in 2 months … (+2 more)", "date": "2016-04-15"},
  "items": [
    {"fact_id": "D:followup:f_12340012_rec_03",
     "text": "Follow-up open: Next carotid duplex ultrasound planned in 2 months – no record in the 3 later document(s)",
     "date": "2016-04-15", "status": "open", "source": {…}, "contradiction_ids": []}
  ]
}
```

#### `image`

`state: "not_available"`, `headline.text: "No images in the documents"`. Show a placeholder.

### Older flat fields

`value` (one text line), `fact_ids`, `note`, `contradiction_ids` and `contradicting_fact_ids`
are still sent for existing code. New code should use the fields above.

## 4. Shared objects

```jsonc
// Source – a passage in a letter; enough to open the letter and highlight it
{"doc_id": "12340018", "doc_title": "Stat. Patientenbrief (Interne)", "doc_date": "2016-04-15",
 "passage_id": "12340018:p012", "start": 812, "end": 880, "quote_de": "…German text of the passage…"}

// Entry – value, history row, list item, or a value found by the text search (fact_id null)
{"fact_id": "F:…" | null, "text": "…", "date": "YYYY-MM-DD" | null, "status": "…" | null,
 "source": Source | null, "contradiction_ids": ["X:…"]}

// TrendPoint
{"date": "YYYY-MM-DD", "value": 78.0, "label": "Weight" | null, "fact_id": "F:…",
 "source": Source | null, "contradiction_ids": []}

// ContradictionRef – on a variable
{"contradiction_id": "X:cf:…", "kind": "calculation", "summary_en": "…"}

// Coding – standard code of a fact, in `codes` on every Entry, TableRow and TrendPoint,
// and as `code` on a trend Series. Never part of `text`, `label` or `headline`.
{"system": "ATC", "code": "B01AC06", "display": null, "uri": "http://www.whocc.no/atc"}
{"system": "LOINC", "code": "29463-7", "display": "Body weight", "uri": "http://loinc.org"}
{"system": "ICD-10", "code": "C50.9", "display": null, "uri": "http://hl7.org/fhir/sid/icd-10"}
```

`codes` is empty when no code is known. ICD-10 and ATC come from IBM's extraction (matched to
our facts and spread over the concept), LOINC from a fixed table of measurements (weight,
height, BMI, blood pressure with systolic/diastolic, heart rate, HbA1c, LVEF, temperature).
A concept can carry two codes when IBM coded it twice (e.g. carotid stenosis I65.2 and G45.3).
Suggested UI: a small badge or tooltip next to the value, not in the card title.

Dates are ISO (`YYYY-MM-DD`); a few facts only know the month (`2016-04`).

## 5. Contradictions

Places in the letters that cannot all be true (left vs. right carotid, two admission dates,
a BMI that does not match weight and height …). They are flagged on three levels:

1. **Screen**: `contradictions` (touch this screen) and `other_contradictions` (the rest).
2. **Card**: `variable.contradictions` – show a warning badge on the card.
3. **Entry / point / row**: `contradiction_ids` – mark exactly the value that is affected.

```jsonc
// Contradiction (screen level)
{
  "contradiction_id": "X:cf:copied_text_laterality_12340012_p075",
  "kind": "laterality",                 // laterality · date · value · calculation · presence · copy_mismatch
  "summary_en": "Carotid finding copied from Radiologischer Befund (2015-06-11) into … has left and right swapped …",
  "passage_ids": ["12340008:p012", "12340012:p075", "…"],   // open each to show both sides
  "concept_ids": ["C:finding:internal_carotid_artery:stenosis"],
  "fact_ids": ["F:12340008:fin:01", "F:12340012:fin:05", "…"],
  "judge": {"verdict": "contradiction", "confidence": 0.9, "rationale": "…"} | null
}
```

Linking is per passage: another finding in the same sentence can be flagged too.

## 6. Showing a source letter

`GET /api/documents/<doc_id>` returns the whole letter:

```jsonc
{
  "doc_id": "12340018", "title": "Stat. Patientenbrief (Interne)", "date": "2016-04-15",
  "type": "Arztbrief(Inter.)", "department": "3. Medizinische Abteilung mit Onkologie",
  "text": "ORTXXX, am 15.04.2016\n\nFrau\n…",          // full normalized text
  "passages": [
    {"passage_id": "12340018:p000", "section": null, "kind": "sentence",
     "start": 0, "end": 21, "text": "ORTXXX, am 15.04.2016", "copy_of": null}
  ]
}
```

To highlight a source: `text.slice(source.start, source.end)` is the cited passage (or find
`passage_id` in `passages`). `copy_of` names the passage a sentence was copied from in an
earlier letter. Single passage: `GET /api/passages/<passage_id>`.

A source without the whole letter – the passage inside its section (e.g. the medication list
around one drug): `GET /api/passages/<passage_id>/context`

```jsonc
{
  "doc": {"doc_id": "12340018", "title": "Stat. Patientenbrief (Interne)", "date": "2016-04-15",
          "type": "Arztbrief(Inter.)", "department": "3. Medizinische Abteilung mit Onkologie"},
  "passage": {"passage_id": "12340018:p054", "kind": "table_row", "start": 2571, "end": 2604,
              "text": "Thrombo ASS 100mg oral 1 0 0 0 ST", …},
  "section": {"name": "Entlassungsmedikation", "start": 2511, "end": 2849,
              "text": "Entlassungsmedikation:\nApplikation mo mi ab na Einheit Info\nThrombo ASS 100mg oral …"}
}
```

Offsets are into the full document text, so highlight `passage.start - section.start` …
inside `section.text` and open the full letter at the same place later.

### The original file (as delivered)

The text above is normalized (HTML entities decoded, `<BR>` as line breaks). To show the
letter exactly as delivered:

| Call | Returns |
|---|---|
| `GET /api/documents/<doc_id>/original` | `{doc_id, file, metadata, text}` – `text` is the original HTML (`<BR>`, `&uuml;` …), `metadata` the original header (title, date, department, admission …) |
| `GET /api/passages/<passage_id>/original` | the same plus `raw_start`, `raw_end`, `raw_quote` (= `text.slice(raw_start, raw_end)`) and `quote` (the normalized passage) |
| `GET /api/documents/<doc_id>/original/file` | the original JSON file, unchanged |

```jsonc
// GET /api/passages/12340018:p054/original
{"doc_id": "12340018", "file": "data/patient-0000-0001/documents/12340018.json",
 "metadata": {"id": "12340018", "title": "Stat. Patientenbrief (Interne)", "date": "2016-04-15", …},
 "text": "ORTXXX, am 15.04.2016<BR><BR>Frau<BR>…",
 "passage_id": "12340018:p054", "raw_start": 2772, "raw_end": 2805,
 "raw_quote": "Thrombo ASS 100mg oral 1 0 0 0 ST", "quote": "Thrombo ASS 100mg oral 1 0 0 0 ST"}
```

To highlight in the original: render `text` as HTML and wrap `text.slice(raw_start, raw_end)`.
Checked for all 349 passages of the patient: every raw span normalizes back to its passage.

## 7. Chat

```
POST /api/v2/chat
{"question": "Which blood thinner does she take?", "screen_id": "<session_id>", "chat_id": "…optional, for follow-ups…"}
```

```jsonc
{
  "chat_id": "3f9a1c0b7e21",                       // send back with the next question
  "answer": "She takes aspirin (Thrombo ASS) 100 mg, 1-0-0, as documented on 15 Apr 2016.",
  "state": "answered",                              // answered · not_documented · check
  "sources": [Source, …],
  "fact_ids": ["F:12340018:med:01"],
  "note": null,                                     // why an answer is "check"
  "timings_ms": {"llm": 1800, "total": 1800}
}
```

About 2 s per answer. The chat knows the session's screen and the conversation, answers in the
language of the question, names contradictions, and gives no treatment advice. Errors: `400`
empty question, `404` unknown `chat_id`, `502` model not reachable. Conversation so far:
`GET /api/v2/chat/<chat_id>`.

### Chat with cards (streamed)

The chat answers in text **and** pulls the variables that answer the question as **cards** –
the same `ScreenVariable` objects as on a screen (section 3): the model picks 1–4 variables
with their output type, the values are pulled from the documents by code, anything the fact
store lacks is searched in the letters (so questions about details that are not a stored fact
work too), and a check that is missing comes back as `state: "not_documented"` with
`headline.text: "Not documented"`. Render the cards with the screen renderers.

**Recommended: one stream.** Nothing waits for the slowest part; later results may come after
10 s, the first ones come at once.

```
POST /api/v2/chat/stream
{"question": "What did the rectal examination in June 2015 show?", "screen_id": "<session_id>", "chat_id": "…optional…"}
```

Server-sent events, in the order they are ready:

| Event | When | `data` |
|---|---|---|
| `answer` | text ready, ~1.5 s | the chat reply (`chat_id`, `answer`, `state`, `sources`, `fact_ids`, `cards_screen_id`) – same as section 7 |
| `cards` | first after ~1.5–3 s, again on every change | `{chat_id, cards_screen_id, cards_status, cards: [ScreenVariable…], elapsed_ms}` – always the **full** current list; replace what you show. Cards from the fact store come first; `pending` cards fill in when the text search is done |
| `done` | everything finished | `{elapsed_ms}` |
| `error` | the text answer failed | `{detail}` (cards may still follow) |

`answer` and the first `cards` can come in either order. `"cards": false` streams only `answer`
and `done`. Pass `chat_id` from the `answer` event with the next question for follow-ups.

Measured through the former Flask gateway (Haiku 5.5): text 1.5–2.6 s, first cards 1.3–3.0 s, done
within 4.5 s; e.g. "What did the rectal examination show?" (not a stored fact) → a `list` card
from the letter text; "Is a CPAP therapy documented?" → `not_documented` card.

**Without streaming** – `POST /api/v2/chat` with `"cards": true`:

```
POST /api/v2/chat
{"question": "How has her weight changed?", "screen_id": "<session_id>", "cards": true, "wait": true}
```

```jsonc
{
  "chat_id": "3f9a1c0b7e21",
  "answer": "Her weight fell from 78 kg (2015-06-28) to 66 kg (2016-04-15), a documented change of -12 kg.",
  "state": "answered",
  "sources": [Source, …],
  "fact_ids": ["F:12340012:mea:05", "F:12340018:mea:03"],
  "cards_status": "complete",
  "cards_screen_id": "9c41e0a7b2d3",
  "cards": [
    {"name": "weight_trend", "label": "Weight trend", "priority": 1, "tier": "critical", "output": "trend",
     "state": "documented", "headline": {"text": "66 kg", "date": "2016-04-15", "change": "-12 kg since 2015-06-28"},
     "series": [{"name": "Body weight", "unit": "kg", "code": {"system": "LOINC", "code": "29463-7", …},
                 "points": [{"date": "2015-06-28", "value": 78, "source": Source, "contradiction_ids": ["X:cf:bmi_12340012_p038"]}, …]}],
     "contradictions": [ContradictionRef, …], "sources": [Source, …]}
  ],
  "timings_ms": {"llm": 2300, "cards": 2800, "total": 2800}
}
```

| Request field | Default | Meaning |
|---|---|---|
| `cards` | `false` | also return cards; without it the chat answers in text only, as before |
| `wait` | `false` | `false`: the reply comes as soon as the text is ready, with `cards: []` and `cards_screen_id` – stream the cards with `GET /api/v2/screens/<cards_screen_id>/stream`. `true`: the finished cards are in this reply (slower) |

Examples from the live test (Haiku 5.5, 12 questions × 2 runs, all as expected):

| Question | Card |
|---|---|
| How has her weight changed? | `trend` with the weight points (and BMI) |
| Show her blood pressure over time. | `trend`, systolic and diastolic series, headline 145/88 mmHg |
| Which medicines does she take? | `table`, 8 rows (item, strength, schedule) |
| What is her latest HbA1c? | `value` HbA1c 6.4% |
| Which open follow-ups are there? | `list` with `status: "open"` / `"done"` |
| Is her kidney function documented? | `value`, `state: "not_documented"` |
| Since when? (follow-up to the blood thinner) | `list` with the aspirin start in 2015 |

Timing with `wait: true`: text answer about 1.6 s on average; the reply with cards about 2.5 s
(single outliers up to about 12 s when a text search is slow – the stream avoids that wait).

## 8. Rendering checklist

- Sort cards by `priority`; render `critical` first and prominently, `background` collapsed.
- Pick the renderer by `output`; a `pending` card keeps its `output`, so the skeleton can match.
- Always show `headline` on the collapsed card; levels 2 and 3 on expand / click.
- Show a badge when `contradictions` is non-empty; mark entries / points / rows by their `contradiction_ids`.
- Every entry, point and row has a `source`: link it to the letter view (section 6).
- `not_documented` is information, not an error.

## 9. Patient header

What the frontend shows on every screen. Built in code from the patient file and the
letters, no LLM, answers at once (404 for a patient that is not loaded).

```
GET /api/v2/patients/0000-0001
```

```jsonc
{
  "patient_id": "0000-0001",
  "name": "Anna Musterfrau",
  "sex": "female",
  "birth_date": "1959-03-15",
  "age": 57,                              // on reference_date, the record's "now" – not today
  "reference_date": "2016-04-15",         // date of the latest letter
  "allergies": [                          // CAVE: newest mention per allergen, with its letter
    {"text": "Contrast media allergy (nausea, dizziness, vomiting)", "date": "2016-04-15",
     "fact_id": "F:12340018:all:01", "source": Source}
  ],
  "height": {"value": 170, "unit": "cm", "date": "2016-04-15", "fact_id": "F:…", "source": Source},
  "weight": {"value": 66, "unit": "kg", "date": "2016-04-15", "fact_id": "F:…", "source": Source},
  "bmi": {"value": 22.8, "date": "2016-04-15", "fact_id": "D:bmi:12340018",
          "text": "Computed BMI 22.8 kg/m² (66 kg / 170 cm)"},   // recomputed from weight and height
  "latest_stay": {"id": "0000-0005", "type": "inpatient", "admission": "2016-04-10", "discharge": "2016-04-15"},
  "record": {"documents": 8, "first_letter": "2015-06-11", "last_letter": "2016-04-15", "contradictions": 9}
}
```

Any field can be `null` (or `[]` for `allergies`) when it is not documented. An empty
`allergies` list means no allergy is documented – show "no known allergies documented",
not "no allergies".

The patient search, `GET /api/v2/patients/search?q=<text>&limit=10` (limit 1–50), returns the
entries of every `data/*/patient.json` whose name or ID contains the text (case-insensitive),
without `ward`; an empty `q` returns `[]`.

