"""
app/runtime/answerer.py – R2: Select fact IDs for each need.

First tries deterministic resolvers for common needs (current meds by drug class,
latest measurement value). Falls back to the LLM for anything unresolved.
One LLM call for all unresolved needs (fact_sheet is a cacheable prefix).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from app.llm.client import get_client
from app.runtime.validator import fact_matches_filter
from app.schemas import AnswererItem, AnswererOutput, Fact, DerivedFact, Need
from app.store import FactStore

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "llm" / "prompts" / "r2_answerer.md"


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Deterministic resolvers
# ---------------------------------------------------------------------------

def _resolve_deterministic(need: Need, store: FactStore, reference_date: str) -> Optional[AnswererItem]:
    """
    Try to answer a need with pure code. Returns AnswererItem or None (→ LLM).
    """
    # Derived facts (weight change, BMI, med changes, follow-ups) are listed in the
    # fact sheet; the LLM picks the one that fits the need instead of returning all of them.

    if need.category == "measurement":
        # Latest value – only when the need names the measurement. Without a name, or when
        # nothing matches it (names vary: "creatinine" vs "Kreatinin"), the LLM decides,
        # so we never answer with an unrelated measurement or claim absence on a name miss.
        name_filter = need.filter.get("name")
        if not name_filter:
            return None
        candidates = [
            f for f in store.facts_by_type("measurement")
            if fact_matches_filter(f, {"name": name_filter})
            and (f.date or "") <= reference_date
        ]
        if not candidates:
            return None
        latest = max(candidates, key=lambda f: f.date or "")
        val = latest.fields.get("value", "")
        unit = latest.fields.get("unit", "")
        name = latest.fields.get("name") or str(name_filter)
        return AnswererItem(
            need_id=need.need_id,
            fact_ids=[latest.fact_id],
            text_en=f"{name}: {val} {unit} ({latest.date or 'date unknown'})",
            absent=False,
        )

    if need.category == "allergy":
        allergies = store.facts_by_type("allergy")
        if allergies:
            return None  # Let LLM summarise; too nuanced for deterministic
        return AnswererItem(need_id=need.need_id, fact_ids=[], text_en="No allergy documented", absent=True)

    return None  # let LLM handle


# ---------------------------------------------------------------------------
# LLM answerer
# ---------------------------------------------------------------------------

async def answer(
    needs: list[Need],
    store: FactStore,
    reference_date: str,
    model: Optional[str] = None,
) -> list[AnswererItem]:
    from app.config import settings

    # First pass: deterministic resolvers
    resolved: dict[str, AnswererItem] = {}
    unresolved: list[Need] = []

    for need in needs:
        item = _resolve_deterministic(need, store, reference_date)
        if item is not None:
            resolved[need.need_id] = item
            logger.debug("Deterministic resolver handled need %s", need.need_id)
        else:
            unresolved.append(need)

    if not unresolved:
        return list(resolved.values())

    # LLM pass for remaining needs
    client = get_client()
    prompt_template = _load_prompt()

    needs_json = json.dumps([n.model_dump() for n in unresolved], ensure_ascii=False)
    prompt = (
        prompt_template
        .replace("{{fact_sheet}}", store.fact_sheet)
        .replace("{{reference_date}}", reference_date or "unknown")
        .replace("{{needs}}", needs_json)
    )

    messages = [
        {"role": "system", "content": "You are a clinical fact selector."},
        {"role": "user", "content": prompt},
    ]

    output: AnswererOutput = await client.generate(
        prompt_name="r2_answerer",
        messages=messages,
        response_schema=AnswererOutput,
        temperature=0.0,
        model=model or settings.RUNTIME_MODEL,
    )

    for item in output.items:
        resolved[item.need_id] = item

    # Fill in any needs the LLM missed
    for need in unresolved:
        if need.need_id not in resolved:
            logger.warning("Answerer: no response for need %s, marking absent", need.need_id)
            resolved[need.need_id] = AnswererItem(
                need_id=need.need_id, fact_ids=[], text_en="Not documented", absent=True
            )

    # Return in the original need order
    return [resolved[n.need_id] for n in needs if n.need_id in resolved]
