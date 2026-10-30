"""
app/chat/router.py – questions about the patient, mounted under /api/v2.

  POST /api/v2/chat              {"question", "chat_id"?, "screen_id"?, "cards"?, "wait"?} → answer with sources
                                 (cards=true: also screen cards for the question, see docs/screen-api.md)
                                 (pass the returned chat_id back for follow-up questions)
  GET  /api/v2/chat/{chat_id}    the conversation so far
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.chat import service
from app.chat.models import Chat, ChatReply, ChatRequest
from app.store import get_store

router = APIRouter(prefix="/api/v2", tags=["chat"])


@router.post("/chat", response_model=ChatReply)
async def ask(req: ChatRequest) -> ChatReply:
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Empty question")
    if req.chat_id and service.get_chat(req.chat_id) is None:
        raise HTTPException(status_code=404, detail=f"Chat '{req.chat_id}' not found")
    try:
        return await service.ask(req.question.strip(), get_store(), req.chat_id, req.screen_id, req.cards, req.wait)
    except Exception as exc:  # quota / network: say so instead of a bare 500
        raise HTTPException(status_code=502, detail=f"Could not answer: {type(exc).__name__}") from exc


@router.get("/chat/{chat_id}", response_model=Chat)
async def get_chat(chat_id: str) -> Chat:
    chat = service.get_chat(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail=f"Chat '{chat_id}' not found")
    return chat
