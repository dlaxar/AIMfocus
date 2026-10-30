"""
preprocess/run.py – CLI orchestrator for the offline preprocessing pipeline.

Usage:
    python -m preprocess.run --patient 0000-0001
    python -m preprocess.run --patient 0000-0001 --from s1
    python -m preprocess.run --patient 0000-0001 --from s3 --model gemini-4-argon
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure the core root is importable when run as a module
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("preprocess.run")

STAGES = ["s0", "s1", "s2", "s3", "s4"]


def _stage_index(name: str) -> int:
    return STAGES.index(name.lower())


def run_pipeline(patient_id: str, from_stage: str, model: str | None) -> None:
    # One event loop for all stages: the LLM client keeps HTTP connections bound to the loop
    # that opened them, so a new loop per stage fails with "Event loop is closed"
    asyncio.run(_run_pipeline_async(patient_id, from_stage, model))


async def _run_pipeline_async(patient_id: str, from_stage: str, model: str | None) -> None:
    data_dir = settings.DATA_DIR
    cache_dir = settings.CACHE_DIR
    start = _stage_index(from_stage)
    out_dir = cache_dir / patient_id

    # Restart from fhir.json when the stage files are gone: write back what the earlier
    # stages produced (resources are tagged with the stage that made them)
    fhir_path = out_dir / "fhir.json"
    if start > 0 and fhir_path.exists() and not (out_dir / "documents.json").exists():
        from app import fhir
        cache = fhir.load(fhir_path)
        cache.drop_from(STAGES[start])
        written = fhir.to_json_dir(cache, out_dir)
        logger.info("Restored %s from fhir.json for --from %s", ", ".join(written), STAGES[start])

    if start <= 0:
        logger.info("=== S0: Normalize + split ===")
        from preprocess import s0_passages
        s0_passages.run(data_dir, cache_dir, patient_id)

    if start <= 1:
        logger.info("=== S1: LLM fact extraction ===")
        from preprocess import s1_extract
        await s1_extract.run_async(data_dir, cache_dir, patient_id, model)

    if start <= 2:
        logger.info("=== S2: Merge + timelines + derived ===")
        from preprocess import s2_merge
        await s2_merge.run_async(cache_dir, patient_id)

    if start <= 3:
        logger.info("=== S3: Contradictions ===")
        from contradictions import run as cx
        await cx.run_async(cache_dir, patient_id, data_dir=data_dir)

    if start <= 4:
        logger.info("=== S4: Export ===")
        from preprocess import s4_export
        manifest = s4_export.run(cache_dir, patient_id)
        logger.info(
            "Pipeline complete. Coverage: %.1f%%. Manifest at %s",
            manifest.coverage_pct,
            cache_dir / patient_id / "manifest.json",
        )

    # The runtime reads one file: the whole cache as a validated FHIR R4 Bundle, with
    # ICD-10 / ATC codes from IBM's extraction and LOINC for measurements
    logger.info("=== FHIR bundle ===")
    from preprocess import convert_cache
    convert_cache.to_fhir(patient_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline preprocessing pipeline")
    parser.add_argument("--patient", default=settings.PATIENT_ID, help="Patient ID (default from .env)")
    parser.add_argument(
        "--from",
        dest="from_stage",
        choices=STAGES,
        default="s0",
        help="Start from this stage (earlier stages assumed complete)",
    )
    parser.add_argument("--model", default=None, help="Override the preprocessing model name")
    parser.add_argument(
        "--offline-llm",
        type=Path,
        default=None,
        metavar="DIR",
        help="Answer LLM calls from DIR/<key>.json instead of Gemini; missing answers are "
             "written as DIR/<key>.prompt.md (re-run after adding them)",
    )
    args = parser.parse_args()

    offline = None
    if args.offline_llm is not None:
        from app.llm.client import OfflineLLMClient, use_client
        offline = OfflineLLMClient(args.offline_llm)
        use_client(offline)

    run_pipeline(args.patient, args.from_stage, args.model)

    if offline is not None and offline.missing:
        logger.warning("%d prompt(s) still need an answer in %s:", len(offline.missing), args.offline_llm)
        for key in offline.missing:
            logger.warning("  %s.prompt.md", key)


if __name__ == "__main__":
    main()
