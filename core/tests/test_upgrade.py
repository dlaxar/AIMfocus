"""
tests/test_upgrade.py – every screen is built twice in parallel (RUNTIME_MODEL fast,
UPGRADE_MODEL slower); the slower screen replaces the fast one under the same ID.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient

from tests.test_smoke import _build_fixture_cache


@pytest.fixture(scope="module", autouse=True)
def fixture_cache():
    _build_fixture_cache()


class _ModelClient:
    """Fake answers, with a delay or a failure per model; records which models were called."""

    def __init__(self, delays: dict[str, float], fail: frozenset = frozenset()):
        from app.llm.client import FakeLLMClient
        self.fake, self.delays, self.fail, self.models = FakeLLMClient(), delays, fail, []

    async def generate(self, prompt_name: str, messages: list, response_schema=None, temperature: float = 0.0,
                       model: Optional[str] = None) -> Any:
        self.models.append((prompt_name, model))
        await asyncio.sleep(self.delays.get(model, 0))
        if model in self.fail:
            raise RuntimeError("model unavailable")
        return await self.fake.generate(prompt_name, messages, response_schema, temperature, model)


@pytest.fixture()
def models(monkeypatch):
    """Install a client: models(delays, fail) → the client; the previous one comes back afterwards."""
    import app.llm.client as client_mod
    from app.config import settings

    monkeypatch.setattr(settings, "RUNTIME_MODEL", "fast-model")
    monkeypatch.setattr(settings, "UPGRADE_MODEL", "slow-model")
    previous = client_mod._client_instance

    def install(delays=None, fail=frozenset()):
        c = _ModelClient(delays or {}, fail)
        client_mod.use_client(c)
        return c

    yield install
    client_mod.use_client(previous)


@pytest.fixture()
def client():
    import app.store as store_mod

    store_mod._store = None
    from app.main import app

    with TestClient(app) as c:
        yield c


def _sse(body: str) -> list[tuple[str, dict]]:
    import json
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


class TestUpgrade:
    def test_fast_screen_first_then_the_upgrade_under_the_same_id(self, client, models):
        llm = models({"slow-model": 0.3})
        screen = client.post("/api/v2/screens", json={"situation": "Anaesthetist, pre-op check"}).json()
        assert screen["model"] == "fast-model" and screen["upgrade"] == "running"
        fast = client.get(f"/api/v2/screens/{screen['screen_id']}?wait=true").json()
        assert fast["status"] == "complete" and fast["model"] == "fast-model" and fast["upgrade"] == "running"
        final = client.get(f"/api/v2/screens/{screen['screen_id']}?wait=true&final=true").json()
        assert final["screen_id"] == screen["screen_id"] and final["model"] == "slow-model"
        assert final["upgrade"] == "done" and final["status"] == "complete" and final["variables"]
        assert final["version"] > fast["version"] and "fast_first_response" in final["timings_ms"]
        assert {m for p, m in llm.models if p == "fp_select"} == {"fast-model", "slow-model"}

    def test_upgrade_already_done_is_shown_at_once(self, client, models):
        models({"fast-model": 0.3})   # the upgrade finishes first (e.g. the patient was picked late)
        screen = client.post("/api/v2/screens", json={"situation": "Anaesthetist, pre-op check"}).json()
        out = client.get(f"/api/v2/screens/{screen['screen_id']}?wait=true&final=true").json()
        assert out["model"] == "slow-model" and out["upgrade"] == "done"

    def test_stream_sends_whole_screens_until_the_upgrade(self, client, models):
        models({"slow-model": 0.3})
        screen = client.post("/api/v2/screens", json={"situation": "Anaesthetist, pre-op check"}).json()
        events = _sse(client.get(f"/api/v2/screens/{screen['screen_id']}/stream").text)
        assert events[-1][0] == "done"
        screens = [d for n, d in events if n == "screen"]
        assert screens[-1]["model"] == "slow-model" and screens[-1]["upgrade"] == "done"
        assert any(s["model"] == "fast-model" and s["status"] == "complete" for s in screens)
        assert all("variables" in s and "screen_id" in s for s in screens)   # whole screens, no diffs
        versions = [s["version"] for s in screens]
        assert versions == sorted(versions)

    def test_failed_upgrade_keeps_the_fast_screen(self, client, models):
        models(fail=frozenset({"slow-model"}))
        screen = client.post("/api/v2/screens", json={"situation": "Anaesthetist, pre-op check"}).json()
        out = client.get(f"/api/v2/screens/{screen['screen_id']}?wait=true&final=true").json()
        assert out["model"] == "fast-model" and out["upgrade"] == "failed" and out["variables"]

    def test_upgrade_can_be_switched_off(self, client, models):
        llm = models()
        screen = client.post("/api/v2/screens", json={"situation": "Anaesthetist, pre-op check", "upgrade": False}).json()
        out = client.get(f"/api/v2/screens/{screen['screen_id']}?wait=true&final=true").json()
        assert out["upgrade"] is None and out["model"] == "fast-model"
        assert {m for _, m in llm.models} == {"fast-model"}

    def test_chat_cards_stay_fast(self, client, models):
        llm = models()
        client.post("/api/v2/chat", json={"question": "Which allergies?", "cards": True, "wait": True})
        assert "slow-model" not in {m for _, m in llm.models}
