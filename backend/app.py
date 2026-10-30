import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from flask import Flask, Response, abort, jsonify, request, stream_with_context

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
# Core service (core/, FastAPI): fact store, LLM pipeline, validation
# ---------------------------------------------------------------------------
CORE_URL = os.environ.get("CORE_URL", "http://127.0.0.1:8001").rstrip("/")


def _core(method, path, payload=None):
    """Call the core service and return its JSON; map its errors onto Flask errors."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{CORE_URL}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        abort(e.code, e.read().decode("utf-8", "replace"))
    except urllib.error.URLError:
        abort(503, f"Core service not reachable at {CORE_URL}. Start it with: "
                   "cd core && uvicorn app.main:app --port 8001")


def _patient_query():
    """'?patient_id=…' when the request names a patient (?patient= or ?patient_id=), else ''."""
    pid = request.args.get("patient_id") or request.args.get("patient")
    return f"?patient_id={urllib.parse.quote(pid)}" if pid else ""


def _core_binary(path):
    """Relay a binary core response (images, DICOM) with its content type and headers."""
    try:
        upstream = urllib.request.urlopen(f"{CORE_URL}{path}", timeout=60)
    except urllib.error.HTTPError as e:
        abort(e.code, e.read().decode("utf-8", "replace"))
    except urllib.error.URLError:
        abort(503, f"Core service not reachable at {CORE_URL}")
    with upstream:
        headers = {k: v for k, v in upstream.headers.items()
                   if k.lower() in ("x-slice-count", "cache-control", "content-disposition")}
        return Response(upstream.read(), mimetype=upstream.headers.get_content_type(), headers=headers)


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

    # The core service answers from its fact store at once and keeps searching the raw
    # text for missing items in the background, so the screen fills while the user
    # picks the patient. The session ID is the core screen ID.
    # ?patient=<id> builds it for that patient (default: the IBM patient).
    payload = {"situation": situation}
    if request.args.get("patient"):
        payload["patient_id"] = request.args["patient"]
    screen = _core("POST", "/api/v2/screens", payload)
    return jsonify(session_id=screen["screen_id"], screen=screen)


@app.post("/api/session/<session_id>/patient")
def switch_session_patient(session_id):
    """
    The same situation for another patient: {"patient_id": ...} → a new session
    {session_id, screen}. Chats then follow the new session's patient.
    """
    body = request.get_json(silent=True) or {}
    if not body.get("patient_id"):
        abort(400, "Missing 'patient_id'")
    old = _core("GET", f"/api/v2/screens/{session_id}")
    screen = _core("POST", "/api/v2/screens", {"situation": old["situation"], "patient_id": body["patient_id"]})
    return jsonify(session_id=screen["screen_id"], screen=screen)


@app.get("/api/session/<session_id>")
def get_session_screen(session_id):
    """Current screen of a session. ?wait=true blocks until the background search is done."""
    wait = "?wait=true" if request.args.get("wait") == "true" else ""
    return jsonify(_core("GET", f"/api/v2/screens/{session_id}{wait}"))


@app.get("/api/session/<session_id>/summary")
def get_session_summary(session_id):
    """Ask the LLM for a short 'Role, Situation' label for this session."""
    data = _core("GET", f"/api/v2/screens/{session_id}/summary")
    return jsonify(data)


@app.post("/api/session/<session_id>/chat")
def chat_about_patient(session_id):
    """
    Question about the patient: {"question": ..., "chat_id": optional}. Pass the returned
    chat_id back for follow-ups; the session's screen gives the question its context.
    """
    body = request.get_json(silent=True) or {}
    if not str(body.get("question", "")).strip():
        abort(400, "Missing 'question'")
    payload = {"question": body["question"], "screen_id": session_id}
    if body.get("chat_id"):
        payload["chat_id"] = body["chat_id"]
    for key in ("cards", "wait"):   # cards=true: also screen cards for the question
        if key in body:
            payload[key] = bool(body[key])
    return jsonify(_core("POST", "/api/v2/chat", payload))


@app.post("/api/session/<session_id>/chat/stream")
def chat_about_patient_stream(session_id):
    """
    Same body as /chat; server-sent events from the core: "answer" (text, ~1.5 s), "cards" on
    every change (store facts first, then what the text search finds), then "done".
    """
    body = request.get_json(silent=True) or {}
    if not str(body.get("question", "")).strip():
        abort(400, "Missing 'question'")
    payload = {"question": body["question"], "screen_id": session_id}
    for key in ("chat_id", "cards"):
        if key in body:
            payload[key] = body[key]
    req = urllib.request.Request(f"{CORE_URL}/api/v2/chat/stream", data=json.dumps(payload).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    try:
        upstream = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        abort(e.code, e.read().decode("utf-8", "replace"))
    except urllib.error.URLError:
        abort(503, f"Core service not reachable at {CORE_URL}")

    def relay():
        with upstream:
            for line in upstream:
                yield line

    return Response(stream_with_context(relay()), mimetype="text/event-stream")


@app.get("/api/session/<session_id>/stream")
def stream_session_screen(session_id):
    """Server-sent events from the core: one "screen" event per update, then "done"."""
    try:
        upstream = urllib.request.urlopen(f"{CORE_URL}/api/v2/screens/{session_id}/stream", timeout=60)
    except urllib.error.HTTPError as e:
        abort(e.code, e.read().decode("utf-8", "replace"))
    except urllib.error.URLError:
        abort(503, f"Core service not reachable at {CORE_URL}")

    def relay():
        with upstream:
            for line in upstream:
                yield line

    return Response(stream_with_context(relay()), mimetype="text/event-stream")


@app.get("/api/documents/<doc_id>/original")
def get_original_document(doc_id):
    """The letter as delivered: {doc_id, file, metadata, text} – text is the original HTML."""
    return jsonify(_core("GET", f"/api/documents/{doc_id}/original{_patient_query()}"))


@app.get("/api/passages/<passage_id>/original")
def get_passage_original(passage_id):
    """Original letter of a passage plus raw_start / raw_end: text[raw_start:raw_end] is the passage."""
    return jsonify(_core("GET", f"/api/passages/{passage_id}/original{_patient_query()}"))


@app.get("/api/documents/<doc_id>")
def get_document(doc_id):
    """Normalized document text + passages with offsets, for highlighting a source."""
    return jsonify(_core("GET", f"/api/documents/{doc_id}{_patient_query()}"))


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


@app.get("/api/patients/available")
def list_available_patients():
    """Patients the core has a cache for (IBM letters and imported FHIR records), with image counts."""
    return jsonify(_core("GET", "/api/v2/patients"))


@app.get("/api/patients/<patient_id>/images")
def list_patient_images(patient_id):
    """The patient's images: [{image_id, modality, body_site, date, frames, preview_url, slice_url, dicom_url, …}]."""
    return jsonify(_core("GET", f"/api/v2/patients/{patient_id}/images"))


@app.get("/api/patients/<patient_id>/images/<image_id>/preview.png")
def get_image_preview(patient_id, image_id):
    """Axial, coronal and sagittal middle slice side by side."""
    return _core_binary(f"/api/v2/patients/{patient_id}/images/{image_id}/preview.png")


@app.get("/api/patients/<patient_id>/images/<image_id>/slice.png")
def get_image_slice(patient_id, image_id):
    """One slice: ?axis=axial|coronal|sagittal&index=N (header X-Slice-Count)."""
    query = urllib.parse.urlencode({k: request.args[k] for k in ("axis", "index") if k in request.args})
    return _core_binary(f"/api/v2/patients/{patient_id}/images/{image_id}/slice.png" + (f"?{query}" if query else ""))


@app.get("/api/patients/<patient_id>/images/<image_id>/dicom")
def get_image_dicom(patient_id, image_id):
    """The original DICOM file."""
    return _core_binary(f"/api/v2/patients/{patient_id}/images/{image_id}/dicom")


@app.get("/api/patients/<patient_id>/profile")
def get_patient_profile(patient_id):
    """Patient header from the core: name, birth date, age, CAVE, height/weight/BMI, latest stay."""
    return jsonify(_core("GET", f"/api/v2/patients/{patient_id}"))


@app.get("/api/patients/<patient_id>")
def get_patient(patient_id):
    """Return a single patient by ID."""
    patient = next((p for p in _PATIENTS if p["id"] == patient_id), None)
    if patient is None:
        abort(404, f"Patient '{patient_id}' not found")
    return jsonify(patient)


if __name__ == "__main__":
    app.run(port=8000, debug=True)
