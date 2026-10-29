"""
app/llm/client.py – thin LLM wrapper.

Default backend: Google Gemini via `google-genai`.
When LLM_FAKE=true, returns canned JSON responses from tests/fixtures/llm_responses/.

Usage:
    from app.llm.client import get_client
    client = get_client()
    result = await client.generate(prompt_name="s1_extract", messages=[...], response_schema=SomeModel)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional, Type

from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

_FIXTURE_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "llm_responses"


# ---------------------------------------------------------------------------
# Protocol / base
# ---------------------------------------------------------------------------

class LLMClient:
    """Abstract interface – concrete implementations below."""

    async def generate(
        self,
        prompt_name: str,
        messages: list[dict[str, str]],
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
    ) -> Any:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Fake client (CI)
# ---------------------------------------------------------------------------

class FakeLLMClient(LLMClient):
    """
    Returns canned JSON from tests/fixtures/llm_responses/<prompt_name>.json.
    Falls back to an empty dict if the file is missing (keeps CI green for stubs).
    """

    async def generate(
        self,
        prompt_name: str,
        messages: list[dict[str, str]],
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
    ) -> Any:
        delay_ms = settings.FAKE_LLM_DELAYS_MS.get(prompt_name, 0)
        if delay_ms:
            import asyncio
            await asyncio.sleep(delay_ms / 1000)

        fixture_path = _FIXTURE_DIR / f"{prompt_name}.json"
        if fixture_path.exists():
            raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        else:
            logger.warning("FakeLLMClient: no fixture for %s, returning {}", prompt_name)
            raw = {}

        if response_schema is not None:
            try:
                return response_schema.model_validate(raw)
            except Exception:
                return response_schema.model_construct()
        return raw


# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------

class GeminiClient(LLMClient):
    """
    Wraps the `google-genai` SDK (the older `google-generativeai` package is deprecated).
    Structured output is requested via `response_json_schema`, which accepts the full
    Pydantic JSON schema ($defs, anyOf, free-form dicts) instead of the restricted
    OpenAPI subset that `response_schema` supports.
    """

    def __init__(self) -> None:
        try:
            from google import genai  # type: ignore[import]
            from google.genai import types  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "google-genai is not installed. "
                "Run: pip install google-genai"
            ) from exc
        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set (or set LLM_FAKE=true)")
        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self._types = types

    async def generate(
        self,
        prompt_name: str,
        messages: list[dict[str, str]],
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
    ) -> Any:
        model_name = model or settings.RUNTIME_MODEL

        # System messages become the system instruction; user/assistant become turns
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_parts.append(content)
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": content}]})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})

        if not contents:
            return {} if response_schema is None else response_schema.model_construct()

        config_kwargs: dict[str, Any] = {"temperature": temperature}
        if system_parts:
            config_kwargs["system_instruction"] = "\n\n".join(system_parts)
        if response_schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_json_schema"] = response_schema.model_json_schema()

        response = await self._client.aio.models.generate_content(
            model=model_name,
            contents=contents,
            config=self._types.GenerateContentConfig(**config_kwargs),
        )
        raw_text = response.text or ""

        if response_schema is not None:
            try:
                return response_schema.model_validate_json(raw_text)
            except Exception as exc:
                logger.error("GeminiClient: JSON parse failed for %s: %s", prompt_name, exc)
                raise

        return raw_text


# ---------------------------------------------------------------------------
# Offline client (preprocessing with responses written by hand or by another model)
# ---------------------------------------------------------------------------

class OfflineLLMClient(LLMClient):
    """
    File-backed client for offline preprocessing: for every call it looks for
    <dir>/<key>.json. If the file is missing, the full prompt is written to
    <dir>/<key>.prompt.md and an empty result is returned, so a run lists every prompt
    that still needs an answer. Answers pass the same code validation as Gemini output.

    Keys: s1_extract calls are keyed by document ID (stable while the concept registry
    grows); every other call by a hash of its messages.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.missing: list[str] = []

    def _key(self, prompt_name: str, messages: list[dict[str, str]]) -> str:
        import hashlib
        import re

        text = "\n\n".join(m.get("content", "") for m in messages)
        if prompt_name == "s1_extract":
            m = re.search(r"Document ID:\s*(\S+)", text)
            retry = any(msg.get("role") == "assistant" for msg in messages)
            if m:
                return f"s1_extract__{m.group(1)}" + ("__retry" if retry else "")
        return f"{prompt_name}__{hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]}"

    async def generate(
        self,
        prompt_name: str,
        messages: list[dict[str, str]],
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
    ) -> Any:
        key = self._key(prompt_name, messages)
        answer = self.directory / f"{key}.json"
        if answer.exists():
            raw = json.loads(answer.read_text(encoding="utf-8"))
        else:
            prompt_file = self.directory / f"{key}.prompt.md"
            prompt_file.write_text(
                "\n\n".join(f"<!-- {m.get('role', 'user')} -->\n{m.get('content', '')}" for m in messages)
                + ("\n\n<!-- response JSON schema -->\n" + json.dumps(response_schema.model_json_schema(), indent=1)
                   if response_schema is not None else ""),
                encoding="utf-8",
            )
            self.missing.append(key)
            logger.warning("OfflineLLMClient: no answer for %s – prompt written to %s", key, prompt_file)
            raw = {}

        if response_schema is not None:
            try:
                return response_schema.model_validate(raw)
            except Exception as exc:
                logger.error("OfflineLLMClient: %s does not match the schema: %s", answer, exc)
                return response_schema.model_construct()
        return raw


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_client_instance: Optional[LLMClient] = None


def use_client(client: LLMClient) -> None:
    """Install a specific client (e.g. OfflineLLMClient for preprocessing)."""
    global _client_instance
    _client_instance = client


def get_client() -> LLMClient:
    """Return a singleton LLM client (Fake or Gemini depending on settings)."""
    global _client_instance
    if _client_instance is None:
        if settings.LLM_FAKE:
            _client_instance = FakeLLMClient()
        else:
            _client_instance = GeminiClient()
    return _client_instance
