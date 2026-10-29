"""
contradictions/model.py – Finding (one detected contradiction with its evidence) and
Corpus (the documents and passages every detector reads).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Iterable, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas import (
    Contradiction,
    ContradictionJudge,
    ContradictionKind,
    Document,
    Fact,
    Passage,
)
from contradictions.text import episodes, parse_iso

# definite: the passages cannot all be true (arithmetic, an impossible date, left vs. right
#           in a copy). possible: needs a clinician's or an LLM judge's second look.
Certainty = Literal["definite", "possible"]


class Finding(BaseModel):
    key: str                                    # stable dedupe key, e.g. "bmi:<doc_id>:p012"
    rule: str                                   # detector name, or "llm_sweep"
    kind: ContradictionKind
    certainty: Certainty
    summary_en: str
    passage_ids: list[str]                      # every passage involved, conflicting ones first
    concept_ids: list[str] = Field(default_factory=list)
    rationale: str = ""                         # why the passages cannot all be true
    judge: Optional[ContradictionJudge] = None  # LLM verdict, when one was asked

    @property
    def contradiction_id(self) -> str:
        return "X:cf:" + re.sub(r"[^a-z0-9]+", "_", self.key.lower()).strip("_")

    def to_contradiction(self, facts: Iterable[Fact] = ()) -> Contradiction:
        """The shared schema the store and the screens read; concepts of S1 facts on the passages are added."""
        wanted = set(self.passage_ids)
        from_facts = sorted({f.concept_id for f in facts if f.passage_id in wanted and f.concept_id})
        judge = self.judge or ContradictionJudge(
            verdict="contradiction",
            confidence=0.95 if self.certainty == "definite" else 0.6,
            rationale=f"rule {self.rule}: {self.rationale}",
        )
        return Contradiction(
            contradiction_id=self.contradiction_id,
            summary_en=self.summary_en,
            kind=self.kind,
            passage_ids=list(self.passage_ids),
            concept_ids=list(dict.fromkeys(self.concept_ids + from_facts)),
            judge=judge,
        )


class Corpus:
    """Documents and passages of one patient, with the lookups the detectors need."""

    def __init__(self, documents: Iterable[Document], passages: Iterable[Passage]) -> None:
        self.documents: dict[str, Document] = {d.doc_id: d for d in documents}
        self.passages: list[Passage] = list(passages)
        self.passage_map: dict[str, Passage] = {p.passage_id: p for p in self.passages}
        self.by_doc: dict[str, list[Passage]] = {}
        for p in self.passages:
            self.by_doc.setdefault(p.doc_id, []).append(p)
        # Position of each passage inside its document
        self.index: dict[str, int] = {p.passage_id: i for ps in self.by_doc.values() for i, p in enumerate(ps)}
        self.episode: dict[str, int] = episodes(self.documents.values())

    def doc_date(self, doc_id: str) -> Optional[date]:
        doc = self.documents.get(doc_id)
        return parse_iso(doc.date) if doc else None

    def ref(self, doc_id: str) -> str:
        """'Entlassungsbrief (2020-02-09)' – how a clinician recognises a document."""
        doc = self.documents.get(doc_id)
        if doc is None:
            return doc_id
        return f"{doc.title or doc_id} ({doc.date or 'undated'})"

    def same_episode(self, doc_a: str, doc_b: str) -> bool:
        ep = self.episode.get(doc_a)
        return ep is not None and ep == self.episode.get(doc_b)
