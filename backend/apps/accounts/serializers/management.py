"""
Serializers cho API Quản trị Người dùng (Management User REST API).
Tuân thủ nghiêm ngặt allowlist trường, không nhận is_superuser, groups, user_permissions từ client.
"""
import re
from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.policies.roles import ALL_ROLES, ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, get_user_role

USERNAME_REGEX = re.compile(r'^[a-z0-9._-]{3,64}$')


class UserManagementSerializer(serializers.ModelSerializer):
    """
    Serializer hiển thị danh sách và chi tiết người dùng cho quản trị viên.
    """
    uuid = serializers.UUIDField(source='id', read_only=True)
    role = serializers.SerializerMethodField()
    accessible_devices_count = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id',
            'uuid',
            'username',
            'full_name',
            'email',
            'role',
            'status',
            'is_active',
            'must_change_password',
            'created_at',
            'last_login',
            'accessible_devices_count',
        ]
        read_only_fields = fields

    def get_role(self, obj: User) -> str:
        return get_user_role(obj)

    def get_accessible_devices_count(self, obj: User) -> int:
        role = get_user_role(obj)
        if role == ROLE_ADMIN:
            from apps.devices.models import Device
            return Device.objects.count()
        return obj.device_accesses.filter(device__status__in=['ACTIVE', 'REGISTERED']).count()


class CreateUserManagementSerializer(serializers.Serializer):
    """
    Serializer tiếp nhận yêu cầu khởi tạo tài khoản người dùng mới.
    Bắt buộc có username, full_name, role và temporary_password.
    """
    username = serializers.CharField(max_length=64, min_length=3, required=True)
    full_name = serializers.CharField(max_length=150, min_length=1, required=True)
    email = serializers.EmailField(required=False, allow_blank=True, default='')
    role = serializers.ChoiceField(choices=ALL_ROLES, default=ROLE_OPERATOR)
    status = serializers.ChoiceField(choices=['ACTIVE', 'LOCKED'], default='ACTIVE')
    temporary_password = serializers.CharField(min_length=12, max_length=128, required=True, write_only=True)

    def validate_username(self, value: str) -> str:
        cleaned = value.strip().lower()
        if not USERNAME_REGEX.match(cleaned):
            raise serializers.ValidationError(
                'Tên đăng nhập phải từ 3 đến 64 ký tự, chỉ chứa chữ thường, số, dấu chấm, gạch dưới hoặc gạch ngang.'
            )
        if User.objects.filter(username=cleaned).exists():
            raise serializers.ValidationError('Tên đăng nhập này đã tồn tại trong hệ thống.')
        return cleaned

    def validate_full_name(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError('Họ và tên không được để trống.')
        return cleaned


class UpdateUserManagementSerializer(serializers.Serializer):
    """
    Serializer tiếp nhận yêu cầu cập nhật tài khoản người dùng.
    Không cho phép thay đổi username của tài khoản đã tồn tại.
    """
    full_name = serializers.CharField(max_length=150, min_length=1, required=False)
    email = serializers.EmailField(required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=ALL_ROLES, required=False)
    status = serializers.ChoiceField(choices=['ACTIVE', 'LOCKED', 'ARCHIVED'], required=False)
    temporary_password = serializers.CharField(
        min_length=12, max_length=128, required=False, allow_blank=True, write_only=True
    )

    def validate_full_name(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError('Họ và tên không được để trống.')
        return cleaned
