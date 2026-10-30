"""
app/fastpath/plan.py – the screen planned before the patient is known.

When the clinician submits the situation, the models (fast and upgrade, in parallel) plan
the screen without any patient data: which variables the role needs, and how to recognise
the facts that answer each one (fact types, terms, measurement keys, derived facts). When
the patient is picked, `materialize` applies a plan to that patient's fact store in code –
no LLM call – and the screen is built from it as before (shape, sources, contradictions;
variables nothing matches go to the text search).
"""
from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from app.config import settings
from app.fastpath.models import OutputType, SelectedVariable, SelectOutput, Tier
from app.llm.client import get_client
from app.store import FactStore
from preprocess.vocab import MEASUREMENT_KEYS

logger = logging.getLogger(__name__)

DerivedKind = Literal["current_medication", "medication_change", "weight_change", "bmi", "age", "followup"]
_DERIVED_PREFIX = {
    "current_medication": "D:curmed:", "medication_change": "D:medchange:", "weight_change": "D:weight_change:",
    "bmi": "D:bmi:", "age": "D:age", "followup": "D:followup:",
}


class PlannedVariable(BaseModel):
    """One variable as planned without the patient: what it is and how to recognise its facts."""
    label: str
    types: list[str] = Field(default_factory=list)      # fact types that can answer it
    terms: list[str] = Field(default_factory=list)      # words that identify its facts
    measures: list[str] = Field(default_factory=list)   # measurement keys (MEASUREMENT_KEYS)
    derived: list[DerivedKind] = Field(default_factory=list)
    scope: Literal["latest", "all"] = "latest"
    tier: Tier = "important"                            # the list order is the priority
    output: OutputType = "value"
    columns: list[str] = Field(default_factory=list)    # table only


class PlanOutput(BaseModel):
    """
    What the plan call returns. One compact line per variable instead of JSON objects:
    output tokens are most of the call's time, and repeated field names were a third of them.
        label | tier | output | scope | types | terms | measures | derived | columns
    """
    role: str = ""
    task: str = ""
    variables: list[str] = Field(default_factory=list)

    def planned(self) -> list[PlannedVariable]:
        out = []
        for line in self.variables:
            parts = [p.strip() for p in line.split("|")] + [""] * 9
            items = lambda s: [x.strip().lower() for x in s.split(",") if x.strip()]
            try:
                out.append(PlannedVariable(
                    label=parts[0], tier=parts[1] or "important", output=parts[2] or "value",
                    scope=parts[3] or "latest", types=items(parts[4]), terms=items(parts[5]),
                    measures=items(parts[6]), derived=[d for d in items(parts[7]) if d in _DERIVED_PREFIX],
                    columns=items(parts[8]),
                ))
            except ValueError:   # a malformed line (unknown tier or output) – skip that variable
                logger.warning("plan: line skipped: %r", line[:120])
        return [v for v in out if v.label]


async def make_plan(situation: str, model: str) -> PlanOutput:
    from app.fastpath.service import _prompt
    fixed = _prompt("fp_plan").replace("{{measurement_keys}}", ", ".join(f"`{k}`" for k in MEASUREMENT_KEYS))
    return await get_client().generate(
        prompt_name="fp_plan",
        messages=[{"role": "user", "content": [{"text": fixed, "cache": True},
                                               {"text": f"## Clinician situation\n{situation}"}]}],
        response_schema=PlanOutput,
        temperature=0.0,
        model=model or settings.RUNTIME_MODEL,
    )


# ---------------------------------------------------------------------------
# Applying a plan to one patient (code only)
# ---------------------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    text = text.lower().replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")
    return re.findall(r"[a-z0-9]+", text)


def _hits(term: list[str], words: list[str]) -> bool:
    """The term's words in a row; its last word may be the start of a longer word ("anticoagul")."""
    n = len(term)
    for i in range(len(words) - n + 1):
        if words[i:i + n - 1] == term[:-1]:
            last, word = term[-1], words[i + n - 1]
            if word == last or (len(last) >= 5 and word.startswith(last)):
                return True
    return False


class _Index:
    """Words of every concept (id and label) and fact (description), built once per store."""

    def __init__(self, store: FactStore) -> None:
        from app.fastpath.service import is_question
        self.concepts = {c.concept_id: _tokens(f"{c.concept_id.split(':', 2)[-1]} {c.label_en}")
                         for c in store.concepts.values()}
        self.facts = {f.fact_id: _tokens(f"{f.en or f.span} {(f.concept_id or '').split(':', 2)[-1]}")
                      for f in store.facts.values() if not is_question(f)}


_indices: dict[str, tuple[int, _Index]] = {}


def _index(store: FactStore) -> _Index:
    cached = _indices.get(store.patient_id)
    if cached is None or cached[0] != id(store):
        cached = _indices[store.patient_id] = (id(store), _Index(store))
    return cached[1]


def match(pv: PlannedVariable, store: FactStore, reference_date: str) -> tuple[list[str], list[str]]:
    """(concept_ids, fact_ids) of the patient's facts that answer a planned variable."""
    from app.fastpath.service import _known_on
    idx = _index(store)
    terms = [t for t in (_tokens(x) for x in pv.terms) if t]
    types = set(pv.types)
    concept_ids: list[str] = []
    fact_ids: list[str] = []

    def add_concept(cid: str) -> None:
        if cid in store.concepts and cid not in concept_ids:
            concept_ids.append(cid)

    for key in pv.measures:
        add_concept(f"C:meas:{key}")
    if terms:
        for cid, words in idx.concepts.items():
            if (not types or store.concepts[cid].type in types) and any(_hits(t, words) for t in terms):
                add_concept(cid)
        for fid, words in idx.facts.items():
            f = store.facts[fid]
            if f.concept_id in concept_ids or (types and f.type not in types):
                continue
            if any(_hits(t, words) for t in terms) and (not reference_date or _known_on(f, store) <= reference_date):
                fact_ids.append(fid)
    for kind in pv.derived:
        prefix = _DERIVED_PREFIX[kind]
        for d in store.derived.values():
            if not d.fact_id.startswith(prefix):
                continue
            # Drug-specific variables keep only their drugs; without terms every one counts
            if kind in ("current_medication", "medication_change", "followup") and terms \
                    and not any(_hits(t, _tokens(d.en)) for t in terms):
                continue
            if kind == "current_medication":
                # The medication rows themselves (strength, schedule columns); the derived line
                # restating them is dropped when the screen is built
                for fid in d.inputs:
                    f = store.get_fact(fid)
                    if f is not None and f.concept_id:
                        add_concept(f.concept_id)
            if d.fact_id not in fact_ids:
                fact_ids.append(d.fact_id)
    return concept_ids, fact_ids


def materialize(plan: PlanOutput, store: FactStore, reference_date: str) -> SelectOutput:
    """The plan as the select call's output for this patient (same ranking: critical first)."""
    from app.fastpath.service import _TIER_RANK, _name
    ranked = sorted(enumerate(plan.planned()), key=lambda iv: (_TIER_RANK[iv[1].tier], iv[0]))
    variables = []
    for rank, (_, pv) in enumerate(ranked, 1):
        concept_ids, fact_ids = match(pv, store, reference_date)
        variables.append(SelectedVariable(
            name=_name(pv.label), label=pv.label, concept_ids=concept_ids, fact_ids=fact_ids,
            time_scope=pv.scope, priority=rank, tier=pv.tier, output=pv.output, columns=pv.columns,
        ))
    return SelectOutput(role=plan.role, task=plan.task, variables=variables)
