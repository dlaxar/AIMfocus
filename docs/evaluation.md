# Evaluation

How we measured AIMfocus against the challenge metrics, and how to repeat the measurements.
The summary is in the [README](../README.md#results).

## Results on the 5 challenge situations (2026-10-30)

![Results slide](eval-results.png)

Every screen is built twice in parallel: Claude Haiku 5.5 (no thinking) answers first, Claude
Opus 5.5 (effort low) replaces it with a more thorough screen when it is done. Accuracy is
measured on the screen after the Opus upgrade, speed on the Haiku screen.

`python -m eval.run_eval --v2 --extra`, once waiting for the Haiku screen and once for the
upgraded one (`GET /api/v2/screens/<id>?wait=true&final=true`); answers scored by
`eval/score_runs.py` against the challenge's `cases/expected.json`.

| Metric | Result | Target |
|---|---|---|
| Correct answers | 15 of 15 (Haiku screen and upgraded screen) | 15 of 15 |
| Traceability | 100% of linked sources (278/278 upgraded, 315/315 Haiku) | 100% |
| Invented facts | 0: no item dropped or marked "check" by the validator on the 5 screens | 0 |
| Contradictions | 8 shown, all checked by hand against the letters, no false alarms | 5 or more |
| First response after picking the patient | 1.8 s slowest, median 117 ms (in the app) | under 10 s |

The 8 contradictions are shown on every screen: those that affect a value on the screen are
marked on that value (5 on the challenge screens) and the others are listed below the screen
("other contradictions in the record", `other_contradictions` in the API). `run_eval` counts
only the marked ones (`contradictions`), so it reports 5.

The first response is measured in the real frontend with a headless browser (Playwright,
Chromium): type the situation and press Enter, pick Anna Musterfrau on the patient page, click
Next 4 s after submitting, and time from that click until the first cards are visible on the
dashboard (5 situations × 2 runs).

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
| All 31 situations (93 questions), Haiku screen | 90 of 93 correct |
| Same – screen after the Opus upgrade | 91 of 93 correct |
| Only the 26 team situations (78 questions), after the Opus upgrade | 76 of 78 |
| Team's 72 additional questions (`run_additional`, Opus 5.5 as judge) – chat | 63 of 72 |
| Same – screen, Haiku | 46 of 72 |
| Same – screen after the Opus upgrade | 49 of 72 |

Earlier runs on the same day: 88–90 of 93 (Haiku screen); on the 72 additional questions chat
66, Haiku screen 42–43, upgraded screen 51–52. The differences are within run-to-run variation.

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
- **Contradictions:** unique across all screens (only those marked on a value; see above).
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
| `docs/eval-results.html` / `.png` | Results slide; render command in the HTML |
