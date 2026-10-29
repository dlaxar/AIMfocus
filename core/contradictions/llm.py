"""
contradictions/llm.py – optional LLM layer on top of the rules.

sweep()   One call with every passage asks the strong model for contradictions the rules
          did not find. The answer is checked in code: every passage ID must exist and every
          quote must be copied verbatim from a cited passage, otherwise the item is dropped.
          Items that repeat a known finding are dropped too.
review()  An independent judge call per "possible" finding (sweep items and the rules'
          uncertain ones) decides contradiction / change over time / measurement noise /
          not related. The rules' "definite" findings are never sent: arithmetic needs no vote.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional, get_args

from pydantic import BaseModel, Field

from app.config import settings
from app.llm.client import get_client
from app.schemas import ContradictionJudge, ContradictionKind, ContradictionVerdict
from contradictions.model import Corpus, Finding
from contradictions.text import norm

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "app" / "llm" / "prompts"
_KINDS = set(get_args(ContradictionKind))
_VERDICTS = set(get_args(ContradictionVerdict))
_MAX_PARALLEL_JUDGES = 4


class SweepItem(BaseModel):
    summary_en: str = ""
    kind: str = "value"
    passage_ids: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)
    why: str = ""


class SweepOutput(BaseModel):
    contradictions: list[SweepItem] = Field(default_factory=list)


class JudgeOutput(BaseModel):
    verdict: str = "not_related"
    confidence: float = 0.0
    rationale: str = ""


def _load(name: str) -> str:
    return (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


def render_documents(c: Corpus) -> str:
    """Every passage as '[passage_id] text', grouped by document in date order."""
    blocks = []
    for doc_id in sorted(c.by_doc, key=lambda d: (str(c.doc_date(d) or ""), d)):
        lines = [f"## {c.ref(doc_id)} · document {doc_id}"]
        lines += [f"[{p.passage_id}] {' '.join(p.text.split())}" for p in c.by_doc[doc_id]]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _render_passages(c: Corpus, passage_ids: list[str]) -> str:
    return "\n".join(
        f"[{pid}] ({c.ref(c.passage_map[pid].doc_id)}): {' '.join(c.passage_map[pid].text.split())}"
        for pid in passage_ids if pid in c.passage_map
    )


def is_duplicate(a: list[str], b: list[str]) -> bool:
    """Same contradiction: two shared passages, or one passage set inside the other.

    One shared passage is not enough: a letter header holds both the admission and the
    discharge date, which are two contradictions.
    """
    sa, sb = set(a), set(b)
    return len(sa & sb) >= 2 or sa <= sb or sb <= sa


def validate_item(item: SweepItem, c: Corpus) -> tuple[Optional[Finding], str]:
    """A sweep item as a 'possible' Finding, or None and the reason it was dropped."""
    pids = [pid for pid in dict.fromkeys(item.passage_ids) if pid in c.passage_map]
    if not pids:
        return None, "no known passage IDs"
    if not item.summary_en.strip():
        return None, "no summary"
    if not item.quotes:
        return None, "no quotes"
    cited = [norm(c.passage_map[pid].text) for pid in pids]
    for quote in item.quotes:
        q = norm(quote).strip(" \"'„“…")
        if not q or not any(q in text for text in cited):
            return None, f"quote not found in the cited passages: {quote!r}"
    return Finding(
        key="llm:" + "+".join(sorted(pids)),
        rule="llm_sweep",
        kind=item.kind if item.kind in _KINDS else "value",
        certainty="possible",
        summary_en=item.summary_en.strip(),
        passage_ids=pids,
        rationale=item.why.strip(),
    ), ""


async def sweep(c: Corpus, known: list[Finding], model: Optional[str] = None) -> list[Finding]:
    """New, validated candidates from one LLM pass over all passages."""
    known_block = "\n".join(f"- {f.summary_en} [{', '.join(f.passage_ids)}]" for f in known) or "(none)"
    prompt = _load("cx_sweep").replace("{{known}}", known_block).replace("{{documents}}", render_documents(c))
    output: SweepOutput = await get_client().generate(
        prompt_name="cx_sweep",
        messages=[{"role": "user", "content": prompt}],
        response_schema=SweepOutput,
        temperature=0.0,
        model=model or settings.PREPROCESS_MODEL,
    )

    found: list[Finding] = []
    for item in output.contradictions:
        finding, reason = validate_item(item, c)
        if finding is None:
            logger.info("cx sweep: dropped (%s): %s", reason, item.summary_en)
        elif any(is_duplicate(finding.passage_ids, k.passage_ids) for k in known + found):
            logger.info("cx sweep: already known: %s", item.summary_en)
        else:
            found.append(finding)
    logger.info("cx sweep: %d new candidate(s) from %d item(s)", len(found), len(output.contradictions))
    return found


async def _judge(c: Corpus, finding: Finding, model: Optional[str]) -> ContradictionJudge:
    prompt = (
        _load("cx_judge")
        .replace("{{summary}}", finding.summary_en)
        .replace("{{kind}}", finding.kind)
        .replace("{{passages}}", _render_passages(c, finding.passage_ids))
    )
    output: JudgeOutput = await get_client().generate(
        prompt_name="cx_judge",
        messages=[{"role": "user", "content": prompt}],
        response_schema=JudgeOutput,
        temperature=0.0,
        model=model or settings.PREPROCESS_MODEL,
    )
    verdict = output.verdict if output.verdict in _VERDICTS else "not_related"
    return ContradictionJudge(verdict=verdict, confidence=max(0.0, min(1.0, output.confidence)), rationale=output.rationale)


async def review(c: Corpus, findings: list[Finding], model: Optional[str] = None) -> None:
    """Ask the judge about every 'possible' finding (in parallel) and store its verdict on the finding."""
    sem = asyncio.Semaphore(_MAX_PARALLEL_JUDGES)

    async def one(finding: Finding) -> None:
        async with sem:
            try:
                finding.judge = await _judge(c, finding, model)
            except Exception as exc:  # one failed call must not lose the other findings
                logger.warning("cx judge failed for %s: %s", finding.key, exc)

    await asyncio.gather(*(one(f) for f in findings if f.certainty == "possible"))
