# Evaluation

How we measured AIMfocus against the challenge metrics, and how to repeat the measurements.
The summary is in the [README](../README.md#results).

## Results on the 5 challenge situations (2026-10-30)

`python -m eval.run_eval --v2 --extra` with Claude Haiku 5.5, two runs; answers scored by
`eval/score_runs.py` against the challenge's `cases/expected.json`. Load time runs from
`POST /api/v2/screens` until the screen is complete (`status: "complete"`: documented cards and
text search done). The Opus upgrade streams in afterwards (7–12 s) and is not part of this time.

| Metric | Result | Target |
|---|---|---|
| Correct answers | 15 of 15 (both runs) | 15 of 15 |
| Traceability | 100% of linked sources (211/211 and 234/234) | 100% |
| Invented facts | 0 shown; the validator held back 1 item per run as "check" | 0 |
| Contradictions | 8 shown, all checked by hand against the letters, no false alarms | 5 or more |
| Load time, slowest of the 5 | 5.2 s and 5.0 s | under 10 s |

### The 8 contradictions

Found by the deterministic detectors in [`core/contradictions/`](../core/contradictions/README.md),
each linking all of its passages:

1. A BMI that does not match the height and weight in the same letter
2. A second BMI that does not match its letter's height and weight
3. Left and right swapped in a carotid report copied into a later letter
4. An admission date in a letter header that differs from the one in its text
5. A discharge date that cannot exist
6. A case number reused from an earlier hospital stay
7. One biopsy with two different dates
8. Enlarged lymph nodes in one letter, none (and N0) in another of the same episode

One more finding (a "thickened left ventricle" with normal wall measurements) is kept back as
*possible* and not shown. Small measurement differences, values that change over time and
differences from IBM's JSON files are deliberately not reported – the challenge counts them as
false alarms.

## Beyond the 5 situations

The jury also tests new situations for the same patient. To prepare, the team wrote 26 more
situations with three questions each (other roles: pharmacist, ward nurse, radiologist, dentist,
clinical coder, …) in [`core/eval/situations_extra.md`](../core/eval/situations_extra.md), with
expected answers in `core/eval/expected_extra.json`. All 31 situations and questions, with the
expected answers and source letters, are collected for clinical review in
[`core/eval/test-questions.md`](../core/eval/test-questions.md).

| Set | Result |
|---|---|
| All 31 situations (93 questions), fast screen | 88–90 of 93 correct; mean load time 4.5 s, slowest 7.2 s |
| Team's 72 additional questions (`run_additional`, Opus 5.5 as judge) – chat | 66 of 72 |
| Same – screen, fast model | 42–43 of 72 |
| Same – screen after the Opus upgrade | 51–52 of 72 |

The screen is built from the situation alone and never sees the question, so details outside
what the role needs are often left to the chat, which answers the question itself.

## How to run

Start the core first (see [getting-started.md](getting-started.md)), then from `core/`:

```bash
# The 5 challenge situations (+ the team's situations with --extra), fast path, timed until complete
.venv/bin/python -m eval.run_eval --v2 --cases-dir ../../TRACK-1-clinical-documentation/cases
.venv/bin/python -m eval.run_eval --v2 --extra --cases-dir <challenge>/cases > /tmp/run.txt

# Score one or more runs with a fixed criterion per question
.venv/bin/python eval/score_runs.py /tmp/run.txt

# Team questions: fast screen, upgraded screen and chat, judged by Claude Opus 5.5
.venv/bin/python -m eval.run_additional --out /tmp/additional.json

# Export all situations and questions for clinical review (markdown + CSV)
.venv/bin/python eval/export_questions.py --cases-dir <challenge>/cases --runs /tmp/run.txt
```

`--cases-dir` defaults to `cases/` next to `data/` (the repository root). The challenge's
`cases/` folder holds `situations.md` and `expected.json`.

`run_eval` sends only each situation's title and description – never the check questions – and
records:

- **Load time:** wall clock from submit to the complete screen.
- **Traceability:** for every source on the screen, the passage at `start:end` must contain the
  quoted text.
- **Invented facts:** items the validator dropped or marked "check" (the hallucination monitor).
- **Contradictions:** unique across all screens.
- Optionally (`--judge`) an LLM checks each question against the screen.

Without `--v2`, `run_eval` uses the earlier runtime (`POST /api/screen`, planner + answerer),
kept for comparison.

| File | Content |
|---|---|
| `core/eval/run_eval.py` | Evaluation harness |
| `core/eval/score_runs.py` | Fixed scoring criteria per question |
| `core/eval/run_additional.py` | Team questions with an Opus judge |
| `core/eval/situations_extra.md`, `expected_extra.json` | 26 team situations and expected answers |
| `core/eval/AIMfocus_test_questions_additional.csv` | 72 additional team questions |
| `core/eval/test-questions.md` / `.csv` | All situations and questions for clinical review |
