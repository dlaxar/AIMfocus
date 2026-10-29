"""
app/config.py – central settings loaded from the environment / .env file.
"""
from __future__ import annotations

from pathlib import Path
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Repo-root .env first, backend/.env (if any) overrides it; real env vars override both
    model_config = SettingsConfigDict(
        env_file=(_BACKEND_DIR.parent / ".env", _BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Defaults are anchored to this package, so the service works from any working directory
    DATA_DIR: Path = _BACKEND_DIR.parent / "data"
    CACHE_DIR: Path = _BACKEND_DIR / "cache"
    PATIENT_ID: str = "0000-0001"

    # Google's SDK convention is GOOGLE_API_KEY; GEMINI_API_KEY is accepted too
    GEMINI_API_KEY: str = Field(default="", validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"))

    # Measured 2026-10-29 on the select prompt (~29k chars): 3.8-flash default 15.7 s,
    # 3.8-flash thinking=low 9.0 s, 3.5-flash thinking=minimal 5.1 s (3.8 does not support "minimal").
    # Full runs on the 5 situations: 3.5-flash-lite (minimal) 1.2-4.9 s per complete screen,
    # 13-14 of 15 answers over 3 runs; 3.8-flash (low) 15/15 but 9.9-14.3 s.
    RUNTIME_MODEL: str = "gemini-3.5-flash-lite"
    RUNTIME_THINKING_LEVEL: str = "minimal"     # applied to RUNTIME_MODEL calls; "" = model default
    PREPROCESS_MODEL: str = "gemini-3.1-pro-preview"

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
