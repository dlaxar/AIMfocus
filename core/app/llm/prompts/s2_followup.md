You check whether a recommendation from a clinical letter was carried out later.

## Recommendation (from a document dated {{recommendation_date}})
{{recommendation}}

## Facts from LATER documents (fact_id | type | concept | description | status | date)
{{later_facts}}

## Task
Decide whether any of the later facts records that this recommendation was done (for example the recommended examination with its result, or the recommended check-up taking place).

## Rules
1. `status: "done"` only if a later fact clearly records it. List those fact IDs in `evidence_fact_ids`, copied exactly from the list.
2. `status: "no_record"` if no later fact records it. A later mention of the same diagnosis, or a repeated recommendation, does NOT count as done.
3. Never cite a fact ID that is not in the list above.
4. `rationale`: one sentence.

## Output JSON
```json
{"status": "done" | "no_record", "evidence_fact_ids": ["..."], "rationale": "..."}
```
