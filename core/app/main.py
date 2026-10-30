"""
app/main.py – FastAPI application with all routes (section 8 of the design doc).

Routes:
  POST /api/screen                  → Screen
  POST /api/screen/stream           → SSE: plan | section | done events
  GET  /api/documents               → list of documents
  GET  /api/documents/{doc_id}      → document text + passages
  GET  /api/passages/{passage_id}   → single passage with offsets
  GET  /api/passages/{passage_id}/context → passage inside its document section
  GET  /api/v2/patients/search?q=   → patient search over data/*/patient.json
  GET  /api/v2/patients/{patient_id} → patient header
  GET  /api/contradictions          → all precomputed contradictions
  GET  /healthz                     → health check
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from app import images, originals, patient
from app.config import settings
from app.runtime import answerer, planner, screen, validator
from app.schemas import Screen
from app.store import FactStore, UnknownPatient, available_patients, get_store, load_store

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


def _store_for(patient_id: Optional[str]) -> FactStore:
    """The patient's store (default patient when None); 404 for a patient without a cache."""
    try:
        return get_store(patient_id)
    except UnknownPatient:
        raise HTTPException(status_code=404, detail=f"Patient '{patient_id}' has no cache – import it first")


@app.get("/api/documents")
async def get_documents(patient_id: Optional[str] = None) -> list[dict[str, Any]]:
    store = _store_for(patient_id)
    return [
        {"doc_id": d.doc_id, "title": d.title, "date": d.date, "type": d.type}
        for d in store.documents.values()
    ]


@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: str, patient_id: Optional[str] = None) -> dict[str, Any]:
    store = _store_for(patient_id)
    doc = store.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")
    passages = store.passages_for_doc(doc_id)
    return {
        **doc.model_dump(),
        "passages": [p.model_dump() for p in passages],
    }


@app.get("/api/passages/{passage_id}")
async def get_passage(passage_id: str, patient_id: Optional[str] = None) -> dict[str, Any]:
    store = _store_for(patient_id)
    p = store.get_passage(passage_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Passage '{passage_id}' not found")
    return p.model_dump()


def _section_bounds(passages: list, idx: int) -> tuple[int, int]:
    """Index range [lo, hi] of the section around passages[idx].

    S0 labels a section's header passage with the *previous* section's name, so a
    section is the run of non-header passages sharing a name, plus the header just before it.
    """
    name = passages[idx].section
    if passages[idx].kind == "header":
        # A header opens the following section
        if idx + 1 < len(passages) and passages[idx + 1].kind != "header":
            idx += 1
            name = passages[idx].section
        else:
            return idx, idx
    lo = hi = idx
    while lo > 0 and passages[lo - 1].section == name and passages[lo - 1].kind != "header":
        lo -= 1
    while hi + 1 < len(passages) and passages[hi + 1].section == name and passages[hi + 1].kind != "header":
        hi += 1
    if lo > 0 and passages[lo - 1].kind == "header":
        lo -= 1
    return lo, hi


@app.get("/api/passages/{passage_id}/context")
async def get_passage_context(passage_id: str, patient_id: Optional[str] = None) -> dict[str, Any]:
    """
    A passage inside its document section, for showing a source without the whole letter:
    {doc: {doc_id, title, date, type, department}, passage, section: {name, start, end, text}}.
    Offsets are into the full document text, so the caller can highlight passage
    start/end inside the section and later open the full document at the same place.
    """
    store = _store_for(patient_id)
    p = store.get_passage(passage_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Passage '{passage_id}' not found")
    doc = store.get_document(p.doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document '{p.doc_id}' not found")
    passages = store.passages_for_doc(p.doc_id)
    idx = next((i for i, q in enumerate(passages) if q.passage_id == passage_id), None)
    if idx is None:
        start, end, name = p.start, p.end, p.section
    else:
        lo, hi = _section_bounds(passages, idx)
        start, end, name = passages[lo].start, passages[hi].end, passages[hi].section
    start, end = min(start, p.start), max(end, p.end)
    doc_info = doc.model_dump()
    return {
        "doc": {k: doc_info.get(k) for k in ("doc_id", "title", "date", "type", "department")},
        "passage": p.model_dump(),
        "section": {"name": name, "start": start, "end": end, "text": doc.text[start:end]},
    }


# ---------------------------------------------------------------------------
# Original source files (as delivered, HTML with <BR>) and where a passage sits in them
# ---------------------------------------------------------------------------

@app.get("/api/documents/{doc_id}/original")
async def get_original_document(doc_id: str, patient_id: Optional[str] = None) -> dict[str, Any]:
    """The original file: {doc_id, file, metadata, text} – text as delivered (HTML)."""
    original = originals.load_original(doc_id, _store_for(patient_id).patient_id)
    if original is None:
        raise HTTPException(status_code=404, detail=f"No original file for document '{doc_id}'")
    return original


@app.get("/api/documents/{doc_id}/original/file")
async def get_original_file(doc_id: str, patient_id: Optional[str] = None) -> FileResponse:
    """The original JSON file itself, unchanged."""
    path = originals.original_file(doc_id, _store_for(patient_id).patient_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No original file for document '{doc_id}'")
    return FileResponse(path, media_type="application/json", filename=path.name)


@app.get("/api/passages/{passage_id}/original")
async def get_passage_original(passage_id: str, patient_id: Optional[str] = None) -> dict[str, Any]:
    """
    The original file of a passage and the passage's position in its raw text:
    text[raw_start:raw_end] is the passage as delivered (with <BR>, entities).
    """
    store = _store_for(patient_id)
    p = store.get_passage(passage_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Passage '{passage_id}' not found")
    original = originals.load_original(p.doc_id, store.patient_id)
    if original is None:
        raise HTTPException(status_code=404, detail=f"No original file for document '{p.doc_id}'")
    span = originals.raw_span(original["text"], p.start, p.end)
    if span is None:
        raise HTTPException(status_code=500, detail="Passage offsets do not fit the original text")
    return {
        **original,
        "passage_id": passage_id,
        "raw_start": span[0],
        "raw_end": span[1],
        "raw_quote": original["text"][span[0]:span[1]],
        "quote": p.text,
    }


# Declared before /api/v2/patients/{patient_id}, which would otherwise match "search"
@app.get("/api/v2/patients/search")
async def search_patients(q: str = "", limit: int = Query(10, ge=1, le=50)) -> list[dict[str, Any]]:
    """Patients whose name or ID contains q (case-insensitive): {id, name, dateOfBirth, …} per patient."""
    return patient.search_patients(q, limit)


@app.get("/api/v2/patients/{patient_id}")
async def get_patient_profile(patient_id: str) -> dict[str, Any]:
    """
    The header the frontend always shows: name, sex, birth date, age on the reference date,
    CAVE (allergies with sources), latest height / weight / BMI, latest stay, record overview.
    """
    return patient.build_profile(_store_for(patient_id))


@app.get("/api/v2/patients")
async def list_patients() -> list[dict[str, Any]]:
    """Every patient with a cache: id, name, sex, birth date, source (IBM letters or FHIR import), images."""
    out = []
    for pid in available_patients():
        info = patient.patient_json(pid)
        out.append({"patient_id": pid, "name": info.get("name"), "sex": info.get("sex"), "birth_date": info.get("dateOfBirth"),
                    "source": info.get("source", "ibm"), "images": len(images.catalogue(settings.CACHE_DIR, pid)),
                    "default": pid == settings.PATIENT_ID})
    return out


# ---------------------------------------------------------------------------
# Images (DICOM) of a patient: quick previews, single slices, the original file
# ---------------------------------------------------------------------------

def _image(patient_id: str, image_id: str) -> dict:
    info = _store_for(patient_id).images.get(image_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"No image '{image_id}' for patient '{patient_id}'")
    return info


def _dicom(patient_id: str, image_id: str) -> dict:
    """The image, when its DICOM file is on this machine (DATA_DIR); the preview needs only the cache."""
    info = _image(patient_id, image_id)
    if not Path(info["dicom"]).exists():
        raise HTTPException(status_code=404, detail=f"DICOM file of '{image_id}' is not in DATA_DIR on this machine "
                                                    f"({info['dicom']}); the preview still works")
    return info


@app.get("/api/v2/patients/{patient_id}/images")
async def list_images(patient_id: str) -> list[dict[str, Any]]:
    """The patient's images with their links (same ImageRef as in sources)."""
    store = _store_for(patient_id)
    out = []
    for image_id, info in store.images.items():
        fact = next((f for f in store.facts.values() if f.fields.get("image_id") == image_id), None)
        ref = images.image_ref(fact, store) if fact else None
        out.append({**(ref.model_dump() if ref else {"image_id": image_id}), "fact_id": fact.fact_id if fact else None,
                    "title": fact.en if fact else None, "rows": info.get("rows"), "columns": info.get("columns")})
    return out


@app.get("/api/v2/patients/{patient_id}/images/{image_id}/preview.png")
async def image_preview(patient_id: str, image_id: str) -> FileResponse:
    """Axial, coronal and sagittal middle slice side by side (rendered at import)."""
    info = _image(patient_id, image_id)
    return FileResponse(settings.CACHE_DIR / patient_id / "images" / info["preview"], media_type="image/png")


@app.get("/api/v2/patients/{patient_id}/images/{image_id}/slice.png")
async def image_slice(patient_id: str, image_id: str, axis: str = "axial", index: Optional[int] = None) -> Response:
    """One slice as PNG (index 0 … frames-1; default the middle). Header X-Slice-Count: slices on that axis."""
    if axis not in images.AXES:
        raise HTTPException(status_code=400, detail=f"axis must be one of {images.AXES}")
    info = _dicom(patient_id, image_id)
    png, count = await asyncio.to_thread(images.render_slice, Path(info["dicom"]), axis, index)
    return Response(png, media_type="image/png", headers={"X-Slice-Count": str(count), "Cache-Control": "max-age=3600"})


@app.get("/api/v2/patients/{patient_id}/images/{image_id}/dicom")
async def image_dicom(patient_id: str, image_id: str) -> FileResponse:
    """The original DICOM file."""
    info = _dicom(patient_id, image_id)
    return FileResponse(info["dicom"], media_type="application/dicom", filename=Path(info["dicom"]).name)


@app.get("/api/contradictions")
async def get_contradictions(patient_id: Optional[str] = None) -> list[dict[str, Any]]:
    store = _store_for(patient_id)
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
