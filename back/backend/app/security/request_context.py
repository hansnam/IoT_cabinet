"""Request metadata; no forwarded-IP trust or retention jobs"""
from uuid import uuid4
from flask import current_app, g, request
from .errors import make_error_response
from .messages import ErrorCode, ErrorMessage

def require_auth_origin():
    origin = request.headers.get("Origin")
    if not origin or origin not in current_app.config["AUTH_ALLOWED_ORIGINS"]:
        return make_error_response(ErrorCode.FORBIDDEN, ErrorMessage.ORIGIN_NOT_ALLOWED, 403)
    return None

def init_request_context(app):
    @app.before_request
    def set_server_request_id():
        g.request_id = str(uuid4())
        request.request_id = g.request_id

        g.client_ip = request.remote_addr
        g.user_agent = request.headers.get("User-Agent", "")[:512]

    @app.after_request
    def apply_security_headers(response):
        response.headers["X-Request-ID"] = g.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"

        return response