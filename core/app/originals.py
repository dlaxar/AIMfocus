"""
app/originals.py – the original source files of the patient's letters, and where a passage
sits in them.

Passages and facts point into the normalized text S0 builds (HTML unescaped, <BR> turned
into line breaks, spaces collapsed). To show a passage in the original file, the same
normalization is replayed here character by character, keeping for every normalized
character the span of raw characters it came from.
"""
from __future__ import annotations

import html
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.config import settings

_ENTITY = re.compile(r"&(#\d+;?|#[xX][0-9a-fA-F]+;?|[A-Za-z][A-Za-z0-9]*;?)")

Tracked = list[tuple[str, int, int]]   # (normalized char, raw start, raw end)


def _documents_dir(patient_id: str) -> Path:
    base = settings.DATA_DIR
    for name in (patient_id, f"patient-{patient_id}"):
        if (base / name / "documents").exists():
            return base / name / "documents"
    return base / f"patient-{patient_id}" / "documents"


@lru_cache(maxsize=256)
def original_file(doc_id: str, patient_id: Optional[str] = None) -> Optional[Path]:
    """The original JSON file of a document: named by its ID, or found by its metadata ID."""
    folder = _documents_dir(patient_id or settings.PATIENT_ID)
    candidate = folder / f"{doc_id}.json"
    if candidate.exists():
        return candidate
    for f in sorted(folder.glob("*.json")) if folder.exists() else []:
        content = json.loads(f.read_text(encoding="utf-8"))
        meta = content.get("metadata", {}) if isinstance(content, dict) else {}
        if str(meta.get("id") or content.get("doc_id", "")) == doc_id:
            return f
    return None


def load_original(doc_id: str, patient_id: Optional[str] = None) -> Optional[dict]:
    path = original_file(doc_id, patient_id)
    if path is None:
        return None
    content = json.loads(path.read_text(encoding="utf-8"))
    return {
        "doc_id": doc_id,
        "file": str(path.relative_to(settings.DATA_DIR.parent)) if path.is_relative_to(settings.DATA_DIR.parent) else path.name,
        "metadata": content.get("metadata", {}),
        "text": content.get("text") or content.get("content") or "",
    }


def _unescape(raw: str) -> Tracked:
    out: Tracked = []
    i = 0
    while i < len(raw):
        m = _ENTITY.match(raw, i) if raw[i] == "&" else None
        if m and html.unescape(m.group(0)) != m.group(0):
            out += [(ch, m.start(), m.end()) for ch in html.unescape(m.group(0))]
            i = m.end()
        else:
            out.append((raw[i], i, i + 1))
            i += 1
    return out


def _sub(chars: Tracked, pattern: str, repl: str, flags: int = 0) -> Tracked:
    """re.sub on tracked text: replacement characters inherit the span of what they replace."""
    text = "".join(c for c, _, _ in chars)
    out: Tracked = []
    last = 0
    for m in re.finditer(pattern, text, flags):
        out += chars[last:m.start()]
        if m.end() > m.start():
            span = (chars[m.start()][1], chars[m.end() - 1][2])
            out += [(ch, *span) for ch in repl]
        last = m.end()
    return out + chars[last:]


def track_normalized(raw: str) -> Tracked:
    """The S0 normalization (preprocess.s0_passages._normalize_text), with raw spans."""
    chars = _unescape(raw)
    chars = _sub(chars, r"<[Bb][Rr]\s*/?>", "\n")
    chars = _sub(chars, r"<[^>]+>", " ")
    chars = _sub(chars, r"[ \t]+", " ")
    chars = _sub(chars, r"\n{3,}", "\n\n")
    start, end = 0, len(chars)
    while start < end and chars[start][0].isspace():
        start += 1
    while end > start and chars[end - 1][0].isspace():
        end -= 1
    return chars[start:end]


def raw_span(raw: str, start: int, end: int) -> Optional[tuple[int, int]]:
    """Raw character span of the normalized span [start, end)."""
    chars = track_normalized(raw)
    if not (0 <= start < end <= len(chars)):
        return None
    return chars[start][1], chars[end - 1][2]
