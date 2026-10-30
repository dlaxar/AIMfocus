"""
app/fastpath/shape.py – turns the facts pulled for a variable into the output type the
select call chose (value, trend, table, list), purely in code.

Detail levels for the frontend:
  1. headline – what the collapsed card shows (newest value, or last point and change)
  2. latest + history / series / table rows / list items
  3. on every entry, point and row: its source passage and the contradictions it is part of
"""
from __future__ import annotations

import re
from typing import Optional

from app.fastpath.models import (
    TABLE_COLUMNS,
    Coding,
    ContradictionRef,
    Entry,
    Headline,
    ScreenVariable,
    SelectedVariable,
    Series,
    Table,
    TableRow,
    TrendPoint,
)
from app.runtime.screen import _best_passage_id, _build_source
from app.schemas import DerivedFact, Fact, Source
from app.store import FactStore

MAX_TABLE_COLUMNS = 4
_DEFAULT_COLUMNS = {"medication": ["item", "strength", "schedule"]}
_NUMBER = re.compile(r"^-?\d+(?:[.,]\d+)?$")


# ---------------------------------------------------------------------------
# Per fact: source and contradictions
# ---------------------------------------------------------------------------

def _source(f: Fact | DerivedFact, store: FactStore) -> Optional[Source]:
    """The fact's own passage (a derived fact: its first input's, or the passage it was computed from)."""
    pid = getattr(f, "passage_id", None) or (f.fields.get("passage_id") if f.type == "derived" else None)
    pid = pid or _best_passage_id(f.fact_id, store)
    src = _build_source(pid, store) if pid else None
    if src is not None:
        from app.images import image_ref
        src.image = image_ref(f, store)
    return src


def _cx_ids(fact_id: Optional[str], store: FactStore) -> list[str]:
    return [cx.contradiction_id for cx in store.contradictions if fact_id and fact_id in cx.fact_ids]


_SYSTEM_NAMES = {"http://snomed.info/sct": "SNOMED CT", "http://www.nlm.nih.gov/research/umls/rxnorm": "RxNorm",
                 "http://fhir.de/CodeSystem/bfarm/icd-10-gm": "ICD-10-GM", "http://hl7.org/fhir/sid/icd-10": "ICD-10", "http://www.whocc.no/atc": "ATC", "http://loinc.org": "LOINC"}


def _codes(fact_id: Optional[str], store: FactStore) -> list[Coding]:
    """Standard codes of a fact (ICD-10, ATC, LOINC) – separate from the screen text."""
    seen = {}
    for c in getattr(store, "codes", {}).get(fact_id or "", []):
        seen.setdefault((c["system"], c["code"]), c)
    return [Coding(system=_SYSTEM_NAMES.get(c["system"], c["system"]), code=c["code"], display=c.get("display"), uri=c["system"])
            for c in seen.values()]


def _entry(f: Fact | DerivedFact, store: FactStore) -> Entry:
    status = f.fields.get("status") if f.type == "derived" else f.status
    if f.type == "derived" and f.formula == "followup_status":
        status = "done" if status == "done" else "open"
    return Entry(
        fact_id=f.fact_id, text=(f.en or getattr(f, "span", "")).strip().rstrip("."), date=f.date,
        status=status if status not in (None, "unknown") else None,
        source=_source(f, store), contradiction_ids=_cx_ids(f.fact_id, store), codes=_codes(f.fact_id, store),
    )


def _group_key(f: Fact | DerivedFact) -> str:
    return getattr(f, "concept_id", None) or f.fact_id


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

def _shape_value(var: ScreenVariable, facts: list, store: FactStore) -> None:
    groups: dict[str, list] = {}
    for f in facts:
        groups.setdefault(_group_key(f), []).append(f)
    latest, history = [], []
    for fs in groups.values():
        fs = sorted(fs, key=lambda f: f.date or "", reverse=True)
        latest.append(_entry(fs[0], store))
        history += [_entry(f, store) for f in fs[1:]]
    seen: set[str] = {e.text for e in latest}
    var.latest = latest
    var.history = [e for e in sorted(history, key=lambda e: e.date or "", reverse=True)
                   if not (e.text in seen or seen.add(e.text))]
    dates = [e.date for e in latest if e.date]
    var.headline = Headline(text="; ".join(e.text for e in latest[:3]), date=max(dates) if dates else None)


def _numbers(f: Fact) -> list[tuple[str, float]]:
    """(series name, value) pairs from a measurement's stored value: '135/84' gives two."""
    raw = str(f.fields.get("value", "")).strip()
    if "/" in raw:
        parts = [p.strip() for p in raw.split("/")]
        if len(parts) == 2 and all(_NUMBER.match(p) for p in parts):
            return [("Systolic", float(parts[0].replace(",", "."))), ("Diastolic", float(parts[1].replace(",", ".")))]
    if _NUMBER.match(raw):
        return [("", float(raw.replace(",", ".")))]
    return []


def _series_code(f: Fact, part: str, store: FactStore) -> Optional[Coding]:
    """LOINC of the series: systolic / diastolic for the two blood pressure series, else the concept's."""
    from app.fhir.codes import LOINC_DIASTOLIC, LOINC_SYSTOLIC, loinc_coding
    if part in ("Systolic", "Diastolic"):
        code, display = LOINC_SYSTOLIC if part == "Systolic" else LOINC_DIASTOLIC
        return Coding(system="LOINC", code=code, display=display, uri="http://loinc.org")
    hit = loinc_coding(f.concept_id)
    return Coding(system="LOINC", code=hit[0]["code"], display=hit[0]["display"], uri="http://loinc.org") if hit else None


def _shape_trend(var: ScreenVariable, facts: list, store: FactStore) -> bool:
    series: dict[str, Series] = {}
    seen: set[tuple] = set()
    for f in sorted(facts, key=lambda f: f.date or ""):
        if f.type == "derived" or not f.date:
            continue
        concept = store.concepts.get(f.concept_id or "")
        base = concept.label_en if concept else (f.fields.get("name") or f.en)
        for part, value in _numbers(f):
            name = f"{base} – {part}" if part else base
            if (name, f.date[:10], value) in seen:   # the same reading copied into a later letter
                continue
            seen.add((name, f.date[:10], value))
            series.setdefault(name, Series(name=name, unit=str(f.fields.get("unit", "")), code=_series_code(f, part, store))).points.append(TrendPoint(
                date=f.date, value=value, label=f.fields.get("context") or f.fields.get("name"), fact_id=f.fact_id,
                source=_source(f, store), contradiction_ids=_cx_ids(f.fact_id, store), codes=_codes(f.fact_id, store),
            ))
    if not series:
        return False
    var.series = list(series.values())
    first = var.series[0]
    last = first.points[-1]
    change = None
    if len(first.points) > 1:
        delta = last.value - first.points[0].value
        change = f"{delta:+g} {first.unit} since {first.points[0].date}".replace("  ", " ")
    text = f"{last.value:g}"
    if first.name.endswith("Systolic") and len(var.series) > 1 and var.series[1].name.endswith("Diastolic"):
        dia = next((p for p in var.series[1].points if p.fact_id == last.fact_id), None)
        text += f"/{dia.value:g}" if dia else ""
    var.headline = Headline(text=f"{text} {first.unit}".strip(), date=last.date, change=change)
    return True


def _cell(column: str, f: Fact, store: FactStore, ref: str) -> str:
    fields = f.fields
    if column == "item":
        generic, brand = fields.get("generic"), fields.get("brand")
        if generic:
            return f"{generic.capitalize()} ({brand})" if brand else generic.capitalize()
        return (f.en or f.span).strip().rstrip(".")
    if column == "class":
        return str(fields.get("drug_class", ""))
    if column == "status":
        return "" if f.status == "unknown" else f.status
    if column == "date":
        return f.date or ""
    if column == "value":
        return f"{fields.get('value', '')} {fields.get('unit', '')}".strip()
    if column == "since":
        dates = [g.date for g in store.facts_by_concept(f.concept_id or "") if g.date and (not ref or g.date <= ref)]
        return min(dates) if dates else ""
    if column == "last_change":
        changes = [d for d in store.derived.values()
                   if d.formula and "change" in d.formula and set(d.inputs) & {g.fact_id for g in store.facts_by_concept(f.concept_id or "")}]
        return changes[-1].en.split(": ", 1)[-1] if changes else ""
    return str(fields.get(column, ""))


def _shape_table(var: ScreenVariable, sel: SelectedVariable, facts: list, store: FactStore, ref: str) -> bool:
    # A derived restatement ("Current medication: X") stands for its input fact here
    rows_facts: dict[str, Fact] = {}
    for f in facts:
        for g in ([store.get_fact(i) for i in f.inputs] if f.type == "derived" else [f]):
            if g is None or g.type == "derived":
                continue
            key = g.concept_id or g.fact_id
            if key not in rows_facts or (g.date or "") > (rows_facts[key].date or ""):
                rows_facts[key] = g
    if not rows_facts:
        return False
    kinds = {f.type for f in rows_facts.values()}
    default = _DEFAULT_COLUMNS.get(next(iter(kinds)), ["item", "date"]) if len(kinds) == 1 else ["item", "date"]
    columns = [c for c in dict.fromkeys(sel.columns) if c in TABLE_COLUMNS] or default
    columns = (["item"] + [c for c in columns if c != "item"])[:MAX_TABLE_COLUMNS]
    var.table = Table(columns=columns, rows=[
        TableRow(cells={c: _cell(c, f, store, ref) for c in columns}, fact_id=f.fact_id, date=f.date,
                 source=_source(f, store), contradiction_ids=_cx_ids(f.fact_id, store), codes=_codes(f.fact_id, store))
        for f in sorted(rows_facts.values(), key=lambda f: _cell("item", f, store, ref))
    ])
    var.headline = Headline(text=f"{len(var.table.rows)} item(s): " + ", ".join(r.cells["item"] for r in var.table.rows[:3]),
                            date=max((r.date for r in var.table.rows if r.date), default=None))
    return True


def _shape_list(var: ScreenVariable, facts: list, store: FactStore) -> None:
    # A diagnosis or allergy repeated in every letter is one item (its newest mention);
    # other items only lose word-for-word repeats
    items, seen = [], set()
    for f in sorted(facts, key=lambda f: f.date or "", reverse=True):
        key = f.concept_id if f.type in ("diagnosis", "allergy") and getattr(f, "concept_id", None) else (f.en or "").lower()
        if key not in seen:
            seen.add(key)
            items.append(_entry(f, store))
    var.items = items
    open_items = [e for e in var.items if e.status == "open"]
    first = open_items[0] if open_items else var.items[0]
    more = f" (+{len(var.items) - 1} more)" if len(var.items) > 1 else ""
    var.headline = Headline(text=first.text + more, date=first.date)


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

def shape_documented(var: ScreenVariable, sel: SelectedVariable, facts: list, store: FactStore, ref: str) -> None:
    """Fill the output-type fields of a variable answered from the fact store."""
    if var.output == "trend" and _shape_trend(var, facts, store):
        pass
    elif var.output == "table" and _shape_table(var, sel, facts, store, ref):
        pass
    elif var.output in ("list", "image"):   # image: one item per study, its source carries the image links
        _shape_list(var, facts, store)
    else:
        var.output = "value"   # also when a trend has no numbers or a table no rows
        _shape_value(var, facts, store)


def shape_text_result(var: ScreenVariable) -> None:
    """A variable answered by the text search (or confirmed missing) as one value."""
    if var.state == "derived":
        var.output = "value"
        var.latest = [Entry(text=var.value, source=var.sources[0] if var.sources else None,
                            contradiction_ids=list(var.contradiction_ids))]
        var.headline = Headline(text=var.value, date=var.sources[0].doc_date if var.sources else None)
    elif var.state == "not_documented":
        var.headline = Headline(text="Not documented")
    elif var.state == "check":
        var.headline = Headline(text="Could not be verified against the documents")


def contradiction_refs(var: ScreenVariable, store: FactStore) -> list[ContradictionRef]:
    ids = set(var.contradiction_ids)
    for e in var.latest + var.history + var.items:
        ids.update(e.contradiction_ids)
    for s in var.series:
        for p in s.points:
            ids.update(p.contradiction_ids)
    if var.table:
        for r in var.table.rows:
            ids.update(r.contradiction_ids)
    return [ContradictionRef(contradiction_id=cx.contradiction_id, kind=cx.kind, summary_en=cx.summary_en)
            for cx in store.contradictions if cx.contradiction_id in ids]
