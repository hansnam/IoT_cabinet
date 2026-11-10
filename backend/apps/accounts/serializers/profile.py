"""
Profile & Password Serializers Module
Định dạng dữ liệu trả về và kiểm tra dữ liệu đầu vào cho hồ sơ cá nhân và đổi mật khẩu:
- UserProfileSerializer: Xuất thông tin hồ sơ người dùng, vai trò, quyền hạn P0 và metadata phiên.
  Tối ưu hóa: Cache kết quả truy vấn registry nội bộ để không truy vấn database lặp.
- UpdateProfileRequestSerializer: Kiểm tra dữ liệu cập nhật full_name, chống mass-assignment.
- ChangePasswordRequestSerializer: Kiểm tra đổi mật khẩu (12-128 ký tự, no trim, Django validators, khác mật khẩu cũ).
"""
from typing import Any, ClassVar

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.accounts.models import AuthSession, User
from apps.accounts.policies.roles import ROLE_PERMISSIONS, get_user_role, has_permission


class UserProfileSerializer(serializers.ModelSerializer):
    """
    Serializer định dạng thông tin hồ sơ cá nhân trả về cho GET và PATCH /api/v1/profile/me/.
    """
    role = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()
    session = serializers.SerializerMethodField()
    expires_at = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields: ClassVar[list[str]] = [
            "id",
            "username",
            "full_name",
            "email",
            "status",
            "must_change_password",
            "role",
            "permissions",
            "session",
            "expires_at",
        ]
        read_only_fields = fields

    def get_role(self, obj: User) -> str | None:
        return get_user_role(obj)

    def get_permissions(self, obj: User) -> list[str]:
        role = get_user_role(obj)
        if not role:
            return []
        allowed_actions = ROLE_PERMISSIONS.get(role, [])
        return [action for action in allowed_actions if has_permission(obj, action)]

    def _get_auth_session(self, obj: User) -> AuthSession | None:
        # Tối ưu hóa: Cache kết quả phiên trên instance serializer để tránh truy vấn DB 2 lần
        if not hasattr(self, "_cached_auth_session"):
            request = self.context.get("request")
            session_key = getattr(getattr(request, "session", None), "session_key", None)
            if request and session_key:
                self._cached_auth_session = AuthSession.objects.filter(
                    session_key=session_key,
                    user=obj,
                    revoked_at__isnull=True
                ).first()
            else:
                self._cached_auth_session = None
        return self._cached_auth_session

    def get_session(self, obj: User) -> dict[str, str | None] | None:
        auth_session = self._get_auth_session(obj)
        if not auth_session:
            return None
        return {
            "session_id": str(auth_session.id),
            "expires_at": auth_session.expires_at.isoformat(),
        }

    def get_expires_at(self, obj: User) -> str | None:
        auth_session = self._get_auth_session(obj)
        return auth_session.expires_at.isoformat() if auth_session else None

class UpdateProfileRequestSerializer(serializers.Serializer):
    """
    Serializer kiểm tra dữ liệu đầu vào cho PATCH /api/v1/profile/me/:
    - Chỉ cho phép duy nhất trường 'full_name' (độ dài 2-128 ký tự).
    - Chốt chặn Allowlist: Từ chối mọi trường lạ hoặc trường đặc quyền qua cấu trúc ValidationError chuẩn.
    """
    full_name = serializers.CharField(
        max_length=128,
        min_length=2,
        trim_whitespace=True,
        required=True,
        allow_blank=False,
        error_messages={
            "required": "Trường 'full_name' là bắt buộc.",
            "blank": "Họ và tên không được để trống.",
            "min_length": "Họ và tên phải có ít nhất 2 ký tự.",
            "max_length": "Họ và tên không được vượt quá 128 ký tự."
        }
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        allowed_keys = set(self.fields.keys())
        unknown_keys = set(self.initial_data.keys()) - allowed_keys
        if unknown_keys:
            raise serializers.ValidationError(
                {"non_field_errors": [f"Trường dữ liệu không được phép: {', '.join(sorted(unknown_keys))}."]}
            )
        return attrs

class ChangePasswordRequestSerializer(serializers.Serializer):
    """
    Serializer kiểm tra payload đổi mật khẩu:
    - old_password: Bắt buộc, không trim khoảng trắng.
    - new_password: Bắt buộc, độ dài 12-128 ký tự, không trim khoảng trắng.
    - Kiểm tra new_password khác old_password và thỏa mãn Django Password Validators.
    """
    old_password = serializers.CharField(
        max_length=128,
        trim_whitespace=False,
        required=True,
        write_only=True,
        error_messages={"blank": "Mật khẩu hiện tại không được để trống."}
    )
    new_password = serializers.CharField(
        max_length=128,
        min_length=12,
        trim_whitespace=False,
        required=True,
        write_only=True,
        error_messages={
            "blank": "Mật khẩu mới không được để trống.",
            "min_length": "Mật khẩu mới phải có độ dài ít nhất 12 ký tự.",
            "max_length": "Mật khẩu mới không được vượt quá 128 ký tự."
        }
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        allowed_keys = set(self.fields.keys())
        unknown_keys = set(self.initial_data.keys()) - allowed_keys
        if unknown_keys:
            raise serializers.ValidationError(
                {"non_field_errors": [f"Trường dữ liệu không được phép: {', '.join(sorted(unknown_keys))}."]}
            )

        old_pass = attrs.get("old_password", "")
        new_pass = attrs.get("new_password", "")

        if old_pass == new_pass:
            raise serializers.ValidationError(
                {"new_password": ["Mật khẩu mới không được trùng với mật khẩu hiện tại."]}
            )

        user = self.context.get("user")
        try:
            validate_password(new_pass, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)})

        return attrs