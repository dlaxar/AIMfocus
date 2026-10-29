"""
contradictions/run.py – find the contradictions of one patient and write them to the cache.

Usage (from core/):
    python -m contradictions --patient 0000-0001                       # rules only, no API key
    python -m contradictions --patient 0000-0001 --llm                 # + LLM sweep and judge
    python -m contradictions --patient 0000-0001 --llm --offline-llm DIR
    python -m contradictions --patient 0000-0001 --keep-s3             # keep S3's own results too

Reads:  cache/<patient>/documents.json, passages.json (S0 runs first if they are missing)
        cache/<patient>/facts.json (optional: S1 concept IDs, so screens can match contradictions)
Writes: cache/<patient>/contradictions.json         shown contradictions (app.schemas.Contradiction),
                                                    the file the FactStore loads
        cache/<patient>/contradictions_report.json  every finding with rule, certainty, judge verdict
        cache/<patient>/manifest.json               contradiction_count, if the manifest exists
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from app.config import settings
from app.schemas import Contradiction, Document, Fact, Passage
from contradictions import llm
from contradictions.model import Corpus, Finding
from contradictions.rules import run_rules

logger = logging.getLogger("contradictions")

OUR_PREFIX = "X:cf:"


def load_corpus(out_dir: Path) -> Corpus:
    documents = [Document.model_validate(d) for d in json.loads((out_dir / "documents.json").read_text(encoding="utf-8"))]
    passages = [Passage.model_validate(p) for p in json.loads((out_dir / "passages.json").read_text(encoding="utf-8"))]
    return Corpus(documents, passages)


def _load_facts(out_dir: Path) -> list[Fact]:
    path = out_dir / "facts.json"
    if not path.exists():
        return []
    return [Fact.model_validate(f) for f in json.loads(path.read_text(encoding="utf-8"))]


def is_shown(finding: Finding, include_possible: bool, threshold: float) -> bool:
    """Definite findings always; possible ones when the judge confirmed them, or on request without a judge."""
    if finding.certainty == "definite":
        return True
    if finding.judge is not None:
        return finding.judge.verdict == "contradiction" and finding.judge.confidence >= threshold
    return include_possible


def _s3_extras(out_dir: Path, ours: list[Contradiction]) -> list[Contradiction]:
    """S3's own results in contradictions.json that this finder does not already report."""
    path = out_dir / "contradictions.json"
    if not path.exists():
        return []
    existing = [Contradiction.model_validate(x) for x in json.loads(path.read_text(encoding="utf-8"))]
    return [
        cx for cx in existing
        if not cx.contradiction_id.startswith(OUR_PREFIX)
        and not any(llm.is_duplicate(cx.passage_ids, o.passage_ids) for o in ours)
    ]


async def run_async(
    cache_dir: Path,
    patient_id: str,
    *,
    data_dir: Optional[Path] = None,
    use_llm: bool = False,
    include_possible: bool = False,
    keep_s3: bool = False,
    model: Optional[str] = None,
) -> list[Contradiction]:
    out_dir = cache_dir / patient_id
    if not (out_dir / "documents.json").exists() or not (out_dir / "passages.json").exists():
        if data_dir is None:
            raise RuntimeError(f"No S0 output in {out_dir} and no data directory to create it from")
        from preprocess import s0_passages
        s0_passages.run(data_dir, cache_dir, patient_id)

    corpus = load_corpus(out_dir)
    facts = _load_facts(out_dir)

    findings = run_rules(corpus)
    logger.info("rules: %d finding(s)", len(findings))
    if use_llm:
        findings += await llm.sweep(corpus, findings, model)
        await llm.review(corpus, findings, model)

    threshold = settings.CONTRADICTION_CONFIDENCE_THRESHOLD
    shown = [f for f in findings if is_shown(f, include_possible, threshold)]
    shown_keys = {f.key for f in shown}
    contradictions = [f.to_contradiction(facts) for f in shown]
    if keep_s3:
        extras = _s3_extras(out_dir, contradictions)
        logger.info("keeping %d contradiction(s) from S3", len(extras))
        contradictions += extras

    (out_dir / "contradictions.json").write_text(
        json.dumps([cx.model_dump() for cx in contradictions], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = [
        {"contradiction_id": f.contradiction_id, "shown": f.key in shown_keys, **f.model_dump()}
        for f in findings
    ]
    (out_dir / "contradictions_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    manifest = out_dir / "manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["contradiction_count"] = len(contradictions)
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    for f in findings:
        mark = "shown " if f.key in shown_keys else "hidden"
        verdict = f" judge={f.judge.verdict}:{f.judge.confidence:.2f}" if f.judge else ""
        logger.info("  [%s] %-9s %-18s %s%s", mark, f.certainty, f.rule, f.summary_en, verdict)
    logger.info("wrote %d contradiction(s) to %s", len(contradictions), out_dir / "contradictions.json")
    return contradictions


def run(cache_dir: Path, patient_id: str, **kwargs) -> list[Contradiction]:
    return asyncio.run(run_async(cache_dir, patient_id, **kwargs))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser(description="Find contradictions in a patient's documents")
    parser.add_argument("--patient", default=settings.PATIENT_ID, help="Patient ID (default from .env)")
    parser.add_argument("--llm", action="store_true", help="Add an LLM sweep and judge the uncertain findings")
    parser.add_argument("--include-possible", action="store_true",
                        help="Without --llm: also show findings the rules mark as possible")
    parser.add_argument("--keep-s3", action="store_true",
                        help="Keep S3's results already in contradictions.json (duplicates are dropped)")
    parser.add_argument("--model", default=None, help="Override the model for sweep and judge")
    parser.add_argument("--offline-llm", type=Path, default=None, metavar="DIR",
                        help="Answer LLM calls from DIR/<key>.json instead of Gemini (see preprocess.run)")
    args = parser.parse_args()

    if args.offline_llm is not None:
        from app.llm.client import OfflineLLMClient, use_client
        use_client(OfflineLLMClient(args.offline_llm))

    run(
        settings.CACHE_DIR, args.patient,
        data_dir=settings.DATA_DIR,
        use_llm=args.llm,
        include_possible=args.include_possible,
        keep_s3=args.keep_s3,
        model=args.model,
    )


if __name__ == "__main__":
    main()
