"""
preprocess/convert_cache.py – convert a patient cache between the JSON files and fhir.json.

Usage:
    python -m preprocess.convert_cache --patient 0000-0001            # JSON files → fhir.json
    python -m preprocess.convert_cache --patient 0000-0001 --to-json  # fhir.json → JSON files
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import fhir  # noqa: E402
from app.config import settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("preprocess.convert_cache")


def to_fhir(patient_id: str, check: bool = True) -> Path:
    out_dir = settings.CACHE_DIR / patient_id
    cache = fhir.from_json_dir(out_dir, patient_id, settings.DATA_DIR)
    path = out_dir / "fhir.json"
    bundle = fhir.save(cache, path, check=check)
    logger.info("Wrote %s (%d resources)", path, len(bundle["entry"]))
    return path


def to_json(patient_id: str) -> list[str]:
    out_dir = settings.CACHE_DIR / patient_id
    written = fhir.to_json_dir(fhir.load(out_dir / "fhir.json"), out_dir)
    logger.info("Wrote %s to %s", ", ".join(f"{n}.json" for n in written), out_dir)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a patient cache between JSON files and fhir.json")
    parser.add_argument("--patient", default=settings.PATIENT_ID)
    parser.add_argument("--to-json", action="store_true", help="Write the JSON files from fhir.json")
    parser.add_argument("--no-check", action="store_true", help="Skip the R4 validation")
    args = parser.parse_args()
    if args.to_json:
        to_json(args.patient)
    else:
        to_fhir(args.patient, check=not args.no_check)


if __name__ == "__main__":
    main()
