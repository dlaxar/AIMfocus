You clean up a list of concept keys that were created one document at a time for one patient record. Some keys may name the same clinical concept in different words (e.g. `dx:hypertension` and `dx:arterial_hypertension`).

## Task
Return groups of keys that mean exactly the same concept. For each group give one `canonical` key (the clearest existing one) and its `aliases`.

## Rules
1. Use only keys from the list, written exactly as listed (without the label).
2. Never group keys with different prefixes (`dx:` with `finding:`, `med:` with `allergy:` …).
3. Never group a broader and a narrower concept (`dx:diabetes` vs `dx:diabetic_retinopathy`), different drugs, or different measurements.
4. When unsure, do not group. Returning no groups is fine.

## Output JSON
```json
{"groups": [{"canonical": "dx:arterial_hypertension", "aliases": ["dx:hypertension"]}]}
```

## Concept keys (key · label)
{{registry}}
