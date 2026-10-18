"""
Auth Schemas Module (TV3-03B Revision 2)
Xác thực dữ liệu đầu vào cho authentication endpoints:
- POST /auth/login: chỉ nhận username và password trong allowlist, field lạ -> 400 Bad Request.
- Tương thích hoàn toàn với Marshmallow 4.3.1 (nhận **kwargs trong các hàm @validates).
- Username: canonical [a-z0-9_.-], 3-64 ký tự.
- Password: kiểu string, độ dài 12-128 ký tự, không trim, không cắt ngắn, không log.
"""
from __future__ import annotations

import re
from marshmallow import Schema, fields, validates, ValidationError, RAISE
from backend.app.auth.messages import AuthMessage

USERNAME_REGEX = re.compile(r"^[a-z0-9_.-]{3,64}$")
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


class LoginRequestSchema(Schema):
    """Schema xác thực payload đăng nhập (POST /api/v1/auth/login)"""
    class Meta:
        unknown = RAISE  # Từ chối mọi field không nằm trong allowlist (HTTP 400)

    username = fields.String(
        required=True,
        error_messages={"required": AuthMessage.USERNAME_REQUIRED, "invalid": AuthMessage.USERNAME_INVALID_TYPE}
    )
    password = fields.String(
        required=True,
        error_messages={"required": AuthMessage.PASSWORD_REQUIRED, "invalid": AuthMessage.PASSWORD_INVALID_TYPE}
    )

    @validates("username")
    def validate_username(self, value: str, **kwargs) -> None:
        """Kiểm tra định dạng canonical username; nhận **kwargs để tương thích Marshmallow 4+."""
        if not isinstance(value, str):
            raise ValidationError(AuthMessage.USERNAME_INVALID_TYPE)
        trimmed = value.strip().lower()
        if not (3 <= len(trimmed) <= 64) or not USERNAME_REGEX.match(trimmed):
            raise ValidationError(AuthMessage.USERNAME_FORMAT_INVALID)

    @validates("password")
    def validate_password(self, value: str, **kwargs) -> None:
        """Kiểm tra độ dài mật khẩu 12-128 ký tự; nhận **kwargs để tương thích Marshmallow 4+."""
        if not isinstance(value, str):
            raise ValidationError(AuthMessage.PASSWORD_INVALID_TYPE)
        # Tuyệt đối không strip()/trim() mật khẩu người dùng
        if not (MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH):
            raise ValidationError(AuthMessage.PASSWORD_LENGTH_INVALID)
