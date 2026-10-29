You clean up the concept keys of one patient record. The documents were processed independently, so the same clinical concept may have received different keys in different documents.

## Task
Return groups of keys that name the same clinical concept. For each group give one `canonical` key (the clearest existing one) and its `aliases`.

## Rules
1. Group keys that name the same clinical concept, whatever its type: the same diagnosis, finding, procedure, medication, measurement or event. Wording, language, abbreviations and level of detail may differ (one document may describe it more specifically than another).
2. Keep keys apart when they name different things, even if related, e.g. a procedure and its result, a drug and its drug class, or a disease and a complication of it.
3. Values, dates, sides (left/right) and negation are not part of a concept (they are stored on the facts), so they are never a reason to keep keys apart. Different anatomical sites are different concepts.
4. Only group keys with the same prefix (the part before the first colon). Use only keys from the list, written exactly as listed.
5. Return only the groups. Keys that stay alone are not listed.

## Output JSON
{"groups": [{"canonical": "<key>", "aliases": ["<key>", ...]}]}

## Concept keys
One per line: key · label · documents · one text it was assigned to
{{registry}}
