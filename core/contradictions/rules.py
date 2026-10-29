"""
contradictions/rules.py – deterministic contradiction detectors over the S0 passages.

Each detector reads the passage text directly (no S1 facts, no LLM), so the result is the
same on every run and needs no API key. Each one is a check a clinician or a documentation
auditor would do by hand:

  stay_dates          admission / discharge date of a stay given twice in one letter, differently
  impossible_date     a date that cannot exist ("09/02/20200")
  bmi_mismatch        stated BMI vs. height and weight in the same document
  case_number         one case number used for two separate stays
  copied_text         text copied from another report with left and right swapped
                      (or with changed values, when both are from the same episode)
  finding_polarity    a finding documented as present and as absent within one episode
  procedure_dates     the same procedure dated differently in two documents of one episode
  age_vs_birth_date   stated age vs. date of birth and document date
  birth_date          two different dates of birth
  lv_wall             "thickened left ventricle" with normal wall measurements (possible only)

Not reported, because the challenge counts them as false alarms: small measurement
differences (height ±2 cm, blood pressure ±10 mmHg), values that change over time
(weight, doses across stays) and differences from IBM's JSON files (never read here).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from app.schemas import Passage
from contradictions.model import Corpus, Finding
from contradictions.text import (
    DateHit,
    clause_bounds,
    find_dates,
    fmt_date,
    fmt_month,
    norm,
    numbers,
    polarity,
    sides,
    snip,
    to_float,
)

# A stated BMI further than this from weight / height² is a contradiction, not rounding
BMI_TOLERANCE = 1.0
# Two dates for one procedure further apart than this are more likely two procedures
SAME_PROCEDURE_DAYS = 14
# Present vs. absent further apart than this may be a real change, so only "possible"
SAME_FINDING_DAYS = 14
# Normal upper limit of septum and posterior wall thickness in echocardiography
LV_WALL_NORMAL_MM = 10.0


def _flat(p: Passage) -> str:
    return " ".join(p.text.split())


def _quote(p: Passage, max_len: int = 90) -> str:
    return f"\"{snip(p.text, max_len).rstrip('.')}\""


# ---------------------------------------------------------------------------
# Stay dates (admission / discharge) and impossible dates
# ---------------------------------------------------------------------------

_HEADER_EVENT = {
    "admission": re.compile(r"\bAufnahme\s+am\s+", re.IGNORECASE),
    "discharge": re.compile(r"\bEntlass(?:en|ung)\s+am\s+", re.IGNORECASE),
}
_NARRATIVE_EVENT = {
    "admission": re.compile(r"\baufgenommen\b", re.IGNORECASE),
    "discharge": re.compile(r"\bentlassen\b", re.IGNORECASE),
}
_PLANNED = re.compile(r"terminisiert|geplant|vorgesehen|vereinbart", re.IGNORECASE)
_AM_BEFORE = re.compile(r"\bam\s*$")
_BIRTH_BEFORE = re.compile(r"\bgeb(?:oren|\.)?\s*(?:am\s*)?$", re.IGNORECASE)


@dataclass
class _StayStatement:
    event: str          # admission | discharge
    hit: DateHit
    passage: Passage
    where: str          # header | text


def _stay_statements(c: Corpus) -> list[_StayStatement]:
    out: list[_StayStatement] = []
    for p in c.passages:
        text = _flat(p)
        if _PLANNED.search(text):
            continue
        hits = find_dates(text)
        for event in ("admission", "discharge"):
            header = list(_HEADER_EVENT[event].finditer(text))
            if header:
                for m in header:
                    hit = next((h for h in hits if h.start == m.end()), None)
                    if hit:
                        out.append(_StayStatement(event, hit, p, "header"))
            elif _NARRATIVE_EVENT[event].search(text):
                # "wurde am 05.02.2020 ... aufgenommen"; not "geb. am 01.01.1960, welcher ... aufgenommen war"
                dated = [
                    h for h in hits
                    if _AM_BEFORE.search(text[:h.start]) and not _BIRTH_BEFORE.search(text[:h.start])
                ]
                if len(dated) == 1:
                    out.append(_StayStatement(event, dated[0], p, "text"))
    return out


def stay_windows(c: Corpus) -> dict[str, tuple[date, date]]:
    """doc_id → (admission, discharge) from the valid stay dates a letter states."""
    adm: dict[str, list[date]] = {}
    dis: dict[str, list[date]] = {}
    for s in _stay_statements(c):
        if s.hit.value:
            (adm if s.event == "admission" else dis).setdefault(s.passage.doc_id, []).append(s.hit.value)
    return {d: (min(adm[d]), max(dis[d])) for d in adm if d in dis}


def stay_dates(c: Corpus) -> tuple[list[Finding], set[tuple[str, str]]]:
    """
    One letter giving two admission (or discharge) dates for its own stay, typically the
    header ("Aufnahme am 03/02/2020") against the narrative ("am 05.02.2020 aufgenommen").
    Also returns the (passage_id, raw date) pairs it explained, so impossible_date skips them.
    """
    findings: list[Finding] = []
    covered: set[tuple[str, str]] = set()
    grouped: dict[tuple[str, str], list[_StayStatement]] = {}
    for s in _stay_statements(c):
        grouped.setdefault((s.passage.doc_id, s.event), []).append(s)

    for (doc_id, event), statements in grouped.items():
        distinct = {s.hit.value or s.hit.raw for s in statements}
        if len(distinct) < 2:
            continue
        parts = []
        for s in statements:
            shown = fmt_date(s.hit.value) if s.hit.value else f"{s.hit.raw} (not a valid date)"
            parts.append(f"{shown} in the {s.where}")
            if s.hit.value is None:
                covered.add((s.passage.passage_id, s.hit.raw))
        label = "Admission" if event == "admission" else "Discharge"
        findings.append(Finding(
            key=f"stay_dates:{doc_id}:{event}",
            rule="stay_dates",
            kind="date",
            certainty="definite",
            summary_en=f"{label} date of the stay in {c.ref(doc_id)} is given as " + " but as ".join(parts) + ".",
            passage_ids=list(dict.fromkeys(s.passage.passage_id for s in statements)),
            concept_ids=[f"C:event:{event}"],
            rationale=f"one stay has one {event} date; the letter states {len(distinct)} different ones",
        ))
    return findings, covered


def impossible_dates(c: Corpus, covered: set[tuple[str, str]] = frozenset()) -> list[Finding]:
    """Dates that cannot exist (five-digit year, month 13, 31 April); a date glued to a time is skipped."""
    findings: list[Finding] = []
    for p in c.passages:
        for hit in find_dates(_flat(p)):
            if hit.value is not None or hit.glued_time or (p.passage_id, hit.raw) in covered:
                continue
            findings.append(Finding(
                key=f"impossible_date:{p.passage_id}:{hit.raw}",
                rule="impossible_date",
                kind="date",
                certainty="definite",
                summary_en=f"Impossible date \"{hit.raw}\" in {c.ref(p.doc_id)}: {_quote(p)}.",
                passage_ids=[p.passage_id],
                rationale=f"{hit.raw} is not a calendar date",
            ))
    return findings


# ---------------------------------------------------------------------------
# BMI vs. height and weight
# ---------------------------------------------------------------------------

_HEIGHT_CM = re.compile(r"\b(?:Körper)?(?:Größe|Groesse)\s*:?\s*(\d{3}(?:[.,]\d)?)\s*cm", re.IGNORECASE)
_HEIGHT_M = re.compile(r"\b(?:Körper)?(?:Größe|Groesse)\s*:?\s*([12][.,]\d{1,2})\s*m\b", re.IGNORECASE)
_WEIGHT = re.compile(r"\b(?:Körper)?Gewicht\s*:?\s*(\d{2,3}(?:[.,]\d)?)\s*kg", re.IGNORECASE)
_BMI = re.compile(r"\bBMI\s*(?:von\s*)?:?\s*(\d{2}(?:[.,]\d+)?)(?!\s*%)", re.IGNORECASE)


def _values(passages: list[Passage], rx: re.Pattern, scale: float = 1.0) -> list[tuple[Passage, float]]:
    out = []
    for p in passages:
        for m in rx.finditer(_flat(p)):
            out.append((p, to_float(m.group(1)) * scale))
    return out


def _nearest(c: Corpus, values: list[tuple[Passage, float]], anchor: Passage) -> Optional[tuple[Passage, float]]:
    """The value closest to the anchor passage within its document."""
    return min(values, key=lambda v: abs(c.index[v[0].passage_id] - c.index[anchor.passage_id]), default=None)


def bmi_mismatch(c: Corpus) -> list[Finding]:
    """A stated BMI that does not follow from the height and weight in the same document."""
    findings: list[Finding] = []
    for doc_id, ps in c.by_doc.items():
        heights = _values(ps, _HEIGHT_CM) + _values(ps, _HEIGHT_M, 100.0)
        weights = _values(ps, _WEIGHT)
        for bmi_p, stated in _values(ps, _BMI):
            h, w = _nearest(c, heights, bmi_p), _nearest(c, weights, bmi_p)
            if h is None or w is None:
                continue
            (h_p, height_cm), (w_p, weight_kg) = h, w
            computed = weight_kg / (height_cm / 100) ** 2
            if abs(computed - stated) <= BMI_TOLERANCE:
                continue
            findings.append(Finding(
                key=f"bmi:{bmi_p.passage_id}",
                rule="bmi_mismatch",
                kind="calculation",
                certainty="definite",
                summary_en=(
                    f"BMI {stated:g} in {c.ref(doc_id)} does not match the height ({height_cm:g} cm) "
                    f"and weight ({weight_kg:g} kg) in the same document, which give a BMI of {computed:.1f}."
                ),
                passage_ids=list(dict.fromkeys([bmi_p.passage_id, h_p.passage_id, w_p.passage_id])),
                concept_ids=["C:meas:bmi", "C:meas:body_height", "C:meas:body_weight"],
                rationale=f"{weight_kg:g} kg / ({height_cm / 100:.2f} m)² = {computed:.1f}, stated {stated:g}",
            ))
    return findings


# ---------------------------------------------------------------------------
# Case numbers
# ---------------------------------------------------------------------------

_CASE = re.compile(r"\bFall(?:-?ID|zahl|nummer|-?Nr\.?)?\s*:\s*(\d{5,})", re.IGNORECASE)


def case_number(c: Corpus) -> list[Finding]:
    """
    One case number (Fall-ID) on documents of two separate stays. A case number identifies
    one stay, so the document from the smaller group carries a wrong one; documents of its
    own stay that carry a different number are linked as the counterpart.
    """
    case_by_doc: dict[str, tuple[Passage, str]] = {}
    for doc_id, ps in c.by_doc.items():
        for p in ps:
            m = _CASE.search(_flat(p))
            if m:
                case_by_doc[doc_id] = (p, m.group(1))
                break

    by_number: dict[str, list[str]] = {}
    for doc_id, (_, number) in case_by_doc.items():
        by_number.setdefault(number, []).append(doc_id)

    windows = stay_windows(c)
    findings: list[Finding] = []
    for number, doc_ids in by_number.items():
        by_episode: dict[int, list[str]] = {}
        for d in doc_ids:
            if d in c.episode:
                by_episode.setdefault(c.episode[d], []).append(d)
        if len(by_episode) < 2:
            continue
        majority = max(by_episode, key=lambda e: (len(by_episode[e]), -e))
        outliers = [d for e, ds in by_episode.items() if e != majority for d in ds]
        usual = by_episode[majority]

        counterparts: list[str] = []
        for od in outliers:
            od_date = c.doc_date(od)
            lo, hi = windows.get(od) or (od_date - timedelta(days=7), od_date)
            for other, (_, other_number) in case_by_doc.items():
                other_date = c.doc_date(other)
                if other_number != number and c.same_episode(other, od) and other_date and lo <= other_date <= hi:
                    counterparts.append(other)

        out_month = fmt_month(c.doc_date(outliers[0]))
        usual_month = fmt_month(c.doc_date(usual[0]))
        summary = (
            f"Case number {number} belongs to the {usual_month} stay ({len(usual)} document(s), e.g. {c.ref(usual[0])}) "
            f"but also appears on {', '.join(c.ref(d) for d in outliers)} from {out_month}."
        )
        if counterparts:
            summary += " " + "; ".join(
                f"{c.ref(d)} from that {out_month} stay has case number {case_by_doc[d][1]}" for d in counterparts
            ) + "."
        findings.append(Finding(
            key=f"case_number:{number}",
            rule="case_number",
            kind="value",
            certainty="definite",
            summary_en=summary,
            passage_ids=[case_by_doc[d][0].passage_id for d in outliers + counterparts + usual],
            rationale=f"a case number identifies one stay, but documents from {out_month} and {usual_month} share it",
        ))
    return findings


# ---------------------------------------------------------------------------
# Copied text: left/right swapped, values changed
# ---------------------------------------------------------------------------

_ANATOMY = [
    (re.compile(r"carot|karot", re.IGNORECASE), "Carotid"),
    (re.compile(r"mamma|brust", re.IGNORECASE), "Breast"),
    (re.compile(r"auge|retina|fundus|papill", re.IGNORECASE), "Eye"),
    (re.compile(r"niere|ren(?:al|is)\b", re.IGNORECASE), "Kidney"),
    (re.compile(r"lunge|pulmo", re.IGNORECASE), "Lung"),
    (re.compile(r"ventrik|vorhof|atri", re.IGNORECASE), "Heart"),
    (re.compile(r"axill|lymphkn", re.IGNORECASE), "Lymph node"),
    (re.compile(r"vertebral|hirn|zerebr", re.IGNORECASE), "Cerebral vessel"),
]


def _subject(text: str) -> str:
    return next((label for rx, label in _ANATOMY if rx.search(text)), "Finding")


def copied_text(c: Corpus) -> list[Finding]:
    """
    Passages that S0 marked as copied from an earlier document (copy_of) but whose text
    differs from the original:
      - left and right swapped: always a contradiction (a side does not change when copied);
        sibling passages of the same copied block that kept the original side are linked
        too, because they show the copy contradicting itself;
      - numbers changed: only within one episode, across stays it can be a real change.
    """
    groups: dict[tuple[str, str, str], list[tuple[Passage, Passage]]] = {}
    for p in c.passages:
        orig = c.passage_map.get(p.copy_of or "")
        if orig is None or norm(p.text) == norm(orig.text):
            continue
        s_copy, s_orig = sides(p.text), sides(orig.text)
        if s_copy != s_orig and len(s_copy) == len(s_orig):
            groups.setdefault((p.doc_id, orig.doc_id, "laterality"), []).append((p, orig))
        elif s_copy == s_orig and c.same_episode(p.doc_id, orig.doc_id) and numbers(p.text) != numbers(orig.text):
            groups.setdefault((p.doc_id, orig.doc_id, "value"), []).append((p, orig))

    findings: list[Finding] = []
    for (copy_doc, orig_doc, issue), pairs in groups.items():
        changed = {p.passage_id for p, _ in pairs}
        first_copy, first_orig = pairs[0]
        passage_ids = [pid for p, o in pairs for pid in (p.passage_id, o.passage_id)]

        if issue == "laterality":
            # Copies from the same original document nearby that kept the original side
            lo = min(c.index[pid] for pid in changed) - 6
            hi = max(c.index[pid] for pid in changed) + 6
            siblings = [
                s for s in c.by_doc[copy_doc][max(lo, 0):hi + 1]
                if s.passage_id not in changed and s.copy_of and s.copy_of.startswith(orig_doc + ":") and sides(s.text)
            ]
            summary = (
                f"{_subject(first_copy.text)} finding copied from {c.ref(orig_doc)} into {c.ref(copy_doc)} "
                f"has left and right swapped: the copy says {_quote(first_copy)}, the original says {_quote(first_orig)}."
            )
            if siblings:
                summary += f" The same copied report still says {_quote(siblings[0])}."
            # Siblings and their originals: a screen showing the side most likely cites one of them
            passage_ids += [pid for s in siblings for pid in (s.passage_id, s.copy_of)]
            rationale = "the side of a finding cannot change when a report is copied"
            kind, certainty = "laterality", "definite"
        else:
            summary = (
                f"Values copied from {c.ref(orig_doc)} into {c.ref(copy_doc)} differ: "
                f"the copy says {_quote(first_copy)}, the original says {_quote(first_orig)}."
            )
            rationale = "the same report quoted twice within one episode with different numbers"
            kind, certainty = "copy_mismatch", "possible"

        findings.append(Finding(
            key=f"copied_text:{issue}:{first_copy.passage_id}",
            rule="copied_text",
            kind=kind,
            certainty=certainty,
            summary_en=summary,
            passage_ids=list(dict.fromkeys(passage_ids)),
            rationale=rationale,
        ))
    return findings


# ---------------------------------------------------------------------------
# Present vs. absent within one episode
# ---------------------------------------------------------------------------

_FINDINGS: list[tuple[str, str, re.Pattern]] = [
    ("lymph_nodes", "Enlarged lymph nodes",
     re.compile(r"vergr[öo]ßerte?n?\s+(?:\w+\s+)?Lymphknoten|Lymphknotenvergr[öo]ßerung|Lymphadenopathie", re.IGNORECASE)),
    ("pericardial_effusion", "Pericardial effusion", re.compile(r"Perikarderg(?:uss|uß)", re.IGNORECASE)),
    ("bleeding", "Bleeding", re.compile(r"\bBlutung", re.IGNORECASE)),
    ("pulmonary_hypertension", "Pulmonary hypertension", re.compile(r"pulmonale[nr]?\s+Hypertonie", re.IGNORECASE)),
    ("focal_deficit", "Focal neurological deficit", re.compile(r"fokal-?\s*neurologische", re.IGNORECASE)),
    ("atrial_fibrillation", "Atrial fibrillation", re.compile(r"Vorhofflimmern|\bVHF\b")),
    ("retinopathy", "Hypertensive retinopathy", re.compile(r"hypertensive\s+Retinopathie", re.IGNORECASE)),
    ("pleural_effusion", "Pleural effusion", re.compile(r"Pleuraerg(?:uss|uß)", re.IGNORECASE)),
    ("thrombosis", "Thrombosis", re.compile(r"Thrombose", re.IGNORECASE)),
]
# TNM N category: N0 = no regional lymph node involvement
_TNM_N = re.compile(r"\b[cpy]?N([0-3])[a-c]?\b")


@dataclass
class _Mention:
    passage: Passage
    polarity: str
    text: str           # the clause that carries the mention, or the TNM N category
    tnm: bool = False


def finding_polarity(c: Corpus) -> list[Finding]:
    """The same finding documented as present and as absent within one episode of care."""
    mentions: dict[str, list[_Mention]] = {}
    for p in c.passages:
        text = _flat(p)
        for key, _, rx in _FINDINGS:
            for m in rx.finditer(text):
                left, right = clause_bounds(text, m.start(), m.end())
                clause = ("…" if left > 0 else "") + text[left:right].strip()
                mentions.setdefault(key, []).append(_Mention(p, polarity(text, m.start(), m.end()), clause))
        for m in _TNM_N.finditer(text):
            if re.search(r"\bT[0-4is]", text):  # only inside a TNM formula
                mentions.setdefault("lymph_nodes", []).append(
                    _Mention(p, "negative" if m.group(1) == "0" else "positive", m.group(), tnm=True))

    labels = {key: label for key, label, _ in _FINDINGS}
    findings: list[Finding] = []
    for key, ms in mentions.items():
        by_episode: dict[int, list[_Mention]] = {}
        for m in ms:
            if m.polarity in ("positive", "negative") and m.passage.doc_id in c.episode:
                by_episode.setdefault(c.episode[m.passage.doc_id], []).append(m)
        for episode, ems in by_episode.items():
            pos = _one_per_doc([m for m in ems if m.polarity == "positive"])
            neg = _one_per_doc([m for m in ems if m.polarity == "negative"])
            if not pos or not neg:
                continue
            dates = [c.doc_date(m.passage.doc_id) for m in pos + neg]
            span = (max(dates) - min(dates)).days if all(dates) else None
            certainty = "definite" if span is not None and span <= SAME_FINDING_DAYS else "possible"
            findings.append(Finding(
                key=f"finding_polarity:{key}:{episode}",
                rule="finding_polarity",
                kind="presence",
                certainty=certainty,
                summary_en=(
                    f"{labels[key]}: documented as present in {_describe(c, pos)} "
                    f"but as absent in {_describe(c, neg)}."
                ),
                passage_ids=list(dict.fromkeys(m.passage.passage_id for m in pos + neg)),
                rationale=f"present and absent within {span} days" if span is not None else "present and absent",
            ))
    return findings


def _describe(c: Corpus, mentions: list[_Mention]) -> str:
    parts = []
    for m in mentions:
        evidence = f"TNM {m.text}" if m.tnm else f"\"{snip(m.text)}\""
        parts.append(f"{c.ref(m.passage.doc_id)} ({evidence})")
    return " and ".join(parts)


def _one_per_doc(mentions: list[_Mention]) -> list[_Mention]:
    """One mention per document, prose before a TNM formula, so the same fact is linked once."""
    best: dict[str, _Mention] = {}
    for m in sorted(mentions, key=lambda m: m.tnm):
        best.setdefault(m.passage.doc_id, m)
    return list(best.values())


# ---------------------------------------------------------------------------
# Procedure dates
# ---------------------------------------------------------------------------

_PROCEDURES: list[tuple[str, str, re.Pattern]] = [
    ("biopsy", "Biopsy", re.compile(r"biops|stanzzylinder", re.IGNORECASE)),
    ("mammography", "Mammography", re.compile(r"mammog", re.IGNORECASE)),
    ("breast_ultrasound", "Breast ultrasound", re.compile(r"(?:ultraschall|sonogra)\w*.*(?:brust|mamma)", re.IGNORECASE)),
    ("carotid_duplex", "Carotid duplex ultrasound", re.compile(r"duplex|doppler|hirnversorgend", re.IGNORECASE)),
    ("head_ct", "Head CT", re.compile(r"sch[äa]del-?ct|\bcct\b", re.IGNORECASE)),
    ("echo", "Echocardiography", re.compile(r"echokardiogra", re.IGNORECASE)),
    ("holter", "Holter ECG", re.compile(r"langzeit-?ekg|\blz-?ekg", re.IGNORECASE)),
    ("abpm", "24-hour blood pressure monitoring", re.compile(r"24\s*h-?rr|24-?stunden-?blutdruck", re.IGNORECASE)),
]
_BIOPSY = _PROCEDURES[0][2]
_PERFORMED = re.compile(r"\b(?:erfolgte|erfolgt|durchgeführt|entnommen|gewonnen|vorgenommen)\b", re.IGNORECASE)
_SAMPLING_DATE = re.compile(r"\bEntnahmedatum\s*:", re.IGNORECASE)
_SAMPLE_SITE = re.compile(r"\b(?:Lokalisation|Untersuchungsmaterial|Entnahmetechnik)\b", re.IGNORECASE)
# Sub-headings that continue the examination block above them
_BLOCK_CONTINUES = {"zusammenfassung", "befund", "befundzusammenfassung", "beurteilung", "gesamtbeurteilung"}
_TITLE_TAIL = re.compile(r"^\)?\s*(?:\d{1,2}:\d{2})?\s*\)?\s*:?\s*$")


def _procedure(text: str) -> Optional[str]:
    return next((key for key, _, rx in _PROCEDURES if rx.search(text)), None)


@dataclass
class _ProcMention:
    key: str
    day: date
    doc_id: str
    passages: list[Passage]
    note: str
    sides: set[str] = field(default_factory=set)


def _dated_title(text: str) -> Optional[tuple[str, DateHit]]:
    """'Schädel-CT (03.02.2020):', 'Duplex Halsgefäße 03.02.2020 10:42' → (title, date)."""
    hits = [h for h in find_dates(text) if h.value]
    if not hits or not _TITLE_TAIL.match(text[hits[-1].end:]):
        return None
    title = re.sub(r"\s*(?:vom|am|\()\s*$", "", text[:hits[-1].start]).strip()
    if not title or len(title.split()) > 8:
        return None
    return title, hits[-1]


def _procedure_mentions(c: Corpus) -> list[_ProcMention]:
    out: list[_ProcMention] = []
    for doc_id, ps in c.by_doc.items():
        # Sampling date of a pathology report: "Entnahmedatum: 04.02.2020" + "Entnahmetechnik: Stanzbiopsie"
        doc_text = " ".join(_flat(p) for p in ps)
        for p in ps:
            hits = [h for h in find_dates(_flat(p)) if h.value]
            if _SAMPLING_DATE.search(p.text) and hits and _BIOPSY.search(doc_text):
                site = [s for s in ps if _SAMPLE_SITE.search(s.text)]
                out.append(_ProcMention("biopsy", hits[0].value, doc_id, [p] + site, "sampling date",
                                        {x for s in site for x in sides(s.text)}))

        # Dated examination titles, and procedures performed inside such an examination block
        block: Optional[tuple[_ProcMention, Passage]] = None
        for p in ps:
            text = _flat(p)
            titled = _dated_title(text)
            if titled and (key := _procedure(titled[0])):
                mention = _ProcMention(key, titled[1].value, doc_id, [p], "examination date", set(sides(titled[0])))
                out.append(mention)
                block = (mention, p)
                continue
            if text.endswith(":") or p.kind == "header":
                if text.rstrip(":").strip().lower() not in _BLOCK_CONTINUES:
                    block = None
                continue
            if block and _PERFORMED.search(text):
                parent, heading = block
                key = _procedure(text)
                if key and key != parent.key:
                    out.append(_ProcMention(
                        key, parent.day, doc_id, [heading, p],
                        f"performed during the examination of {fmt_date(parent.day)}",
                        parent.sides | set(sides(text)),
                    ))
            # Inline "<procedure> vom <date>" in running text
            for hit in find_dates(text):
                if hit.value and re.search(r"\bvom\s*$", text[:hit.start]):
                    key = _procedure(text[max(0, hit.start - 60):hit.start])
                    if key:
                        out.append(_ProcMention(key, hit.value, doc_id, [p], "examination date", set(sides(text))))
    return out


def procedure_dates(c: Corpus) -> list[Finding]:
    """The same procedure dated differently by documents of one episode (gap ≤ SAME_PROCEDURE_DAYS)."""
    grouped: dict[tuple[str, int], list[_ProcMention]] = {}
    for m in _procedure_mentions(c):
        if m.doc_id in c.episode:
            grouped.setdefault((m.key, c.episode[m.doc_id]), []).append(m)

    labels = {key: label for key, label, _ in _PROCEDURES}
    findings: list[Finding] = []
    for (key, episode), ms in grouped.items():
        days = sorted({m.day for m in ms})
        if len(days) < 2 or (days[-1] - days[0]).days > SAME_PROCEDURE_DAYS:
            continue
        # Left in one, right in the other: two procedures, not one with two dates
        stated_sides = [m.sides for m in ms if m.sides]
        if stated_sides and not set.intersection(*stated_sides):
            continue
        first_per_day = {}
        for m in ms:
            first_per_day.setdefault(m.day, m)
        parts = [f"{fmt_date(d)} in {c.ref(first_per_day[d].doc_id)} ({first_per_day[d].note})" for d in days]
        findings.append(Finding(
            key=f"procedure_dates:{key}:{episode}",
            rule="procedure_dates",
            kind="date",
            certainty="definite",
            summary_en=f"{labels[key]} is dated " + " but ".join(parts) + ".",
            passage_ids=list(dict.fromkeys(p.passage_id for d in days for p in first_per_day[d].passages)),
            rationale=f"{len(days)} dates within {(days[-1] - days[0]).days} days for one {labels[key].lower()}",
        ))
    return findings


# ---------------------------------------------------------------------------
# Age and date of birth
# ---------------------------------------------------------------------------

_AGE = re.compile(r"\b(\d{1,3})\s*-?\s*j[äa]hrige[nrs]?\b", re.IGNORECASE)
_BIRTH = re.compile(r"\bgeb(?:oren|\.)?\b", re.IGNORECASE)


def _birth_dates(c: Corpus) -> dict[str, tuple[Passage, date]]:
    out: dict[str, tuple[Passage, date]] = {}
    for p in c.passages:
        text = _flat(p)
        m = _BIRTH.search(text)
        if not m:
            continue
        hit = next((h for h in find_dates(text) if h.value and h.start >= m.end()), None)
        if hit and p.doc_id not in out:
            out[p.doc_id] = (p, hit.value)
    return out


def age_vs_birth_date(c: Corpus) -> list[Finding]:
    """'60-jährige Pat.' must match the date of birth at the document date (±1 year)."""
    births = _birth_dates(c)
    findings: list[Finding] = []
    for p in c.passages:
        m = _AGE.search(_flat(p))
        doc_date = c.doc_date(p.doc_id)
        if not m or p.doc_id not in births or doc_date is None:
            continue
        birth_p, born = births[p.doc_id]
        actual = doc_date.year - born.year - ((doc_date.month, doc_date.day) < (born.month, born.day))
        stated = int(m.group(1))
        if abs(actual - stated) <= 1:
            continue
        findings.append(Finding(
            key=f"age:{p.passage_id}",
            rule="age_vs_birth_date",
            kind="calculation",
            certainty="definite",
            summary_en=(
                f"Age {stated} in {c.ref(p.doc_id)} does not match the date of birth {fmt_date(born)}, "
                f"which gives {actual} on {fmt_date(doc_date)}."
            ),
            passage_ids=[p.passage_id, birth_p.passage_id],
            concept_ids=["C:demo:age"],
            rationale=f"born {fmt_date(born)}, document of {fmt_date(doc_date)}: {actual} years",
        ))
    return findings


def birth_date(c: Corpus) -> list[Finding]:
    """Two different dates of birth for the same patient."""
    births = _birth_dates(c)
    distinct: dict[date, list[Passage]] = {}
    for p, born in births.values():
        distinct.setdefault(born, []).append(p)
    if len(distinct) < 2:
        return []
    parts = [f"{fmt_date(d)} in {', '.join(c.ref(p.doc_id) for p in ps)}" for d, ps in sorted(distinct.items())]
    return [Finding(
        key="birth_date",
        rule="birth_date",
        kind="value",
        certainty="definite",
        summary_en="Date of birth is given as " + " but as ".join(parts) + ".",
        passage_ids=[ps[0].passage_id for ps in distinct.values()],
        concept_ids=["C:demo:date_of_birth"],
        rationale="a patient has one date of birth",
    )]


# ---------------------------------------------------------------------------
# Echocardiography: thickened wall vs. normal measurements
# ---------------------------------------------------------------------------

_LV_THICK = re.compile(r"Verdickung\s+des\s+linken\s+Ventrikels|linksventrikul[äa]re\s+Hypertrophie|\bLVH\b", re.IGNORECASE)
_SEPTUM = re.compile(r"\b(?:Septum|IVS)\s*:?\s*(\d{1,2}(?:[.,]\d)?)\s*mm", re.IGNORECASE)
_POST_WALL = re.compile(r"\b(?:HW|Hinterwand|LVPW)\s*:?\s*(\d{1,2}(?:[.,]\d)?)\s*mm", re.IGNORECASE)


def lv_wall(c: Corpus) -> list[Finding]:
    """'Leichte Verdickung des linken Ventrikels' next to septum and posterior wall within normal limits."""
    findings: list[Finding] = []
    for doc_id, ps in c.by_doc.items():
        thick = [p for p in ps if (m := _LV_THICK.search(_flat(p))) and polarity(_flat(p), m.start(), m.end()) == "positive"]
        septum, wall = _values(ps, _SEPTUM), _values(ps, _POST_WALL)
        if not thick or not septum or not wall:
            continue
        if max(v for _, v in septum + wall) > LV_WALL_NORMAL_MM:
            continue
        findings.append(Finding(
            key=f"lv_wall:{doc_id}",
            rule="lv_wall",
            kind="value",
            certainty="possible",
            summary_en=(
                f"Echocardiography in {c.ref(doc_id)} reports a thickened left ventricle ({_quote(thick[0])}) "
                f"but septum {septum[0][1]:g} mm and posterior wall {wall[0][1]:g} mm are within normal limits."
            ),
            passage_ids=list(dict.fromkeys([thick[0].passage_id, septum[0][0].passage_id, wall[0][0].passage_id])),
            concept_ids=["C:meas:septum_thickness"],
            rationale=f"wall thickness ≤ {LV_WALL_NORMAL_MM:g} mm is normal",
        ))
    return findings


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_rules(c: Corpus) -> list[Finding]:
    """All detectors; findings with the same key are reported once (the first wins)."""
    stay, covered = stay_dates(c)
    findings = (
        stay
        + impossible_dates(c, covered)
        + bmi_mismatch(c)
        + case_number(c)
        + copied_text(c)
        + finding_polarity(c)
        + procedure_dates(c)
        + age_vs_birth_date(c)
        + birth_date(c)
        + lv_wall(c)
    )
    unique: dict[str, Finding] = {}
    for f in findings:
        unique.setdefault(f.key, f)
    return list(unique.values())
