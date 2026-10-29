"""
app/chat/service.py – answers a clinician's questions about the patient, one LLM call each.

The fixed part of the prompt (rules, the fact store under short handles, the raw text of
all letters) is the same for every question and is sent as a cached prefix, so a question
costs little more than its own tokens. The conversation so far and the clinician's screen
follow it, so follow-up questions work. The model names the facts and passages it used;
code checks them (handles exist, quotes verbatim, numbers backed) and builds the sources.

Chats live in memory (single process), like screens.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Optional

from app.chat.models import Chat, ChatAnswer, ChatMessage, ChatReply
from app.config import settings
from app.fastpath import service as fastpath
from app.llm.client import get_client
from app.runtime.screen import _build_source
from app.schemas import Source
from app.store import FactStore

logger = logging.getLogger(__name__)

_chats: dict[str, Chat] = {}
_HISTORY_TURNS = 6   # earlier question/answer pairs sent with a follow-up


def get_chat(chat_id: str) -> Optional[Chat]:
    return _chats.get(chat_id)


def _fixed_prompt(store: FactStore) -> str:
    return (
        fastpath._prompt("chat")
        .replace("{{catalogue}}", fastpath.catalogue(store).text)
        .replace("{{passages}}", fastpath.passages_block(store))
    )


def _question_part(question: str, chat: Chat, store: FactStore) -> str:
    lines = [f"## Reference date\n{store.reference_date() or 'unknown'}"]
    screen = fastpath.get_screen(chat.screen_id) if chat.screen_id else None
    if screen is not None:
        lines.append(f"\n## Clinician's screen\nRole: {screen.role or '-'}. Task: {screen.task or screen.situation}")
        for v in screen.variables:
            lines.append(f"- {v.label}: {v.value or v.state}")
    history = chat.messages[-2 * _HISTORY_TURNS:]
    if history:
        lines.append("\n## Conversation so far")
        lines += [f"{'Clinician' if m.role == 'user' else 'You'}: {m.content}" for m in history]
    lines.append(f"\n## Question\n{question}")
    return "\n".join(lines)


def _with_day_month_dates(text: str) -> str:
    """Add 05.04.2016 / 5.4.2016 / 5 for every 2016-04-05, so an answer may write "5 Apr 2016" or as the letters do."""
    extra = [f"{d}.{m}.{y} {int(d)}.{int(m)}.{y} {int(d)}" for y, m, d in re.findall(r"(\d{4})-(\d{2})-(\d{2})", text)]
    return " ".join([text, *extra])


def _check(out: ChatAnswer, store: FactStore) -> tuple[list[str], list[str], list[str]]:
    """Resolve and check the citations. Returns (fact_ids, passage_ids, errors)."""
    cat = fastpath.catalogue(store)
    errors: list[str] = []
    fact_ids, backing = [], []
    for handle in out.facts:
        fid = cat.resolve(handle)
        f = store.get_any_fact(fid)
        if f is None:  # dropped like on screens; the answer still needs other backing below
            logger.warning("chat: answer cites unknown fact %s – dropped", handle)
            continue
        fact_ids.append(fid)
        backing.append(" ".join(filter(None, [f.en, getattr(f, "span", ""), f.date])))

    passages = [store.get_passage(pid) for pid in out.passages]
    if any(p is None for p in passages):
        errors.append(f"unknown passage in {out.passages}")
    passages = [p for p in passages if p is not None]
    passage_text = " ".join(fastpath._ws(p.text) for p in passages)
    for q in out.quotes:
        if fastpath._ws(q) not in passage_text:
            errors.append(f"quote not verbatim in cited passages: {q[:60]!r}")

    # Letter dates and the reference date are record metadata the answer may name ("no value
    # in the letter of 15 April 2016"), not values that need a citation
    dates = " ".join(filter(None, [d.date for d in store.documents.values()] + [store.reference_date()]))
    backed = (
        fastpath._numbers(_with_day_month_dates(" ".join(backing) + " " + dates)) | fastpath._numbers(passage_text)
        | fastpath._letter_date_numbers(passages, store)
    )
    for n in fastpath._numbers(out.answer) - backed:
        errors.append(f"number {n} not found in the cited facts or passages")
    if out.found and not fact_ids and not passages:
        errors.append("no valid source cited")
    return fact_ids, [p.passage_id for p in passages], errors


async def ask(question: str, store: FactStore, chat_id: Optional[str] = None,
              screen_id: Optional[str] = None) -> ChatReply:
    t0 = time.perf_counter()
    chat = _chats.get(chat_id or "") or Chat(chat_id=uuid.uuid4().hex[:12], screen_id=screen_id)
    if screen_id:
        chat.screen_id = screen_id
    _chats[chat.chat_id] = chat

    out: ChatAnswer = await get_client().generate(
        prompt_name="chat",
        messages=[{"role": "user", "content": [
            {"text": _fixed_prompt(store), "cache": True},
            {"text": _question_part(question, chat, store)},
        ]}],
        response_schema=ChatAnswer,
        temperature=0.0,
        model=settings.RUNTIME_MODEL,
    )
    t_llm = fastpath._elapsed_ms(t0)

    fact_ids, passage_ids, errors = _check(out, store)
    sources: list[Source] = fastpath._sources_for_facts(fact_ids, store)
    seen = {s.passage_id for s in sources}
    sources += [s for s in (_build_source(pid, store) for pid in passage_ids) if s and s.passage_id not in seen]

    if not out.found:
        state, note = "not_documented", None
    elif errors:
        logger.warning("chat: answer to %r failed validation: %s", question[:60], errors)
        state, note = "check", "; ".join(errors)
    else:
        state, note = "answered", None
    answer = out.answer.strip() or "Not documented."

    chat.messages.append(ChatMessage(role="user", content=question))
    chat.messages.append(ChatMessage(role="assistant", content=answer, state=state, sources=sources))
    return ChatReply(
        chat_id=chat.chat_id, answer=answer, state=state, sources=sources, fact_ids=fact_ids, note=note,
        timings_ms={"llm": t_llm, "total": fastpath._elapsed_ms(t0)},
    )
