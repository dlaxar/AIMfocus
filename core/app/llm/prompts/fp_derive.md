The structured fact store has no direct answer for one variable a clinician needs. Search the raw passages of all German clinical documents for it. The variable is given at the end.

## What counts as an answer
- A passage that states it directly, or
- A **derived concept**: something the text clearly implies about the variable. Examples: "head CT done without contrast because of a known contrast allergy" answers "contrast precautions"; "next carotid duplex planned in 2 months" plus no later report of it answers "follow-up carotid ultrasound status" with "planned, no record that it took place".

## Rules
1. Cite every passage you use in `passage_ids` and copy the relevant words **verbatim** into `quotes` (German, one quote per passage, same order).
2. `value`: one short English line (≤ 30 words). Every number in it must appear in a quote or be the date of the cited letter.
3. If the documents do not contain it, set `found: false`, `value: ""` and leave the lists empty. Never guess.
4. `rationale`: one sentence on how the quotes answer the variable.

## Passages ([passage_id] (document title, date, section) text)
{{passages}}
