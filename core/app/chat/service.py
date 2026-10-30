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

import asyncio
import logging
import re
import time
import uuid
from typing import AsyncIterator, Optional

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


# Handles and passage IDs the model sometimes writes into the answer despite the rule: "(f246, d3)",
# "(12340012:p053)", "f40". They are citations, not content – removed before the answer is checked
_HANDLE = r"(?:\b[cfd]\d+\b|\b[\w.-]+:p\d{3}\b)"
_HANDLES = re.compile(rf"\s*\(\s*{_HANDLE}(?:\s*[,;]\s*{_HANDLE})*\s*\)|\s*{_HANDLE}")


def _strip_handles(text: str) -> str:
    return re.sub(r"\s+([.,;:])", r"\1", _HANDLES.sub("", text)).strip()


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


def _open_chat(chat_id: Optional[str], screen_id: Optional[str], patient_id: str = "") -> Chat:
    chat = _chats.get(chat_id or "") or Chat(chat_id=uuid.uuid4().hex[:12], screen_id=screen_id, patient_id=patient_id)
    if screen_id:
        chat.screen_id = screen_id
    _chats[chat.chat_id] = chat
    return chat


CHAT_CARDS = 2   # extra cards per chat answer, next to the clinician's screen


async def _start_cards(question: str, chat: Chat, store: FactStore):
    """1-2 extra cards for this question, built in the background like any screen (select →
    analytic pull → text search in the letters for what the store lacks), leaving out what
    the clinician's screen already shows."""
    screen = fastpath.get_screen(chat.screen_id) if chat.screen_id else None
    shown = frozenset(fid for v in (screen.variables if screen else []) for fid in v.fact_ids)
    return await fastpath.create_screen(_card_situation(question, chat), store, max_variables=CHAT_CARDS, shown_fact_ids=shown)


async def _text_reply(question: str, chat: Chat, store: FactStore, t0: float) -> ChatReply:
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
    out.answer = _strip_handles(out.answer)

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
        timings_ms={"llm": t_llm},
    )


async def ask(question: str, store: FactStore, chat_id: Optional[str] = None,
              screen_id: Optional[str] = None, cards: bool = False, wait: bool = False) -> ChatReply:
    t0 = time.perf_counter()
    chat = _open_chat(chat_id, screen_id, store.patient_id)
    card_screen = await _start_cards(question, chat, store) if cards else None
    reply = await _text_reply(question, chat, store, t0)
    if card_screen is not None:
        if wait:
            card_screen = await fastpath.wait_for(card_screen.screen_id, timeout_s=CARDS_WAIT_S) or card_screen
            reply.cards = list(card_screen.variables)
            reply.timings_ms["cards"] = card_screen.timings_ms.get("complete") or fastpath._elapsed_ms(t0)
        reply.cards_screen_id, reply.cards_status = card_screen.screen_id, card_screen.status
    reply.timings_ms["total"] = fastpath._elapsed_ms(t0)
    return reply


async def stream(question: str, store: FactStore, chat_id: Optional[str] = None,
                 screen_id: Optional[str] = None, cards: bool = True) -> AsyncIterator[tuple[str, dict]]:
    """
    Events as they are ready: ("answer", ChatReply) as soon as the text is there,
    ("cards", {status, cards, …}) on every change of the card screen – facts from the store
    first, then what the text search finds in the letters – and finally ("done", {}).
    Nothing waits for the slowest part.
    """
    t0 = time.perf_counter()
    chat = _open_chat(chat_id, screen_id, store.patient_id)
    card_screen = await _start_cards(question, chat, store) if cards else None
    text = asyncio.create_task(_text_reply(question, chat, store, t0))
    sent_answer, last_version = False, -1
    while True:
        if not sent_answer and text.done():
            sent_answer = True
            try:
                reply = text.result()
            except Exception as exc:  # the cards may still come; say what failed
                yield "error", {"detail": f"Could not answer: {type(exc).__name__}"}
            else:
                if card_screen is not None:
                    reply.cards_screen_id = card_screen.screen_id
                reply.timings_ms["total"] = fastpath._elapsed_ms(t0)
                yield "answer", reply.model_dump()
        if card_screen is not None and card_screen.version != last_version:
            last_version = card_screen.version
            yield "cards", {"chat_id": chat.chat_id, "cards_screen_id": card_screen.screen_id,
                            "cards_status": card_screen.status, "cards": [v.model_dump() for v in card_screen.variables],
                            "elapsed_ms": fastpath._elapsed_ms(t0)}
        cards_done = card_screen is None or card_screen.status == "complete"
        if sent_answer and cards_done:
            yield "done", {"elapsed_ms": fastpath._elapsed_ms(t0)}
            return
        await asyncio.sleep(0.1)


CARDS_WAIT_S = 20.0


def _card_situation(question: str, chat: Chat) -> str:
    """The question as a situation for the select call, with the role and the last exchange."""
    screen = fastpath.get_screen(chat.screen_id) if chat.screen_id else None
    role = f" The clinician is: {screen.role} ({screen.task})." if screen and screen.role else ""
    last = [m for m in chat.messages[-2:]]
    earlier = (f" Earlier: they asked \"{last[0].content}\", answer: \"{last[1].content}\"."
               if len(last) == 2 and last[0].role == "user" else "")
    on_screen = "; ".join(v.label for v in (screen.variables if screen else []))
    already = f" Already on the clinician's screen: {on_screen}." if on_screen else ""
    return f"Question about the patient: {question}.{role}{earlier}{already}"
