# Contradiction finder

Finds passages in a patient's documents that cannot all be true and writes them to
`cache/<patient>/contradictions.json` in the shared `app.schemas.Contradiction` format,
the file `FactStore` already loads (`/api/contradictions`, screen filtering).

**Rules first, LLM optional.** The detectors read the S0 passages directly. They need
neither S1 facts nor an API key, take about 15 ms per patient and give the same result on
every run. An optional LLM sweep looks for anything the rules cannot see. Its claims are
checked in code and then confirmed by a separate judge call.

## Run

```bash
cd core
.venv/bin/python -m contradictions --patient 0000-0001            # rules only
.venv/bin/python -m contradictions --patient 0000-0001 --llm      # + LLM sweep and judge (PREPROCESS_MODEL)
.venv/bin/python -m contradictions --patient 0000-0001 --keep-s3  # also keep S3's own results
```

If `documents.json` / `passages.json` are missing, S0 runs first. If `facts.json` exists,
the S1 concept IDs of the linked passages are added to each contradiction, so screens can
match it by concept as well as by passage. `--offline-llm DIR` works as in `preprocess.run`.

| Output | Content |
|---|---|
| `contradictions.json` | Shown contradictions: summary, kind, **all** linked passage IDs, concept IDs, verdict |
| `contradictions_report.json` | Every finding, including hidden ones, with rule, certainty, rationale, judge verdict |
| `manifest.json` | `contradiction_count` updated, if the manifest exists |

## Detectors (`rules.py`)

| Rule | Check | Certainty |
|---|---|---|
| `stay_dates` | One letter gives two admission (or discharge) dates for its own stay: header vs. narrative | definite |
| `impossible_date` | A date that cannot exist (five-digit year, 31 April). A date glued to a time is skipped | definite |
| `bmi_mismatch` | Stated BMI vs. weight / height² in the same document (tolerance ±1) | definite |
| `case_number` | One case number on documents of two separate stays. Documents of the same stay with another number are linked | definite |
| `copied_text` | Text that S0 marked as copied from an earlier report, with left/right swapped. Unchanged sibling passages and their originals are linked | definite |
| `finding_polarity` | A finding (e.g. enlarged lymph nodes, also TNM N0) documented as present and as absent within one episode | definite ≤ 14 days, else possible |
| `procedure_dates` | One procedure (e.g. a biopsy) dated differently by two documents of one episode: sampling date, dated examination block, "… vom <date>" | definite |
| `age_vs_birth_date` | "60-jährige" vs. date of birth at the document date (±1 year) | definite |
| `birth_date` | Two dates of birth | definite |
| `lv_wall` | "Thickened left ventricle" with septum and posterior wall ≤ 10 mm | possible |

**Not reported**, because the challenge counts them as false alarms: small measurement
differences (height ±2 cm, blood pressure ±10 mmHg), values that change
over time (weight, doses across stays) and differences from IBM's JSON files, which are
never read.

**Each contradiction counts once.** Duplicates are merged by a semantic key (rule +
document + event), not by shared passages. Example: a letter header can hold both a
wrong admission date and an impossible discharge date, and these stay two contradictions.

## LLM layer (`llm.py`, `--llm`)

1. **Sweep** (`app/llm/prompts/cx_sweep.md`): one call with every passage and the list of
   findings so far. It returns new candidates with passage IDs and verbatim quotes. Code
   drops unknown passage IDs, items whose quotes are not in the cited passages, and
   repeats of known findings (two shared passages, or one passage set inside the other).
2. **Judge** (`app/llm/prompts/cx_judge.md`): one call per *possible* finding, from the
   sweep or the rules, in parallel. The verdict is contradiction, change over time,
   measurement noise or not related. A finding is shown only if the verdict is
   contradiction with confidence ≥ `CONTRADICTION_CONFIDENCE_THRESHOLD`. *Definite* rule
   findings are never sent to the judge.

## Result on patient 0000-0001

Rules only: **8 contradictions, 0 false alarms**, checked by hand against all eight
documents:
- 2 BMI values that do not match height and weight
- left/right swapped in a copied carotid report
- admission date: header vs. text
- impossible discharge date
- a case number reused from an earlier stay
- a biopsy with two dates
- enlarged lymph nodes vs. none (and N0)

One finding is kept back as *possible* (`lv_wall`).

## Integration

- **Pipeline:** this finder *is* stage S3 of `python -m preprocess.run` (rules only; the
  earlier `preprocess/s3_contradictions.py` is no longer called by the pipeline). The
  pipeline then writes `fhir.json`, which the runtime reads. After a standalone
  `python -m contradictions` run, call `python -m preprocess.convert_cache --patient <id>`
  to put the new `contradictions.json` into `fhir.json`.
- **Screen:** a contradiction is attached to a card only when a value shown on it carries
  one of the contradiction's passages or concepts (`screen.contradictions`, marked on the
  values). Every other contradiction is listed in `screen.other_contradictions`, shown
  below the screen with links to all its `passage_ids`. See
  [screen-api.md](../docs/screen-api.md#5-contradictions).

## Tests

`tests/test_contradictions.py` covers every detector on invented documents, plus the LLM
validation and the runner. The tests need no API key.
