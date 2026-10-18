"""
Auth Package
Quản lý xác thực người dùng, băm mật khẩu Argon2id, cấp phát Access JWT 15 phút,
đăng nhập / đăng xuất / hồ sơ me và kiểm soát tốc độ thử đăng nhập (Rate Limiting).
P0 hoãn Refresh Token, auth cookies, CSRF cookies và session UI.
"""
from backend.app.auth.messages import AuthFailureCode, AuthMessage
from backend.app.auth.passwords import (
    hash_password,
    verify_password,
    needs_rehash,
    verify_and_rehash,
    validate_password_complexity,
)
from backend.app.auth.tokens import (
    issue_access_token,
    validate_access_token_claims,
    is_valid_access_token_claims,
    decode_and_verify_access_token,
    AccessTokenData,
)
from backend.app.auth.sessions import (
    verify_session_and_user,
    revoke_session,
    revoke_all_user_sessions,
)
from backend.app.auth.rate_limit import (
    compute_rate_limit_keys,
    check_rate_limit,
    record_login_attempt,
    acquire_rate_limit_advisory_locks,
)
from backend.app.auth.service import AuthService
from backend.app.auth.routes import auth_bp

__all__ = [
    "AuthFailureCode",
    "AuthMessage",
    "hash_password",
    "verify_password",
    "needs_rehash",
    "verify_and_rehash",
    "validate_password_complexity",
    "issue_access_token",
    "validate_access_token_claims",
    "is_valid_access_token_claims",
    "decode_and_verify_access_token",
    "AccessTokenData",
    "verify_session_and_user",
    "revoke_session",
    "revoke_all_user_sessions",
    "compute_rate_limit_keys",
    "check_rate_limit",
    "record_login_attempt",
    "acquire_rate_limit_advisory_locks",
    "AuthService",
    "auth_bp",
]
