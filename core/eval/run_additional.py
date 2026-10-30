"""
eval/run_additional.py – the team's additional questions (AIMfocus_test_questions_additional.csv,
24 situations, 72 questions) against a running core, judged by Claude Opus 5.5:

  fast   the screen of the fast model (Haiku), built from the situation alone
  final  the same screen after the Opus upgrade (upgrade "done")
  chat   each question asked in the chat, on that screen

Correct = the judge finds the expected answer and, for a positive answer, a cited document
from the expected sources. Needs CLAUDE_API_KEY (the judge).

    uvicorn app.main:app --port 8001 &
    python -m eval.run_additional [--csv eval/AIMfocus_test_questions_additional.csv]
                                  [--url http://localhost:8001] [--out /tmp/additional.json]
"""
import asyncio, csv, json, re, time
from collections import defaultdict

import httpx
from pydantic import BaseModel, Field

import argparse
from pathlib import Path

from app.llm.client import AnthropicClient

_ap = argparse.ArgumentParser(description="Additional team questions: screens, upgrade, chat")
_ap.add_argument("--csv", default=str(Path(__file__).parent / "AIMfocus_test_questions_additional.csv"))
_ap.add_argument("--url", default="http://localhost:8001")
_ap.add_argument("--out", default="/tmp/additional.json", help="per-question results and answers (JSON)")
_args = _ap.parse_args()
CSV, BASE, OUT = _args.csv, _args.url.rstrip("/"), _args.out
JUDGE_MODEL = "claude-opus-5-5"


class Verdict(BaseModel):
    question_id: str
    answered: bool = False
    item_indices: list[int] = Field(default_factory=list)
    rationale: str = ""


class Verdicts(BaseModel):
    questions: list[Verdict] = Field(default_factory=list)


SCREEN_PROMPT = """You check strictly whether a clinical screen answers given questions.

## Screen items (index, card label, state, text, cited documents)
{items}

## Questions with the expected answer
{questions}

## Rules
- Answered = the screen states the expected answer's key facts (wording may differ; numbers, dates, sides and doses must match) without the reader having to ask. Calculations the expected answer marks as "calculated" count if the screen gives the inputs or the result.
- An expected "not documented" answer counts only if the screen says that this information is not documented (an empty or missing card does not count).
- An item that states something different from or contradicting the expected answer does not count.
- List the indices of the items that give the answer in `item_indices`.
- One verdict per question, `question_id` exactly as given; give a short rationale.
"""

CHAT_PROMPT = """You check strictly whether chatbot answers to clinical questions are correct.

## Questions, expected answers and the chatbot's answers
{qa}

## Rules
- Correct = the answer states the expected answer's key facts (wording may differ; numbers, dates, sides and doses must match) and says nothing that contradicts it.
- An expected "not documented" answer is correct only if the answer says it is not documented (and does not invent it).
- Set `answered` true for a correct answer. `item_indices` stays empty. One verdict per question, `question_id` exactly as given; short rationale.
"""


def doc_ids(text):
    return set(re.findall(r"\b1234\d{4}\b", text))


def not_documented(expected):
    return bool(re.match(r"\s*(not documented|no\b)", expected, re.I)) and "not documented" in expected.lower()


def load():
    rows = list(csv.DictReader(open(CSV, encoding="utf-8-sig"), delimiter=";"))
    sits = defaultdict(list)
    for r in rows:
        sits[r["id"].split("-")[0]].append(r)
    return sits


def items_of(screen):
    items = []
    for v in screen["variables"]:
        items.append({"label": v["label"], "state": v["state"], "text": v["value"] or (v.get("headline") or {}).get("text", ""),
                      "docs": sorted({s["doc_id"] for s in v["sources"]})})
    return items


def render(items):
    return "\n".join(f"[{i}] ({it['label']}) [{it['state']}] {it['text']}  — sources: {', '.join(it['docs']) or 'none'}"
                     for i, it in enumerate(items))


def questions_block(rows):
    return "\n".join(f"- {r['id']}: {r['question']}\n  expected: {r['expected']}" for r in rows)


def score(verdict, row, docs_of):
    """docs_of(verdict) → documents the answer cites."""
    if verdict is None or not verdict.answered:
        return False, "not answered" + (f": {verdict.rationale}" if verdict else "")
    want = doc_ids(row["sources"])
    if not want or not_documented(row["expected"]):
        return True, "ok"
    cited = docs_of(verdict)
    return (True, "ok") if cited & want else (False, f"answered, cites {sorted(cited) or 'nothing'} not {sorted(want)}")


async def judge(client, prompt):
    return await client.generate("eval_judge", [{"role": "user", "content": prompt}], Verdicts, model=JUDGE_MODEL)


async def run_situation(sid, rows, http, client, sem):
    async with sem:
        r0 = rows[0]
        situation = f"{r0['situation']}. {r0['situation_text']}"
        t0 = time.perf_counter()
        s = (await http.post(f"{BASE}/api/v2/screens", json={"situation": situation})).json()
        fast = (await http.get(f"{BASE}/api/v2/screens/{s['screen_id']}", params={"wait": "true"})).json()
        t_fast = time.perf_counter() - t0
        final = (await http.get(f"{BASE}/api/v2/screens/{s['screen_id']}", params={"wait": "true", "final": "true"})).json()
        t_final = time.perf_counter() - t0
        chats = []
        for r in rows:
            a = (await http.post(f"{BASE}/api/v2/chat", json={"question": r["question"], "screen_id": s["screen_id"]})).json()
            chats.append(a)
        out = {"situation": sid, "title": r0["situation"], "t_fast": round(t_fast, 1), "t_final": round(t_final, 1),
               "final_model": final["model"], "upgrade": final["upgrade"], "results": {}}
        for name, screen in (("fast", fast), ("final", final)):
            items = items_of(screen)
            v = await judge(client, SCREEN_PROMPT.format(items=render(items), questions=questions_block(rows)))
            by = {x.question_id: x for x in v.questions}
            out["results"][name] = {
                r["id"]: score(by.get(r["id"]), r, lambda vd: {d for i in vd.item_indices if 0 <= i < len(items) for d in items[i]["docs"]})
                for r in rows}
            out[f"{name}_cards"] = [f"{it['label']}: {it['text'][:120]}" for it in items]
        qa = "\n".join(f"- {r['id']}: {r['question']}\n  expected: {r['expected']}\n  chatbot ({a['state']}): {a['answer']}"
                       for r, a in zip(rows, chats))
        v = await judge(client, CHAT_PROMPT.format(qa=qa))
        by = {x.question_id: x for x in v.questions}
        out["results"]["chat"] = {
            r["id"]: score(by.get(r["id"]), r, lambda vd, a=a: {s["doc_id"] for s in a["sources"]}) for r, a in zip(rows, chats)}
        out["chat_answers"] = {r["id"]: f"({a['state']}) {a['answer']}" for r, a in zip(rows, chats)}
        print(f"{sid} done: fast {t_fast:.1f}s final {t_final:.1f}s ({final['model']}, {final['upgrade']})", flush=True)
        return out


async def main():
    sits = load()
    client = AnthropicClient()
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=240) as http:
        results = await asyncio.gather(*(run_situation(k, v, http, client, sem) for k, v in sorted(sits.items())))
    json.dump(results, open(OUT, "w"), indent=1, ensure_ascii=False)
    rows = {r["id"]: r for v in sits.values() for r in v}
    for mode in ("fast", "final", "chat"):
        ok = sum(res["results"][mode][q][0] for res in results for q in res["results"][mode])
        print(f"\n=== {mode}: {ok}/{len(rows)}")
        for res in results:
            for q, (good, why) in res["results"][mode].items():
                if not good:
                    print(f"  {q} ({res['title'][:45]}): {why[:150]}")
    tf = [r["t_fast"] for r in results]; tl = [r["t_final"] for r in results]
    print(f"\nfast screen: mean {sum(tf)/len(tf):.1f}s max {max(tf):.1f}s | upgraded: mean {sum(tl)/len(tl):.1f}s max {max(tl):.1f}s"
          f" | upgrades done {sum(r['upgrade'] == 'done' for r in results)}/{len(results)}")


if __name__ == "__main__":
    asyncio.run(main())
