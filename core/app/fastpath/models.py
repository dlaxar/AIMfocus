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
    """
    One variable from the select call. The LLM only says WHICH facts answer it; the value
    shown on screen is pulled from the fact store by code (see service.resolve_variable).
    Whether it is documented is decided by code too: no resolvable fact → derive call.
    """
    name: str                                   # snake_case, e.g. "antithrombotic_medication"
    label: str                                  # short English label for the screen
    concept_ids: list[str] = Field(default_factory=list)  # pull facts of these concepts …
    fact_ids: list[str] = Field(default_factory=list)     # … and/or these exact facts (e.g. "D:" ones)
    time_scope: Literal["latest", "all"] = "latest"       # per concept: newest date only, or the whole history
    priority: int = 3                           # 1 = safety-critical


class SelectOutput(BaseModel):
    role: str = ""
    task: str = ""
    variables: list[SelectedVariable] = Field(default_factory=list)


class PickedVariable(BaseModel):
    """
    What the select call returns per variable. Kept short, since output tokens are most of
    the call's time: short handles (c12, f40, d3) instead of full IDs, no separate snake_case
    name (code derives it from the label). service._select maps it to SelectedVariable.
    """
    label: str                                  # short English label for the screen
    concepts: list[str] = Field(default_factory=list)  # concept handles (c12)
    facts: list[str] = Field(default_factory=list)     # fact handles (f40, d3)
    scope: Literal["latest", "all"] = "latest"
    priority: int = 3                           # 1 = safety-critical


class PickOutput(BaseModel):
    role: str = ""
    task: str = ""
    variables: list[PickedVariable] = Field(default_factory=list)


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
    # starting: ID handed out, select call running · partial: documented items there,
    # text search running · complete: everything resolved (or `error` says why not)
    status: Literal["starting", "partial", "complete"] = "starting"
    variables: list[ScreenVariable] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)        # touch this screen's items
    other_contradictions: list[Contradiction] = Field(default_factory=list)  # all the others in the record
    timings_ms: dict[str, float] = Field(default_factory=dict)
    error: Optional[str] = None                 # set when the select call failed
    version: int = 0                            # bumped on every update (for SSE / polling)


class ScreenRequest(BaseModel):
    situation: str
