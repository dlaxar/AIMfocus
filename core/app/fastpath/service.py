"""
app/fastpath/service.py – fast-path screen builder.

create_screen() registers the screen and returns its ID at once (status "starting"); the rest
runs in the background and fills the stored screen in place:
  1. One select call (fact-store structure + fact sheet → variables + which concepts/facts
     answer them). The LLM writes no values.
  2. Code pulls the facts from the store (unknown IDs dropped, time scope applied) and builds
     the value from their stored text, with sources (status "partial"). A variable with no
     resolvable fact is "pending".
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
from app.fastpath import shape
from app.fastpath.models import (
    DeriveOutput,
    FastScreen,
    Headline,
    PickOutput,
    ScreenVariable,
    SelectedVariable,
    SelectOutput,
)
from app.llm.client import get_client
from app.runtime.screen import _best_passage_id, _build_source
from app.schemas import DerivedFact, Fact, Source
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


class Catalogue:
    """
    The fact store as the select call sees it: every concept and fact under a short handle
    (c12, f40, d3). Full IDs such as "F:12340018:med:01" cost many output tokens each.
    """

    # Facts shown under each concept, newest first. The concept handle pulls all of them;
    # older ones only matter one by one in rare cases, and a long list invites wrong picks.
    FACTS_PER_CONCEPT = 4

    def __init__(self, store: FactStore) -> None:
        self.handles: dict[str, str] = {}   # handle → concept_id / fact_id
        facts = sorted((f for f in store.facts.values() if not is_question(f)), key=lambda f: (f.date or "", f.fact_id))
        fact_handle = {f.fact_id: self._add(f"f{i}", f.fact_id) for i, f in enumerate(facts, 1)}
        by_concept: dict[str, list] = {}
        for f in facts:
            by_concept.setdefault(f.concept_id or "", []).append(f)

        def fact_line(f) -> str:
            return "  " + " | ".join([fact_handle[f.fact_id], f.en or f.span, f.status, f.laterality or "-", f.date or "-"])

        lines = ["### Concepts with their facts",
                 "c-handle | type | label | number of facts",
                 "  f-handle | description | status | laterality | date   (newest first)"]
        for i, c in enumerate(sorted(store.concepts.values(), key=lambda c: (c.type, c.concept_id)), 1):
            self._add(f"c{i}", c.concept_id)
            cfacts = by_concept.pop(c.concept_id, [])[::-1]
            lines.append(f"c{i} | {c.type} | {c.label_en} | {len(cfacts)}")
            seen: set[str] = set()   # the same statement in several letters: list the newest once
            shown = [f for f in cfacts if not ((f.en or f.span).strip(" .").lower() in seen or seen.add((f.en or f.span).strip(" .").lower()))]
            lines += [fact_line(f) for f in shown[:self.FACTS_PER_CONCEPT]]
            if len(shown) > self.FACTS_PER_CONCEPT:
                lines.append(f"  (+{len(shown) - self.FACTS_PER_CONCEPT} older, included when the concept is chosen with scope all)")
        orphans = [f for fs in by_concept.values() for f in fs]
        if orphans:
            lines += ["", "### Facts without a concept"] + [fact_line(f) for f in orphans[::-1]]
        lines += ["", "### Derived facts (handle | description | date)"]
        for i, d in enumerate(sorted(store.derived.values(), key=lambda d: d.fact_id), 1):
            self._add(f"d{i}", d.fact_id)
            lines.append(f"d{i} | {d.en} | {d.date or '-'}")
        self.text = "\n".join(lines)

    def _add(self, handle: str, full_id: str) -> str:
        self.handles[handle] = full_id
        return handle

    def resolve(self, handle: str) -> str:
        """Full ID for a handle; a full ID passes through (unknown ones are dropped later)."""
        return self.handles.get(handle.strip(), handle.strip())


_catalogues: dict[str, tuple[int, Catalogue]] = {}   # patient → (id of its store, catalogue)


def catalogue(store: FactStore) -> Catalogue:
    cached = _catalogues.get(store.patient_id)
    if cached is None or cached[0] != id(store):   # first use, or the store was reloaded
        cached = _catalogues[store.patient_id] = (id(store), Catalogue(store))
    return cached[1]


def _name(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "variable"


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
    cat = catalogue(store)
    # Everything before the situation is the same for every screen, so it is sent as a
    # cacheable first part (prompt caching on Claude, implicit caching on Gemini)
    fixed = _prompt("fp_select").replace("{{catalogue}}", cat.text)
    situation_part = (
        f"## Clinician situation\nPresent tense means as of {reference_date or 'unknown'}.\n\n{situation}"
    )
    out: PickOutput = await get_client().generate(
        prompt_name="fp_select",
        messages=[{"role": "user", "content": [{"text": fixed, "cache": True}, {"text": situation_part}]}],
        response_schema=PickOutput,
        temperature=0.0,
        model=settings.RUNTIME_MODEL,
    )
    # Priority is a unique rank: critical first, then the model's own order within each tier
    ranked = sorted(enumerate(out.variables), key=lambda iv: (_TIER_RANK[iv[1].tier], iv[0]))
    return SelectOutput(role=out.role, task=out.task, variables=[
        SelectedVariable(
            name=_name(v.label), label=v.label,
            concept_ids=[cat.resolve(h) for h in v.concepts],
            fact_ids=[cat.resolve(h) for h in v.facts],
            time_scope=v.scope, priority=rank, tier=v.tier, output=v.output, columns=v.columns,
        )
        for rank, (_, v) in enumerate(ranked, 1)
    ])


_TIER_RANK = {"critical": 0, "important": 1, "background": 2}


def _own_passage_ids(fid: str, store: FactStore) -> list[str]:
    """The passage(s) a fact was extracted from; for a derived fact, those of its inputs."""
    fact = store.get_fact(fid)
    if fact is not None:
        return [fact.passage_id]
    df = store.derived.get(fid)
    if df and df.fields.get("passage_id"):  # computed from one passage, e.g. age from the date of birth
        return [df.fields["passage_id"]]
    return [pid for inp in (df.inputs if df else []) for pid in _own_passage_ids(inp, store)]


def _sources_for_facts(fact_ids: list[str], store: FactStore) -> list[Source]:
    """
    Each fact's own passage first – so "current medication" links the newest letter, not
    the 2015 letter the row was copied from – then the original of an exact copy.
    """
    sources: list[Source] = []
    seen: set[str] = set()
    from app.images import image_ref
    for fid in fact_ids:
        own = _own_passage_ids(fid, store)
        for pid in own + [_best_passage_id(fid, store)]:
            if pid and pid not in seen:
                seen.add(pid)
                src = _build_source(pid, store)
                if src:
                    if pid in own:   # an imaging study's own passage links its image
                        src.image = image_ref(store.get_any_fact(fid), store)
                    sources.append(src)
    return sources


def _known_on(f: Fact | DerivedFact, store: FactStore) -> str:
    """
    The date the record knew a fact: the date of its letter. A plan "scheduled for 16 May
    2016" in the letter of 15 April is known on 15 April, though its own date is in May.
    """
    passage_id = getattr(f, "passage_id", None)
    doc = store.get_document(passage_id.split(":")[0]) if passage_id else None
    return (doc.date if doc and doc.date else None) or f.date or ""


_QUESTION = re.compile(r"^\s*(clinical\s+)?question\s*:", re.I)
_LONG_TERM = re.compile(r"lz-?ekg|holter|langzeit|24\s*-?\s*h|24-|tages|nacht|day\s*mean|night\s*mean|mittelwert", re.I)


_EXAM = re.compile(r"\b(ecg|ekg|holter|echo(?:cardiograph\w*)?|duplex|doppler|ct|mri|ultrasound|sonograph\w*|"
                   r"x-ray|mammograph\w*|histolog\w*|fundus|spirometr\w*|biops\w*)\b", re.I)


def _exams(f) -> set[str]:
    """The examinations a finding or measurement names (e.g. {"ecg"}); empty for other facts."""
    if getattr(f, "type", "") not in ("finding", "measurement"):
        return set()
    return {m.lower().rstrip("s") for m in _EXAM.findall(f.en or "")}


def is_question(f) -> bool:
    """A letter's referral question ("Fragestellung: VHF?") extracted as a suspected finding – not a finding."""
    return getattr(f, "status", "") == "suspected" and bool(_QUESTION.match(f.en or ""))


def _setting(f) -> str:
    """Long-term recording (Holter, 24-hour BP) or a single measurement."""
    ctx = f"{f.fields.get('context', '')} {f.fields.get('name', '')}" if f.type == "measurement" else ""
    return "long_term" if _LONG_TERM.search(ctx) else "spot"


def resolve_facts(v: SelectedVariable, store: FactStore, reference_date: str) -> list[Fact | DerivedFact]:
    """
    Pull the facts a variable points at, purely in code:
      - fact_ids: taken as they are; IDs that do not exist are dropped.
      - concept_ids: all facts of the concept up to the reference date; with time_scope
        "latest" only those of the newest date, with "all" the whole history.
    Returned oldest first, without duplicates.
    """
    picked: dict[str, Fact | DerivedFact] = {}
    for fid in v.fact_ids:
        f = store.get_any_fact(fid)
        if f is None:
            logger.warning("fastpath: %s cites unknown fact %s – dropped", v.name, fid)
        else:
            picked.setdefault(f.fact_id, f)

    for cid in v.concept_ids:
        facts = [f for f in store.facts_by_concept(cid)
                 if (not reference_date or _known_on(f, store) <= reference_date) and not is_question(f)]
        if not facts:
            if cid not in store.concepts:
                logger.warning("fastpath: %s cites unknown concept %s – dropped", v.name, cid)
            continue
        if v.time_scope == "latest":
            # Newest per kind of measurement: a later spot heart rate does not replace the
            # Holter result, nor an office blood pressure the 24-hour mean
            newest: dict[str, str] = {}
            for f in facts:
                newest[_setting(f)] = max(newest.get(_setting(f), ""), f.date or "")
            facts = [f for f in facts if (f.date or "") == newest[_setting(f)]]
        for f in facts:
            picked.setdefault(f.fact_id, f)

    # One examination's results often sit in one sentence but in different concepts
    # ("EKG: HF 90/min, Normaltyp" → heart rate and axis): a picked result brings the other
    # results of the same examination from the same passage
    for f in list(picked.values()):
        exams = _exams(f)
        if not exams:
            continue
        for g in getattr(store, "facts", {}).values():
            if g.passage_id == f.passage_id and g.fact_id not in picked and _exams(g) & exams:
                picked[g.fact_id] = g

    # A recommendation is shown as its follow-up status ("Follow-up open: … – no record in the
    # 3 later documents"), which repeats the recommendation and says what became of it
    followups = {d.fields.get("rec_fact_id"): d for d in store.derived.values() if d.formula == "followup_status"}
    for fid in list(picked):
        if fid in followups:
            del picked[fid]
            picked.setdefault(followups[fid].fact_id, followups[fid])

    # A derived fact that only restates facts already picked (e.g. "Current medication: X"
    # next to the medication row X itself) would show the same value twice
    for fid, f in list(picked.items()):
        inputs = getattr(f, "inputs", None)
        if f.type == "derived" and inputs and all(i in picked for i in inputs):
            del picked[fid]

    return sorted(picked.values(), key=lambda f: f.date or "")


def render_value(facts: list[Fact | DerivedFact]) -> str:
    """The screen value: the stored English text of each fact with its date, oldest first."""
    parts: list[str] = []
    for f in facts:
        text = (f.en or getattr(f, "span", "")).strip().rstrip(".")
        if f.date and f.type != "derived" and f.date not in text:
            text = f"{text} ({f.date})"
        if text and text not in parts:  # the same statement copied into several letters
            parts.append(text)
    return "; ".join(parts)


def _documented_variable(
    v: SelectedVariable, store: FactStore, reference_date: str, t0: float,
) -> Optional[ScreenVariable]:
    """Documented variable built from the fact store. None → no fact found, derive it."""
    facts = resolve_facts(v, store, reference_date)
    if not facts:
        return None
    fact_ids = [f.fact_id for f in facts]
    var = ScreenVariable(
        name=v.name, label=v.label, state="documented",
        value=render_value(facts),
        fact_ids=fact_ids, sources=_sources_for_facts(fact_ids, store),
        priority=v.priority, tier=v.tier, output=v.output, resolved_ms=_elapsed_ms(t0),
    )
    shape.shape_documented(var, v, facts, store, reference_date)
    return var


def _image_variable(
    v: SelectedVariable, store: FactStore, reference_date: str, t0: float,
) -> Optional[ScreenVariable]:
    """The imaging studies chosen that have an image (else every one that has), newest first.
    None → the record holds no image."""
    if not store.images:
        return None
    def imaged(facts):
        return [f for f in facts if f.fields.get("image_id") in store.images]
    facts = imaged(resolve_facts(v, store, reference_date)) or imaged(store.facts.values())
    if not facts:
        return None
    facts = sorted(facts, key=lambda f: f.date or "", reverse=True)
    fact_ids = [f.fact_id for f in facts]
    var = ScreenVariable(
        name=v.name, label=v.label, state="documented", value=render_value(facts),
        fact_ids=fact_ids, sources=_sources_for_facts(fact_ids, store),
        priority=v.priority, tier=v.tier, output="image", resolved_ms=_elapsed_ms(t0),
    )
    shape.shape_documented(var, v, facts, store, reference_date)
    return var


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
    # Numbers must be backed by the cited passages themselves, not by (possibly invented) quotes.
    # The letter's date counts too: the prompt shows it with every passage ("in June 2015").
    backed = _numbers(passage_text) | _letter_date_numbers(passages, store)
    for n in _numbers(out.value) - backed:
        errors.append(f"number {n} in value not found in the cited passages")
    return errors


# A statement that something is missing from the record – not a documented negative finding
# ("No focal deficits on admission" is a finding and stays one)
_ABSENCE = re.compile(r"not documented|no record|not (?:reported|mentioned|listed|recorded)|\bno\b[^.]*\bdocumented\b", re.I)


def _letter_date_numbers(passages: list, store: FactStore) -> set[str]:
    numbers: set[str] = set()
    for p in passages:
        doc = store.get_document(p.doc_id) if p else None
        if doc and doc.date and len(doc.date) == 10:
            y, m, d = doc.date.split("-")
            numbers |= _numbers(f"{doc.date} {d}.{m}.{y} {int(d)}.{int(m)}.{y}")
    return numbers


async def _derive_one(
    screen: FastScreen,
    var: ScreenVariable,
    store: FactStore,
    passages: str,
    sem: asyncio.Semaphore,
    t0: float,
) -> None:
    # Rules and passages are the same for every variable: a cacheable first part
    fixed = _prompt("fp_derive").replace("{{passages}}", passages)
    variable_part = (
        f"## Variable\n- Name: {var.name}\n- Label: {var.label}\n"
        f"- Clinician role and task: {screen.role or 'clinician'} – {screen.task or screen.situation}\n"
        f"- Reference date (present tense means as of): {screen.reference_date or 'unknown'}"
    )
    try:
        async with sem:
            out: DeriveOutput = await get_client().generate(
                prompt_name="fp_derive",
                messages=[{"role": "user", "content": [{"text": fixed, "cache": True}, {"text": variable_part}]}],
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
            if errors and _ABSENCE.search(out.value or ""):
                # "No CPAP therapy is documented" with a quote that does not match: there is
                # no evidence it is documented, which is what "not documented" means
                logger.info("fastpath: derive for %s says absent, citation failed – not documented", var.name)
                var.state, var.value = "not_documented", "Not documented"
                var.note = "The text search found no statement of it"
            elif errors:
                logger.warning("fastpath: derive result for %s rejected: %s", var.name, errors)
                var.state = "check"
                var.value = "Answer could not be verified against the documents"
                var.note = "; ".join(errors)
            else:
                var.state, var.value, var.note = "derived", out.value, out.rationale or None
                var.sources = [s for s in (_build_source(pid, store) for pid in out.passage_ids) if s]
    shape.shape_text_result(var)
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
    # Per value: which contradictions touch it (through its facts, or the passages a text
    # search cited) and which of its facts are part of one, so the UI can flag them
    for v in screen.variables:
        passages = {s.passage_id for s in v.sources}
        touching = [cx for cx in store.contradictions if set(cx.fact_ids) & set(v.fact_ids) or set(cx.passage_ids) & passages]
        v.contradiction_ids = [cx.contradiction_id for cx in touching]
        in_cx = {fid for cx in touching for fid in cx.fact_ids}
        v.contradicting_fact_ids = [fid for fid in v.fact_ids if fid in in_cx]
        if v.state == "derived" and v.latest:   # a text-search value: contradictions via its passages
            v.latest[0].contradiction_ids = list(v.contradiction_ids)
        v.contradictions = shape.contradiction_refs(v, store)
    # Relevant = flagged on a shown value. Sharing only a concept is not enough: the screen
    # would announce a contradiction that no value on it carries
    flagged = {cid for v in screen.variables for cid in v.contradiction_ids}
    screen.contradictions = [cx for cx in store.contradictions if cx.contradiction_id in flagged]
    screen.other_contradictions = [cx for cx in store.contradictions if cx.contradiction_id not in flagged]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def create_screen(situation: str, store: FactStore, max_variables: Optional[int] = None,
                        shown_fact_ids: frozenset = frozenset()) -> FastScreen:
    """
    Register a screen and return it at once (status "starting", no variables yet), so the
    caller gets its ID without waiting for an LLM call. Select and derive run in the
    background; the screen object is filled in place.
    """
    t0 = time.perf_counter()
    reference_date = store.reference_date() or ""
    screen = FastScreen(
        screen_id=uuid.uuid4().hex[:12],
        patient_id=store.patient_id,
        situation=situation,
        reference_date=reference_date or None,
    )
    _screens[screen.screen_id] = screen
    _tasks[screen.screen_id] = asyncio.create_task(_build(screen, store, t0, max_variables, shown_fact_ids))
    return screen


async def _build(screen: FastScreen, store: FactStore, t0: float, max_variables: Optional[int] = None,
                 shown_fact_ids: frozenset = frozenset()) -> None:
    """max_variables / shown_fact_ids: chat cards – at most that many, nothing the screen already shows."""
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
        if max_variables is not None and len(screen.variables) >= max_variables:
            break
        if v.output == "image":
            documented = _image_variable(v, store, screen.reference_date or "", t0)
            if documented is None:   # the record holds no image (the IBM letters are text only)
                screen.variables.append(ScreenVariable(
                    name=v.name, label=v.label, state="not_available", priority=v.priority, tier=v.tier,
                    output="image", headline=Headline(text="No images in the record"), resolved_ms=_elapsed_ms(t0),
                ))
                continue
        else:
            documented = _documented_variable(v, store, screen.reference_date or "", t0)
        if documented and shown_fact_ids and set(documented.fact_ids) <= shown_fact_ids:
            continue   # everything on this card is already on the clinician's screen
        screen.variables.append(documented or ScreenVariable(
            name=v.name, label=v.label, state="pending", priority=v.priority, tier=v.tier, output=v.output,
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


async def get_summary(screen: FastScreen) -> dict:
    """
    Ask the LLM to produce a short 'Role, Situation' label and a matching icon URL.
    Falls back to the raw situation text and a generic icon if the LLM call fails.

    The icon_url points to a publicly available Lucide SVG via unpkg, e.g.:
      https://unpkg.com/lucide-static@latest/icons/stethoscope.svg
    """
    prompt = (
        "You are a summariser. Given a clinical situation description, return a JSON object "
        "with exactly two keys:\n"
        "  \"summary\": a very short label in the form \"<Role>, <Situation>\" "
        "(e.g. \"Anaesthetist, PreOp Info\"). Use title case. No punctuation at the end.\n"
        "  \"icon\": the name of the single most fitting Lucide icon for the clinical role "
        "(kebab-case filename without extension, e.g. \"stethoscope\", \"heart-pulse\", "
        "\"scissors\", \"eye\", \"bone\", \"brain\", \"activity\", \"user\", \"baby\", "
        "\"syringe\", \"scan\", \"flask-conical\", \"microscope\", \"thermometer\"). "
        "Pick the icon that best matches the clinician's specialty or task. "
        "Return only the JSON object, no explanation.\n\n"
        f"Situation: {screen.situation}"
    )
    fallback = {"summary": screen.situation, "icon_url": "https://unpkg.com/lucide-static@latest/icons/user.svg"}
    try:
        result = await get_client().generate(
            prompt_name="fp_summary",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            model=settings.RUNTIME_MODEL,
        )
        text = str(result).strip()
        # Strip markdown code fences if the model wrapped the JSON
        if text.startswith("```"):
            text = re.sub(r"^```[^\n]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text.strip())
        data = json.loads(text)
        summary = str(data.get("summary", "")).strip() or screen.situation
        icon_name = str(data.get("icon", "user")).strip().lower()
        # Allow only safe kebab-case names to build the URL
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", icon_name):
            icon_url = f"https://unpkg.com/lucide-static@latest/icons/{icon_name}.svg"
        else:
            icon_url = "https://unpkg.com/lucide-static@latest/icons/user.svg"
        return {"summary": summary, "icon_url": icon_url}
    except Exception:
        logger.exception("fastpath: summary generation failed for screen %s", screen.screen_id)
        return fallback
