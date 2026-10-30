"""
tests/test_chat.py – questions about the patient (/api/v2/chat): sources, follow-ups, validation.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.test_smoke import _build_fixture_cache


@pytest.fixture(scope="module", autouse=True)
def fixture_cache():
    _build_fixture_cache()


@pytest.fixture()
def client():
    import app.store as store_mod

    store_mod._store = None
    from app.main import app

    with TestClient(app) as c:
        yield c


def _sse(body: str) -> list[tuple[str, dict]]:
    """Server-sent events as (event, data) pairs."""
    import json as _json
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], _json.loads(lines["data"])))
    return out


class TestChat:
    def test_answer_comes_with_its_sources(self, client):
        out = client.post("/api/v2/chat", json={"question": "Which allergies?"}).json()
        assert out["state"] == "answered"
        assert out["fact_ids"] == ["F:TEST001:all:01"]          # the unknown handle is dropped
        assert out["sources"][0]["doc_id"] == "TEST001"
        assert out["chat_id"]

    def test_follow_up_continues_the_conversation(self, client):
        first = client.post("/api/v2/chat", json={"question": "Which allergies?"}).json()
        second = client.post("/api/v2/chat", json={"question": "How did she react?", "chat_id": first["chat_id"]}).json()
        assert second["chat_id"] == first["chat_id"]
        chat = client.get(f"/api/v2/chat/{first['chat_id']}").json()
        assert [m["role"] for m in chat["messages"]] == ["user", "assistant", "user", "assistant"]

    def test_follow_up_sees_the_conversation_and_screen(self, client):
        from app.chat import service
        from app.chat.models import Chat, ChatMessage
        from app.store import get_store

        chat = Chat(chat_id="c1", messages=[ChatMessage(role="user", content="Which blood thinner?"),
                                            ChatMessage(role="assistant", content="Aspirin 100 mg")])
        part = service._question_part("Since when?", chat, get_store())
        assert "Clinician: Which blood thinner?" in part and "You: Aspirin 100 mg" in part
        assert part.rstrip().endswith("Since when?")

    def test_unknown_chat_and_empty_question(self, client):
        assert client.post("/api/v2/chat", json={"question": "x", "chat_id": "nope"}).status_code == 404
        assert client.post("/api/v2/chat", json={"question": "  "}).status_code == 400


class TestChatCards:
    def test_cards_come_in_the_reply_with_type_and_state(self, client):
        out = client.post("/api/v2/chat", json={"question": "Which allergies?", "cards": True, "wait": True}).json()
        assert out["answer"] and out["cards_status"] == "complete"
        cards = {c["name"]: c for c in out["cards"]}
        assert cards["contrast_allergy"]["state"] == "documented" and cards["contrast_allergy"]["headline"]["text"]
        assert cards["contrast_allergy"]["latest"][0]["source"]["doc_id"] == "TEST001"
        assert len(out["cards"]) <= 2                                           # extra cards, not a whole screen
        assert cards["antithrombotic_medication"]["state"] == "not_documented"  # said, not left out
        assert cards["antithrombotic_medication"]["headline"]["text"] == "Not documented"
        assert all(c["output"] in ("value", "trend", "table", "list", "image") for c in out["cards"])
        assert "cards" in out["timings_ms"]

    def test_cards_can_be_streamed_instead(self, client):
        out = client.post("/api/v2/chat", json={"question": "Which allergies?", "cards": True}).json()   # wait=false is the default
        assert out["cards"] == [] and out["cards_screen_id"]
        screen = client.get(f"/api/v2/screens/{out['cards_screen_id']}?wait=true").json()
        assert screen["status"] == "complete" and screen["variables"]

    def test_stream_sends_answer_then_cards_then_done(self, client):
        import json as _json
        body = client.post("/api/v2/chat/stream", json={"question": "Which allergies?"}).text
        events = _sse(body)
        names = [n for n, _ in events]
        assert names[0] in ("answer", "cards") and names[-1] == "done" and "answer" in names
        last_cards = [d for n, d in events if n == "cards"][-1]
        assert last_cards["cards_status"] == "complete"
        states = {c["name"]: c["state"] for c in last_cards["cards"]}
        assert states["contrast_allergy"] == "documented" and states["antithrombotic_medication"] == "not_documented"
        answer = next(d for n, d in events if n == "answer")
        assert answer["answer"] and answer["cards_screen_id"] == last_cards["cards_screen_id"]

    def test_stream_without_cards(self, client):
        body = client.post("/api/v2/chat/stream", json={"question": "Which allergies?", "cards": False}).text
        assert [n for n, _ in _sse(body)] == ["answer", "done"]

    def test_cards_leave_out_what_the_screen_shows(self, client):
        screen = client.post("/api/v2/screens", json={"situation": "Anaesthetist, pre-op check"}).json()
        client.get(f"/api/v2/screens/{screen['screen_id']}?wait=true")
        out = client.post("/api/v2/chat", json={"question": "Which allergies?", "cards": True, "wait": True,
                                                "screen_id": screen["screen_id"]}).json()
        assert "contrast_allergy" not in {c["name"] for c in out["cards"]}     # already on the screen

    def test_no_cards_unless_asked(self, client):
        out = client.post("/api/v2/chat", json={"question": "Which allergies?"}).json()
        assert out["cards"] == [] and out["cards_screen_id"] is None

    def test_card_question_carries_role_and_last_exchange(self, client):
        from app.chat import service
        from app.chat.models import Chat, ChatMessage

        chat = Chat(chat_id="c2", messages=[ChatMessage(role="user", content="Which blood thinner?"),
                                            ChatMessage(role="assistant", content="Aspirin 100 mg")])
        text = service._card_situation("Since when?", chat)
        assert text.startswith("Question about the patient: Since when?")
        assert "Which blood thinner?" in text and "Aspirin 100 mg" in text


class TestChatValidation:
    def test_number_not_in_the_cited_facts_is_flagged(self, client):
        from app.chat.models import ChatAnswer
        from app.chat.service import _check
        from app.store import get_store

        out = ChatAnswer(answer="Creatinine 1.4 mg/dl", found=True, facts=["F:TEST001:all:01"])
        _, _, errors = _check(out, get_store())
        assert any("1.4" in e for e in errors)

    def test_dates_may_be_written_as_in_the_letters(self, client):
        from app.chat.models import ChatAnswer
        from app.chat.service import _check
        from app.store import get_store

        fact = get_store().get_fact("F:TEST001:all:01")
        y, m, d = fact.date.split("-")
        out = ChatAnswer(answer=f"Contrast allergy, documented {d}.{m}.{y}.", found=True, facts=["F:TEST001:all:01"])
        assert _check(out, get_store())[2] == []

    def test_quote_must_be_verbatim(self, client):
        from app.chat.models import ChatAnswer
        from app.chat.service import _check
        from app.store import get_store

        store = get_store()
        p = next(p for p in store.passages.values() if "Testopril" in p.text)
        out = ChatAnswer(answer="Testopril", found=True, passages=[p.passage_id], quotes=["Erfundenes Zitat"])
        assert any("quote" in e for e in _check(out, store)[2])

    def test_handles_in_the_answer_text_are_removed(self):
        from app.chat.service import _strip_handles
        assert _strip_handles("Donepezil (f246, d3), aspirin 100 mg (d5).") == "Donepezil, aspirin 100 mg."
        assert _strip_handles("The CT referral (12340012:p053) says nausea.") == "The CT referral says nausea."
        assert _strip_handles("HbA1c 6.4% in 2015.") == "HbA1c 6.4% in 2015."
