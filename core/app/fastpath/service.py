"""
app/fastpath/service.py – fast-path screen builder.

create_screen() registers the screen and returns its ID at once (status "starting"); the rest
runs in the background and fills the stored screen in place:
  1. One select call (fact-store structure + fact sheet → variables with values + fact IDs).
  2. Validate each documented variable with the R3 validator; build sources (status "partial").
     Variables without facts are "pending".
  3. One derive call per pending variable over the raw passages,
     all in parallel (bounded by FASTPATH_MAX_PARALLEL_DERIVE). Each result is validated
     (passage IDs exist, quotes verbatim, numbers backed by quotes) and written into the
     stored screen; the screen becomes "complete" when all are done.

Screens live in memory (single process). Good enough for the demo; use Redis or similar
if the API ever runs with several workers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from app.config import settings
from app.fastpath.models import (
    DeriveOutput,
    FastScreen,
    ScreenVariable,
    SelectedVariable,
    SelectOutput,
)
from app.llm.client import get_client
from app.runtime.screen import _best_passage_id, _build_source
from app.runtime.validator import validate_item
from app.schemas import AnswererItem, Source
from app.store import FactStore

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent.parent / "llm" / "prompts"

_screens: dict[str, FastScreen] = {}
_tasks: dict[str, asyncio.Task] = {}


def _prompt(name: str) -> str:
    return (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


def _ws(text: str) -> str:
    return " ".join(text.split()).lower()


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text.replace(",", ".")))


def _elapsed_ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


# ---------------------------------------------------------------------------
# Prompt context
# ---------------------------------------------------------------------------

def concepts_block(store: FactStore) -> str:
    lines = [
        f"{c.concept_id} | {c.type} | {c.label_en} | {len(c.fact_ids)} fact(s)"
        for c in sorted(store.concepts.values(), key=lambda c: c.concept_id)
    ]
    return "\n".join(lines) or "(no concepts)"


def passages_block(store: FactStore) -> str:
    lines = []
    for p in store.passages.values():
        if p.kind == "header":
            continue
        doc = store.get_document(p.doc_id)
        title = doc.title if doc else p.doc_id
        date = (doc.date if doc else None) or "unknown"
        lines.append(f"[{p.passage_id}] ({title}, {date}, {p.section or '-'}) {' '.join(p.text.split())}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 1: select
# ---------------------------------------------------------------------------

async def _select(situation: str, store: FactStore, reference_date: str) -> SelectOutput:
    prompt = (
        _prompt("fp_select")
        .replace("{{concepts}}", concepts_block(store))
        .replace("{{fact_sheet}}", store.fact_sheet)
        .replace("{{reference_date}}", reference_date or "unknown")
        .replace("{{situation}}", situation)
    )
    return await get_client().generate(
        prompt_name="fp_select",
        messages=[{"role": "user", "content": prompt}],
        response_schema=SelectOutput,
        temperature=0.0,
        model=settings.RUNTIME_MODEL,
    )


def _sources_for_facts(fact_ids: list[str], store: FactStore) -> list[Source]:
    sources: list[Source] = []
    seen: set[str] = set()
    for fid in fact_ids:
        pid = _best_passage_id(fid, store)
        if pid and pid not in seen:
            seen.add(pid)
            src = _build_source(pid, store)
            if src:
                sources.append(src)
    return sources


def _documented_variable(v: SelectedVariable, store: FactStore, t0: float) -> Optional[ScreenVariable]:
    """Validate a documented variable. None → treat it as missing and derive it."""
    item = AnswererItem(need_id=v.name, fact_ids=v.fact_ids, text_en=v.value, absent=False)
    result = validate_item(item, store.get_any_fact, [])
    if not v.value.strip():
        # A small model sometimes cites facts but writes no value: treat as not passed
        result.fail("empty value")
    if result.passed:
        return ScreenVariable(
            name=v.name, label=v.label, state="documented", value=v.value,
            fact_ids=v.fact_ids, sources=_sources_for_facts(v.fact_ids, store),
            priority=v.priority, resolved_ms=_elapsed_ms(t0),
        )

    logger.warning("fastpath: select value for %s rejected: %s", v.name, result.errors)
    valid = [f for f in (store.get_any_fact(fid) for fid in v.fact_ids) if f is not None]
    if not valid:
        return None
    # Never show the rejected LLM text; fall back to the stored renderings of real facts
    return ScreenVariable(
        name=v.name, label=v.label, state="documented",
        value="; ".join(f.en or getattr(f, "span", "") for f in valid),
        fact_ids=[f.fact_id for f in valid],
        sources=_sources_for_facts([f.fact_id for f in valid], store),
        note="Summary replaced by stored fact text (validation failed)",
        priority=v.priority, resolved_ms=_elapsed_ms(t0),
    )


# ---------------------------------------------------------------------------
# Step 2: derive (background, parallel)
# ---------------------------------------------------------------------------

def _check_derive(out: DeriveOutput, store: FactStore) -> list[str]:
    """Return validation errors for a derive result (empty = valid)."""
    errors: list[str] = []
    passages = [store.get_passage(pid) for pid in out.passage_ids]
    if not out.passage_ids or any(p is None for p in passages):
        errors.append(f"unknown or missing passage_ids: {out.passage_ids}")
        return errors
    passage_text = " ".join(_ws(p.text) for p in passages if p)
    for q in out.quotes:
        if _ws(q) not in passage_text:
            errors.append(f"quote not verbatim in cited passages: {q[:60]!r}")
    # Numbers must be backed by the cited passages themselves, not by (possibly invented) quotes
    for n in _numbers(out.value) - _numbers(passage_text):
        errors.append(f"number {n} in value not found in the cited passages")
    return errors


async def _derive_one(
    screen: FastScreen,
    var: ScreenVariable,
    store: FactStore,
    passages: str,
    sem: asyncio.Semaphore,
    t0: float,
) -> None:
    prompt = (
        _prompt("fp_derive")
        .replace("{{name}}", var.name)
        .replace("{{label}}", var.label)
        .replace("{{role}}", screen.role or "clinician")
        .replace("{{task}}", screen.task or screen.situation)
        .replace("{{reference_date}}", screen.reference_date or "unknown")
        .replace("{{passages}}", passages)
    )
    try:
        async with sem:
            out: DeriveOutput = await get_client().generate(
                prompt_name="fp_derive",
                messages=[{"role": "user", "content": prompt}],
                response_schema=DeriveOutput,
                temperature=0.0,
                model=settings.RUNTIME_MODEL,
            )
    except Exception as exc:  # network / quota / schema errors must not kill the screen
        logger.exception("fastpath: derive failed for %s", var.name)
        var.state, var.note = "check", f"Text search failed: {type(exc).__name__}"
    else:
        if not out.found:
            var.state, var.value = "not_documented", "Not documented"
            var.note = "No passage in the documents covers this"
        else:
            errors = _check_derive(out, store)
            if errors:
                logger.warning("fastpath: derive result for %s rejected: %s", var.name, errors)
                var.state = "check"
                var.value = "Answer could not be verified against the documents"
                var.note = "; ".join(errors)
            else:
                var.state, var.value, var.note = "derived", out.value, out.rationale or None
                var.sources = [s for s in (_build_source(pid, store) for pid in out.passage_ids) if s]
    var.resolved_ms = _elapsed_ms(t0)
    screen.version += 1


async def _derive_all(screen: FastScreen, store: FactStore, t0: float) -> None:
    pending = [v for v in screen.variables if v.state == "pending"]
    t_derive = time.perf_counter()
    sem = asyncio.Semaphore(settings.FASTPATH_MAX_PARALLEL_DERIVE)
    passages = passages_block(store)
    await asyncio.gather(*(_derive_one(screen, v, store, passages, sem, t0) for v in pending))
    screen.timings_ms["derive"] = _elapsed_ms(t_derive)
    screen.timings_ms["complete"] = _elapsed_ms(t0)
    _attach_contradictions(screen, store)
    screen.status = "complete"
    screen.version += 1


def _attach_contradictions(screen: FastScreen, store: FactStore) -> None:
    """Relevant ones first; every other contradiction of the record stays available too."""
    screen.contradictions = _relevant_contradictions(screen, store)
    shown = {cx.contradiction_id for cx in screen.contradictions}
    screen.other_contradictions = [cx for cx in store.contradictions if cx.contradiction_id not in shown]


def _relevant_contradictions(screen: FastScreen, store: FactStore):
    concept_ids: set[str] = set()
    passage_ids: set[str] = set()
    for v in screen.variables:
        for fid in v.fact_ids:
            f = store.get_fact(fid)
            if f and f.concept_id:
                concept_ids.add(f.concept_id)
        passage_ids.update(s.passage_id for s in v.sources)
    return [
        cx for cx in store.contradictions
        if set(cx.concept_ids) & concept_ids or set(cx.passage_ids) & passage_ids
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def create_screen(situation: str, store: FactStore) -> FastScreen:
    """
    Register a screen and return it at once (status "starting", no variables yet), so the
    caller gets its ID without waiting for an LLM call. Select and derive run in the
    background; the screen object is filled in place.
    """
    t0 = time.perf_counter()
    reference_date = store.reference_date() or ""
    screen = FastScreen(
        screen_id=uuid.uuid4().hex[:12],
        situation=situation,
        reference_date=reference_date or None,
    )
    _screens[screen.screen_id] = screen
    _tasks[screen.screen_id] = asyncio.create_task(_build(screen, store, t0))
    return screen


async def _build(screen: FastScreen, store: FactStore, t0: float) -> None:
    try:
        selected = await _select(screen.situation, store, screen.reference_date or "")
    except Exception as exc:  # the ID is already out: report the failure on the screen
        logger.exception("fastpath: select failed for screen %s", screen.screen_id)
        screen.error = f"Could not prepare the screen: {type(exc).__name__}"
        screen.status = "complete"
        screen.timings_ms["complete"] = _elapsed_ms(t0)
        screen.version += 1
        return

    screen.role, screen.task = selected.role, selected.task
    screen.timings_ms["select"] = _elapsed_ms(t0)
    for v in sorted(selected.variables, key=lambda v: v.priority):
        documented = _documented_variable(v, store, t0) if v.documented and v.fact_ids else None
        screen.variables.append(documented or ScreenVariable(
            name=v.name, label=v.label, state="pending", priority=v.priority,
        ))
    _attach_contradictions(screen, store)
    screen.timings_ms["first_response"] = _elapsed_ms(t0)

    if any(v.state == "pending" for v in screen.variables):
        screen.status = "partial"
        screen.version += 1
        await _derive_all(screen, store, t0)
    else:
        screen.status = "complete"
        screen.timings_ms["complete"] = screen.timings_ms["first_response"]
        screen.version += 1


def get_screen(screen_id: str) -> Optional[FastScreen]:
    return _screens.get(screen_id)


async def wait_for(screen_id: str, timeout_s: float = 60.0) -> Optional[FastScreen]:
    """
    Wait until the screen is complete (tests / ?wait=true). After `timeout_s` the screen
    is returned as it stands (status "starting" or "partial") instead of failing.
    """
    task = _tasks.get(screen_id)
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.warning("fastpath: screen %s not complete after %.0f s", screen_id, timeout_s)
    return _screens.get(screen_id)


def screen_json(screen: FastScreen) -> str:
    return json.dumps(screen.model_dump(), ensure_ascii=False)
