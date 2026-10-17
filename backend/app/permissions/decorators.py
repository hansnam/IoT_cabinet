"""
Permissions & Security Decorators (Thành viên 3 chủ trì - TV3-03A Revision 3)
Cung cấp decorator @require_auth để bảo vệ các endpoint API:
1. Xác minh Bearer JWT qua pipeline 02B (chữ ký, thuật toán, claims, lifetime 1..900s).
2. Tra cứu và xác minh tính hợp lệ của User, Session, và Role trong database.
3. Khi hợp lệ: Gắn AuthContext vào request.auth (nguồn danh tính duy nhất, cô lập tuyệt đối theo request).
4. Khi không hợp lệ: Trả về HTTP 401 generic envelope (make_error_response).
5. Khi DB không khả dụng (OperationalError, InterfaceError, SQLAlchemyTimeoutError):
   Trả về HTTP 503 fail-closed envelope.
   Các lỗi lập trình/cú pháp khác (ProgrammingError, InvalidRequestError) để tầng 500 xử lý.
"""
from functools import wraps
from flask import request
import jwt as pyjwt
from flask_jwt_extended.exceptions import JWTExtendedException
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError as SQLAlchemyTimeoutError

from backend.app.auth.tokens import decode_and_verify_access_token
from backend.app.auth.sessions import verify_session_and_user
from backend.app.security.errors import make_error_response
from backend.app.security.messages import ErrorCode, ErrorMessage
from backend.app.extensions import db

# Danh sách các ngoại lệ mất kết nối hoặc timeout DB dự kiến trả về 503
DB_UNAVAILABLE_ERRORS = (OperationalError, InterfaceError, SQLAlchemyTimeoutError)


def require_auth(fn):
    """Decorator bảo vệ endpoint API yêu cầu xác thực người dùng (TV3-03A).
    - Chỉ chấp nhận Authorization: Bearer <token>. Từ chối cookie/query/body.
    - Xác minh token qua pipeline 02B trước khi gọi DB.
    - Kiểm tra user ACTIVE, session hợp lệ, đúng token_version, role DB.
    - Gắn AuthContext vào request.auth (nguồn danh tính duy nhất).
    - Caller truy xuất context qua get_current_auth() hoặc request.auth.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
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
            # Bất kỳ vi phạm chữ ký, expired, thiếu type, sai alg hay claims đều trả về 401
            return make_error_response(ErrorCode.UNAUTHORIZED, ErrorMessage.UNAUTHORIZED, 401)

        # 3. Tra cứu DB kiểm tra user, session, token_version, expiry và role
        try:
            auth_context = verify_session_and_user(jwt_claims, db_session=db.session)
            if auth_context is None:
                return make_error_response(ErrorCode.UNAUTHORIZED, ErrorMessage.UNAUTHORIZED, 401)
        except DB_UNAVAILABLE_ERRORS:
            # Lỗi mất kết nối, lỗi interface hoặc timeout connection pool DB -> fail closed 503
            return make_error_response(ErrorCode.SERVICE_UNAVAILABLE, ErrorMessage.SERVICE_UNAVAILABLE, 503)

        # 4. Gắn AuthContext vào request.auth (nguồn danh tính duy nhất, không dùng g.auth)
        request.auth = auth_context

        return fn(*args, **kwargs)

    return wrapper