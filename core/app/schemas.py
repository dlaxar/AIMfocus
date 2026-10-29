"""
app/schemas.py – shared Pydantic data model (sections 4 and 6 of the design doc).

All types are used by both preprocessing (cache files) and the runtime API.
IDs are stable strings so a re-run with identical inputs produces identical IDs.
"""
from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 4.1  Document
# ---------------------------------------------------------------------------

class Document(BaseModel):
    doc_id: str
    title: str
    date: Optional[str] = None          # ISO date from document body, e.g. "2016-04-15"
    date_source: Optional[str] = None   # "body" | "metadata" | "conflict"
    date_meta: Optional[str] = None     # date from documents.json metadata
    type: Optional[str] = None          # Arztbrief, Befund, …
    department: Optional[str] = None
    text: str                           # normalized document text


# ---------------------------------------------------------------------------
# 4.2  Passage
# ---------------------------------------------------------------------------

PassageKind = Literal["sentence", "list_item", "table_row", "header", "other"]


class Passage(BaseModel):
    passage_id: str                     # "<doc_id>:p<NNN>"
    doc_id: str
    section: Optional[str] = None
    kind: PassageKind = "sentence"
    start: int                          # char offset into Document.text
    end: int
    text: str
    copy_of: Optional[str] = None       # passage_id of the original, if this is a copy


# ---------------------------------------------------------------------------
# 4.3  Fact
# ---------------------------------------------------------------------------

FactType = Literal[
    "medication", "allergy", "diagnosis", "measurement",
    "procedure", "finding", "recommendation", "event", "plan", "demographic",
]

FactStatus = Literal[
    # diagnosis / finding
    "positive", "negative", "suspected", "history",
    # medication
    "current", "discontinued", "started", "changed", "historical",
    # recommendation
    "recommended", "done", "open",
    # generic
    "unknown",
]

DatePrecision = Literal["day", "month", "year", "approximate", "unknown"]
DateBasis = Literal["document_date", "text_date", "derived", "unknown"]


class Fact(BaseModel):
    fact_id: str                        # "F:<doc_id>:<type_abbr>:<NN>"
    type: FactType
    passage_id: str
    span: str                           # verbatim text copied from the passage
    fields: dict[str, Any] = Field(default_factory=dict)
    status: FactStatus = "unknown"
    laterality: Optional[Literal["left", "right", "bilateral", "unknown"]] = None
    date: Optional[str] = None
    date_precision: DatePrecision = "unknown"
    date_basis: DateBasis = "unknown"
    concept_id: Optional[str] = None
    en: str = ""                        # short English rendering
    de: str = ""                        # German rendering (usually == span)


# ---------------------------------------------------------------------------
# 4.4  Concept and timeline
# ---------------------------------------------------------------------------

class TimelinePoint(BaseModel):
    date: Optional[str] = None
    value: Optional[Any] = None
    unit: Optional[str] = None
    fact_id: str


class Concept(BaseModel):
    concept_id: str                     # "C:<type>:<label_slug>"
    label_en: str
    type: FactType
    fact_ids: list[str] = Field(default_factory=list)
    series: list[TimelinePoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 4.5  Derived fact
# ---------------------------------------------------------------------------

class DerivedFact(BaseModel):
    fact_id: str                        # "D:<slug>"
    type: Literal["derived"] = "derived"
    en: str
    inputs: list[str] = Field(default_factory=list)
    formula: Optional[str] = None
    fields: dict[str, Any] = Field(default_factory=dict)
    date: Optional[str] = None


# ---------------------------------------------------------------------------
# 4.6  Contradiction
# ---------------------------------------------------------------------------

ContradictionKind = Literal[
    "laterality", "date", "value", "calculation",
    "presence", "copy_mismatch",
]

ContradictionVerdict = Literal[
    "contradiction", "change_over_time", "measurement_noise", "not_related",
]


class ContradictionJudge(BaseModel):
    verdict: ContradictionVerdict
    confidence: float
    rationale: str


class Contradiction(BaseModel):
    contradiction_id: str               # "X:<slug>"
    summary_en: str
    kind: ContradictionKind
    passage_ids: list[str] = Field(default_factory=list)
    concept_ids: list[str] = Field(default_factory=list)
    judge: Optional[ContradictionJudge] = None
    # Facts extracted from the linked passages, and derived facts built on them. Filled by
    # FactStore at load time (not stored in contradictions.json).
    fact_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 4.7  Runtime types – Need, ScreenItem, ScreenSection, Screen
# ---------------------------------------------------------------------------

FactCategory = Literal[
    "medication", "allergy", "diagnosis", "measurement",
    "procedure", "finding", "recommendation", "event", "plan", "demographic", "derived",
]

TimeScope = Literal["current", "latest", "all", "historical"]


class Need(BaseModel):
    need_id: str                        # "N1", "N2", …
    label: str
    category: FactCategory
    filter: dict[str, Any] = Field(default_factory=dict)
    time_scope: TimeScope = "current"
    report_absence: bool = True
    priority: int = 5


class Source(BaseModel):
    doc_id: str
    doc_title: str
    doc_date: Optional[str] = None
    passage_id: str
    start: int
    end: int
    quote_de: str


class ScreenItem(BaseModel):
    kind: Literal["documented", "derived", "not_documented", "check"]
    text_en: str
    fact_ids: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    warning: Optional[str] = None       # inline contradiction warning


class ScreenSection(BaseModel):
    need_id: str
    label: str
    items: list[ScreenItem] = Field(default_factory=list)


class ScreenMeta(BaseModel):
    timings_ms: dict[str, float] = Field(default_factory=dict)
    models: dict[str, str] = Field(default_factory=dict)
    cache_version: str = ""
    dropped_items: int = 0


class Screen(BaseModel):
    situation: str
    role: Optional[str] = None
    task: Optional[str] = None
    reference_date: Optional[str] = None
    sections: list[ScreenSection] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    other_contradictions: list[Contradiction] = Field(default_factory=list)
    meta: ScreenMeta = Field(default_factory=ScreenMeta)


# ---------------------------------------------------------------------------
# Preprocessing helpers
# ---------------------------------------------------------------------------

class PlannerOutput(BaseModel):
    role: str
    task: str
    needs: list[Need]


class AnswererItem(BaseModel):
    need_id: str
    fact_ids: list[str] = Field(default_factory=list)
    text_en: str
    absent: bool = False


class AnswererOutput(BaseModel):
    items: list[AnswererItem]


class S1Report(BaseModel):
    doc_id: str
    total_passages: int
    flagged_no_content: int
    facts_extracted: int
    facts_rejected: int
    uncovered_passages: list[str] = Field(default_factory=list)  # passage_ids


class CacheManifest(BaseModel):
    patient_id: str
    completed_at: str           # ISO datetime
    preprocess_model: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    document_count: int
    passage_count: int
    fact_count: int
    derived_fact_count: int
    contradiction_count: int
    coverage_pct: float         # % passages with at least one fact or no_content flag
