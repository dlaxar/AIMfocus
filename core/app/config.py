"""
app/config.py – central settings loaded from the environment / .env file.
"""
from __future__ import annotations

from pathlib import Path
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_CORE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Repo-root .env first, core/.env (if any) overrides it; real env vars override both
    model_config = SettingsConfigDict(
        env_file=(_CORE_DIR.parent / ".env", _CORE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Defaults are anchored to this package, so the service works from any working directory
    DATA_DIR: Path = _CORE_DIR.parent / "data"
    CACHE_DIR: Path = _CORE_DIR / "cache"
    # Recorded screen updates (0.json, 1.json, …) replayed by /api/v2/simulated/stream
    STREAMS_DIR: Path = _CORE_DIR.parent / "streams"
    PATIENT_ID: str = "0000-0001"

    # Google's SDK convention is GOOGLE_API_KEY; GEMINI_API_KEY is accepted too
    GEMINI_API_KEY: str = Field(default="", validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"))
    # Anthropic key for "claude-" models (RUNTIME_MODEL=claude-haiku-5-5 etc.)
    CLAUDE_API_KEY: str = Field(default="", validation_alias=AliasChoices("CLAUDE_API_KEY", "ANTHROPIC_API_KEY"))

    # Measured 2026-10-29 on the select prompt (~29k chars): 3.8-flash default 15.7 s,
    # 3.8-flash thinking=low 9.0 s, 3.5-flash thinking=minimal 5.1 s (3.8 does not support "minimal").
    # Full runs on the 5 situations: 3.5-flash-lite (minimal) 1.2-4.9 s per complete screen,
    # 13-14 of 15 answers over 3 runs; 3.8-flash (low) 15/15 but 9.9-14.3 s.
    # 2026-10-30, all 23 situations (69 questions) with the fast select call: claude-haiku-5-5
    # without thinking 58-59/69, every screen under 8 s; 3.5-flash-lite 49/69 in under 3 s.
    RUNTIME_MODEL: str = "claude-haiku-5-5"
    RUNTIME_THINKING_LEVEL: str = "minimal"     # Gemini only; "" = model default
    # Token budget instead of a level; 0 = no thinking (Haiku 5.5 and Gemini flash models
    # allow it, Opus 5.5 does not: set -1 and RUNTIME_EFFORT=low there). -1 = unset.
    # Takes precedence over RUNTIME_THINKING_LEVEL.
    RUNTIME_THINKING_BUDGET: int = 0
    # Claude only: output_config.effort for RUNTIME_MODEL calls (low | medium | high); "" = model default
    RUNTIME_EFFORT: str = ""
    # Second, slower model started with every screen; when it is done, its screen replaces the
    # fast one under the same ID ("" = off). Opus 5.5 cannot turn thinking off: depth is set by
    # UPGRADE_EFFORT (output_config.effort, "low" is the lowest), and no thinking parameter is sent.
    UPGRADE_MODEL: str = "claude-opus-5-5"
    UPGRADE_EFFORT: str = "low"
    PREPROCESS_MODEL: str = "gemini-4-argon"
    # Preprocessing: max parallel LLM calls for independent work (S2 follow-ups)
    PREPROCESS_MAX_PARALLEL: int = 6
    # S1 splits documents with more passages than this into parts extracted in parallel
    # (measured on the 133-passage letter with gemini-4-argon: 250-312 s in one call,
    # 54 s in 7 parts, same facts). 0 = one call per document.
    S1_CHUNK_PASSAGES: int = 30
    # S1 extracts all documents at once instead of oldest first. No document then sees the
    # concept keys of the others; the alias merge after S1 links them. Measured on 0000-0001:
    # whole pipeline 1 min 38 s instead of 6 min 57 s, facts within run-to-run variation.
    S1_PARALLEL_DOCS: bool = True

    # Cache the runtime reads: "auto" = cache/<patient>/fhir.json when it exists, else the JSON files;
    # "fhir" = fhir.json only; "json" = the JSON files only
    CACHE_FORMAT: str = "auto"

    LLM_FAKE: bool = False
    # Fake-mode latency per prompt, to demo async behaviour, e.g. '{"fp_derive": 3000}' (ms)
    FAKE_LLM_DELAYS_MS: dict[str, int] = {}
    CONTRADICTION_LLM_SWEEP: bool = False

    # Fast path (/api/v2): max parallel derive calls per screen
    FASTPATH_MAX_PARALLEL_DERIVE: int = 6

    REFERENCE_DATE: str = "auto"  # "auto" = latest document date

    # Validation thresholds
    CONTRADICTION_CONFIDENCE_THRESHOLD: float = 0.80
    COPY_SIMILARITY_THRESHOLD: float = 0.85


settings = Settings()
