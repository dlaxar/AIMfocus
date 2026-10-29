"""
preprocess/s4_export.py – S4: Export the fact store to runtime-ready files.

Writes:
  cache/<patient>/fact_sheet.txt  – one line per fact + derived fact (~2–3 k tokens)
  cache/<patient>/manifest.json   – metadata, version, counts, coverage %
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.schemas import CacheManifest, DerivedFact, Fact, S1Report

logger = logging.getLogger(__name__)


def _fact_line(fact: Fact) -> str:
    date_str = fact.date or "unknown"
    return (
        f"{fact.fact_id} | {fact.type} | {fact.concept_id or '-'} | {fact.en} | "
        f"{fact.status} | {fact.laterality or '-'} | {date_str}"
    )


def _derived_line(df: DerivedFact) -> str:
    date_str = df.date or "unknown"
    return f"{df.fact_id} | derived | - | {df.en} | - | - | {date_str}"


def coverage_pct(reports: list[S1Report]) -> float:
    """Share of passages that have a fact, a no-content flag, or are headers."""
    total = sum(r.total_passages for r in reports)
    uncovered = sum(len(r.uncovered_passages) for r in reports)
    return (total - uncovered) / total * 100 if total > 0 else 0.0


def run(cache_dir: Path, patient_id: str) -> CacheManifest:
    out_dir = cache_dir / patient_id

    facts: list[Fact] = [
        Fact.model_validate(f)
        for f in json.loads((out_dir / "facts.json").read_text(encoding="utf-8"))
    ]
    derived: list[DerivedFact] = [
        DerivedFact.model_validate(d)
        for d in json.loads((out_dir / "derived.json").read_text(encoding="utf-8"))
    ]
    documents = json.loads((out_dir / "documents.json").read_text(encoding="utf-8"))
    passages = json.loads((out_dir / "passages.json").read_text(encoding="utf-8"))

    # Load S1 reports for coverage stats
    reports: list[S1Report] = []
    s1_path = out_dir / "s1_report.json"
    if s1_path.exists():
        reports = [
            S1Report.model_validate(r)
            for r in json.loads(s1_path.read_text(encoding="utf-8"))
        ]

    # Build fact sheet
    lines = [
        "# Fact sheet",
        f"# Patient: {patient_id}",
        f"# Columns: fact_id | type | concept_id | description | status | laterality | date",
        "",
    ]
    for fact in facts:
        lines.append(_fact_line(fact))
    lines.append("")
    for df in derived:
        lines.append(_derived_line(df))

    fact_sheet = "\n".join(lines)
    (out_dir / "fact_sheet.txt").write_text(fact_sheet, encoding="utf-8")
    logger.info("S4: wrote fact_sheet.txt (%d lines)", len(lines))

    coverage = coverage_pct(reports)

    contradictions_path = out_dir / "contradictions.json"
    contradiction_count = 0
    if contradictions_path.exists():
        contradiction_count = len(json.loads(contradictions_path.read_text(encoding="utf-8")))

    manifest = CacheManifest(
        patient_id=patient_id,
        completed_at=datetime.now(timezone.utc).isoformat(),
        preprocess_model=settings.PREPROCESS_MODEL,
        prompt_versions={},
        document_count=len(documents),
        passage_count=len(passages),
        fact_count=len(facts),
        derived_fact_count=len(derived),
        contradiction_count=contradiction_count,
        coverage_pct=round(coverage, 1),
    )

    (out_dir / "manifest.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    logger.info(
        "S4: manifest written – coverage %.1f%%, %d facts, %d derived, %d contradictions",
        coverage, len(facts), len(derived), contradiction_count,
    )
    return manifest
