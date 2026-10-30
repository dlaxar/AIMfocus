"""
app/fastpath/router.py – REST API for the fast path, mounted under /api/v2.

  Every route takes an optional patient_id (query, or in the POST body); default PATIENT_ID.

  GET  /api/v2/schema                     fact-store structure (types, fields, concepts)
  GET  /api/v2/concepts[?type=]           concepts, optionally filtered by fact type
  GET  /api/v2/concepts/{concept_id}      one concept with its facts and sources
  GET  /api/v2/facts/{fact_id}            one fact (or derived fact) with its source
  POST /api/v2/screens                    {"situation": ...} → 202 with the screen ID at once
  GET  /api/v2/screens?situation=...      same, for quick tests from a browser / curl
  GET  /api/v2/screens/{id}[?wait=true]   current state; wait=true blocks until complete
                                          (&final=true: also until the upgrade run is done)
  GET  /api/v2/screens/{id}/stream        SSE: a "screen" event per update (the whole screen,
                                          also when the upgrade replaces it), then "done"
  GET  /api/v2/simulated/stream[?interval=1]  same SSE, replaying the recorded screens in streams/

  Every screen is built twice in parallel: RUNTIME_MODEL (fast, shown first) and UPGRADE_MODEL
  (slower); the slower one replaces the fast one under the same ID when it is done.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.fastpath import service
from app.fastpath.models import FastScreen, ScreenRequest
from app.runtime.screen import _best_passage_id, _build_source
from app.schemas import Fact
from app.store import FactStore, UnknownPatient, get_store

router = APIRouter(prefix="/api/v2", tags=["fast path (mockup)"])


def store_for(patient_id: Optional[str]) -> FactStore:
    """The patient's store (default patient when None); 404 for a patient without a cache."""
    try:
        return get_store(patient_id)
    except UnknownPatient:
        raise HTTPException(status_code=404, detail=f"Patient '{patient_id}' has no cache – import it first")


# ---------------------------------------------------------------------------
# Fact store
# ---------------------------------------------------------------------------

@router.get("/schema")
async def get_schema(patient_id: Optional[str] = None) -> dict[str, Any]:
    store = store_for(patient_id)
    by_type: dict[str, int] = {}
    for f in store.facts.values():
        by_type[f.type] = by_type.get(f.type, 0) + 1
    return {
        "patient_reference_date": store.reference_date(),
        "fact_fields": {name: str(field.annotation) for name, field in Fact.model_fields.items()},
        "fact_counts_by_type": by_type,
        "derived_fact_count": len(store.derived),
        "concepts": [
            {"concept_id": c.concept_id, "type": c.type, "label_en": c.label_en, "fact_count": len(c.fact_ids)}
            for c in sorted(store.concepts.values(), key=lambda c: c.concept_id)
        ],
    }


@router.get("/concepts")
async def list_concepts(type: Optional[str] = None, patient_id: Optional[str] = None) -> list[dict[str, Any]]:
    store = store_for(patient_id)
    return [
        {"concept_id": c.concept_id, "type": c.type, "label_en": c.label_en, "fact_count": len(c.fact_ids)}
        for c in sorted(store.concepts.values(), key=lambda c: c.concept_id)
        if type is None or c.type == type
    ]


def _fact_payload(fact_id: str, store: FactStore) -> dict[str, Any]:
    fact = store.get_any_fact(fact_id)
    if fact is None:
        raise HTTPException(status_code=404, detail=f"Fact '{fact_id}' not found")
    pid = _best_passage_id(fact_id, store)
    source = _build_source(pid, store) if pid else None
    return {**fact.model_dump(), "source": source.model_dump() if source else None}


@router.get("/concepts/{concept_id}")
async def get_concept(concept_id: str, patient_id: Optional[str] = None) -> dict[str, Any]:
    store = store_for(patient_id)
    concept = store.concepts.get(concept_id)
    if concept is None:
        raise HTTPException(status_code=404, detail=f"Concept '{concept_id}' not found")
    return {**concept.model_dump(), "facts": [_fact_payload(fid, store) for fid in concept.fact_ids]}


@router.get("/facts/{fact_id}")
async def get_fact(fact_id: str, patient_id: Optional[str] = None) -> dict[str, Any]:
    return _fact_payload(fact_id, store_for(patient_id))


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------

async def _create(situation: str, patient_id: Optional[str], upgrade: bool = True) -> JSONResponse:
    if not situation.strip():
        raise HTTPException(status_code=422, detail="situation must not be empty")
    screen = await service.create_screen(situation, store_for(patient_id), upgrade=upgrade)
    code = 200 if screen.status == "complete" else 202
    return JSONResponse(
        status_code=code,
        content=screen.model_dump(),
        headers={"Location": f"/api/v2/screens/{screen.screen_id}"},
    )


@router.post("/screens", response_model=FastScreen, status_code=202)
async def post_screen(req: ScreenRequest) -> JSONResponse:
    return await _create(req.situation, req.patient_id, req.upgrade)


@router.get("/screens", response_model=FastScreen)
async def get_screen_by_situation(situation: str = Query(..., min_length=1), patient_id: Optional[str] = None,
                                  upgrade: bool = True) -> JSONResponse:
    return await _create(situation, patient_id, upgrade)


@router.get("/screens/{screen_id}", response_model=FastScreen)
async def get_screen(screen_id: str, wait: bool = False, final: bool = False) -> FastScreen:
    screen = await service.wait_for(screen_id, final=final) if wait else service.get_screen(screen_id)
    if screen is None:
        raise HTTPException(status_code=404, detail=f"Screen '{screen_id}' not found")
    return screen


@router.get("/screens/{screen_id}/summary")
async def get_screen_summary(screen_id: str) -> dict[str, str]:
    screen = service.get_screen(screen_id)
    if screen is None:
        raise HTTPException(status_code=404, detail=f"Screen '{screen_id}' not found")
    return await service.get_summary(screen)


@router.get("/screens/{screen_id}/stream")
async def stream_screen(screen_id: str) -> StreamingResponse:
    if service.get_screen(screen_id) is None:
        raise HTTPException(status_code=404, detail=f"Screen '{screen_id}' not found")

    async def _events() -> AsyncGenerator[str, None]:
        last_version = -1
        while True:
            screen = service.get_screen(screen_id)
            if screen is None:
                return
            if screen.version != last_version:
                last_version = screen.version
                yield f"event: screen\ndata: {service.screen_json(screen)}\n\n"
            if screen.status == "complete" and screen.upgrade != "running":
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(0.2)

    return StreamingResponse(_events(), media_type="text/event-stream")


@router.get("/simulated/stream")
async def stream_simulated(interval: float = Query(1.0, ge=0, le=60)) -> StreamingResponse:
    """Replays streams/0.json, 1.json, … as "screen" events, `interval` seconds apart, then "done"."""
    files = sorted(
        (p for p in settings.STREAMS_DIR.glob("*.json") if p.stem.isdigit()),
        key=lambda p: int(p.stem),
    )
    if not files:
        raise HTTPException(status_code=404, detail=f"No recorded screens in {settings.STREAMS_DIR}")
    # Read up front so a broken file fails the request instead of the stream half-way
    screens = [json.loads(p.read_text(encoding="utf-8")) for p in files]

    async def _events() -> AsyncGenerator[str, None]:
        for i, screen in enumerate(screens):
            if i:
                await asyncio.sleep(interval)
            yield f"event: screen\ndata: {json.dumps(screen, ensure_ascii=False)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(_events(), media_type="text/event-stream")
