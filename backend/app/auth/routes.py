"""
Auth Routes Module (TV3-03B Revision 2)
Cung cấp các endpoint REST API trong Blueprint 'auth' với tiền tố /api/v1/auth:
1. POST /api/v1/auth/login: Xác thực thông tin đăng nhập, rate-limited, cấp Access JWT 15 phút.
2. POST /api/v1/auth/logout: Thu hồi phiên đăng nhập hiện tại của người dùng.
3. GET  /api/v1/auth/me: Đọc hồ sơ cá nhân, vai trò và danh sách quyền hạn P0 hiện tại.
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request
from marshmallow import ValidationError
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError as SQLAlchemyTimeoutError

from backend.app.auth.schemas import LoginRequestSchema
from backend.app.auth.service import AuthService, AuthenticationFailed, RateLimitExceeded
from backend.app.extensions import db
from backend.app.permissions.decorators import require_auth
from backend.app.security.errors import make_error_response
from backend.app.security.messages import ErrorCode, ErrorMessage
from backend.app.security.request_context import require_auth_origin

# Danh sách lỗi mất kết nối DB chuyển thành 503 Service Unavailable
DB_UNAVAILABLE_ERRORS = (OperationalError, InterfaceError, SQLAlchemyTimeoutError)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")
login_schema = LoginRequestSchema()


@auth_bp.route("/login", methods=["POST"])
def login():
    """POST /api/v1/auth/login
    Đăng nhập hệ thống bằng username/password, kiểm soát tốc độ và cấp phát Access JWT.
    """
    # 1. Bắt buộc kiểm tra Origin header hợp lệ theo cấu hình AUTH_ALLOWED_ORIGINS
    origin_error = require_auth_origin()
    if origin_error is not None:
        return origin_error

    # 2. Xác thực cấu trúc body JSON qua LoginRequestSchema (Allowlist strict)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return make_error_response(ErrorCode.BAD_REQUEST, ErrorMessage.BAD_REQUEST, 400)

    try:
        validated_data = login_schema.load(payload)
    except ValidationError:
        return make_error_response(ErrorCode.BAD_REQUEST, ErrorMessage.BAD_REQUEST, 400)

    # 3. Trích xuất metadata từ request context (sử dụng g.request_id hoặc request.request_id)
    client_ip = request.remote_addr or "127.0.0.1"
    user_agent = request.headers.get("User-Agent", "")
    request_id = getattr(request, "request_id", None) or getattr(g, "request_id", None)

    # 4. Thực thi quy trình đăng nhập qua AuthService
    try:
        result = AuthService.login(
            username=validated_data["username"],
            password=validated_data["password"],
            client_ip=client_ip,
            user_agent=user_agent,
            request_id=request_id,
            db_session=db.session,
        )
    except RateLimitExceeded as e:
        response, status = make_error_response(ErrorCode.TOO_MANY_REQUESTS, ErrorMessage.TOO_MANY_REQUESTS, 429)
        response.headers["Retry-After"] = str(e.retry_after)
        return response, status
    except AuthenticationFailed:
        return make_error_response(ErrorCode.UNAUTHORIZED, ErrorMessage.UNAUTHORIZED, 401)
    except DB_UNAVAILABLE_ERRORS:
        try:
            db.session.rollback()
        except Exception:
            pass
        return make_error_response(ErrorCode.SERVICE_UNAVAILABLE, ErrorMessage.SERVICE_UNAVAILABLE, 503)
    except Exception:
        # Xử lý các lỗi hệ thống khác (ví dụ writer thất bại) mà không làm lộ traceback
        try:
            db.session.rollback()
        except Exception:
            pass
        return make_error_response(ErrorCode.INTERNAL_SERVER_ERROR, ErrorMessage.INTERNAL_SERVER_ERROR, 500)

    return jsonify({
        "data": {
            "access_token": result.token_data.access_token,
            "expires_in": result.token_data.expires_in,
            "user": result.user_data,
        }
    }), 200


@auth_bp.route("/logout", methods=["POST"])
@require_auth
def logout():
    """POST /api/v1/auth/logout
    Thu hồi phiên đăng nhập hiện tại và ghi nhận nhật ký kiểm toán.
    """
    origin_error = require_auth_origin()
    if origin_error is not None:
        return origin_error

    auth_context = request.auth
    try:
        AuthService.logout(actor=auth_context, db_session=db.session)
    except DB_UNAVAILABLE_ERRORS:
        return make_error_response(ErrorCode.SERVICE_UNAVAILABLE, ErrorMessage.SERVICE_UNAVAILABLE, 503)
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return make_error_response(ErrorCode.INTERNAL_SERVER_ERROR, ErrorMessage.INTERNAL_SERVER_ERROR, 500)

    return jsonify({
        "data": {
            "logged_out": True
        }
    }), 200


@auth_bp.route("/me", methods=["GET"])
@require_auth
def me():
    """GET /api/v1/auth/me
    Lấy thông tin tài khoản, vai trò và danh sách quyền hạn P0 của người dùng hiện tại.
    """
    auth_context = request.auth
    try:
        user_info = AuthService.get_me(actor=auth_context, db_session=db.session)
    except AuthenticationFailed:
        return make_error_response(ErrorCode.UNAUTHORIZED, ErrorMessage.UNAUTHORIZED, 401)
    except DB_UNAVAILABLE_ERRORS:
        return make_error_response(ErrorCode.SERVICE_UNAVAILABLE, ErrorMessage.SERVICE_UNAVAILABLE, 503)
    except Exception:
        return make_error_response(ErrorCode.INTERNAL_SERVER_ERROR, ErrorMessage.INTERNAL_SERVER_ERROR, 500)

    return jsonify({
        "data": user_info
    }), 200
