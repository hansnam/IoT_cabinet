"""Only pass public messages, never raw exception strings."""
from flask import g, jsonify

def make_error_response(code, message, status_code):
    return jsonify({"error": {
        "code": code,
        "message": message,
        "request_id": getattr(g, "request_id", None),
    }}), status_code