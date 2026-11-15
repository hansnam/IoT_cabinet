"""
Authentication Serializers Module
Xác thực dữ liệu đầu vào cho quy trình đăng nhập và định dạng phản hồi:
- LoginRequestSerializer: Kiểm tra username và mật khẩu (tuyệt đối không trim khoảng trắng mật khẩu).
- UserSummarySerializer: Chuẩn hóa thông tin người dùng và vai trò trả về cho frontend.
"""
from typing import ClassVar

from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.policies.roles import get_user_role

USERNAME_REGEX = r'^[a-zA-Z0-9._-]{3,64}$'

class LoginRequestSerializer(serializers.Serializer):
    """
    Serializer kiểm tra payload gửi lên tại endpoint /api/v1/auth/login/.
    """
    username = serializers.RegexField(
        regex=USERNAME_REGEX,
        max_length=64,
        trim_whitespace=True,
        required=True,
        error_messages={
            "blank": "Username không được để trống.",
            "invalid": "Tên đăng nhập không đúng định dạng (chỉ cho phép 3-64 ký tự gồm chữ cái, chữ số, dấu chấm, gạch dưới và gạch ngang)."
        }
    )
    password = serializers.CharField(
        max_length=128,
        trim_whitespace=False,  # Bắt buộc False: Bảo toàn khoảng trắng của mật khẩu
        required=True,
        write_only=True,
        error_messages={"blank": "Mật khẩu không được để trống."}
    )

class UserSummarySerializer(serializers.ModelSerializer):
    """
    Serializer định dạng thông tin người dùng cơ bản trả về sau khi đăng nhập.
    """
    role = serializers.SerializerMethodField()


    class Meta:
        model = User
        fields: ClassVar[list[str]] = [ 
            "id",
            "username",
            "full_name",
            "email",
            "role",
            "status",
            "must_change_password",
        ]
        read_only_fields = fields

    def get_role(self, obj: User) -> str | None:
        """Lấy vai trò duy nhất của người dùng qua policy get_user_role."""
        return get_user_role(obj)