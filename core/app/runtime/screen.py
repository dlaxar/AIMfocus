"""
app/runtime/screen.py – R4: Assemble the final Screen from validated items.

Resolves fact_ids → passages → sources with char offsets.
Prefers original passages over copies.
Attaches relevant contradictions.
Sorts sections by need priority.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.runtime.validator import ValidatedItem
from app.schemas import (
    Contradiction,
    Need,
    PlannerOutput,
    Screen,
    ScreenItem,
    ScreenMeta,
    ScreenSection,
    Source,
)
from app.store import FactStore

logger = logging.getLogger(__name__)


def _build_source(passage_id: str, store: FactStore) -> Optional[Source]:
    p = store.get_passage(passage_id)
    if p is None:
        return None
    doc = store.get_document(p.doc_id)
    return Source(
        doc_id=p.doc_id,
        doc_title=doc.title if doc else p.doc_id,
        doc_date=doc.date if doc else None,
        passage_id=p.passage_id,
        start=p.start,
        end=p.end,
        quote_de=p.text,
    )


def _best_passage_id(fact_id: str, store: FactStore) -> Optional[str]:
    """Return the passage_id for a fact, preferring the original over a copy."""
    fact = store.get_fact(fact_id)
    if fact is None:
        df = store.derived.get(fact_id)
        if df and df.inputs:
            return _best_passage_id(df.inputs[0], store)
        return None

    p = store.get_passage(fact.passage_id)
    if p is None:
        return fact.passage_id

    # If this passage is a copy, use the original
    if p.copy_of:
        orig = store.get_passage(p.copy_of)
        if orig:
            return orig.passage_id
    return p.passage_id


def assemble(
    plan: PlannerOutput,
    validated_items: list[ValidatedItem],
    store: FactStore,
    reference_date: Optional[str],
    timings_ms: dict[str, float],
    model_names: dict[str, str],
    situation: str,
    dropped_items: int,
) -> Screen:
    need_map = {n.need_id: n for n in plan.needs}

    sections: list[ScreenSection] = []
    touched_concept_ids: set[str] = set()

    for vi in validated_items:
        need = need_map.get(vi.item.need_id)
        label = need.label if need else vi.item.need_id

        # Build ScreenItem
        sources: list[Source] = []
        seen_passages: set[str] = set()

        if not vi.item.absent:
            for fid in vi.item.fact_ids:
                pid = _best_passage_id(fid, store)
                if pid and pid not in seen_passages:
                    src = _build_source(pid, store)
                    if src:
                        sources.append(src)
                    seen_passages.add(pid)

                    # Also add the copy passage for context if it exists
                    p = store.get_passage(pid)
                    if p and p.copy_of:
                        copy_pid = p.copy_of
                        if copy_pid not in seen_passages:
                            copy_src = _build_source(copy_pid, store)
                            if copy_src:
                                sources.append(copy_src)
                            seen_passages.add(copy_pid)

                # Track concept IDs for contradiction filtering
                fact = store.get_fact(fid)
                if fact and fact.concept_id:
                    touched_concept_ids.add(fact.concept_id)

        screen_item = ScreenItem(
            kind=vi.screen_kind,  # type: ignore[arg-type]
            text_en=vi.item.text_en,
            fact_ids=vi.item.fact_ids,
            sources=sources,
        )

        # Find existing section for this need or create new one
        existing = next((s for s in sections if s.need_id == vi.item.need_id), None)
        if existing:
            existing.items.append(screen_item)
        else:
            sections.append(ScreenSection(
                need_id=vi.item.need_id,
                label=label,
                items=[screen_item],
            ))

    # Sort sections by need priority
    def _priority(section: ScreenSection) -> int:
        n = need_map.get(section.need_id)
        return n.priority if n else 99

    sections.sort(key=_priority)

    # Contradiction filtering
    relevant_cx = store.contradictions_for_concepts(touched_concept_ids)
    other_cx = [cx for cx in store.contradictions if cx not in relevant_cx]

    # Inline contradiction warnings on items
    cx_by_concept: dict[str, list[Contradiction]] = {}
    for cx in relevant_cx:
        for cid in cx.concept_ids:
            cx_by_concept.setdefault(cid, []).append(cx)

    for section in sections:
        for item in section.items:
            for fid in item.fact_ids:
                fact = store.get_fact(fid)
                if fact and fact.concept_id and fact.concept_id in cx_by_concept:
                    cxs = cx_by_concept[fact.concept_id]
                    item.warning = "; ".join(cx.summary_en for cx in cxs[:2])

    timings_ms["total"] = sum(timings_ms.values())

    meta = ScreenMeta(
        timings_ms=timings_ms,
        models=model_names,
        cache_version=store.manifest.completed_at if store.manifest else "",
        dropped_items=dropped_items,
    )

    return Screen(
        situation=situation,
        role=plan.role,
        task=plan.task,
        reference_date=reference_date,
        sections=sections,
        contradictions=relevant_cx,
        other_contradictions=other_cx,
        meta=meta,
    )
