import base64

from flask import Flask, abort, request

app = Flask(__name__)


# The frontend dev server proxies /api/* here unchanged, so routes keep the /api prefix.
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


if __name__ == "__main__":
    app.run(port=8000, debug=True)
