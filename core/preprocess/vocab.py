"""
preprocess/vocab.py – controlled vocabulary for concept keys (shared by S1 and S2).

A concept key groups facts about the same thing across documents and LLM calls:
  C:med:<generic>  C:allergy:<substance>  C:dx:<condition>  C:meas:<measurement>
  C:finding:<anatomy>:<finding>  C:proc:<procedure>  C:rec:<action>  C:event:<event>
  C:plan:<action>  C:demo:<item>
Laterality and negation are never part of a key; they live in Fact.laterality / Fact.status.
"""
from __future__ import annotations

import re
from typing import Optional

TYPE_PREFIX: dict[str, str] = {
    "medication": "med",
    "allergy": "allergy",
    "diagnosis": "dx",
    "measurement": "meas",
    "procedure": "proc",
    "finding": "finding",
    "recommendation": "rec",
    "event": "event",
    "plan": "plan",
    "demographic": "demo",
}
_PREFIXES = set(TYPE_PREFIX.values())

# Closed list for measurements, so the same value from different letters lands in one timeline.
# Runtime need filters ({"name": "lvef"}) match against these slugs.
MEASUREMENT_KEYS: list[str] = [
    "body_weight", "body_height", "bmi", "blood_pressure", "heart_rate", "temperature",
    "hba1c", "blood_glucose", "lvef", "tapse", "la_diameter", "septum_thickness",
    "creatinine", "egfr", "mmse", "birads", "ki67", "er_percent", "pr_percent",
    "flow_velocity", "stenosis_degree", "pa_pressure",
]

# Common German / short names → measurement key
MEASUREMENT_ALIASES: dict[str, str] = {
    "gewicht": "body_weight", "korpergewicht": "body_weight", "weight": "body_weight",
    "grosse": "body_height", "korpergrosse": "body_height", "height": "body_height",
    "blutdruck": "blood_pressure", "rr": "blood_pressure", "bp": "blood_pressure",
    "hf": "heart_rate", "puls": "heart_rate", "herzfrequenz": "heart_rate",
    "korpertemperatur": "temperature",
    "ef": "lvef", "ejektionsfraktion": "lvef", "ejection_fraction": "lvef",
    "blutzucker": "blood_glucose", "kreatinin": "creatinine", "gfr": "egfr",
    "ki_67": "ki67",
}

_LATERALITY_TOKENS = {"left", "right", "bilateral", "links", "rechts", "li", "re", "bds", "sin", "dext"}


def slug(text: str) -> str:
    text = (text.lower()
            .replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss"))
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _drop_laterality(part: str) -> str:
    return "_".join(t for t in part.split("_") if t and t not in _LATERALITY_TOKENS)


def normalize_measurement(name: str) -> Optional[str]:
    """Map a measurement name or key to an entry of MEASUREMENT_KEYS, or None."""
    s = slug(name)
    if s in MEASUREMENT_KEYS:
        return s
    return MEASUREMENT_ALIASES.get(s)


def normalize_concept_key(raw: str, fact_type: str) -> Optional[str]:
    """
    Turn an LLM-proposed key (e.g. "dx:Diabetes Type 2", "meas:EF") into a canonical
    concept_id ("C:dx:diabetes_type_2", "C:meas:lvef"). The prefix always follows the
    fact type. Returns None if nothing usable is left.
    """
    prefix = TYPE_PREFIX.get(fact_type)
    if not prefix or not raw:
        return None
    raw = re.sub(r"^c:", "", raw.strip(), flags=re.IGNORECASE)
    parts = [_drop_laterality(slug(p)) for p in raw.split(":")]
    parts = [p for p in parts if p]
    if parts and parts[0] in _PREFIXES:
        parts = parts[1:]
    if not parts:
        return None

    if prefix == "meas":
        if parts[0] == "other":
            parts = parts[1:]
        name = "_".join(parts)
        if not name:
            return None
        known = normalize_measurement(name)
        return f"C:meas:{known}" if known else f"C:meas:other:{name}"

    return "C:" + prefix + ":" + ":".join(parts)


def render_registry(registry: dict[str, str]) -> str:
    """Registry lines for the prompt: '<key without C:> · <label>'."""
    if not registry:
        return "(empty – this is the first document)"
    return "\n".join(f"{cid[2:]} · {label}" for cid, label in sorted(registry.items()))
