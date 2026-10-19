from flask import g
from werkzeug.exceptions import HTTPException
from .context import AuthContext
from .errors import make_error_response
from .messages import ErrorMessage, ErrorCode
from .request_context import init_request_context, require_auth_origin
from .config_validation import validate_config
from .messages import HTTP_ERRORS
from backend.app.extensions import jwt
from backend.app.auth.tokens import is_valid_access_token_claims

__all__ = ["AuthContext", "make_error_response", "require_auth_origin", "register_security"]

def register_security(app):
    validate_config(app)
    init_request_context(app)
    register_jwt_callbacks(jwt)

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

def register_jwt_callbacks(jwt_manager):
    """Đăng ký các callbacks xử lý lỗi JWT với envelope chuẩn một lần duy nhất.
    Trả về mã 401 với định dạng:
    {"error": {"code": "UNAUTHORIZED", "message": "Unauthorized", "request_id": "<uuid>"}}
    Tuyệt đối không echo token, hash hoặc exception string ra client.
    """
    if getattr(jwt_manager, "_tv3_callbacks_registered", False):
        return
    jwt_manager._tv3_callbacks_registered = True

    @jwt_manager.unauthorized_loader
    @jwt_manager.invalid_token_loader
    @jwt_manager.expired_token_loader
    @jwt_manager.revoked_token_loader
    @jwt_manager.token_verification_failed_loader
    def unauthorized_callback(*_ignored):
        return make_error_response(ErrorCode.UNAUTHORIZED, ErrorMessage.UNAUTHORIZED, 401)

    @jwt_manager.token_verification_loader
    def token_verification_callback(jwt_header: dict, jwt_payload: dict) -> bool:
        return is_valid_access_token_claims(jwt_header, jwt_payload)