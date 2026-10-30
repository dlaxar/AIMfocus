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
