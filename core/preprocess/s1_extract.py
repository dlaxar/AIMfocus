"""
preprocess/s1_extract.py – S1: LLM fact extraction (one call per document).

Documents are processed in date order. For each document:
  1. Build the prompt: full document text for context, the passage list to cite, and the
     concept registry (concept keys created for earlier documents, to be reused).
  2. Call the LLM with the s1_extract prompt + structured output schema.
  3. Immediately validate each extracted fact (passage exists, span verbatim, numbers in
     span, enum values).
  4. One batched retry per document for all failing facts, with their error messages.
  5. Build a coverage report (passages with no fact and no no_content flag).
After all documents: one alias-merge call that folds near-duplicate concept keys together.

Output: cache/<patient>/facts.raw.json, cache/<patient>/s1_report.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from app.config import settings
from app.llm.client import get_client
from app.schemas import Document, Fact, Passage, S1Report
from preprocess.vocab import MEASUREMENT_KEYS, normalize_concept_key, render_registry

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent.parent / "app" / "llm" / "prompts"
_PROMPT_PATH = _PROMPT_DIR / "s1_extract.md"
_ALIAS_PROMPT_PATH = _PROMPT_DIR / "s1_alias.md"


# ---------------------------------------------------------------------------
# LLM output schema (raw, before validation)
# ---------------------------------------------------------------------------

class RawFactField(BaseModel):
    brand: str = ""
    generic: str = ""
    strength: str = ""
    schedule: str = ""
    route: str = ""
    drug_class: str = ""
    indication: str = ""
    substance: str = ""
    reaction: list[str] = Field(default_factory=list)
    label: str = ""
    icd10: str = ""
    laterality: str = ""
    name: str = ""
    value: Any = None
    unit: str = ""
    context: str = ""
    result_summary: str = ""
    action: str = ""
    due: str = ""
    responsible: str = ""
    modality: str = ""


class RawFact(BaseModel):
    passage_id: str
    no_clinical_content: bool = False
    type: str = ""
    span: str = ""
    concept_key: str = ""
    concept_label_en: str = ""
    fields: RawFactField = Field(default_factory=RawFactField)
    status: str = "unknown"
    laterality: str = ""
    date: str = ""
    date_precision: str = "unknown"
    date_basis: str = "unknown"
    en: str = ""
    de: str = ""


class LLMExtractionOutput(BaseModel):
    facts: list[RawFact] = Field(default_factory=list)


class AliasGroup(BaseModel):
    canonical: str
    aliases: list[str] = Field(default_factory=list)


class AliasOutput(BaseModel):
    groups: list[AliasGroup] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_TYPES: set[str] = {
    "medication", "allergy", "diagnosis", "measurement",
    "procedure", "finding", "recommendation", "event", "plan", "demographic",
}
_VALID_STATUSES: set[str] = {
    "positive", "negative", "suspected", "history",
    "current", "discontinued", "started", "changed", "historical",
    "recommended", "done", "open", "unknown",
}
_VALID_PRECISIONS = {"day", "month", "year", "approximate", "unknown"}
_VALID_BASES = {"document_date", "text_date", "derived", "unknown"}
_VALID_LATERALITY = {"", "left", "right", "bilateral", "unknown"}


def _ws(text: str) -> str:
    """Collapse all whitespace (passages may contain line breaks the LLM writes as spaces)."""
    return " ".join(text.split())


def _numbers(text: str) -> set[str]:
    """Numeric tokens, comma→dot, without a trailing '.0' (78.0 == 78)."""
    out = set()
    for n in re.findall(r"\d+(?:[.,]\d+)?", str(text)):
        n = n.replace(",", ".")
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        out.add(n)
    return out


def _load_prompt(path: Path = _PROMPT_PATH) -> str:
    return path.read_text(encoding="utf-8")


def _build_passage_block(passages: list[Passage]) -> str:
    lines = []
    for p in passages:
        sec = f"section: {p.section}" if p.section else "section: (unknown)"
        lines.append(f"[{p.passage_id}] ({sec}) {_ws(p.text)}")
    return "\n".join(lines)


def _normalize_schedule(raw: str) -> str:
    """Convert '1 0 1 0' or '1-0-1-0' to '1-0-1' (or '1-0-1-1' if a night dose is present)."""
    parts = [p for p in re.split(r"[\s\-]+", raw.strip()) if p]
    if not parts:
        return raw
    # Drop the night column when it is zero
    if len(parts) == 4 and parts[3] == "0":
        parts = parts[:3]
    return "-".join(parts)


def _validate_raw_fact(raw: RawFact, passage_map: dict[str, Passage]) -> tuple[list[str], list[str]]:
    """
    Return (hard_errors, soft_errors); both empty = valid.
    Hard errors drop the fact if the retry does not fix them (wrong passage, span not
    verbatim, unknown type, numbers not in span). Soft errors are enum values that are
    coerced to "unknown" if the retry does not fix them.
    """
    hard: list[str] = []
    soft: list[str] = []

    if raw.no_clinical_content:
        if raw.passage_id not in passage_map:
            hard.append(f"passage_id '{raw.passage_id}' does not exist")
        return hard, soft

    if raw.passage_id not in passage_map:
        hard.append(f"passage_id '{raw.passage_id}' does not exist")
        return hard, soft

    passage = passage_map[raw.passage_id]
    if not raw.span:
        hard.append("span is empty")
    elif _ws(raw.span) not in _ws(passage.text):
        hard.append(f"span not found verbatim in passage text: '{raw.span[:60]}'")

    if raw.type not in _VALID_TYPES:
        hard.append(f"unknown type: '{raw.type}'")

    span_numbers = _numbers(raw.span)
    for key in ("value", "strength"):
        val = getattr(raw.fields, key)
        if val in (None, ""):
            continue
        missing = _numbers(val) - span_numbers
        if missing:
            hard.append(f"fields.{key} number(s) {sorted(missing)} not in span '{raw.span[:60]}'")

    if raw.status not in _VALID_STATUSES:
        soft.append(f"unknown status: '{raw.status}'")
    if raw.date_precision not in _VALID_PRECISIONS:
        soft.append(f"unknown date_precision: '{raw.date_precision}'")
    if raw.date_basis not in _VALID_BASES:
        soft.append(f"unknown date_basis: '{raw.date_basis}'")
    if (raw.laterality or "") not in _VALID_LATERALITY:
        soft.append(f"unknown laterality: '{raw.laterality}'")

    return hard, soft


def _raw_to_fact(raw: RawFact, doc_id: str, type_counters: dict[str, int]) -> Optional[Fact]:
    """Build a Fact, coercing invalid enum values instead of crashing. None if unusable."""
    fields_dict = raw.fields.model_dump(exclude_none=True, exclude_defaults=True)

    if raw.type == "medication" and "schedule" in fields_dict:
        fields_dict["schedule"] = _normalize_schedule(fields_dict["schedule"])

    laterality = raw.laterality or fields_dict.get("laterality") or None
    if laterality not in ("left", "right", "bilateral", "unknown"):
        laterality = None

    type_abbr = raw.type[:3] if raw.type else "unk"
    try:
        fact = Fact(
            fact_id=f"F:{doc_id}:{type_abbr}:{type_counters.get(type_abbr, 0) + 1:02d}",
            type=raw.type,  # type: ignore[arg-type]
            passage_id=raw.passage_id,
            span=raw.span,
            fields=fields_dict,
            status=raw.status if raw.status in _VALID_STATUSES else "unknown",  # type: ignore[arg-type]
            laterality=laterality,  # type: ignore[arg-type]
            date=raw.date or None,
            date_precision=raw.date_precision if raw.date_precision in _VALID_PRECISIONS else "unknown",  # type: ignore[arg-type]
            date_basis=raw.date_basis if raw.date_basis in _VALID_BASES else "unknown",  # type: ignore[arg-type]
            concept_id=normalize_concept_key(raw.concept_key, raw.type),
            en=raw.en,
            de=raw.de or raw.span,
        )
    except ValidationError as exc:
        logger.warning("S1: could not build fact for %s: %s", raw.passage_id, exc)
        return None
    type_counters[type_abbr] = type_counters.get(type_abbr, 0) + 1
    return fact


def _build_prompt(doc: Document, passages: list[Passage], registry: dict[str, str]) -> str:
    return (
        _load_prompt()
        .replace("{{measurement_keys}}", ", ".join(MEASUREMENT_KEYS))
        .replace("{{doc_id}}", doc.doc_id)
        .replace("{{doc_title}}", doc.title or "unknown")
        .replace("{{doc_date}}", doc.date or "unknown")
        .replace("{{doc_type}}", doc.type or "unknown")
        .replace("{{registry}}", render_registry(registry))
        .replace("{{document_text}}", doc.text)
        .replace("{{passages}}", _build_passage_block(passages))
    )


# ---------------------------------------------------------------------------
# Per-document extraction
# ---------------------------------------------------------------------------

async def _extract_document(
    doc: Document,
    passages: list[Passage],
    model: str,
    registry: dict[str, str],
) -> tuple[list[Fact], S1Report]:
    """Extract facts for one document. Adds the document's new concept keys to `registry`."""
    client = get_client()
    passage_map = {p.passage_id: p for p in passages}
    messages = [
        {"role": "system", "content": "Extract clinical facts as JSON."},
        {"role": "user", "content": _build_prompt(doc, passages, registry)},
    ]

    output: LLMExtractionOutput = await client.generate(
        prompt_name="s1_extract",
        messages=messages,
        response_schema=LLMExtractionOutput,
        temperature=0.0,
        model=model,
    )

    accepted: list[RawFact] = []
    no_content_passage_ids: set[str] = set()
    failing: list[tuple[RawFact, list[str], list[str]]] = []

    for raw in output.facts:
        hard, soft = _validate_raw_fact(raw, passage_map)
        if raw.no_clinical_content:
            if not hard:
                no_content_passage_ids.add(raw.passage_id)
            continue
        if hard or soft:
            failing.append((raw, hard, soft))
        else:
            accepted.append(raw)

    rejected = 0
    if failing:
        # One batched retry for all failing facts of this document
        listing = "\n".join(
            f"- {raw.model_dump_json()}\n  errors: {hard + soft}" for raw, hard, soft in failing
        )
        retry_messages = messages + [
            {"role": "assistant", "content": output.model_dump_json()},
            {"role": "user", "content": (
                "These facts were rejected:\n" + listing + "\n\n"
                "Return corrected versions of ONLY these facts, following all rules. "
                "Leave out a fact if it cannot be fixed."
            )},
        ]
        retry_out: LLMExtractionOutput = await client.generate(
            prompt_name="s1_extract",
            messages=retry_messages,
            response_schema=LLMExtractionOutput,
            temperature=0.0,
            model=model,
        )
        # Accept at most as many corrected facts per passage as failed there
        budget: dict[str, int] = {}
        for raw, _, _ in failing:
            budget[raw.passage_id] = budget.get(raw.passage_id, 0) + 1
        fixed_by_passage: dict[str, int] = {}
        for fixed in retry_out.facts:
            if fixed.no_clinical_content or budget.get(fixed.passage_id, 0) <= 0:
                continue
            hard, _soft = _validate_raw_fact(fixed, passage_map)
            if hard:
                continue
            budget[fixed.passage_id] -= 1
            fixed_by_passage[fixed.passage_id] = fixed_by_passage.get(fixed.passage_id, 0) + 1
            accepted.append(fixed)

        # Facts the retry did not replace: keep them if only enum values were wrong
        # (coerced to "unknown"), otherwise drop them
        for raw, hard, soft in failing:
            if fixed_by_passage.get(raw.passage_id, 0) > 0:
                fixed_by_passage[raw.passage_id] -= 1
                continue
            if hard:
                logger.warning("S1: fact for %s still invalid after retry: %s", raw.passage_id, hard)
                rejected += 1
            else:
                accepted.append(raw)

    facts: list[Fact] = []
    type_counters: dict[str, int] = {}
    covered_passage_ids: set[str] = set(no_content_passage_ids)
    for raw in accepted:
        fact = _raw_to_fact(raw, doc.doc_id, type_counters)
        if fact is None:
            rejected += 1
            continue
        facts.append(fact)
        covered_passage_ids.add(fact.passage_id)
        if fact.concept_id and fact.concept_id not in registry:
            registry[fact.concept_id] = raw.concept_label_en or raw.en or fact.concept_id

    uncovered = [
        p.passage_id
        for p in passages
        if p.passage_id not in covered_passage_ids and p.kind != "header"
    ]

    report = S1Report(
        doc_id=doc.doc_id,
        total_passages=len(passages),
        flagged_no_content=len(no_content_passage_ids),
        facts_extracted=len(facts),
        facts_rejected=rejected,
        uncovered_passages=uncovered,
    )
    return facts, report


# ---------------------------------------------------------------------------
# Alias merge (near-duplicate concept keys)
# ---------------------------------------------------------------------------

def _as_concept_id(key: str) -> str:
    key = key.strip()
    return key if key.startswith("C:") else "C:" + key


def _alias_map(output: AliasOutput, registry: dict[str, str]) -> dict[str, str]:
    """alias concept_id → canonical concept_id, only for groups that are safe to apply."""
    mapping: dict[str, str] = {}
    for group in output.groups:
        canonical = _as_concept_id(group.canonical)
        aliases = [_as_concept_id(a) for a in group.aliases if _as_concept_id(a) != canonical]
        keys = [canonical] + aliases
        if not aliases or any(k not in registry for k in keys):
            continue
        if len({k.split(":")[1] for k in keys}) != 1:
            continue  # never merge across types (prefixes)
        for alias in aliases:
            mapping.setdefault(alias, canonical)
    return mapping


async def _merge_aliases(facts: list[Fact], registry: dict[str, str], model: str) -> list[Fact]:
    if len(registry) < 2:
        return facts
    prompt = _load_prompt(_ALIAS_PROMPT_PATH).replace("{{registry}}", render_registry(registry))
    output: AliasOutput = await get_client().generate(
        prompt_name="s1_alias",
        messages=[{"role": "user", "content": prompt}],
        response_schema=AliasOutput,
        temperature=0.0,
        model=model,
    )
    mapping = _alias_map(output, registry)
    if not mapping:
        return facts
    logger.info("S1: merging %d alias concept keys", len(mapping))
    for alias in mapping:
        registry.pop(alias, None)
    return [
        f.model_copy(update={"concept_id": mapping[f.concept_id]}) if f.concept_id in mapping else f
        for f in facts
    ]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_async(
    data_dir: Path,
    cache_dir: Path,
    patient_id: str,
    model: Optional[str] = None,
) -> tuple[list[Fact], list[S1Report]]:
    out_dir = cache_dir / patient_id
    documents: list[Document] = [
        Document.model_validate(d)
        for d in json.loads((out_dir / "documents.json").read_text(encoding="utf-8"))
    ]
    passages_raw: list[Passage] = [
        Passage.model_validate(p)
        for p in json.loads((out_dir / "passages.json").read_text(encoding="utf-8"))
    ]

    passages_by_doc: dict[str, list[Passage]] = {}
    for p in passages_raw:
        passages_by_doc.setdefault(p.doc_id, []).append(p)

    m = model or settings.PREPROCESS_MODEL
    all_facts: list[Fact] = []
    all_reports: list[S1Report] = []
    registry: dict[str, str] = {}  # concept_id → English label, grows document by document

    # Oldest first, so later documents reuse the keys of earlier ones
    for doc in sorted(documents, key=lambda d: (d.date or "", d.doc_id)):
        doc_passages = passages_by_doc.get(doc.doc_id, [])
        logger.info("S1: extracting %s (%d passages, registry %d)", doc.doc_id, len(doc_passages), len(registry))
        facts, report = await _extract_document(doc, doc_passages, m, registry)
        all_facts.extend(facts)
        all_reports.append(report)
        logger.info(
            "  -> %d facts, %d rejected, %d uncovered passages",
            report.facts_extracted, report.facts_rejected, len(report.uncovered_passages),
        )

    all_facts = await _merge_aliases(all_facts, registry, m)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "facts.raw.json").write_text(
        json.dumps([f.model_dump() for f in all_facts], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "s1_report.json").write_text(
        json.dumps([r.model_dump() for r in all_reports], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("S1: wrote facts.raw.json and s1_report.json (%d concepts)", len(registry))
    return all_facts, all_reports


def run(data_dir: Path, cache_dir: Path, patient_id: str, model: Optional[str] = None) -> tuple[list[Fact], list[S1Report]]:
    return asyncio.run(run_async(data_dir, cache_dir, patient_id, model))
