"""
app/runtime/templates.py – hard-coded role/task → needs templates.

When the planner recognises a known role/task combination, the template needs
are unioned with the planner's output (never replacing it).
"""
from __future__ import annotations

import re
from typing import Optional

from app.schemas import Need

# ---------------------------------------------------------------------------
# Template definitions
# ---------------------------------------------------------------------------

_TEMPLATES: list[dict] = [
    {
        "roles": ["anaesthetist", "anesthesiologist", "anaesthesia", "anesthesia"],
        "tasks": ["pre-op", "preoperative", "pre-operative", "preop", "before surgery", "before operation"],
        "needs": [
            Need(need_id="T:anaes:01", label="Current antithrombotic / anticoagulant medication", category="medication",
                 filter={"drug_class": ["antithrombotic", "anticoagulant", "antiplatelet"]},
                 time_scope="current", report_absence=True, priority=1),
            Need(need_id="T:anaes:02", label="Allergies", category="allergy",
                 filter={}, time_scope="all", report_absence=True, priority=1),
            Need(need_id="T:anaes:03", label="Cardiac function (LVEF / echo)", category="measurement",
                 filter={"name": "lvef"}, time_scope="latest", report_absence=True, priority=2),
            Need(need_id="T:anaes:04", label="Kidney function (creatinine / GFR)", category="measurement",
                 filter={"name": "creatinine"}, time_scope="latest", report_absence=True, priority=2),
            Need(need_id="T:anaes:05", label="Diabetes medications (esp. metformin)", category="medication",
                 filter={"drug_class": ["biguanide", "antidiabetic", "glp1", "thiazolidinedione"]},
                 time_scope="current", report_absence=False, priority=2),
            Need(need_id="T:anaes:06", label="Known cardiac diagnoses", category="diagnosis",
                 filter={"type": "diagnosis"}, time_scope="all", report_absence=False, priority=3),
            Need(need_id="T:anaes:07", label="Current blood pressure medications", category="medication",
                 filter={"drug_class": ["antihypertensive", "ace_inhibitor", "arb", "beta_blocker", "calcium_channel_blocker"]},
                 time_scope="current", report_absence=False, priority=3),
        ],
    },
    {
        "roles": ["surgeon", "operating surgeon", "surgical"],
        "tasks": ["tumour board", "tumor board", "oncology", "planning", "operation planning"],
        "needs": [
            Need(need_id="T:surg:01", label="Tumour histology and TNM stage", category="finding",
                 filter={}, time_scope="latest", report_absence=True, priority=1),
            Need(need_id="T:surg:02", label="Receptor status and Ki-67", category="finding",
                 filter={}, time_scope="latest", report_absence=True, priority=1),
            Need(need_id="T:surg:03", label="Tumour board recommendation and scheduled date", category="recommendation",
                 filter={}, time_scope="latest", report_absence=True, priority=1),
            Need(need_id="T:surg:04", label="Previous breast / oncology procedures", category="procedure",
                 filter={}, time_scope="all", report_absence=False, priority=2),
            Need(need_id="T:surg:05", label="Allergies", category="allergy",
                 filter={}, time_scope="all", report_absence=True, priority=2),
            Need(need_id="T:surg:06", label="Anticoagulant / antithrombotic medication", category="medication",
                 filter={"drug_class": ["antithrombotic", "anticoagulant", "antiplatelet"]},
                 time_scope="current", report_absence=True, priority=2),
        ],
    },
    {
        "roles": ["gp", "general practitioner", "hausarzt", "family doctor"],
        "tasks": ["diabetes", "diabetes review", "diabetes check", "hba1c"],
        "needs": [
            Need(need_id="T:gp_dm:01", label="Glucose-lowering medications", category="medication",
                 filter={"drug_class": ["antidiabetic", "biguanide", "glp1", "thiazolidinedione", "sulfonylurea", "dpp4", "sglt2", "insulin"]},
                 time_scope="current", report_absence=True, priority=1),
            Need(need_id="T:gp_dm:02", label="Latest HbA1c and date", category="measurement",
                 filter={"name": "hba1c"}, time_scope="latest", report_absence=True, priority=1),
            Need(need_id="T:gp_dm:03", label="Weight over time", category="measurement",
                 filter={"name": "body_weight"}, time_scope="all", report_absence=False, priority=2),
            Need(need_id="T:gp_dm:04", label="Weight change (derived)", category="derived",
                 filter={}, time_scope="all", report_absence=False, priority=2),
            Need(need_id="T:gp_dm:05", label="Kidney function", category="measurement",
                 filter={"name": "creatinine"}, time_scope="latest", report_absence=True, priority=2),
        ],
    },
    {
        "roles": ["emergency physician", "emergency doctor", "ed physician", "emergency", "ed"],
        "tasks": ["night", "night duty", "acute", "dizziness", "amaurosis", "tia", "stroke"],
        "needs": [
            Need(need_id="T:ed:01", label="Previous neurological events", category="diagnosis",
                 filter={}, time_scope="all", report_absence=False, priority=1),
            Need(need_id="T:ed:02", label="Carotid stenosis", category="finding",
                 filter={}, time_scope="all", report_absence=False, priority=1),
            Need(need_id="T:ed:03", label="Contrast media allergy", category="allergy",
                 filter={"substance": "contrast"}, time_scope="all", report_absence=True, priority=1),
            Need(need_id="T:ed:04", label="Metformin (contra-indicated with contrast)", category="medication",
                 filter={"drug_class": ["biguanide"]}, time_scope="current", report_absence=True, priority=1),
            Need(need_id="T:ed:05", label="Kidney function (required before contrast)", category="measurement",
                 filter={"name": "creatinine"}, time_scope="latest", report_absence=True, priority=1),
            Need(need_id="T:ed:06", label="Antihypertensive medications", category="medication",
                 filter={"drug_class": ["antihypertensive", "arb", "ace_inhibitor", "beta_blocker"]},
                 time_scope="current", report_absence=False, priority=2),
            Need(need_id="T:ed:07", label="Current antithrombotic medication", category="medication",
                 filter={"drug_class": ["antithrombotic", "antiplatelet", "anticoagulant"]},
                 time_scope="current", report_absence=False, priority=2),
        ],
    },
    {
        "roles": ["gp", "general practitioner", "family doctor"],
        "tasks": ["follow-up", "follow up", "followup", "discharge", "2015 hospital", "open follow"],
        "needs": [
            Need(need_id="T:gp_fu:01", label="Recommendations from discharge letter", category="recommendation",
                 filter={}, time_scope="all", report_absence=True, priority=1),
            Need(need_id="T:gp_fu:02", label="Follow-up status (done / open)", category="derived",
                 filter={}, time_scope="all", report_absence=False, priority=1),
            Need(need_id="T:gp_fu:03", label="Carotid stenosis laterality and degree", category="finding",
                 filter={}, time_scope="all", report_absence=False, priority=2),
        ],
    },
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _matches(tokens: list[str], patterns: list[str]) -> bool:
    text = " ".join(tokens)
    return any(p in text for p in patterns)


def template_needs_for(role: str, task: str) -> list[Need]:
    """Return template needs for the given role + task combination."""
    role_norm = role.lower().strip()
    task_norm = task.lower().strip()

    matched: list[Need] = []
    for tmpl in _TEMPLATES:
        role_match = any(r in role_norm for r in tmpl["roles"])
        task_match = any(t in task_norm for t in tmpl["tasks"])
        if role_match and task_match:
            matched.extend(tmpl["needs"])

    return matched


def merge_needs(planner_needs: list[Need], template_needs: list[Need]) -> list[Need]:
    """
    Union of planner needs and template needs.
    Template needs with IDs already present in planner needs are skipped.
    Template needs are appended after planner needs, sorted by priority.
    """
    existing_ids = {n.need_id for n in planner_needs}
    extra = [n for n in template_needs if n.need_id not in existing_ids]
    combined = planner_needs + extra
    combined.sort(key=lambda n: n.priority)
    return combined
