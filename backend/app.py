import base64
import json
import os

from flask import Flask, abort, jsonify, request

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Patient data loaded from data/{patient-dir}/patient.json at startup
# ---------------------------------------------------------------------------
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

def _load_patients():
    patients = []
    for entry in sorted(os.listdir(_DATA_DIR)):
        patient_json = os.path.join(_DATA_DIR, entry, "patient.json")
        if os.path.isfile(patient_json):
            with open(patient_json, encoding="utf-8") as f:
                data = json.load(f)
            data.pop("ward", None)
            patients.append(data)
    return patients

_PATIENTS = _load_patients()


# ---------------------------------------------------------------------------
# The frontend dev server proxies /api/* here unchanged, so routes keep the /api prefix.
# ---------------------------------------------------------------------------
@app.get("/api/session/get")
def get_session():
    encoded = request.args.get("situation")
    if not encoded:
        abort(400, "Missing 'situation' query parameter")
    try:
        situation = base64.b64decode(encoded, validate=True).decode("utf-8")
    except ValueError:
        abort(400, "'situation' must be base64-encoded UTF-8")

    app.logger.info("New session for situation: %s", situation)
    # TODO: create a real session; a fixed ID is enough for the prototype
    return "1"


@app.get("/api/patients/search")
def search_patients():
    """Return patients whose name or ID contains the query string (case-insensitive).

    Query params:
      q   – search term (required, min 1 char)
      limit – max results to return (default 10, max 50)
    """
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])

    try:
        limit = max(1, min(int(request.args.get("limit", 10)), 50))
    except ValueError:
        limit = 10

    q_lower = q.lower()
    results = [
        p for p in _PATIENTS
        if q_lower in p["name"].lower() or q_lower in p["id"].lower()
    ][:limit]

    return jsonify(results)


@app.get("/api/patients/<patient_id>")
def get_patient(patient_id):
    """Return a single patient by ID."""
    patient = next((p for p in _PATIENTS if p["id"] == patient_id), None)
    if patient is None:
        abort(404, f"Patient '{patient_id}' not found")
    return jsonify(patient)


if __name__ == "__main__":
    app.run(port=8000, debug=True)
