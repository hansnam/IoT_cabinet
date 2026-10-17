"""
Auth Package
Quản lý xác thực người dùng, băm mật khẩu Argon2id và cấp phát Access JWT 15 phút.
P0 hoãn Refresh Token, auth cookies, CSRF cookies và session UI.
"""
from backend.app.auth.messages import AuthMessage
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

__all__ = [
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
]
