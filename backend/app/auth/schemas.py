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
from marshmallow import Schema, fields, validates, validates_schema, ValidationError, RAISE
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

class UpdateProfileRequestSchema(Schema):
    """Schema xác thực payload cập nhật hồ sơ cá nhân (PATCH /api/v1/auth/me)"""
    class Meta:
        unknown = RAISE  # Từ chối mọi field không nằm trong allowlist (HTTP 400)

    full_name = fields.String(
        required=True,
        error_messages={"required": AuthMessage.FULL_NAME_REQUIRED, "invalid": AuthMessage.FULL_NAME_INVALID_TYPE}
    )

    @validates("full_name")
    def validate_full_name(self, value: str, **kwargs) -> None:
        """Kiểm tra độ dài tên 1-128 ký tự sau khi trim; tương thích Marshmallow 4+."""
        if not isinstance(value, str):
            raise ValidationError(AuthMessage.FULL_NAME_INVALID_TYPE)
        trimmed = value.strip()
        if not (1 <= len(trimmed) <= 128):
            raise ValidationError(AuthMessage.FULL_NAME_LENGTH_INVALID)


class ChangePasswordRequestSchema(Schema):
    """Schema xác thực payload đổi mật khẩu (POST /api/v1/auth/change-password)"""
    class Meta:
        unknown = RAISE  # Từ chối mọi field không nằm trong allowlist (HTTP 400)

    old_password = fields.String(
        required=True,
        error_messages={"required": AuthMessage.OLD_PASSWORD_REQUIRED, "invalid": AuthMessage.PASSWORD_INVALID_TYPE}
    )
    new_password = fields.String(
        required=True,
        error_messages={"required": AuthMessage.NEW_PASSWORD_REQUIRED, "invalid": AuthMessage.PASSWORD_INVALID_TYPE}
    )

    @validates("old_password")
    def validate_old_password(self, value: str, **kwargs) -> None:
        """Kiểm tra độ dài mật khẩu cũ 12-128 ký tự; tương thích Marshmallow 4+."""
        if not isinstance(value, str):
            raise ValidationError(AuthMessage.PASSWORD_INVALID_TYPE)
        if not (MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH):
            raise ValidationError(AuthMessage.PASSWORD_LENGTH_INVALID)

    @validates("new_password")
    def validate_new_password(self, value: str, **kwargs) -> None:
        """Kiểm tra độ dài mật khẩu mới 12-128 ký tự; tương thích Marshmallow 4+."""
        if not isinstance(value, str):
            raise ValidationError(AuthMessage.PASSWORD_INVALID_TYPE)
        if not (MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH):
            raise ValidationError(AuthMessage.PASSWORD_LENGTH_INVALID)

    @validates_schema
    def validate_passwords_differ(self, data: dict, **kwargs) -> None:
        """Từ chối nếu mật khẩu mới trùng mật khẩu cũ; tương thích Marshmallow 4+."""
        old_pw = data.get("old_password")
        new_pw = data.get("new_password")
        if old_pw is not None and new_pw is not None and old_pw == new_pw:
            raise ValidationError(AuthMessage.NEW_PASSWORD_SAME_AS_OLD, field_name="new_password")
