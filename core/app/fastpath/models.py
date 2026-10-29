"""
app/fastpath/models.py – LLM output schemas and API models for the fast path.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas import Contradiction, Source


# ---------------------------------------------------------------------------
# LLM outputs
# ---------------------------------------------------------------------------

class SelectedVariable(BaseModel):
    """One variable from the select call: name + value straight from the fact store."""
    name: str                                   # snake_case, e.g. "antithrombotic_medication"
    label: str                                  # short English label for the screen
    value: str = ""                             # restates only the cited facts
    fact_ids: list[str] = Field(default_factory=list)
    documented: bool = True                     # false → the store has no fact for it
    priority: int = 3                           # 1 = safety-critical


class SelectOutput(BaseModel):
    role: str = ""
    task: str = ""
    variables: list[SelectedVariable] = Field(default_factory=list)


class DeriveOutput(BaseModel):
    """Result of searching the raw passages for one missing variable."""
    found: bool = False
    value: str = ""
    passage_ids: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)  # verbatim German quotes from those passages
    rationale: str = ""


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------

VariableState = Literal[
    "documented",       # answered from the fact store by the select call
    "pending",          # derive call still running
    "derived",          # answered from the raw text by a derive call
    "not_documented",   # derive call confirmed it is not in the documents
    "check",            # an answer existed but failed validation
]


class ScreenVariable(BaseModel):
    name: str
    label: str
    state: VariableState
    value: str = ""
    fact_ids: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    note: Optional[str] = None
    priority: int = 3
    resolved_ms: Optional[float] = None         # time from request to this value


class FastScreen(BaseModel):
    screen_id: str
    situation: str
    role: str = ""
    task: str = ""
    reference_date: Optional[str] = None
    status: Literal["partial", "complete"] = "partial"
    variables: list[ScreenVariable] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    timings_ms: dict[str, float] = Field(default_factory=dict)
    version: int = 0                            # bumped on every update (for SSE / polling)


class ScreenRequest(BaseModel):
    situation: str
