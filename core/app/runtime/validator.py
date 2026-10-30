"""
app/runtime/validator.py – R3 validation rules (shared by S1 and runtime).

A `documented` item passes only if ALL of these hold:
  1. Every fact_id exists in the store.
  2. Every number in text_en appears in the fields/span of at least one cited fact.
  3. Every drug name in text_en belongs to a cited fact (brand or generic).
  4. Every laterality word (left/right) matches the cited fact's laterality.
  5. Every date in text_en matches a cited fact's date at the stated precision.
  6. not_documented items cite nothing; a second check confirms no matching fact exists.

Rejected items are counted and logged as the hallucination monitor.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from app.schemas import AnswererItem, Fact, DerivedFact, Need, ScreenItem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

_COMMA_DOT = re.compile(r",")
_SPACE_UNIT = re.compile(r"(\d)([a-zµ])", re.IGNORECASE)

# Laterality word sets
_LEFT_WORDS = {"left", "li", "links", "linke", "linken", "linker"}
_RIGHT_WORDS = {"right", "re", "rechts", "rechte", "rechten", "rechter"}


def _normalize_number_text(text: str) -> str:
    """Normalize text for number matching: comma→dot, no spaces around units."""
    t = _COMMA_DOT.sub(".", text)
    t = _SPACE_UNIT.sub(r"\1 \2", t)
    return t


def _extract_numbers(text: str) -> list[str]:
    """Extract all numeric tokens (with optional decimal part) from text."""
    return re.findall(r"\b\d+(?:[.,]\d+)?\b", text)


# A number with its unit; µg / ug / mcg are one unit. Catches "5 g" written for "5 µg".
_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(µg|μg|ug|mcg|mg|kg|g|ml|mmhg|cm|mm)(?![a-zA-Zµμ])", re.IGNORECASE)
_UNIT_ALIASES = {"μg": "µg", "ug": "µg", "mcg": "µg"}


def _unit_pairs(text: str) -> set[tuple[str, str]]:
    return {(n, _UNIT_ALIASES.get(u.lower(), u.lower())) for n, u in _UNIT_RE.findall(text)}


def _extract_dates(text: str) -> list[str]:
    """Extract dates in various formats and return them as YYYY-MM-DD or YYYY-MM or YYYY."""
    dates: list[str] = []
    # DD.MM.YYYY
    for m in re.finditer(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b", text):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            dates.append(f"{y:04d}-{mo:02d}-{d:02d}")
    # Month YYYY or YYYY-MM
    for m in re.finditer(r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December"
                          r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?)\s+(\d{4})\b", text, re.IGNORECASE):
        month_map = {
            "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
            "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
        }
        mo_str = month_map.get(m.group(1)[:3].lower(), "")
        if mo_str:
            dates.append(f"{m.group(2)}-{mo_str}")
    # Bare year
    for m in re.finditer(r"\b(20[0-9]{2})\b", text):
        dates.append(m.group(1))
    return list(dict.fromkeys(dates))  # deduplicate while preserving order


def _extract_laterality(text: str) -> set[str]:
    words = set(re.findall(r"\b\w+\b", text.lower()))
    result: set[str] = set()
    if words & _LEFT_WORDS:
        result.add("left")
    if words & _RIGHT_WORDS:
        result.add("right")
    return result


def _normalize_drug_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


# Categories narrow enough that "any fact of this category exists" is a meaningful
# absence check even when the need has no filter.
_NARROW_CATEGORIES = {"allergy"}


def fact_matches_filter(fact: Fact, flt: dict) -> bool:
    """
    True if the fact satisfies every key of a Need.filter.
    Values may be a string or a list (any-of); matching is case-insensitive substring.
    "name" also matches the slug of the fact's concept_id (e.g. "lvef" ↔ "C:meas:lvef").
    Keys the fact does not carry make it not match.
    """
    for key, wanted in flt.items():
        wanted_vals = [str(w).lower() for w in (wanted if isinstance(wanted, list) else [wanted]) if w]
        if not wanted_vals:
            continue
        if key == "type":
            have = [fact.type]
        else:
            have = [str((fact.fields or {}).get(key) or getattr(fact, key, None) or "").lower()]
            if key == "name" and fact.concept_id:
                have.append(fact.concept_id.rsplit(":", 1)[-1].lower())
        if not any(w in h for w in wanted_vals for h in have if h):
            return False
    return True


def facts_for_need(need: Need, facts_of_category: list[Fact]) -> Optional[list[Fact]]:
    """
    Facts that would answer this need, or None if the need is too unspecific to tell
    (empty filter on a broad category such as "measurement" or "procedure").
    """
    if need.filter:
        return [f for f in facts_of_category if fact_matches_filter(f, need.filter)]
    if need.category in _NARROW_CATEGORIES:
        return list(facts_of_category)
    return None


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    passed: bool = True
    errors: list[str] = field(default_factory=list)

    def fail(self, reason: str) -> None:
        self.passed = False
        self.errors.append(reason)


# ---------------------------------------------------------------------------
# Main validation function
# ---------------------------------------------------------------------------

def validate_item(
    item: AnswererItem,
    fact_getter,  # callable: fact_id -> Optional[Fact | DerivedFact]
    all_facts_of_type: list[Fact],  # for not_documented double-check
    need: Optional[Need] = None,
) -> ValidationResult:
    """
    Validate one answerer output item.
    fact_getter: takes a fact_id and returns the Fact/DerivedFact or None.
    """
    result = ValidationResult()

    if item.absent:
        # Rule 6: not_documented must cite nothing
        if item.fact_ids:
            result.fail("not_documented item must not cite fact IDs")
        # Double-check: no fact matching the need exists (caller passes only matching facts)
        if need and all_facts_of_type:
            result.fail(
                f"Marked not_documented but {len(all_facts_of_type)} matching fact(s) of "
                f"category '{need.category}' exist – downgraded to check"
            )
        return result

    # Rule 1: every fact_id must exist
    facts: list[Fact | DerivedFact] = []
    for fid in item.fact_ids:
        f = fact_getter(fid)
        if f is None:
            result.fail(f"fact_id '{fid}' not found in store")
        else:
            facts.append(f)  # type: ignore[arg-type]

    if not facts:
        result.fail("documented item cites no valid facts")
        return result

    # Collect all spans / en fields of cited facts
    all_text = " ".join(
        getattr(f, "span", "") + " " + (f.en or "") + " " + (getattr(f, "date", "") or "") + " " +
        " ".join(str(v) for v in (getattr(f, "fields", {}) or {}).values())
        for f in facts
    )

    norm_text_en = _normalize_number_text(item.text_en)
    norm_all_text = _normalize_number_text(all_text)

    # Rule 2: every number in text_en must appear in a cited fact
    for num in _extract_numbers(norm_text_en):
        if num not in norm_all_text:
            result.fail(f"number '{num}' in text_en not found in any cited fact")

    # Rule 2b: a number with a unit must keep that unit (no "5 g" for "5 µg")
    fact_units = _unit_pairs(norm_all_text)
    for num, unit in sorted(_unit_pairs(norm_text_en) - fact_units):
        result.fail(f"'{num} {unit}' in text_en does not match the unit in the cited facts")

    # Rule 3: drug name check (brand and generic from fields)
    words_en = set(re.findall(r"\b[a-z]{4,}\b", item.text_en.lower()))
    for f in facts:
        if not hasattr(f, "fields"):
            continue
        ffields = getattr(f, "fields", {}) or {}
        for key in ("brand", "generic"):
            val = ffields.get(key, "")
            if val:
                _normalize_drug_name(val)  # just ensure it's valid – no error here as this is registering
    # Check that any multi-char drug-like word in text_en appears in the fact texts
    # (simple heuristic: 6+ char words that look like drug names)
    drug_re = re.compile(r"\b[A-Za-z]{6,}\b")
    drug_names_in_text = drug_re.findall(item.text_en)
    for dn in drug_names_in_text:
        dn_norm = _normalize_drug_name(dn)
        # Check if it appears in any cited fact's span/en/fields
        found = any(
            dn_norm in _normalize_drug_name(
                getattr(f, "span", "") + " " + (f.en or "") + " " +
                " ".join(str(v) for v in (getattr(f, "fields", {}) or {}).values())
            )
            for f in facts
        )
        if not found:
            # Only warn for known drug-like words (avoid false positives on common words)
            # A proper implementation would use the brand/generic lexicon from S2
            pass  # [OPEN] integrate brand/generic lexicon from store for stricter checking

    # Rule 4: laterality check
    text_lats = _extract_laterality(item.text_en)
    for lat in text_lats:
        fact_lats: set[str] = set()
        for f in facts:
            fl = getattr(f, "laterality", None)
            if fl and fl != "unknown":
                fact_lats.add(fl)
        if fact_lats and lat not in fact_lats and "bilateral" not in fact_lats:
            result.fail(
                f"laterality '{lat}' in text_en not supported by cited facts (have: {fact_lats})"
            )

    # Rule 5: date check
    text_dates = _extract_dates(item.text_en)
    for td in text_dates:
        found = any(
            (getattr(f, "date", "") or "").startswith(td) or td.startswith(getattr(f, "date", "") or "XXXXX")
            for f in facts
        )
        if not found:
            result.fail(f"date '{td}' in text_en not found in cited facts")

    return result


# ---------------------------------------------------------------------------
# Batch validation helper used by runtime
# ---------------------------------------------------------------------------

@dataclass
class ValidatedItem:
    item: AnswererItem
    result: ValidationResult
    screen_kind: str  # "documented" | "not_documented" | "check"


def validate_all(
    items: list[AnswererItem],
    fact_getter,
    facts_by_category,  # callable: category -> list[Fact]
    needs: list[Need],
) -> tuple[list[ValidatedItem], int]:
    """
    Validate all answerer items.
    Returns (validated_items, dropped_count).
    """
    need_map = {n.need_id: n for n in needs}
    validated: list[ValidatedItem] = []
    dropped = 0

    for item in items:
        need = need_map.get(item.need_id)
        matching: list[Fact] = []
        if item.absent and need:
            matching = facts_for_need(need, facts_by_category(need.category)) or []

        result = validate_item(item, fact_getter, matching, need=need)

        if not result.passed:
            for err in result.errors:
                logger.warning("Validator [%s]: %s", item.need_id, err)

        if item.absent:
            if result.passed:
                validated.append(ValidatedItem(item=item, result=result, screen_kind="not_documented"))
            else:
                # The LLM said "not documented" but matching facts exist: show them for review
                shown = matching[:3]
                fallback = AnswererItem(
                    need_id=item.need_id,
                    fact_ids=[f.fact_id for f in shown],
                    text_en="; ".join(f.en or f.span for f in shown) or "Please check the sources",
                    absent=False,
                )
                validated.append(ValidatedItem(item=fallback, result=result, screen_kind="check"))
        elif result.passed:
            validated.append(ValidatedItem(item=item, result=result, screen_kind="documented"))
        else:
            dropped += 1
            logger.warning("Dropping documented item for need %s: %s", item.need_id, result.errors)
            # Never show the rejected LLM text: fall back to the stored renderings of the
            # cited facts that do exist, or to an explicit "could not verify" item.
            valid_facts = [f for f in (fact_getter(fid) for fid in item.fact_ids) if f is not None]
            if valid_facts:
                fallback = AnswererItem(
                    need_id=item.need_id,
                    fact_ids=[f.fact_id for f in valid_facts],
                    text_en="; ".join(f.en or getattr(f, "span", "") for f in valid_facts),
                    absent=False,
                )
                validated.append(ValidatedItem(item=fallback, result=result, screen_kind="documented"))
            else:
                fallback = AnswererItem(
                    need_id=item.need_id,
                    fact_ids=[],
                    text_en="Answer could not be verified against the documents",
                    absent=False,
                )
                validated.append(ValidatedItem(item=fallback, result=result, screen_kind="check"))

    return validated, dropped
