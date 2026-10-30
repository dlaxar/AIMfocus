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

Tier = Literal["critical", "important", "background"]   # critical: first and large; background: collapsed
OutputType = Literal[
    "value",   # the current value(s), with older values as history
    "trend",   # numeric series for a chart
    "table",   # one row per item (e.g. medication), columns chosen for the role
    "list",    # findings, diagnoses, recommendations with their status
    "image",   # requested image; the documents contain none → "not_available"
]
# Columns a table may have; the select call picks a few per role
TABLE_COLUMNS = ["item", "strength", "schedule", "route", "class", "status", "since", "last_change", "value", "date"]


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
    priority: int = 3                           # 1 = shown first; unique rank on the screen
    tier: Tier = "important"
    output: OutputType = "value"
    columns: list[str] = Field(default_factory=list)      # table only


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
    tier: Tier = "important"                    # the list order is the priority
    output: OutputType = "value"
    columns: list[str] = Field(default_factory=list)   # table only: a few of TABLE_COLUMNS


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
    "not_available",    # an output the documents cannot hold (images)
]


class Entry(BaseModel):
    """One fact as shown: a value, a history row or a list item. Always with source and contradictions."""
    fact_id: Optional[str] = None               # None for a value found by the text search
    text: str
    date: Optional[str] = None
    status: Optional[str] = None                # list items: "open" / "done" / diagnosis status …
    source: Optional[Source] = None
    contradiction_ids: list[str] = Field(default_factory=list)


class Headline(BaseModel):
    """Detail level 1: what the collapsed card shows."""
    text: str
    date: Optional[str] = None
    change: Optional[str] = None                # trend: e.g. "−12 kg since 2015-06-28"


class TrendPoint(BaseModel):
    date: str
    value: float
    label: Optional[str] = None                 # e.g. "24h mean" for one blood pressure reading
    fact_id: str
    source: Optional[Source] = None
    contradiction_ids: list[str] = Field(default_factory=list)


class Series(BaseModel):
    name: str                                   # e.g. "Body weight", "Systolic"
    unit: str = ""
    points: list[TrendPoint] = Field(default_factory=list)   # oldest first


class TableRow(BaseModel):
    cells: dict[str, str] = Field(default_factory=dict)      # column → text
    fact_id: str
    date: Optional[str] = None
    source: Optional[Source] = None
    contradiction_ids: list[str] = Field(default_factory=list)


class Table(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list[TableRow] = Field(default_factory=list)


class ContradictionRef(BaseModel):
    contradiction_id: str
    kind: str
    summary_en: str


class ScreenVariable(BaseModel):
    name: str
    label: str
    state: VariableState
    priority: int = 3                           # unique rank: fetch and show in this order
    tier: Tier = "important"
    output: OutputType = "value"
    # Detail level 1
    headline: Optional[Headline] = None
    # Detail level 2, by output type (each entry / point / row carries its source and contradictions)
    latest: list[Entry] = Field(default_factory=list)        # value: newest per concept
    history: list[Entry] = Field(default_factory=list)       # value: older ones, newest first
    series: list[Series] = Field(default_factory=list)       # trend
    table: Optional[Table] = None                            # table
    items: list[Entry] = Field(default_factory=list)         # list
    contradictions: list[ContradictionRef] = Field(default_factory=list)
    # Flat fields (kept for existing clients)
    value: str = ""
    fact_ids: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    note: Optional[str] = None
    resolved_ms: Optional[float] = None         # time from request to this value
    contradiction_ids: list[str] = Field(default_factory=list)       # contradictions touching this value
    contradicting_fact_ids: list[str] = Field(default_factory=list)  # its facts that are part of one


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
