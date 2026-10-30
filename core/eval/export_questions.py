"""
eval/export_questions.py – all test situations and questions for clinical review.

Writes eval/test-questions.md (readable) and eval/test-questions.csv (spreadsheet), with
the expected answer, the source letters, and – when run outputs are given – whether the
screen answered each question.

    python eval/export_questions.py --cases-dir ../../TRACK-1-clinical-documentation/cases \
        [--runs /tmp/run1.txt /tmp/run2.txt]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).parent
sys.path.insert(0, str(EVAL_DIR.parent))

from eval.run_eval import _parse_situations_md  # noqa: E402
from eval.score_runs import score  # noqa: E402


def letters(data_dir: Path) -> dict[str, str]:
    """doc_id → "Title (date)" from the patient's documents.json."""
    for path in sorted(data_dir.glob("*/documents.json")):
        docs = json.loads(path.read_text(encoding="utf-8")).get("documents", [])
        return {str(d["id"]): f"{d.get('title', d['id'])} ({d.get('date', '?')})" for d in docs}
    return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases-dir", type=Path, required=True)
    ap.add_argument("--data-dir", type=Path, default=EVAL_DIR.parent.parent / "data")
    ap.add_argument("--runs", nargs="*", default=[], help="run_eval --v2 --extra outputs to show results for")
    args = ap.parse_args()

    situations = _parse_situations_md(args.cases_dir / "situations.md") + _parse_situations_md(EVAL_DIR / "situations_extra.md")
    expected = {e["id"]: e for e in json.loads((args.cases_dir / "expected.json").read_text(encoding="utf-8"))}
    expected.update({e["id"]: e for e in json.loads((EVAL_DIR / "expected_extra.json").read_text(encoding="utf-8"))})
    doc_names = letters(args.data_dir)
    results = [score(r) for r in args.runs]

    rows = []
    for s in situations:
        for i, question in enumerate(s["questions"], 1):
            qid = f"{s['id']}.{i}"
            e = expected.get(qid, {})
            answered = [r.get(qid) for r in results]
            rows.append({
                "id": qid, "set": "challenge" if int(s["id"]) <= 5 else "team",
                "situation": s["title"], "situation_text": s["description"], "question": question,
                "expected": e.get("answer", ""), "sources": "; ".join(doc_names.get(d, d) for d in e.get("sources", [])) or "–",
                "answered": f"{sum(bool(a) for a in answered)}/{len(answered)}" if answered else "",
            })

    with (EVAL_DIR / "test-questions.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter=";")
        w.writeheader()
        w.writerows(rows)

    out = ["# Testfragen – Patientin Anna Musterfrau (geb. 15.03.1959)", "",
           "Diese Fragen prüfen, ob der Bildschirm einer Rolle die richtigen Informationen zeigt. "
           "Eingegeben wird nur die **Situation** (Titel und Text); die Fragen dienen nur der Prüfung. "
           "Gegenwart heißt: Stand 15.04.2016 (letzter Brief). Situationen 1–5 stammen aus der Challenge, "
           "6–31 hat das Team geschrieben. Bitte prüfen: Ist die Frage für diese Rolle sinnvoll? Stimmt die "
           "erwartete Antwort mit den Briefen überein? Fehlt etwas Wichtiges?", "",
           f"{len(situations)} Situationen, {len(rows)} Fragen."]
    if results:
        total = sum(int(r["answered"].split("/")[0]) for r in rows)
        out += ["", f"**Beantwortet** = in wie vielen der {len(results)} letzten Testläufe der Bildschirm die erwartete "
                    f"Antwort enthielt (gesamt {total} von {len(rows) * len(results)})."]
    current = None
    for r in rows:
        if r["situation"] != current:
            current = r["situation"]
            out += ["", f"## {r['id'].split('.')[0]}. {current}", "", f"> {r['situation_text']}", "",
                    "| # | Frage | Erwartete Antwort | Quellen |" + (" Beantwortet |" if results else ""),
                    "|---|---|---|---|" + ("---|" if results else "")]
        cells = [r["id"], r["question"], r["expected"], r["sources"]] + ([r["answered"]] if results else [])
        out.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    (EVAL_DIR / "test-questions.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} questions to {EVAL_DIR / 'test-questions.md'} and .csv")


if __name__ == "__main__":
    main()
