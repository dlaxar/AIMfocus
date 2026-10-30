"""
app/fhir/ids.py – our IDs as FHIR resource ids ([A-Za-z0-9-.]{1,64}).

"F:12340012:dia:01" → "F-12340012-dia-01". The original ID is kept in the resource's
original-values extension, so the mapping does not need to be reversible.
"""
from __future__ import annotations

import hashlib
import re

_INVALID = re.compile(r"[^A-Za-z0-9.\-]")


def fhir_id(our_id: str) -> str:
    s = _INVALID.sub("-", our_id).strip("-") or "x"
    if len(s) > 64:
        s = s[:51] + "-" + hashlib.sha1(our_id.encode("utf-8")).hexdigest()[:12]
    return s
