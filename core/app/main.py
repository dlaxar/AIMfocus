"""
app/main.py – FastAPI application with all routes (section 8 of the design doc).

Routes:
  POST /api/screen                  → Screen
  POST /api/screen/stream           → SSE: plan | section | done events
  GET  /api/documents               → list of documents
  GET  /api/documents/{doc_id}      → document text + passages
  GET  /api/passages/{passage_id}   → single passage with offsets
  GET  /api/contradictions          → all precomputed contradictions
  GET  /healthz                     → health check
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.runtime import answerer, planner, screen, validator
from app.schemas import Screen
from app.store import FactStore, load_store, get_store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_store()
    logger.info("FactStore loaded and ready")
    yield


app = FastAPI(
    title="Clinical Documentation Screen API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Fast-path mockup (/api/v2): immediate answers from the fact store + parallel text search
from app.fastpath.router import router as fastpath_router  # noqa: E402

app.include_router(fastpath_router)

# Questions about the patient (/api/v2/chat): one cached-prefix LLM call per question
from app.chat.router import router as chat_router  # noqa: E402

app.include_router(chat_router)

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ScreenRequest(BaseModel):
    situation: str


# ---------------------------------------------------------------------------
# Core pipeline (shared by /screen and /screen/stream)
# ---------------------------------------------------------------------------

async def _run_pipeline(situation: str, store: FactStore, sse_queue: asyncio.Queue | None = None):
    """
    Run R1 → R2 → R3 → R4 and return a Screen.
    If sse_queue is provided, push partial events to it.
    """
    ref_date = store.reference_date() or ""
    timings: dict[str, float] = {}

    # R1 – Planner
    t0 = time.perf_counter()
    plan_output = await planner.plan(
        situation=situation,
        reference_date=ref_date,
        available_types=store.available_types(),
        concept_labels=store.concept_labels_sample(),
    )
    timings["plan"] = round((time.perf_counter() - t0) * 1000, 1)

    if sse_queue:
        await sse_queue.put({"event": "plan", "data": plan_output.model_dump()})

    # R2 – Answerer
    t0 = time.perf_counter()
    answerer_items = await answerer.answer(
        needs=plan_output.needs,
        store=store,
        reference_date=ref_date,
    )
    timings["answer"] = round((time.perf_counter() - t0) * 1000, 1)

    # R3 – Validator
    t0 = time.perf_counter()
    validated, dropped = validator.validate_all(
        items=answerer_items,
        fact_getter=store.get_any_fact,
        facts_by_category=store.facts_by_type,
        needs=plan_output.needs,
    )

    # Retry failed documented items once
    retry_ids = [vi.item.need_id for vi in validated if not vi.result.passed and not vi.item.absent]
    if retry_ids:
        retry_needs = [n for n in plan_output.needs if n.need_id in retry_ids]
        retry_items = await answerer.answer(
            needs=retry_needs,
            store=store,
            reference_date=ref_date,
        )
        retry_validated, retry_dropped = validator.validate_all(
            items=retry_items,
            fact_getter=store.get_any_fact,
            facts_by_category=store.facts_by_type,
            needs=retry_needs,
        )
        dropped += retry_dropped
        # Replace original validated items for those need_ids
        by_need = {vi.item.need_id: vi for vi in retry_validated}
        validated = [by_need.get(vi.item.need_id, vi) for vi in validated]

    timings["validate"] = round((time.perf_counter() - t0) * 1000, 1)

    # R4 – Screen assembly
    t0 = time.perf_counter()
    result = screen.assemble(
        plan=plan_output,
        validated_items=validated,
        store=store,
        reference_date=ref_date,
        timings_ms=timings,
        model_names={
            "planner": settings.RUNTIME_MODEL,
            "answerer": settings.RUNTIME_MODEL,
        },
        situation=situation,
        dropped_items=dropped,
    )
    timings["assemble"] = round((time.perf_counter() - t0) * 1000, 1)

    if sse_queue:
        for section in result.sections:
            await sse_queue.put({"event": "section", "data": section.model_dump()})
        await sse_queue.put({"event": "done", "data": result.meta.model_dump()})

    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/api/screen", response_model=Screen)
async def post_screen(req: ScreenRequest) -> Screen:
    store = get_store()
    return await _run_pipeline(req.situation, store)


@app.post("/api/screen/stream")
async def post_screen_stream(req: ScreenRequest) -> StreamingResponse:
    store = get_store()
    queue: asyncio.Queue = asyncio.Queue()

    async def _bg():
        try:
            await _run_pipeline(req.situation, store, sse_queue=queue)
        except Exception as exc:
            await queue.put({"event": "error", "data": str(exc)})
        finally:
            await queue.put(None)  # sentinel

    asyncio.create_task(_bg())

    async def _generate() -> AsyncGenerator[str, None]:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


@app.get("/api/documents")
async def get_documents() -> list[dict[str, Any]]:
    store = get_store()
    return [
        {"doc_id": d.doc_id, "title": d.title, "date": d.date, "type": d.type}
        for d in store.documents.values()
    ]


@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: str) -> dict[str, Any]:
    store = get_store()
    doc = store.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")
    passages = store.passages_for_doc(doc_id)
    return {
        **doc.model_dump(),
        "passages": [p.model_dump() for p in passages],
    }


@app.get("/api/passages/{passage_id}")
async def get_passage(passage_id: str) -> dict[str, Any]:
    store = get_store()
    p = store.get_passage(passage_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Passage '{passage_id}' not found")
    return p.model_dump()


@app.get("/api/contradictions")
async def get_contradictions() -> list[dict[str, Any]]:
    store = get_store()
    return [cx.model_dump() for cx in store.contradictions]


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    store = get_store()
    return {
        "status": "ok",
        "cache_loaded": store.manifest is not None,
        "manifest_version": store.manifest.completed_at if store.manifest else None,
        "model": settings.RUNTIME_MODEL,
        "patient_id": settings.PATIENT_ID,
        "fact_count": len(store.facts),
        "contradiction_count": len(store.contradictions),
    }
