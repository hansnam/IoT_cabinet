from flask import g
from werkzeug.exceptions import HTTPException
from .context import AuthContext
from .errors import make_error_response
from .messages import ErrorMessage, ErrorCode
from .request_context import init_request_context, require_auth_origin
from .config_validation import validate_config
from .messages import HTTP_ERRORS

__all__ = ["AuthContext", "make_error_response", "require_auth_origin", "register_security"]

def register_security(app):
    validate_config(app)
    init_request_context(app)

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        code, message = HTTP_ERRORS.get(error.code, (
            ErrorCode.HTTP_ERROR.format(status=error.code), ErrorMessage.HTTP_ERROR
        ))
        body, status = make_error_response(code, message, error.code)
        response = error.get_response()
        response.set_data(body.get_data())
        response.content_type = "application/json"
        response.status_code = status

        return response

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        app.logger.error(ErrorMessage.UNEXPECTED_ERROR_LOG, getattr(g, "request_id", None))
        return make_error_response(ErrorCode.INTERNAL_SERVER_ERROR, ErrorMessage.INTERNAL_SERVER_ERROR, 500)