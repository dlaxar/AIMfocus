"""
contradictions/text.py – parsing helpers shared by the detectors.

Dates (including impossible ones), numbers, left/right words, negation, episodes of
care and short quotes. Everything works on the normalized S0 passage text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from app.schemas import Document

# Documents closer together than this belong to the same episode of care
EPISODE_GAP_DAYS = 30

_MONTHS_DE = {
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}
_MONTHS_EN = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

# "05.02.2020", "05/02/2020", "09/02/20200": the year group is open-ended on purpose,
# so impossible dates are found instead of being cut to a plausible prefix
_NUMERIC_DATE = re.compile(r"(?<![\d./])(\d{1,2})([./])(\d{1,2})\2(\d{2,})(?!\d|\.\d)")   # not the start of an OID such as 1.2.840.10008…
_TEXT_DATE = re.compile(
    r"(?<!\d)(\d{1,2})\.\s*(" + "|".join(_MONTHS_DE) + r")\s+(\d{4})(?!\d)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DateHit:
    raw: str                    # as written, e.g. "09/02/20200"
    start: int
    end: int
    value: Optional[date]       # None: the date cannot exist
    glued_time: bool = False    # "03.02.202011:02" – date and time without a space


def _make_date(year_s: str, month: int, day: int) -> Optional[date]:
    if len(year_s) == 4:
        year = int(year_s)
    elif len(year_s) == 2:
        year = 2000 + int(year_s) if int(year_s) < 50 else 1900 + int(year_s)
    else:
        return None
    if not 1900 <= year <= 2100:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def find_dates(text: str) -> list[DateHit]:
    """All dates in a text, in order; impossible ones have value None."""
    hits: list[DateHit] = []
    for m in _NUMERIC_DATE.finditer(text):
        day, month, year_s = int(m.group(1)), int(m.group(3)), m.group(4)
        end, glued = m.end(), False
        # A missing space before the time is a formatting slip, not a wrong date
        if len(year_s) == 6 and text[m.end():m.end() + 1] == ":":
            year_s, end, glued = year_s[:4], m.end() - 2, True
        hits.append(DateHit(text[m.start():end], m.start(), end, _make_date(year_s, month, day), glued))
    for m in _TEXT_DATE.finditer(text):
        month = _MONTHS_DE[m.group(2).lower()]
        hits.append(DateHit(m.group(), m.start(), m.end(), _make_date(m.group(3), month, int(m.group(1)))))
    return sorted(hits, key=lambda h: h.start)


def parse_iso(value: Optional[str]) -> Optional[date]:
    if not value or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def fmt_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def fmt_month(d: date) -> str:
    return f"{_MONTHS_EN[d.month - 1]} {d.year}"


def episodes(documents: Iterable[Document], gap_days: int = EPISODE_GAP_DAYS) -> dict[str, int]:
    """doc_id → episode index; a gap of more than gap_days between documents starts a new one."""
    dated = sorted((d, doc.doc_id) for doc in documents if (d := parse_iso(doc.date)))
    out: dict[str, int] = {}
    episode, prev = -1, None
    for d, doc_id in dated:
        if prev is None or (d - prev).days > gap_days:
            episode += 1
        out[doc_id] = episode
        prev = d
    return out


# ---------------------------------------------------------------------------
# Numbers, sides, negation
# ---------------------------------------------------------------------------

def to_float(value: str) -> float:
    return float(value.replace(",", "."))


def numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)?", text)


_SIDE_LONG = re.compile(
    r"\b(?:(links|linke[nmrs]?|linksseitig\w*|sinistr[ae]?)|(rechts|rechte[nmrs]?|rechtsseitig\w*|dextr[ae]?))\b",
    re.IGNORECASE,
)
# Abbreviations only in lower case ("li.", "li > re", "A. carotis sin."), so "Re-" or "LI" never count
_SIDE_SHORT = re.compile(r"\b(?:(li|sin)|(re|dext))\b")


def sides(text: str) -> list[str]:
    """Left/right words in reading order, as "L" / "R"."""
    found = [(m.start(), "L" if m.group(1) else "R") for rx in (_SIDE_LONG, _SIDE_SHORT) for m in rx.finditer(text)]
    return [side for _, side in sorted(found)]


_NEGATION = re.compile(r"\b(?:kein\w*|ohne|nicht|ausgeschlossen|negativ)\b", re.IGNORECASE)
_SUSPECTED = re.compile(r"\b(?:Vd\.\s?a\.|V\.\s?a\.|Verdacht\w*|fraglich\w*|suspekt\w*)", re.IGNORECASE)
_CLAUSE_BREAK = re.compile(r"[.;,:!?](?:\s|$)|\bsowie\b|\baber\b")


def clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Start and end of the clause around text[start:end] (commas, semicolons, 'sowie' split clauses)."""
    left = 0
    for m in _CLAUSE_BREAK.finditer(text, 0, start):
        left = m.end()
    m = _CLAUSE_BREAK.search(text, end)
    return left, (m.start() if m else len(text))


def polarity(text: str, start: int, end: int) -> str:
    """'negative', 'suspected', 'question' or 'positive' for the mention text[start:end]."""
    left, right = clause_bounds(text, start, end)
    if text[right:right + 1] == "?" or re.match(r"\s*Fragestellung", text):
        return "question"
    if _NEGATION.search(text[left:start]) or re.match(r"\W*(?:nicht nachweisbar|ausgeschlossen)", text[end:right]):
        return "negative"
    # Whole prefix: the period in "Vd.a. Mamma-Ca" would otherwise end the clause
    if _SUSPECTED.search(text[:start]):
        return "suspected"
    return "positive"


# ---------------------------------------------------------------------------
# Text for summaries
# ---------------------------------------------------------------------------

def norm(text: str) -> str:
    return " ".join(text.lower().split())


def snip(text: str, max_len: int = 90) -> str:
    """Whitespace-collapsed text, cut at a word boundary."""
    flat = " ".join(text.split())
    if len(flat) <= max_len:
        return flat
    cut = flat.rfind(" ", 0, max_len)
    return flat[:cut if cut > 0 else max_len].rstrip(",;:") + "…"
