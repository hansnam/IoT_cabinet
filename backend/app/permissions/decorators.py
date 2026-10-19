"""
Permissions & Security Decorators (Thành viên 3 chủ trì - TV3-04A Revision 3 / TV3-03B Delta)
Cung cấp decorator @require_auth và @require_permission:
1. @require_auth: Xác minh Bearer JWT, session DB và gắn request.auth.
   - Hỗ trợ tái sử dụng request.auth nếu đã được xác thực trước đó (tránh lặp query).
   - Enforce ràng buộc must_change_password ở cấp độ backend guard dùng chung.
2. @require_permission: Kiểm tra quyền hạn và device scope:
   - Tự động áp dụng @require_auth.
   - Fail-closed HTTP 400 nếu cấu hình device_id_param nhưng không tìm thấy trong URL/kwargs.
   - Trả về đúng HTTP status theo phân tầng: 401 Unauthorized, 404 Not Found (device scope), 403 Forbidden.
   - Fail-closed HTTP 503 khi database gặp sự cố kết nối.
"""
from functools import wraps
from flask import request
import jwt as pyjwt
from flask_jwt_extended.exceptions import JWTExtendedException
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError as SQLAlchemyTimeoutError

from backend.app.auth.tokens import decode_and_verify_access_token
from backend.app.auth.sessions import verify_session_and_user
from backend.app.permissions.service import (
    PermissionService,
    AuthRequired,
    DeviceScopeNotFound,
    PermissionDenied,
)
from backend.app.security.errors import make_error_response
from backend.app.security.messages import ErrorCode, ErrorMessage
from backend.app.extensions import db

# Danh sách các ngoại lệ mất kết nối hoặc timeout DB dự kiến trả về 503
DB_UNAVAILABLE_ERRORS = (OperationalError, InterfaceError, SQLAlchemyTimeoutError)

# Whitelist các endpoint được phép gọi khi tài khoản đang trong trạng thái must_change_password=True
MUST_CHANGE_PASSWORD_ALLOWED_ROUTES = frozenset({
    ("GET", "/api/v1/auth/me"),
    ("PATCH", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/change-password"),
    ("POST", "/api/v1/auth/logout"),
})


def _is_must_change_password_allowed(method: str, path: str) -> bool:
    """Helper kiểm tra endpoint có thuộc danh sách ngoại lệ được phép của must_change_password hay không."""
    return (method.upper(), path.rstrip("/")) in MUST_CHANGE_PASSWORD_ALLOWED_ROUTES


def require_auth(fn):
    """Decorator bảo vệ endpoint API yêu cầu xác thực người dùng (TV3-03A / TV3-03B).
    - Chỉ chấp nhận Authorization: Bearer <token>. Từ chối cookie/query/body.
    - Xác minh token qua pipeline 02B trước khi gọi DB.
    - Kiểm tra user ACTIVE, session hợp lệ, đúng token_version, role DB.
    - Gắn AuthContext vào request.auth (nguồn danh tính duy nhất).
    - Caller truy xuất context qua get_current_auth() hoặc request.auth.
    - Tự động tái sử dụng nếu request.auth đã tồn tại từ decorator khác.
    - Enforce ràng buộc must_change_password: chặn mọi route ngoài whitelist bằng 403.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # 0. Nếu request đã được xác thực trước đó (ví dụ qua decorator lồng), tái sử dụng ngay
        auth_context = getattr(request, "auth", None)
        if auth_context is not None:
            if getattr(request, "must_change_password", False) and not _is_must_change_password_allowed(request.method, request.path):
                return make_error_response(ErrorCode.FORBIDDEN, ErrorMessage.FORBIDDEN, 403)
            return fn(*args, **kwargs)

        # 1. Trích xuất Authorization Bearer token từ header
        auth_header = request.headers.get("Authorization", "").strip()
        parts = auth_header.split()
        if len(parts) != 2 or parts[0] != "Bearer":
            return make_error_response(ErrorCode.UNAUTHORIZED, ErrorMessage.UNAUTHORIZED, 401)

        token = parts[1]

        # 2. Xác minh chữ ký mật mã, thời hạn và claims token qua pipeline 02B
        try:
            jwt_claims = decode_and_verify_access_token(token)
        except (ValueError, TypeError, pyjwt.PyJWTError, JWTExtendedException):
            return make_error_response(ErrorCode.UNAUTHORIZED, ErrorMessage.UNAUTHORIZED, 401)

        # 3. Tra cứu DB kiểm tra user, session, token_version, expiry và role
        try:
            auth_context = verify_session_and_user(jwt_claims, db_session=db.session)
            if auth_context is None:
                return make_error_response(ErrorCode.UNAUTHORIZED, ErrorMessage.UNAUTHORIZED, 401)
        except DB_UNAVAILABLE_ERRORS:
            return make_error_response(ErrorCode.SERVICE_UNAVAILABLE, ErrorMessage.SERVICE_UNAVAILABLE, 503)

        # 4. Gắn AuthContext vào request.auth (nguồn danh tính duy nhất)
        request.auth = auth_context

        # 5. Ràng buộc must_change_password: từ chối các endpoint ngoài whitelist
        if getattr(request, "must_change_password", False) and not _is_must_change_password_allowed(request.method, request.path):
            return make_error_response(ErrorCode.FORBIDDEN, ErrorMessage.FORBIDDEN, 403)

        return fn(*args, **kwargs)

    return wrapper


def require_permission(code: str, device_id_param: str | None = None):
    """Decorator kiểm tra quyền hạn (permission) và phạm vi thiết bị (device scope).

    - Tự động áp dụng @require_auth để xác thực token và session trước.
    - Tái sử dụng request.auth nếu đã được xác thực, không giải mã JWT hay query DB lặp lại.
    - Nếu device_id_param được chỉ định nhưng không tìm thấy trong URL/kwargs -> trả về HTTP 400 Bad Request fail-closed.
    - Nếu device_id_param=None -> kiểm tra quyền ở cấp độ Global/List.
    """
    def decorator(fn):
        @wraps(fn)
        @require_auth
        def wrapper(*args, **kwargs):
            auth_context = request.auth

            device_id = None
            if device_id_param is not None:
                if request.view_args and device_id_param in request.view_args:
                    device_id = request.view_args[device_id_param]
                elif device_id_param in kwargs:
                    device_id = kwargs[device_id_param]

                if not device_id:
                    return make_error_response(ErrorCode.BAD_REQUEST, ErrorMessage.BAD_REQUEST, 400)

            try:
                PermissionService.require(
                    code,
                    device_id=device_id,
                    actor=auth_context,
                    db_session=db.session,
                )
            except AuthRequired as e:
                return make_error_response(e.code, e.message, e.status_code)
            except DeviceScopeNotFound as e:
                return make_error_response(e.code, e.message, e.status_code)
            except PermissionDenied as e:
                return make_error_response(e.code, e.message, e.status_code)
            except DB_UNAVAILABLE_ERRORS:
                return make_error_response(ErrorCode.SERVICE_UNAVAILABLE, ErrorMessage.SERVICE_UNAVAILABLE, 503)

            return fn(*args, **kwargs)

        return wrapper

    return decorator
