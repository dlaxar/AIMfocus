"""
app/chat/models.py – LLM output schema and API models for questions about the patient.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.fastpath.models import ScreenVariable
from app.schemas import Source


class ChatAnswer(BaseModel):
    """What the chat call returns. Citations are checked in code before anyone sees the answer."""
    answer: str = ""
    found: bool = False
    facts: list[str] = Field(default_factory=list)     # fact handles (f40, d3)
    passages: list[str] = Field(default_factory=list)  # passage IDs, for what the facts do not cover
    quotes: list[str] = Field(default_factory=list)    # verbatim German quotes from those passages


AnswerState = Literal[
    "answered",         # backed by the cited facts / passages
    "not_documented",   # the documents do not answer it
    "check",            # an answer exists but failed validation – show with a warning
]


class ChatRequest(BaseModel):
    question: str
    chat_id: Optional[str] = None     # continue a conversation (follow-up questions)
    screen_id: Optional[str] = None   # the clinician's screen, so follow-ups can refer to it
    cards: bool = False               # also answer with screen cards (value / trend / table / list)
    wait: bool = True                 # cards: in this reply (default); false = stream them via cards_screen_id


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    state: Optional[AnswerState] = None
    sources: list[Source] = Field(default_factory=list)


class ChatReply(BaseModel):
    chat_id: str
    answer: str
    state: AnswerState
    sources: list[Source] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    note: Optional[str] = None        # why an answer is "check"
    # With cards=true: a screen built for this question, same schema as /api/v2/screens.
    # cards_screen_id can be streamed (/api/v2/screens/{id}/stream); with wait=true the
    # finished cards are in `cards` already.
    cards_screen_id: Optional[str] = None
    cards_status: Optional[str] = None    # starting / partial / complete
    cards: list[ScreenVariable] = Field(default_factory=list)
    timings_ms: dict[str, float] = Field(default_factory=dict)


class Chat(BaseModel):
    chat_id: str
    screen_id: Optional[str] = None
    messages: list[ChatMessage] = Field(default_factory=list)
