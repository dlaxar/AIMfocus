"""
preprocess/validate_fhir.py – check cache/<patient>/fhir.json with the official HL7 FHIR
Validator (validator_cli.jar, Java 17+), against FHIR R4 4.0.1 plus our extension
definitions in app/fhir/ig/.

    python -m preprocess.validate_fhir --patient 0000-0001
    FHIR_VALIDATOR_JAR=~/tools/validator_cli.jar JAVA=~/tools/jdk-21/bin/java python -m preprocess.validate_fhir

Download: https://github.com/hapifhir/org.hl7.fhir.core/releases/latest/download/validator_cli.jar
Exit code 1 when the validator reports errors.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings  # noqa: E402

IG_DIR = Path(__file__).parent.parent / "app" / "fhir" / "ig"


def _java() -> str:
    if os.environ.get("JAVA"):
        return os.environ["JAVA"]
    local = sorted(glob.glob(os.path.expanduser("~/tools/jdk-*/bin/java")))
    return local[-1] if local else "java"


def _jar() -> str:
    return os.environ.get("FHIR_VALIDATOR_JAR", os.path.expanduser("~/tools/validator_cli.jar"))


def validate(bundle_path: Path) -> dict:
    """Run the validator; returns {severity: count} and the grouped messages."""
    with tempfile.TemporaryDirectory() as tmp:
        ig = Path(tmp) / "ig"
        shutil.copytree(IG_DIR, ig)
        # The patient's concept CodeSystem, so codings in it can be checked too
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        for entry in bundle.get("entry", []):
            if entry["resource"]["resourceType"] == "CodeSystem":
                (ig / f"CodeSystem-{entry['resource']['id']}.json").write_text(json.dumps(entry["resource"]), encoding="utf-8")
        out = Path(tmp) / "outcome.json"
        cmd = [_java(), "-Xmx3g", "-jar", _jar(), str(bundle_path), "-version", "4.0.1", "-ig", str(ig), "-output", str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if not out.exists():
            raise RuntimeError(f"validator produced no outcome (exit {proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
        outcome = json.loads(out.read_text(encoding="utf-8"))

    issues = outcome.get("issue", [])
    counts = collections.Counter(i["severity"] for i in issues)
    grouped: dict[str, collections.Counter] = {}
    for i in issues:
        text = (i.get("details") or {}).get("text") or i.get("diagnostics", "")
        key = re.sub(r"\d+", "N", re.sub(r"'[^']*'", "'…'", text))[:180]
        grouped.setdefault(i["severity"], collections.Counter())[key] += 1
    return {"counts": dict(counts), "grouped": grouped,
            "errors": [((i.get("expression") or [""])[0], (i.get("details") or {}).get("text", ""))
                       for i in issues if i["severity"] in ("error", "fatal")]}


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate fhir.json with the official HL7 FHIR Validator")
    ap.add_argument("--patient", default=settings.PATIENT_ID)
    ap.add_argument("--file", type=Path, default=None, help="bundle to check (default cache/<patient>/fhir.json)")
    args = ap.parse_args()
    path = args.file or settings.CACHE_DIR / args.patient / "fhir.json"
    result = validate(path)
    print(f"{path}: {result['counts']}")
    for severity in ("fatal", "error", "warning"):
        for msg, n in result["grouped"].get(severity, collections.Counter()).most_common(8):
            print(f"  {severity:8s} {n:4d}  {msg}")
    for where, text in result["errors"][:10]:
        print(f"  ERROR at {where}: {text[:200]}")
    sys.exit(1 if result["errors"] else 0)


if __name__ == "__main__":
    main()
