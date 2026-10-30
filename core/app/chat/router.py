"""
app/chat/router.py – questions about the patient, mounted under /api/v2.

  POST /api/v2/chat              {"question", "chat_id"?, "screen_id"?, "cards"?, "wait"?} → answer with sources
                                 (cards=true: also screen cards for the question, see docs/screen-api.md)
                                 (pass the returned chat_id back for follow-up questions)
  POST /api/v2/chat/stream       same body, SSE: "answer" (text, ~1.5 s), "cards" on every change
                                 (store facts first, then text-search results), then "done"
  GET  /api/v2/chat/{chat_id}    the conversation so far
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.fastpath import service as fastpath
from app.chat import service
from app.chat.models import Chat, ChatReply, ChatRequest
from app.fastpath.router import store_for
from app.store import FactStore

router = APIRouter(prefix="/api/v2", tags=["chat"])


def _patient_store(req: ChatRequest) -> FactStore:
    """The patient asked about: given explicitly, else the one of the chat or of the screen."""
    if req.chat_id and service.get_chat(req.chat_id) is None:
        raise HTTPException(status_code=404, detail=f"Chat '{req.chat_id}' not found")
    chat = service.get_chat(req.chat_id) if req.chat_id else None
    screen = fastpath.get_screen(req.screen_id) if req.screen_id else None
    if chat and screen and chat.patient_id and screen.patient_id and chat.patient_id != screen.patient_id:
        # The screen was switched to another patient (PUT /screens/{id}/patient): a new conversation
        req.chat_id, chat = None, None
    known = {p for p in (chat.patient_id if chat else "", screen.patient_id if screen else "") if p}
    if req.patient_id and known - {req.patient_id} or len(known) > 1:
        raise HTTPException(status_code=400, detail="chat, screen and patient_id belong to different patients")
    return store_for(req.patient_id or next(iter(known), None))


@router.post("/chat", response_model=ChatReply)
async def ask(req: ChatRequest) -> ChatReply:
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Empty question")
    store = _patient_store(req)
    try:
        return await service.ask(req.question.strip(), store, req.chat_id, req.screen_id, req.cards, req.wait)
    except Exception as exc:  # quota / network: say so instead of a bare 500
        raise HTTPException(status_code=502, detail=f"Could not answer: {type(exc).__name__}") from exc


@router.post("/chat/stream")
async def ask_stream(req: ChatRequest) -> StreamingResponse:
    """Answer first, cards as they come (cards default to on here)."""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Empty question")
    store = _patient_store(req)
    cards = req.cards or "cards" not in req.model_fields_set

    async def events():
        async for name, data in service.stream(req.question.strip(), store, req.chat_id, req.screen_id, cards):
            yield f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/chat/{chat_id}", response_model=Chat)
async def get_chat(chat_id: str) -> Chat:
    chat = service.get_chat(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail=f"Chat '{chat_id}' not found")
    return chat
