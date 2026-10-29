"""
app/runtime/planner.py – R1: Parse the situation and produce a Need list.

Uses Gemini Flash via the LLM client and merges output with role templates.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.llm.client import get_client
from app.schemas import Need, PlannerOutput
from app.runtime.templates import merge_needs, template_needs_for

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "llm" / "prompts" / "r1_planner.md"


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


async def plan(
    situation: str,
    reference_date: str,
    available_types: list[str],
    concept_labels: list[str],
    model: str | None = None,
) -> PlannerOutput:
    """
    R1: Return a PlannerOutput (role, task, needs[]).
    Template needs are merged in.
    """
    from app.config import settings
    client = get_client()
    prompt_template = _load_prompt()

    prompt = (
        prompt_template
        .replace("{{situation}}", situation)
        .replace("{{reference_date}}", reference_date or "unknown")
        .replace("{{available_types}}", ", ".join(available_types))
        .replace("{{concept_labels}}", ", ".join(concept_labels[:30]))
    )

    messages = [
        {"role": "system", "content": "You are a clinical information planner."},
        {"role": "user", "content": prompt},
    ]

    output: PlannerOutput = await client.generate(
        prompt_name="r1_planner",
        messages=messages,
        response_schema=PlannerOutput,
        temperature=0.0,
        model=model or settings.RUNTIME_MODEL,
    )

    # Merge with template needs
    tmpl_needs = template_needs_for(output.role, output.task)
    output.needs = merge_needs(output.needs, tmpl_needs)

    logger.info(
        "Planner: role='%s', task='%s', %d needs (%d from templates)",
        output.role, output.task, len(output.needs), len(tmpl_needs),
    )
    return output
