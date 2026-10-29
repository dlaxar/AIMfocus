import base64

from flask import Flask, abort, jsonify, request

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Demo patient data
# ---------------------------------------------------------------------------
_PATIENTS = [
    {"id": "P001", "name": "Alice Müller", "dob": "1985-03-14", "ward": "Cardiology"},
    {"id": "P002", "name": "Bob Fischer", "dob": "1972-07-22", "ward": "Neurology"},
    {"id": "P003", "name": "Clara Schneider", "dob": "1990-11-05", "ward": "Oncology"},
    {"id": "P004", "name": "David Wagner", "dob": "1965-01-30", "ward": "Orthopedics"},
    {"id": "P005", "name": "Eva Becker", "dob": "2001-09-18", "ward": "Pediatrics"},
    {"id": "P006", "name": "Frank Hoffmann", "dob": "1958-06-02", "ward": "Cardiology"},
    {"id": "P007", "name": "Greta Koch", "dob": "1979-12-25", "ward": "Neurology"},
    {"id": "P008", "name": "Hans Richter", "dob": "1943-04-11", "ward": "Geriatrics"},
    {"id": "P009", "name": "Ingrid Bauer", "dob": "1995-08-07", "ward": "Emergency"},
    {"id": "P010", "name": "Jan Krause", "dob": "1988-02-19", "ward": "Orthopedics"},
    {"id": "P011", "name": "Klara Lange", "dob": "2010-05-23", "ward": "Pediatrics"},
    {"id": "P012", "name": "Leon Schmitt", "dob": "1970-10-31", "ward": "Oncology"},
]


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
