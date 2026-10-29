"""
preprocess/s3_contradictions.py – S3: Contradiction detection.

Steps:
  1. Rule-based candidate generation (laterality, BMI recalculation, event dates,
     polarity, copy mismatch, malformed dates).
  2. LLM judge for each candidate; only "contradiction" above the threshold is kept.
  3. Confirmed contradictions that share a passage are merged, so each counts once.

Reads: facts.json, concepts.json, passages.json, documents.json, derived.json
Writes: contradictions.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date as _date
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from app.config import settings
from app.llm.client import get_client
from app.schemas import (
    Concept,
    Contradiction,
    ContradictionJudge,
    ContradictionKind,
    DerivedFact,
    Fact,
    Passage,
)

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "app" / "llm" / "prompts" / "s3_judge.md"

BMI = "C:meas:bmi"
WEIGHT = "C:meas:body_weight"
HEIGHT = "C:meas:body_height"

# Event/procedure dates closer than this are taken to describe the same event
_SAME_EVENT_DAYS = 14


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower())
    return s.strip("_")[:max_len]


def _doc_id(passage_id: str) -> str:
    return passage_id.split(":")[0]


def _doc_ref(doc_id: str, doc_map: dict[str, dict]) -> str:
    """'Stat. Patientenbrief (2016-04-15)' – how a clinician recognises a document."""
    doc = doc_map.get(doc_id, {})
    return f"{doc.get('title') or doc_id} ({doc.get('date') or 'undated'})"


def _parse_day(value: Optional[str]) -> Optional[_date]:
    if not value or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return _date.fromisoformat(value)
    except ValueError:
        return None


def _passage_text_block(passage_ids: list[str], passage_map: dict[str, Passage], doc_map: dict[str, dict]) -> str:
    lines = []
    for pid in passage_ids:
        p = passage_map.get(pid)
        if p is None:
            continue
        lines.append(f"[{pid}] ({_doc_ref(p.doc_id, doc_map)}): {' '.join(p.text.split())}")
    return "\n".join(lines)


def _concepts_on_passages(passage_ids: list[str], facts: list[Fact]) -> list[str]:
    wanted = set(passage_ids)
    return sorted({f.concept_id for f in facts if f.passage_id in wanted and f.concept_id})


# ---------------------------------------------------------------------------
# Rule-based candidate generation
# ---------------------------------------------------------------------------

CandidateTuple = tuple[str, ContradictionKind, list[str], list[str], str]  # (summary, kind, passage_ids, concept_ids, slug)


def _candidates_laterality(facts: list[Fact], concepts: dict[str, Concept], doc_map: dict[str, dict]) -> list[CandidateTuple]:
    """Same concept documented on the left in one place and on the right in another."""
    candidates: list[CandidateTuple] = []
    fact_map = {f.fact_id: f for f in facts}
    for cid, concept in concepts.items():
        by_side: dict[str, list[Fact]] = {"left": [], "right": []}
        for fid in concept.fact_ids:
            f = fact_map.get(fid)
            # A negated finding on the other side ("no stenosis on the right") is no conflict
            if f and f.laterality in by_side and f.status != "negative":
                by_side[f.laterality].append(f)
        if not by_side["left"] or not by_side["right"]:
            continue
        sides = []
        for side, side_facts in by_side.items():
            docs = sorted({_doc_ref(_doc_id(f.passage_id), doc_map) for f in side_facts})
            sides.append(f"{side} in {', '.join(docs)}")
        summary = f"{concept.label_en}: documented as " + " but as ".join(sides) + "."
        passage_ids = list(dict.fromkeys(f.passage_id for fs in by_side.values() for f in fs))
        candidates.append((summary, "laterality", passage_ids, [cid], f"lat_{_slug(cid)}"))
    return candidates


def _candidates_bmi(derived: list[DerivedFact], facts: list[Fact], doc_map: dict[str, dict]) -> list[CandidateTuple]:
    """Recomputed BMI ≠ stated BMI in the same document (tolerance ±0.5)."""
    candidates: list[CandidateTuple] = []
    fact_map = {f.fact_id: f for f in facts}
    stated_bmi = [f for f in facts if f.concept_id == BMI]

    for db in derived:
        if not db.fact_id.startswith("D:bmi:"):
            continue
        doc_id = db.fact_id.split(":")[-1]
        stated = next((f for f in stated_bmi if _doc_id(f.passage_id) == doc_id), None)
        if stated is None:
            continue
        m = re.search(r"\d+(?:[.,]\d+)?", str(stated.fields.get("value", "")))
        computed = db.fields.get("bmi")
        if not m or computed is None:
            continue
        stated_val = float(m.group().replace(",", "."))
        if abs(stated_val - float(computed)) <= 0.5:
            continue
        passage_ids = [fact_map[fid].passage_id for fid in db.inputs if fid in fact_map] + [stated.passage_id]
        summary = (
            f"BMI {stated_val:g} stated in {_doc_ref(doc_id, doc_map)}, but the weight "
            f"({db.fields.get('weight_kg'):g} kg) and height ({db.fields.get('height_cm'):g} cm) "
            f"in the same letter give a BMI of {float(computed):.1f}."
        )
        candidates.append((summary, "calculation", list(dict.fromkeys(passage_ids)), [BMI, WEIGHT, HEIGHT], f"bmi_{doc_id}"))
    return candidates


def _candidates_event_dates(facts: list[Fact], concepts: dict[str, Concept], doc_map: dict[str, dict]) -> list[CandidateTuple]:
    """
    Same event or procedure with two different exact dates close together,
    e.g. admission "10/04/2016" in the header vs "am 12.04.2016 aufgenommen" in the text.
    """
    candidates: list[CandidateTuple] = []
    fact_map = {f.fact_id: f for f in facts}
    for cid, concept in concepts.items():
        if not (cid.startswith("C:event:") or cid.startswith("C:proc:")):
            continue
        by_day: dict[_date, list[Fact]] = {}
        for fid in concept.fact_ids:
            f = fact_map.get(fid)
            day = _parse_day(f.date) if f else None
            if day:
                by_day.setdefault(day, []).append(f)  # type: ignore[arg-type]
        days = sorted(by_day)
        for d1, d2 in zip(days, days[1:]):
            if (d2 - d1).days > _SAME_EVENT_DAYS:
                continue
            f1, f2 = by_day[d1], by_day[d2]
            where1 = ", ".join(sorted({_doc_ref(_doc_id(f.passage_id), doc_map) for f in f1}))
            where2 = ", ".join(sorted({_doc_ref(_doc_id(f.passage_id), doc_map) for f in f2}))
            summary = f"{concept.label_en}: dated {d1:%d.%m.%Y} in {where1} but {d2:%d.%m.%Y} in {where2}."
            passage_ids = list(dict.fromkeys(f.passage_id for f in f1 + f2))
            candidates.append((summary, "date", passage_ids, [cid], f"date_{_slug(cid)}_{d1:%Y%m%d}_{d2:%Y%m%d}"))
    return candidates


def _candidates_polarity(facts: list[Fact], concepts: dict[str, Concept], doc_map: dict[str, dict]) -> list[CandidateTuple]:
    """Same diagnosis or finding documented as present and as absent."""
    candidates: list[CandidateTuple] = []
    fact_map = {f.fact_id: f for f in facts}

    for cid, concept in concepts.items():
        if concept.type not in ("diagnosis", "finding"):
            continue
        members = [fact_map[fid] for fid in concept.fact_ids if fid in fact_map]
        pos = [f for f in members if f.status in ("positive", "suspected")]
        neg = [f for f in members if f.status == "negative"]
        if not pos or not neg:
            continue
        pos_docs = ", ".join(sorted({_doc_ref(_doc_id(f.passage_id), doc_map) for f in pos}))
        neg_docs = ", ".join(sorted({_doc_ref(_doc_id(f.passage_id), doc_map) for f in neg}))
        summary = f"{concept.label_en}: documented as present in {pos_docs} but as absent in {neg_docs}."
        passage_ids = list(dict.fromkeys(f.passage_id for f in pos + neg))
        candidates.append((summary, "presence", passage_ids, [cid], f"polarity_{_slug(cid)}"))
    return candidates


def _candidates_copy_mismatch(passages: list[Passage], facts: list[Fact], doc_map: dict[str, dict]) -> list[CandidateTuple]:
    """Passages copied from another document whose text differs from the original."""
    passage_map = {p.passage_id: p for p in passages}
    candidates: list[CandidateTuple] = []

    for p in passages:
        if p.copy_of is None:
            continue
        orig = passage_map.get(p.copy_of)
        if orig is None:
            continue
        if " ".join(p.text.lower().split()) == " ".join(orig.text.lower().split()):
            continue
        summary = (
            f"Text copied into {_doc_ref(p.doc_id, doc_map)} differs from the original in "
            f"{_doc_ref(orig.doc_id, doc_map)}: \"{' '.join(p.text.split())[:120]}\" vs "
            f"\"{' '.join(orig.text.split())[:120]}\"."
        )
        passage_ids = [p.passage_id, p.copy_of]
        candidates.append((
            summary, "copy_mismatch", passage_ids,
            _concepts_on_passages(passage_ids, facts), f"copy_{_slug(p.passage_id)}",
        ))
    return candidates


def _candidates_malformed_dates(passages: list[Passage], facts: list[Fact], doc_map: dict[str, dict]) -> list[CandidateTuple]:
    """Dates that cannot exist, e.g. a five-digit year."""
    candidates: list[CandidateTuple] = []
    # "(?!:)" skips a date glued to a time ("12.06.201511:02"): a missing space, not a wrong date
    bad_date = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{5,}\b(?!:)")
    for p in passages:
        m = bad_date.search(p.text)
        if not m:
            continue
        summary = f"Impossible date \"{m.group()}\" in {_doc_ref(p.doc_id, doc_map)}: \"{' '.join(p.text.split())[:100]}\"."
        candidates.append((
            summary, "date", [p.passage_id],
            _concepts_on_passages([p.passage_id], facts), f"malformed_date_{_slug(p.passage_id)}",
        ))
    return candidates


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

class JudgeOutput(BaseModel):
    verdict: str = "not_related"
    confidence: float = 0.0
    rationale: str = ""


async def _judge(candidate: CandidateTuple, passage_map: dict[str, Passage], doc_map: dict[str, dict]) -> Optional[Contradiction]:
    summary, kind, passage_ids, concept_ids, slug = candidate
    prompt = (
        _load_prompt()
        .replace("{{passages}}", _passage_text_block(passage_ids, passage_map, doc_map))
        .replace("{{kind}}", kind)
        .replace("{{summary}}", summary)
    )

    output: JudgeOutput = await get_client().generate(
        prompt_name="s3_judge",
        messages=[{"role": "user", "content": prompt}],
        response_schema=JudgeOutput,
        temperature=0.0,
        model=settings.PREPROCESS_MODEL,
    )

    if output.verdict != "contradiction" or output.confidence < settings.CONTRADICTION_CONFIDENCE_THRESHOLD:
        logger.debug("Judge rejected candidate '%s': %s (%.2f)", slug, output.verdict, output.confidence)
        return None

    return Contradiction(
        contradiction_id=f"X:{slug}",
        summary_en=summary,
        kind=kind,
        passage_ids=passage_ids,
        concept_ids=concept_ids,
        judge=ContradictionJudge(
            verdict="contradiction",
            confidence=output.confidence,
            rationale=output.rationale,
        ),
    )


def _merge_overlapping(contradictions: list[Contradiction]) -> list[Contradiction]:
    """Fold contradictions that share a passage into the first one (each counts once)."""
    merged: list[Contradiction] = []
    for cx in contradictions:
        target = next((m for m in merged if set(m.passage_ids) & set(cx.passage_ids)), None)
        if target is None:
            merged.append(cx)
            continue
        target.passage_ids = list(dict.fromkeys(target.passage_ids + cx.passage_ids))
        target.concept_ids = list(dict.fromkeys(target.concept_ids + cx.concept_ids))
    return merged


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_candidates(
    facts: list[Fact],
    concepts: dict[str, Concept],
    passages: list[Passage],
    derived: list[DerivedFact],
    doc_map: dict[str, dict],
) -> list[CandidateTuple]:
    """All rule-based candidates, most specific rules first (they win when merging)."""
    candidates: list[CandidateTuple] = []
    candidates.extend(_candidates_laterality(facts, concepts, doc_map))
    candidates.extend(_candidates_bmi(derived, facts, doc_map))
    candidates.extend(_candidates_event_dates(facts, concepts, doc_map))
    candidates.extend(_candidates_polarity(facts, concepts, doc_map))
    candidates.extend(_candidates_copy_mismatch(passages, facts, doc_map))
    candidates.extend(_candidates_malformed_dates(passages, facts, doc_map))
    return candidates


async def run_async(cache_dir: Path, patient_id: str) -> list[Contradiction]:
    out_dir = cache_dir / patient_id

    facts = [Fact.model_validate(f) for f in json.loads((out_dir / "facts.json").read_text(encoding="utf-8"))]
    passages = [Passage.model_validate(p) for p in json.loads((out_dir / "passages.json").read_text(encoding="utf-8"))]
    documents_raw = json.loads((out_dir / "documents.json").read_text(encoding="utf-8"))
    derived = [DerivedFact.model_validate(d) for d in json.loads((out_dir / "derived.json").read_text(encoding="utf-8"))]
    concepts = {
        c["concept_id"]: Concept.model_validate(c)
        for c in json.loads((out_dir / "concepts.json").read_text(encoding="utf-8"))
    }

    passage_map = {p.passage_id: p for p in passages}
    doc_map = {d["doc_id"]: d for d in documents_raw}

    candidates = generate_candidates(facts, concepts, passages, derived, doc_map)
    logger.info("S3: %d candidates to judge", len(candidates))

    # Judge in parallel (bounded); gather keeps candidate order, which the merge relies on
    sem = asyncio.Semaphore(max(1, settings.PREPROCESS_MAX_PARALLEL))

    async def one(cand: CandidateTuple) -> Optional[Contradiction]:
        async with sem:
            return await _judge(cand, passage_map, doc_map)

    results = await asyncio.gather(*(one(c) for c in candidates))
    confirmed = [r for r in results if r]

    contradictions = _merge_overlapping(confirmed)
    logger.info("S3: %d contradictions confirmed (%d before merging)", len(contradictions), len(confirmed))

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "contradictions.json").write_text(
        json.dumps([c.model_dump() for c in contradictions], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("S3: wrote contradictions.json")
    return contradictions


def run(cache_dir: Path, patient_id: str) -> list[Contradiction]:
    return asyncio.run(run_async(cache_dir, patient_id))
