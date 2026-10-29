"""
eval/run_eval.py – evaluation harness.

Usage:
    python -m eval.run_eval
    python -m eval.run_eval --extra          # include situations_extra.md
    python -m eval.run_eval --judge          # LLM checks each question against the screen
    python -m eval.run_eval --url http://localhost:8000 --cases-dir ../cases

For each situation in <cases-dir>/situations.md (and optionally eval/situations_extra.md):
  1. POST "title. description" to /api/screen – never the check questions – and record
     wall-clock time.
  2. Check traceability: the passage at start:end must contain the quoted text.
  3. Count validator drops from meta.dropped_items (hallucination monitor).
  4. Collect contradictions (unique across all screens).
  5. With --judge: an LLM marks which screen items answer each expected question; code
     then checks that those items cite one of the expected source documents
     ("not documented" answers need no source).

Outputs markdown tables to stdout, suitable for copy-paste into PR descriptions.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel, Field

EVAL_DIR = Path(__file__).parent


def _default_cases_dir() -> Path:
    from app.config import settings
    return Path(settings.DATA_DIR).parent / "cases"


# ---------------------------------------------------------------------------
# Load situations
# ---------------------------------------------------------------------------

_QUESTION_RE = re.compile(r"^\s*(\d+)\.\s+(.*)$")


def _parse_situations_md(path: Path) -> list[dict]:
    """
    Parse a situations markdown file into {id, title, description, questions} dicts.
    The description is the text between the "## N. Title" heading and the first
    numbered question; the questions are only for checking the screen.
    """
    situations: list[dict] = []
    current: Optional[dict] = None

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if current:
                situations.append(current)
            parts = line[3:].strip().split(". ", 1)
            current = {
                "id": parts[0],
                "title": parts[1] if len(parts) > 1 else line[3:].strip(),
                "description_lines": [],
                "questions": [],
            }
        elif current is None or line.startswith("# "):
            continue  # preamble / top-level header
        else:
            m = _QUESTION_RE.match(line)
            if m:
                current["questions"].append(m.group(2).strip())
            elif not current["questions"] and line.strip():
                current["description_lines"].append(line.strip())

    if current:
        situations.append(current)

    for s in situations:
        s["description"] = " ".join(s.pop("description_lines"))
    return situations


def situation_text(sit: dict) -> str:
    """What a clinician would type: title and description, without the check questions."""
    title = sit["title"].rstrip(".")
    return f"{title}. {sit['description']}".strip()


def _load_expected(cases_dir: Path) -> dict[str, dict]:
    path = cases_dir / "expected.json"
    if not path.exists():
        return {}
    items = json.loads(path.read_text(encoding="utf-8"))
    return {item["id"]: item for item in items}


# ---------------------------------------------------------------------------
# Screen helpers
# ---------------------------------------------------------------------------

def screen_items(screen: dict) -> list[dict]:
    """Flatten sections into a numbered item list (index = position in this list)."""
    items = []
    for section in screen.get("sections", []):
        for item in section.get("items", []):
            items.append({**item, "section": section.get("label", "")})
    return items


def fetch_v2_screen(base_url: str, situation: str) -> tuple[dict, int, int]:
    """
    Run one situation through the fast path (/api/v2) and wait until it is complete.
    Returns (screen in the /api/screen shape, first-response ms, complete ms).
    """
    t0 = time.perf_counter()
    resp = httpx.post(f"{base_url}/api/v2/screens", json={"situation": situation}, timeout=60)
    resp.raise_for_status()
    screen = resp.json()
    if screen["status"] != "complete":
        screen = httpx.get(f"{base_url}/api/v2/screens/{screen['screen_id']}", params={"wait": "true"}, timeout=90).json()
    complete_ms = round((time.perf_counter() - t0) * 1000)
    # The ID returns at once; "first response" is when the documented items were ready
    first_ms = round(screen.get("timings_ms", {}).get("first_response", complete_ms))
    sections = [
        {"label": v["label"], "items": [{"kind": v["state"], "text_en": v["value"], "sources": v["sources"]}]}
        for v in screen["variables"]
    ]
    shaped = {
        "sections": sections,
        "contradictions": screen.get("contradictions", []),
        "meta": {"dropped_items": sum(v["state"] == "check" for v in screen["variables"])},
    }
    return shaped, first_ms, complete_ms


def _render_screen(items: list[dict]) -> str:
    lines = []
    for i, item in enumerate(items):
        docs = ", ".join(sorted({s["doc_id"] for s in item.get("sources", [])})) or "no source"
        lines.append(f"[{i}] ({item['section']}) [{item.get('kind')}] {item.get('text_en', '')}  — sources: {docs}")
    return "\n".join(lines)


def _check_traceability(screen: dict, doc_texts: dict[str, str]) -> tuple[int, int, list[str]]:
    """
    For each source in the screen, check that the passage text at start:end
    contains the quoted text (quote_de).
    Returns (passed, total, errors).
    """
    passed = 0
    total = 0
    errors = []
    for item in screen_items(screen):
        for src in item.get("sources", []):
            total += 1
            snippet = doc_texts.get(src["doc_id"], "")[src.get("start", 0):src.get("end", 0)]
            quote = src.get("quote_de", "")
            if not quote or " ".join(quote.split()) in " ".join(snippet.split()):
                passed += 1
            else:
                errors.append(
                    f"  Traceability fail: passage {src['passage_id']} "
                    f"at [{src.get('start')}:{src.get('end')}] does not contain '{quote[:30]}...'"
                )
    return passed, total, errors


# ---------------------------------------------------------------------------
# LLM judge (optional)
# ---------------------------------------------------------------------------

class QuestionVerdict(BaseModel):
    question_id: str
    answered: bool = False
    item_indices: list[int] = Field(default_factory=list)
    rationale: str = ""


class JudgeResult(BaseModel):
    questions: list[QuestionVerdict] = Field(default_factory=list)


_JUDGE_PROMPT = """You check whether a clinical screen answers given questions.

## Screen items (index, section, kind, text, cited documents)
{screen}

## Questions with the expected answer
{questions}

## Rules
- A question is answered if the screen states the expected answer (wording may differ) without the reader having to click anything. An expected "not documented" answer is answered only if the screen says that the information is not documented.
- List the indices of the items that give the answer in `item_indices`.
- Do not count items that state something different from the expected answer.
- One verdict per question, `question_id` exactly as given.
"""


async def judge_screen(items: list[dict], expected: list[dict]) -> JudgeResult:
    from app.llm.client import get_client
    questions = "\n".join(f"- {e['id']}: {e['question']}\n  expected: {e['answer']}" for e in expected)
    prompt = _JUDGE_PROMPT.format(screen=_render_screen(items), questions=questions)
    return await get_client().generate(
        prompt_name="eval_judge",
        messages=[{"role": "user", "content": prompt}],
        response_schema=JudgeResult,
        temperature=0.0,
    )


def score_question(verdict: Optional[QuestionVerdict], expected: dict, items: list[dict]) -> tuple[bool, str]:
    """Correct = answered AND (no source expected OR an answering item cites an expected source)."""
    if verdict is None or not verdict.answered:
        return False, "not answered"
    expected_sources = set(expected.get("sources", []))
    if not expected_sources:
        return True, "ok (not documented, no source needed)"
    cited = {
        s["doc_id"]
        for i in verdict.item_indices if 0 <= i < len(items)
        for s in items[i].get("sources", [])
    }
    if cited & expected_sources:
        return True, "ok"
    return False, f"answered but cites {sorted(cited) or 'nothing'}, expected one of {sorted(expected_sources)}"


# ---------------------------------------------------------------------------
# Main eval loop
# ---------------------------------------------------------------------------

def run_eval(base_url: str, include_extra: bool, cases_dir: Path, use_judge: bool, v2: bool = False) -> None:
    situations = _parse_situations_md(cases_dir / "situations.md")
    if include_extra:
        extra_path = EVAL_DIR / "situations_extra.md"
        if extra_path.exists():
            situations.extend(_parse_situations_md(extra_path))

    expected = _load_expected(cases_dir)
    if include_extra and (EVAL_DIR / "expected_extra.json").exists():
        extra = json.loads((EVAL_DIR / "expected_extra.json").read_text(encoding="utf-8"))
        expected.update({item["id"]: item for item in extra})

    # Fetch document texts for traceability check
    doc_texts: dict[str, str] = {}
    try:
        for doc_meta in httpx.get(f"{base_url}/api/documents", timeout=10).json():
            doc_id = doc_meta["doc_id"]
            doc_texts[doc_id] = httpx.get(f"{base_url}/api/documents/{doc_id}", timeout=10).json().get("text", "")
    except Exception as e:
        print(f"Warning: could not fetch documents for traceability check: {e}", file=sys.stderr)

    rows = [
        "| # | Situation | Time (ms) | Traceability | Dropped | Contradictions | Correct |",
        "|---|-----------|-----------|--------------|---------|----------------|---------|",
    ]
    times: list[int] = []
    tr_pass_all = tr_total_all = dropped_all = 0
    contradictions: dict[str, dict] = {}
    correct_all = questions_all = 0
    question_rows: list[str] = []

    for sit in situations:
        sit_id = sit["id"]
        t0 = time.perf_counter()
        try:
            if v2:
                screen, first_ms, elapsed_ms = fetch_v2_screen(base_url, situation_text(sit))
                print(f"Situation {sit_id}: first response {first_ms} ms, complete {elapsed_ms} ms", file=sys.stderr)
            else:
                resp = httpx.post(f"{base_url}/api/screen", json={"situation": situation_text(sit)}, timeout=60)
                elapsed_ms = round((time.perf_counter() - t0) * 1000)
                if resp.status_code != 200:
                    rows.append(f"| {sit_id} | {sit['title'][:40]} | {elapsed_ms} | ❌ HTTP {resp.status_code} | – | – | – |")
                    continue
                screen = resp.json()
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - t0) * 1000)
            rows.append(f"| {sit_id} | {sit['title'][:40]} | {elapsed_ms} | ❌ {exc} | – | – | – |")
            continue
        times.append(elapsed_ms)

        tr_pass, tr_total, tr_errors = _check_traceability(screen, doc_texts)
        tr_pass_all += tr_pass
        tr_total_all += tr_total
        for e in tr_errors:
            print(e, file=sys.stderr)

        dropped = screen.get("meta", {}).get("dropped_items", 0)
        dropped_all += dropped

        shown_cx = screen.get("contradictions", []) + screen.get("other_contradictions", [])
        for cx in shown_cx:
            contradictions.setdefault(cx["contradiction_id"], cx)

        items = screen_items(screen)
        sit_expected = [v for k, v in sorted(expected.items()) if k.startswith(f"{sit_id}.")]
        correct_str = "–"
        if use_judge and sit_expected:
            result = asyncio.run(judge_screen(items, sit_expected))
            by_q = {v.question_id: v for v in result.questions}
            n_ok = 0
            for exp in sit_expected:
                ok, why = score_question(by_q.get(exp["id"]), exp, items)
                n_ok += ok
                question_rows.append(f"| {exp['id']} | {'✅' if ok else '❌'} | {why} |")
            correct_all += n_ok
            questions_all += len(sit_expected)
            correct_str = f"{n_ok}/{len(sit_expected)}"
        elif sit_expected:
            print(f"\n### Situation {sit_id}: {sit['title']}")
            for exp in sit_expected:
                print(f"  Q{exp['id']}: {exp['question']}\n  Expected: {exp['answer']}")
            print("  Screen:")
            print("    " + _render_screen(items).replace("\n", "\n    "))

        rows.append(
            f"| {sit_id} | {sit['title'][:40]} | {elapsed_ms} | {tr_pass}/{tr_total} | {dropped} | {len(shown_cx)} | {correct_str} |"
        )

    print()
    print("\n".join(rows))
    if question_rows:
        print("\n| Question | Correct | Note |\n|---|---|---|")
        print("\n".join(question_rows))

    # Metrics as defined in the challenge README
    trace_pct = f"{tr_pass_all / tr_total_all * 100:.0f}%" if tr_total_all else "–"
    print("\n| Metric | Result | Target |\n|---|---|---|")
    print(f"| Correct answers | {f'{correct_all}/{questions_all}' if use_judge else 'run with --judge'} | 15 of 15 |")
    print(f"| Traceability | {trace_pct} ({tr_pass_all}/{tr_total_all} sources) | 100% |")
    print(f"| Invented facts | {dropped_all} validator drops (judge the screens by hand) | 0 |")
    print(f"| Contradictions | {len(contradictions)} unique shown (mark real vs. false alarm by hand) | 5 or more |")
    print(f"| Load time | {max(times) if times else '–'} ms slowest | under 10 s |")

    if contradictions:
        print("\nContradictions shown:")
        for cx in contradictions.values():
            print(f"  - [{cx.get('kind')}] {cx.get('summary_en')}  ({', '.join(cx.get('passage_ids', []))})")


def main():
    parser = argparse.ArgumentParser(description="Run eval against the running backend")
    parser.add_argument("--url", default="http://localhost:8000", help="Backend base URL")
    parser.add_argument("--extra", action="store_true", help="Include situations_extra.md")
    parser.add_argument("--cases-dir", type=Path, default=None,
                        help="Folder with situations.md and expected.json (default: next to DATA_DIR)")
    parser.add_argument("--judge", action="store_true", help="Score answers with the LLM judge")
    parser.add_argument("--v2", action="store_true", help="Use the fast path (/api/v2/screens), timed until complete")
    args = parser.parse_args()
    run_eval(args.url, args.extra, args.cases_dir or _default_cases_dir(), args.judge, v2=args.v2)


if __name__ == "__main__":
    main()
