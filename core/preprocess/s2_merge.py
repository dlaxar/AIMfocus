"""
preprocess/s2_merge.py – S2: Concept resolution, timelines, and derived facts.

Steps:
  1. Concept resolution: use the concept_id S1 assigned (controlled vocabulary + registry);
     facts without one get a key from the same vocabulary. Laterality and negation are
     never part of a key, so left/right and positive/negative facts share a concept.
  2. Build timelines per concept, sorted by date.
  3. Determine reference date (latest document date).
  4. Compute derived facts: current med list, med changes, weight change, BMI recompute.
  5. Follow-up status: for each recommendation, a small LLM step checks whether a later
     document records it as done.

Reads: facts.raw.json, documents.json
Writes: facts.json, concepts.json, derived.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.config import settings
from app.llm.client import get_client
from app.schemas import (
    Concept,
    DerivedFact,
    Fact,
    TimelinePoint,
)
from preprocess.vocab import normalize_concept_key, normalize_measurement, slug

logger = logging.getLogger(__name__)

_FOLLOWUP_PROMPT_PATH = Path(__file__).parent.parent / "app" / "llm" / "prompts" / "s2_followup.md"

WEIGHT = "C:meas:body_weight"
HEIGHT = "C:meas:body_height"
BMI = "C:meas:bmi"


def _doc_id(fact: Fact) -> str:
    return fact.passage_id.split(":")[0]


def _to_number(value: Any) -> Optional[float]:
    """First number in a value such as 66, "66 kg", "22,8" or "29.4"; None if there is none."""
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"\d+(?:[.,]\d+)?", str(value or ""))
    return float(m.group().replace(",", ".")) if m else None


def _fmt(x: float) -> str:
    return f"{x:g}"


# ---------------------------------------------------------------------------
# Concept resolution
# ---------------------------------------------------------------------------

def _fallback_concept_id(fact: Fact) -> Optional[str]:
    """Concept key for facts S1 left without one, built with the same vocabulary."""
    f = fact.fields
    if fact.type == "medication":
        return normalize_concept_key(f.get("generic") or f.get("brand") or "", "medication")
    if fact.type == "measurement":
        return normalize_concept_key(f.get("name") or f.get("label") or "", "measurement")
    if fact.type == "allergy":
        return normalize_concept_key(f.get("substance") or "", "allergy")
    candidate = f.get("label") or f.get("name") or f.get("action") or fact.en
    return normalize_concept_key((candidate or "")[:60], fact.type)


def _concept_label(fact: Fact, concept_id: str) -> str:
    f = fact.fields
    label = f.get("generic") or f.get("substance") or f.get("label") or f.get("action")
    if label:
        return str(label)
    return concept_id.rsplit(":", 1)[-1].replace("_", " ").capitalize()


def _resolve_concepts(facts: list[Fact]) -> tuple[list[Fact], dict[str, Concept]]:
    """Assign concept_ids to facts and build the concept map."""
    concepts: dict[str, Concept] = {}
    updated_facts: list[Fact] = []

    for fact in facts:
        cid = fact.concept_id or _fallback_concept_id(fact)
        if not cid:
            updated_facts.append(fact)
            continue
        if cid not in concepts:
            concepts[cid] = Concept(concept_id=cid, label_en=_concept_label(fact, cid), type=fact.type)
        if fact.fact_id not in concepts[cid].fact_ids:
            concepts[cid].fact_ids.append(fact.fact_id)
        updated_facts.append(fact.model_copy(update={"concept_id": cid}) if cid != fact.concept_id else fact)

    return updated_facts, concepts


# ---------------------------------------------------------------------------
# Timeline building
# ---------------------------------------------------------------------------

def _build_timelines(facts: list[Fact], concepts: dict[str, Concept]) -> dict[str, Concept]:
    """Add timeline series to each concept from its facts."""
    fact_map = {f.fact_id: f for f in facts}

    for concept in concepts.values():
        series: list[TimelinePoint] = []
        for fid in concept.fact_ids:
            fact = fact_map.get(fid)
            if fact is None:
                continue
            value = None
            unit = None
            if fact.type == "measurement":
                raw = fact.fields.get("value")
                num = _to_number(raw)
                # Keep composite values like "145/88" as text
                value = num if num is not None and re.fullmatch(r"\s*\d+(?:[.,]\d+)?\s*", str(raw)) else raw
                unit = fact.fields.get("unit")
            elif fact.type == "medication":
                value = fact.fields.get("schedule")
                unit = fact.fields.get("strength")
            series.append(TimelinePoint(date=fact.date, value=value, unit=unit, fact_id=fid))

        series.sort(key=lambda p: p.date or "")
        concept.series = series

    return concepts


# ---------------------------------------------------------------------------
# Reference date
# ---------------------------------------------------------------------------

def _latest_document_date(documents: list[dict]) -> Optional[str]:
    dates = [d.get("date") for d in documents if d.get("date")]
    return max(dates) if dates else None


# ---------------------------------------------------------------------------
# Derived facts
# ---------------------------------------------------------------------------

def _derive_current_meds(facts: list[Fact], reference_date: str, concepts: dict[str, Concept]) -> list[DerivedFact]:
    """
    Current medication = latest status per concept with status in (current, started, changed)
    as of reference_date.
    """
    derived: list[DerivedFact] = []
    for cid, concept in concepts.items():
        if not cid.startswith("C:med:"):
            continue
        current_facts = [
            f for f in facts
            if f.concept_id == cid
            and f.type == "medication"
            and f.status in ("current", "started", "changed")
            and (f.date or "") <= reference_date
        ]
        if not current_facts:
            continue
        latest = max(current_facts, key=lambda f: f.date or "")
        derived.append(DerivedFact(
            fact_id=f"D:curmed:{slug(concept.label_en)}",
            en=f"Current medication: {latest.en}",
            inputs=[latest.fact_id],
            formula="latest_current_medication",
            date=reference_date,
        ))
    return derived


def _derive_weight_change(facts: list[Fact], concepts: dict[str, Concept]) -> list[DerivedFact]:
    """Weight change between the first and the last documented weight."""
    if WEIGHT not in concepts:
        return []
    series = [(p, _to_number(p.value)) for p in concepts[WEIGHT].series]
    series = [(p, v) for p, v in series if v is not None]
    if len(series) < 2:
        return []

    (first, w0), (last, w1) = series[0], series[-1]
    delta = w1 - w0
    sign = "+" if delta >= 0 else ""
    return [DerivedFact(
        fact_id=f"D:weight_change:{first.date}_{last.date}",
        en=(
            f"Weight change {sign}{_fmt(round(delta, 1))} kg "
            f"({_fmt(w0)} kg → {_fmt(w1)} kg, {first.date} to {last.date})"
        ),
        inputs=[first.fact_id, last.fact_id],
        formula="last - first",
        fields={"delta_kg": round(delta, 1), "first_kg": w0, "last_kg": w1},
        date=last.date,
    )]


def _derive_bmi(facts: list[Fact]) -> list[DerivedFact]:
    """Recompute BMI from weight and height facts in the same document."""
    derived: list[DerivedFact] = []
    w_by_doc: dict[str, Fact] = {}
    h_by_doc: dict[str, Fact] = {}
    for f in facts:
        if f.concept_id == WEIGHT:
            w_by_doc.setdefault(_doc_id(f), f)
        elif f.concept_id == HEIGHT:
            h_by_doc.setdefault(_doc_id(f), f)

    for doc_id in sorted(set(w_by_doc) & set(h_by_doc)):
        wf, hf = w_by_doc[doc_id], h_by_doc[doc_id]
        w_kg = _to_number(wf.fields.get("value"))
        h = _to_number(hf.fields.get("value"))
        if not w_kg or not h:
            continue
        h_m = h / 100 if h > 3 else h  # cm or metres
        bmi = round(w_kg / (h_m ** 2), 1)
        derived.append(DerivedFact(
            fact_id=f"D:bmi:{doc_id}",
            en=f"Computed BMI {_fmt(bmi)} kg/m² ({_fmt(w_kg)} kg / {_fmt(h)} cm)",
            inputs=[wf.fact_id, hf.fact_id],
            formula="weight_kg / (height_m ** 2)",
            fields={"bmi": bmi, "weight_kg": w_kg, "height_cm": h},
            date=wf.date,
        ))
    return derived


_DAYTIME = {"morgens": "1-0-0", "früh": "1-0-0", "mittags": "0-1-0", "abends": "0-0-1", "nachts": "0-0-0-1"}


def _norm_strength(s: str) -> str:
    """'100 mg' = '100mg', '5 mcg' = '5µg', '0,5' = '0.5'."""
    s = str(s).lower().replace(" ", "").replace(",", ".").replace("µg", "mcg").replace("ug", "mcg")
    return s


def _norm_schedule(s: str) -> str:
    """Same dosing written differently: 'morgens' = '1-0-0', '1-0-0-0' = '1-0-0',
    '1x/Woche-1-Tabl.' = '1x/week-1-tablet', '2-0-2-0-drops' = '2-0-2'."""
    s = str(s).lower().strip()
    s = _DAYTIME.get(s, s)
    s = s.replace("woche", "week").replace("wöchentlich", "1x/week").replace("tabl.", "tablet").replace("tablette", "tablet")
    s = re.sub(r"[-\s]*(drops?|tropfen|doses?|hübe|puffs?|tablets?|stk\.?)\b", "", s)
    s = re.sub(r"\s+", "", s)
    m = re.fullmatch(r"(\d+(?:/\d+)?-\d+(?:/\d+)?-\d+(?:/\d+)?)-0", s)   # four slots, last one empty
    return m.group(1) if m else s


def _differs(a: str, b: str, norm) -> bool:
    """A change only when both sides state the value and they differ after normalising."""
    return bool(a) and bool(b) and norm(a) != norm(b)


def _derive_med_changes(facts: list[Fact], concepts: dict[str, Concept]) -> list[DerivedFact]:
    """Detect schedule/strength changes for each medication concept."""
    derived: list[DerivedFact] = []

    for cid, concept in concepts.items():
        if not cid.startswith("C:med:"):
            continue
        med_facts = sorted(
            [f for f in facts if f.concept_id == cid and f.type == "medication"],
            key=lambda f: f.date or "",
        )
        for prev, curr in zip(med_facts, med_facts[1:]):
            if prev.date == curr.date:
                continue
            prev_sched = prev.fields.get("schedule", "")
            curr_sched = curr.fields.get("schedule", "")
            prev_str = prev.fields.get("strength", "")
            curr_str = curr.fields.get("strength", "")
            if _differs(prev_sched, curr_sched, _norm_schedule) or _differs(prev_str, curr_str, _norm_strength):
                label = concept.label_en
                derived.append(DerivedFact(
                    fact_id=f"D:medchange:{slug(label)}:{prev.date}_{curr.date}",
                    en=(
                        f"{label.title()} changed: "
                        f"{prev_str} {prev_sched} ({prev.date}) → "
                        f"{curr_str} {curr_sched} ({curr.date})"
                    ),
                    inputs=[prev.fact_id, curr.fact_id],
                    formula="schedule_or_strength_change",
                    date=curr.date,
                ))

    return derived


# ---------------------------------------------------------------------------
# Follow-up status (small LLM step)
# ---------------------------------------------------------------------------

class FollowupOutput(BaseModel):
    status: str = ""                 # "done" | "no_record"; empty = no answer
    evidence_fact_ids: list[str] = Field(default_factory=list)
    rationale: str = ""


def _fact_line(f: Fact) -> str:
    return f"{f.fact_id} | {f.type} | {f.concept_id or '-'} | {f.en} | {f.status} | {f.date or 'unknown'}"


def _is_recommendation(f: Fact) -> bool:
    return f.type == "recommendation" or f.status == "recommended"


async def _derive_followups(facts: list[Fact], doc_dates: dict[str, str], model: str) -> list[DerivedFact]:
    """
    For each recommendation: does a fact from a LATER document record it as done?
    The answer must cite existing later facts; otherwise no derived fact is written,
    so the app never claims a status the LLM could not support.
    """
    derived: list[DerivedFact] = []
    recs = [f for f in facts if _is_recommendation(f)]
    if not recs:
        return derived

    client = get_client()
    template = _FOLLOWUP_PROMPT_PATH.read_text(encoding="utf-8")
    fact_map = {f.fact_id: f for f in facts}
    all_docs = sorted(doc_dates.items(), key=lambda kv: kv[1] or "")

    sem = asyncio.Semaphore(max(1, settings.PREPROCESS_MAX_PARALLEL))

    async def one(rec: Fact) -> Optional[DerivedFact]:
        rec_date = doc_dates.get(_doc_id(rec), "") or ""
        later_docs = [d for d, date in all_docs if (date or "") > rec_date]
        later = [f for f in facts if _doc_id(f) in later_docs and f.fact_id != rec.fact_id]
        checked = f"{len(later_docs)} later document(s)"

        if not later:
            status, evidence = "no_record", []
        else:
            prompt = (
                template
                .replace("{{recommendation}}", _fact_line(rec))
                .replace("{{recommendation_date}}", rec_date or "unknown")
                .replace("{{later_facts}}", "\n".join(_fact_line(f) for f in later))
            )
            async with sem:
                out: FollowupOutput = await client.generate(
                    prompt_name="s2_followup",
                    messages=[{"role": "user", "content": prompt}],
                    response_schema=FollowupOutput,
                    temperature=0.0,
                    model=model,
                )
            status = out.status
            evidence = [fid for fid in out.evidence_fact_ids if fid in fact_map and fid in {f.fact_id for f in later}]
            if status == "done" and not evidence:
                logger.warning("S2: follow-up for %s claimed done without valid evidence – skipped", rec.fact_id)
                return None
            if status not in ("done", "no_record"):
                logger.warning("S2: no follow-up answer for %s – skipped", rec.fact_id)
                return None

        what = rec.en or rec.span
        if status == "done":
            done_by = "; ".join(fact_map[fid].en for fid in evidence)
            en = f"Follow-up done: {what} → {done_by}"
        else:
            en = f"Follow-up open: {what} – no record in the {checked}"
        return DerivedFact(
            fact_id=f"D:followup:{slug(rec.fact_id)}",
            en=en,
            inputs=[rec.fact_id] + evidence,
            formula="followup_status",
            fields={"status": status, "rec_fact_id": rec.fact_id, "evidence": evidence},
            date=max(doc_dates.values()) if doc_dates else None,
        )

    # Independent calls: run them in parallel (bounded), keep recommendation order
    derived.extend(d for d in await asyncio.gather(*(one(rec) for rec in recs)) if d)
    return derived


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_async(cache_dir: Path, patient_id: str) -> tuple[list[Fact], dict[str, Concept], list[DerivedFact]]:
    out_dir = cache_dir / patient_id

    facts = [
        Fact.model_validate(f)
        for f in json.loads((out_dir / "facts.raw.json").read_text(encoding="utf-8"))
    ]
    documents = json.loads((out_dir / "documents.json").read_text(encoding="utf-8"))
    doc_dates = {d["doc_id"]: d.get("date") or "" for d in documents}

    facts, concepts = _resolve_concepts(facts)
    concepts = _build_timelines(facts, concepts)

    reference_date = _latest_document_date(documents) or ""

    derived: list[DerivedFact] = []
    derived.extend(_derive_weight_change(facts, concepts))
    derived.extend(_derive_bmi(facts))
    derived.extend(_derive_med_changes(facts, concepts))
    derived.extend(_derive_current_meds(facts, reference_date, concepts))
    derived.extend(await _derive_followups(facts, doc_dates, settings.PREPROCESS_MODEL))

    logger.info(
        "S2: %d facts, %d concepts, %d derived facts",
        len(facts), len(concepts), len(derived),
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "facts.json").write_text(
        json.dumps([f.model_dump() for f in facts], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "concepts.json").write_text(
        json.dumps([c.model_dump() for c in concepts.values()], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "derived.json").write_text(
        json.dumps([d.model_dump() for d in derived], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("S2: wrote facts.json, concepts.json, derived.json")
    return facts, concepts, derived


def run(cache_dir: Path, patient_id: str) -> tuple[list[Fact], dict[str, Concept], list[DerivedFact]]:
    return asyncio.run(run_async(cache_dir, patient_id))
