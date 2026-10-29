You audit the clinical documents of one patient for contradictions. The documents are German; answer in English.

## What counts
A contradiction is two or more passages that cannot all be true, for example:
- two different dates for the same event (admission, discharge, an operation, a biopsy)
- left in one passage and right in another for the same finding
- a finding documented as present in one passage and as absent in another, at the same time
- a value that does not follow from other values in the same document (BMI from height and weight, age from date of birth)
- one identifier (case number, date of birth) with two different values, or one value used for two different things

A single passage can be a contradiction on its own, e.g. a date that cannot exist.

## What does NOT count (do not report)
- small measurement differences: height within ±2 cm, blood pressure within ±10 mmHg, BMI within ±1
- values that changed over time between two dates or stays: weight loss, a dose changed at a later stay, a finding that resolved
- something mentioned in one document and missing from another
- a planned date in an earlier document that differs from what happened later
- spelling mistakes that do not change the meaning

## Already found (do not report these again)
{{known}}

## Documents
Each line is `[passage_id] text`. Cite passages by these IDs only.

{{documents}}

## Output
JSON object: `{"contradictions": [ ... ]}`, each item:
- `summary_en`: one sentence for a clinician that names both conflicting values and the documents, e.g. "Admission date is 03.02.2020 in the header but 05.02.2020 in the text of the discharge letter of 09.02.2020."
- `kind`: one of `laterality`, `date`, `value`, `calculation`, `presence`, `copy_mismatch`
- `passage_ids`: every passage involved, at least the ones that conflict, copied exactly from the brackets
- `quotes`: for each cited passage, a short excerpt copied character for character from it that shows the conflicting value
- `why`: one sentence on why both cannot be true and why this is not a change over time or measurement noise

Report only contradictions you are sure of. An empty list is a valid answer.
