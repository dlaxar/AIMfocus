"""
preprocess/s0_passages.py – S0: Normalize documents and split into passages.

Steps:
  1. Load documents/*.json, unescape HTML, normalize whitespace.
  2. Detect document date from the body text.
  3. Detect sections via known German headers.
  4. Split into passages (table rows / list items / sentences).
  5. Copy detection: fuzzy-match passages across documents, set copy_of.

Output files written to cache/<patient>/:
  documents.json  – list[Document]
  passages.json   – list[Passage]
"""
from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from typing import Optional

from app.schemas import Document, Passage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# German abbreviations that should NOT end a sentence
# ---------------------------------------------------------------------------
_ABBREVS = re.compile(
    r"\b(?:Dr|Prof|Hr|Fr|St|ca|bzw|ggf|inkl|evtl|usw|etc|Abb|Tab|Nr|Tel"
    r"|re|li|bds|vs|Str|u\.a|z\.B|d\.h|i\.e|p\.o|s\.c|i\.v|i\.m"
    r"|Z\.n|St\.p|Pat|Anm|Mio|Jh|Jg|geb|sin|dext|vgl|lt|sog|Art|Vd\.a|V\.a|wg|ventr|Diab|Mell|Gem|Prim|[A-Z])\."
    r"(?=\s)",
    re.IGNORECASE,
)

# "vom 03. April 2016": the day's period must not end the sentence
_DAY_BEFORE_MONTH = re.compile(
    r"\b\d{1,2}\.(?=\s+(?:Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\b)"
)

# Section header patterns (German discharge letters)
_SECTION_HEADERS = [
    "Diagnosen", "Nebendiagnosen", "Allergien", "Anamnese",
    "Therapie und Verlauf", "Verlauf", "Therapie",
    "Entlassungsmedikation", "Medikation bei Entlassung",
    "Gesamtbeurteilung", "Zusammenfassung",
    "Empfehlung", "Procedere", "Prozeduren",
    "Laborwerte", "Labor", "Untersuchungsergebnis",
    "Echokardiographie", "Echo", "EKG",
    "Befund", "Anlass", "Fragestellung",
    "Vitalparameter", "Befunde", "Erhobene Befunde", "Weiteres Procedere", "Interpretation",
    "Befundzusammenfassung", "Makroskopischer Befund", "Mikroskopischer Befund",
    "Immunhistologisches Profil", "Hormonrezeptorstatus", "Interne-Status präsens",
    # Section ends: keep signature and lab-table lines out of the medication section
    "Laborbefund", "Mit freundlichen Grüßen", "Mit kollegialen Grüßen",
]
_SECTION_RE = re.compile(
    r"^(?P<header>" + "|".join(re.escape(h) for h in _SECTION_HEADERS) + r")[:\s]*$",
    re.IGNORECASE | re.MULTILINE,
)

# Date extraction from German document body
_DATE_BODY_RE = re.compile(
    r"\b(?:am|vom|den|,\s*)?"
    r"(?P<d>\d{1,2})[.\-/](?P<m>\d{1,2})[.\-/](?P<y>\d{4})\b"
)

# Medication table row: starts with a drug-like token followed by dosage pattern
_MED_TABLE_ROW_RE = re.compile(
    r"^[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-/]+\s+\d",
)

# List item (German bullet patterns)
_LIST_ITEM_RE = re.compile(r"^[-•*–]\s+|^\d+[.)]\s+")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_text(raw: str) -> str:
    """Unescape HTML entities, convert <BR> to newline, collapse runs of spaces."""
    text = html.unescape(raw)
    text = re.sub(r"<[Bb][Rr]\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)          # strip remaining tags
    text = re.sub(r"[ \t]+", " ", text)            # collapse horizontal whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)          # max two consecutive newlines
    return text.strip()


def _extract_date_from_body(text: str) -> Optional[str]:
    """Return the first plausible document date found in the text body."""
    for m in _DATE_BODY_RE.finditer(text[:2000]):  # look in first 2 000 chars
        d, mo, y = int(m.group("d")), int(m.group("m")), int(m.group("y"))
        if 1 <= d <= 31 and 1 <= mo <= 12 and 2000 <= y <= 2030:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def _detect_sections(text: str) -> list[tuple[int, int, str]]:
    """
    Return list of (start_char, end_char, section_label) spans.
    'start_char' points to the first char after the header line.
    """
    spans: list[tuple[int, int, str]] = []
    for m in _SECTION_RE.finditer(text):
        spans.append((m.end(), len(text), m.group("header").strip(":")))
    # Trim each span's end to the start of the next
    for i in range(len(spans) - 1):
        spans[i] = (spans[i][0], spans[i + 1][0], spans[i][2])
    return spans


def _section_for_offset(offset: int, sections: list[tuple[int, int, str]]) -> Optional[str]:
    for start, end, label in sections:
        if start <= offset < end:
            return label
    return None


def _passage_id(doc_id: str, index: int) -> str:
    return f"{doc_id}:p{index:03d}"


_MEDICATION_SECTIONS = {"entlassungsmedikation", "medikation bei entlassung"}


def _split_into_passages(text: str, doc_id: str, sections: list[tuple[int, int, str]]) -> list[Passage]:
    """Split normalized document text into passages.

    Every passage is an exact slice: text[p.start:p.end] == p.text. Prose is grouped into
    paragraphs (blank-line separated) before sentence splitting, so sentences that wrap
    over several lines stay in one passage; such passages keep their inner line breaks.
    Single index source: always len(passages) at the moment of appending.
    """
    passages: list[Passage] = []

    def _add_passage(start: int, end: int, kind: str) -> None:
        """Append text[start:end] trimmed of surrounding whitespace (offsets adjusted)."""
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start >= end:
            return
        idx = len(passages)
        passages.append(
            Passage(
                passage_id=_passage_id(doc_id, idx),
                doc_id=doc_id,
                section=_section_for_offset(start, sections),
                kind=kind,  # type: ignore[arg-type]
                start=start,
                end=end,
                text=text[start:end],
            )
        )

    def _flush_prose(block_start: int, block_end: int) -> None:
        """Sentence-split a prose block and emit each sentence as a passage."""
        # Same-length substitutions only, so match offsets map 1:1 onto `text`
        flat = text[block_start:block_end].replace("\n", " ")
        protected = _ABBREVS.sub(lambda m: m.group().replace(".", "\x00"), flat)
        protected = _DAY_BEFORE_MONTH.sub(lambda m: m.group().replace(".", "\x00"), protected)
        pos = 0
        for m in re.finditer(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9])", protected):
            _add_passage(block_start + pos, block_start + m.start(), "sentence")
            pos = m.end()
        _add_passage(block_start + pos, block_end, "sentence")

    buf_start: Optional[int] = None
    buf_end = 0

    def _flush() -> None:
        nonlocal buf_start
        if buf_start is not None:
            _flush_prose(buf_start, buf_end)
            buf_start = None

    prev_end = 0
    for line_match in re.finditer(r"[^\n]+", text):
        line = line_match.group()
        lstart, lend = line_match.start(), line_match.end()
        stripped = line.strip()

        # Blank or whitespace-only line(s) since the previous line → paragraph break
        if text[prev_end:lstart].count("\n") >= 2 or not stripped:
            _flush()
        prev_end = lend
        if not stripped:
            continue

        section = _section_for_offset(lstart, sections)
        in_med_table = (section or "").lower() in _MEDICATION_SECTIONS

        if _SECTION_RE.match(stripped):
            _flush()
            _add_passage(lstart, lend, "header")
        elif (in_med_table and re.search(r"\d", stripped)) or _MED_TABLE_ROW_RE.match(stripped):
            _flush()
            _add_passage(lstart, lend, "table_row")
        elif _LIST_ITEM_RE.match(stripped):
            _flush()
            _add_passage(lstart, lend, "list_item")
        else:
            # A line continues the previous one if it starts in lower case (a wrapped
            # sentence) or the previous line ends with a period ("Diab. / Mell. / II" on three
            # lines, "vom 03. / April"): the sentence splitter re-splits real sentence ends, while
            # abbreviations stay joined. Otherwise it is a new unit, e.g. one diagnosis per line.
            prev_ends_with_period = buf_start is not None and text[buf_start:buf_end].rstrip().endswith(".")
            if buf_start is not None and not stripped[0].islower() and not prev_ends_with_period:
                _flush()
            if buf_start is None:
                buf_start = lstart
            buf_end = lend

    _flush()
    return passages


# ---------------------------------------------------------------------------
# Copy detection
# ---------------------------------------------------------------------------

def _token_set_ratio(a: str, b: str) -> float:
    """Fast token-set similarity (Jaccard on word sets)."""
    ta = set(re.findall(r"\w+", a.lower()))
    tb = set(re.findall(r"\w+", b.lower()))
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


_MIN_COPY_TOKENS = 5


def detect_copies(
    passages: list[Passage],
    threshold: float = 0.85,
    doc_dates: Optional[dict[str, Optional[str]]] = None,
) -> list[Passage]:
    """
    Mark passages that are near-duplicates of a passage in an EARLIER document:
    copy_of points from the later (copied) passage to the earlier original.

    Documents are ordered by date (doc_dates), then doc_id. Word-set similarity is
    used on purpose: a copy with "links"/"rechts" swapped still matches, and S3
    then reports the text difference as a copy_mismatch candidate.

    Returns the updated passage list in the original order.
    """
    doc_dates = doc_dates or {}
    by_doc: dict[str, list[Passage]] = {}
    for p in passages:
        by_doc.setdefault(p.doc_id, []).append(p)

    def _eligible(p: Passage) -> bool:
        return p.kind != "header" and len(re.findall(r"\w+", p.text)) >= _MIN_COPY_TOKENS

    ordered = sorted(by_doc, key=lambda d: (doc_dates.get(d) or "", d))
    updated = {p.passage_id: p for p in passages}

    for i, later_doc in enumerate(ordered):
        earlier_docs = ordered[:i]
        for pl in by_doc[later_doc]:
            if not _eligible(pl):
                continue
            best_ratio = 0.0
            best: Optional[Passage] = None
            for earlier_doc in earlier_docs:
                for pe in by_doc[earlier_doc]:
                    if not _eligible(pe):
                        continue
                    r = _token_set_ratio(pl.text, pe.text)
                    if r > best_ratio:
                        best_ratio, best = r, pe
            if best is not None and best_ratio >= threshold:
                # Point at the root original if the match is itself a copy
                original = updated[best.passage_id]
                root_id = original.copy_of or original.passage_id
                updated[pl.passage_id] = updated[pl.passage_id].model_copy(update={"copy_of": root_id})

    return [updated[p.passage_id] for p in passages]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def load_raw_documents(data_dir: Path, patient_id: str) -> list[dict]:
    """Load the raw JSON array from data/<patient>/documents/*.json."""
    patient_dir = data_dir / patient_id
    raw_docs: list[dict] = []
    # IBM folders are named "patient-<id>"; accept the bare ID as well
    if not patient_dir.exists() and (data_dir / f"patient-{patient_id}").exists():
        patient_dir = data_dir / f"patient-{patient_id}"

    # The IBM dataset may have one file per document or one array file
    doc_dir = patient_dir / "documents"
    if doc_dir.exists():
        for f in sorted(doc_dir.glob("*.json")):
            content = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(content, list):
                raw_docs.extend(content)
            else:
                # The file name is the document ID; keep it as a last-resort fallback
                content.setdefault("doc_id", f.stem)
                raw_docs.append(content)
    else:
        # Fallback: look for documents.json at patient root
        docs_file = patient_dir / "documents.json"
        if docs_file.exists():
            raw_docs = json.loads(docs_file.read_text(encoding="utf-8"))

    return raw_docs


def run(data_dir: Path, cache_dir: Path, patient_id: str, copy_threshold: float = 0.85) -> tuple[list[Document], list[Passage]]:
    """
    Run S0 for one patient.
    Returns (documents, passages) and writes them to cache_dir.
    """
    raw_docs = load_raw_documents(data_dir, patient_id)
    logger.info("S0: loaded %d raw documents for patient %s", len(raw_docs), patient_id)

    documents: list[Document] = []
    all_passages: list[Passage] = []

    for raw in raw_docs:
        # IBM files keep id/title/date/... under "metadata"; flat fixtures keep them at top level
        meta = raw.get("metadata") or {}

        def _field(*keys: str) -> Optional[str]:
            for src in (raw, meta):
                for k in keys:
                    if src.get(k):
                        return src[k]
            return None

        doc_id = str(_field("id", "doc_id") or "unknown")
        title = _field("title", "Titel") or ""
        raw_text = raw.get("text") or raw.get("content") or ""
        doc_type = _field("type", "Typ") or ""
        department = _field("department", "Abteilung") or ""
        meta_date = _field("date", "Datum")

        norm_text = _normalize_text(raw_text)
        body_date = _extract_date_from_body(norm_text)

        date_source: str
        if body_date and meta_date and body_date != meta_date:
            date_source = "conflict"
        elif body_date:
            date_source = "body"
        else:
            date_source = "metadata"

        effective_date = body_date or meta_date

        doc = Document(
            doc_id=doc_id,
            title=title,
            date=effective_date,
            date_source=date_source,
            date_meta=meta_date,
            type=doc_type,
            department=department,
            text=norm_text,
        )
        documents.append(doc)

        sections = _detect_sections(norm_text)
        passages = _split_into_passages(norm_text, doc_id, sections)
        all_passages.extend(passages)
        logger.info("  %s: %d passages", doc_id, len(passages))

    # Copy detection across documents
    all_passages = detect_copies(
        all_passages,
        threshold=copy_threshold,
        doc_dates={d.doc_id: d.date for d in documents},
    )
    copies = sum(1 for p in all_passages if p.copy_of)
    logger.info("S0: total passages=%d, copies=%d", len(all_passages), copies)

    # Write cache
    out_dir = cache_dir / patient_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "documents.json").write_text(
        json.dumps([d.model_dump() for d in documents], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "passages.json").write_text(
        json.dumps([p.model_dump() for p in all_passages], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("S0: wrote documents.json and passages.json to %s", out_dir)
    return documents, all_passages
