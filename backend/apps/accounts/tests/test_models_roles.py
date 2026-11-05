"""
Test module kiểm tra các Model và Chính sách Vai trò của accounts:
- Khóa chính UUID v4 của User.
- Chuẩn hóa username chữ thường và kiểm tra độ dài.
- Thuật toán băm mật khẩu Argon2id qua set_password() và check_password().
- Đồng bộ trường is_active với status cả khi dùng update_fields.
- Ghi đè get_full_name() và get_short_name().
- Kiểm tra tính hợp lệ đầu vào khi create_user().
- Bộ kiểm tra mật khẩu PasswordLengthValidator.
- Model AuthSession và LoginAttempt.
- Đồng bộ và gán quyền hạn vai trò (RolePolicyTestCase: Idempotency, Single-role, staff=True/superuser=False).
- Xóa cache permission instance khi đổi vai trò (clear_user_permission_cache).
- Chống bypass qua user_permissions trực tiếp.
- Rollback an toàn khi assign_role gặp sự cố.
- seed_roles ném CommandError khi thiếu permission.
- Lệnh quản trị khởi tạo tài khoản an toàn (BootstrapAdminCommandTestCase).
"""
import os
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.accounts.models import AuthSession, LoginAttempt, User
from apps.accounts.policies.catalog import (
    ACTION_AUDIT_VIEW,
    ACTION_USER_CREATE,
)
from apps.accounts.policies.roles import (
    ALL_ROLES,
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    assign_role,
    get_user_role,
    has_permission,
    has_role,
    require_permission,
)
from apps.accounts.security.password_validators import PasswordLengthValidator


class PasswordLengthValidatorTestCase(SimpleTestCase):
    """Kiểm tra đơn vị logic của PasswordLengthValidator không cần DB."""

    def setUp(self):
        self.validator = PasswordLengthValidator(min_length=12, max_length=128)

    def test_valid_password_passes(self):
        """Mật khẩu đủ từ 12 đến 128 ký tự phải vượt qua kiểm tra."""
        self.validator.validate("Password@123456")
        self.validator.validate("A" * 12)
        self.validator.validate("A" * 128)

    def test_too_short_password_fails(self):
        """Mật khẩu dưới 12 ký tự phải bị ném ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            self.validator.validate("Short@123")
        self.assertIn("Mật khẩu quá ngắn", str(ctx.exception))

    def test_too_long_password_fails(self):
        """Mật khẩu trên 128 ký tự phải bị ném ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            self.validator.validate("A" * 129)
        self.assertIn("Mật khẩu quá dài", str(ctx.exception))

    def test_password_is_not_trimmed(self):
        """Khoảng trắng đầu cuối không bị trim: '   123456   ' có độ dài 12 ký tự."""
        self.validator.validate("   123456   ")


class UserModelTestCase(TestCase):
    """Kiểm tra tích hợp Model User và các quan hệ trên Database."""

    def test_create_user_with_uuid_and_argon2(self):
        """Xác minh User được cấp khóa chính UUID và mật khẩu được băm bằng Argon2id."""
        user = User.objects.create_user(
            username="Test_Admin_01",
            full_name="Nguyễn Văn Quản Trị",
            password="SecurePassword@2026!"
        )

        # 1. Khóa chính phải là UUID v4 hợp lệ
        self.assertIsInstance(user.id, uuid.UUID)

        # 2. Username phải được chuyển thành chữ thường (canonicalized)
        self.assertEqual(user.username, "test_admin_01")

        # 3. Chuỗi băm mật khẩu phải sử dụng Argon2
        self.assertTrue(user.password.startswith("argon2"), f"Hash không dùng Argon2: {user.password}")
        self.assertTrue(user.check_password("SecurePassword@2026!"))
        self.assertFalse(user.check_password("WrongPassword@2026!"))

        # 4. Trạng thái ACTIVE thì is_active phải là True
        self.assertEqual(user.status, User.Status.ACTIVE)
        self.assertTrue(user.is_active)

    def test_get_full_name_and_short_name(self):
        """Xác minh get_full_name() và get_short_name() trả về đúng tên chứ không phải 'None None'."""
        user = User.objects.create_user(
            username="operator_demo",
            full_name="Nguyễn Văn An",
            password="OperatorPassword@2026!"
        )
        self.assertEqual(user.get_full_name(), "Nguyễn Văn An")
        self.assertEqual(user.get_short_name(), "Nguyễn")

    def test_create_user_rejects_invalid_username_length(self):
        """Xác minh create_user từ chối username ngắn hơn 3 ký tự."""
        with self.assertRaises(ValidationError):
            User.objects.create_user(
                username="a",
                full_name="Người Dùng Lỗi",
                password="ValidPassword@2026!"
            )

    def test_create_user_rejects_empty_or_whitespace_fullname(self):
        """Xác minh create_user từ chối họ tên toàn khoảng trắng."""
        with self.assertRaises(ValueError):
            User.objects.create_user(
                username="valid_user",
                full_name="    ",
                password="ValidPassword@2026!"
            )

    def test_create_user_rejects_short_password(self):
        """Xác minh create_user kích hoạt validator từ chối mật khẩu quá ngắn."""
        with self.assertRaises(ValidationError):
            User.objects.create_user(
                username="valid_user",
                full_name="Người Dùng Hợp Lệ",
                password="short"
            )

    def test_status_and_is_active_sync_with_update_fields_and_refresh(self):
        """
        Xác minh khóa tài khoản với save(update_fields=['status'])
        vẫn đồng bộ is_active=False xuống PostgreSQL và kiểm tra lại bằng refresh_from_db().
        """
        user = User.objects.create_user(
            username="operator_lock_test",
            full_name="Nhân viên vận hành",
            password="OperatorPassword@2026!"
        )
        self.assertTrue(user.is_active)

        # Khóa tài khoản sử dụng update_fields
        user.status = User.Status.LOCKED
        user.save(update_fields=['status'])

        # Nạp lại bản ghi từ PostgreSQL để chứng minh DB thật đã được cập nhật
        user.refresh_from_db()
        self.assertEqual(user.status, User.Status.LOCKED)
        self.assertFalse(user.is_active)

        # Mở khóa tài khoản lại
        user.status = User.Status.ACTIVE
        user.save(update_fields=['status'])
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_auth_session_lifecycle(self):
        """Xác minh vòng đời và cờ is_active của AuthSession."""
        user = User.objects.create_user(
            username="session_test_user",
            full_name="Người Dùng Test Session",
            password="ValidPassword@2026!"
        )
        now = timezone.now()
        session = AuthSession.objects.create(
            user=user,
            session_key="django_session_key_sample_123456",
            expires_at=now + timedelta(minutes=15),
            ip_address="127.0.0.1",
            user_agent="Mozilla/5.0 TestBrowser"
        )

        self.assertIsInstance(session.id, uuid.UUID)
        self.assertTrue(session.is_active)

        # Thu hồi session
        session.revoked_at = timezone.now()
        session.revoke_reason = "USER_LOGOUT"
        session.save()
        self.assertFalse(session.is_active)

    def test_login_attempt_record(self):
        """Xác minh tạo bản ghi LoginAttempt với HMAC key và UUID request ID."""
        attempt = LoginAttempt.objects.create(
            username_key="a" * 64,
            ip_key="b" * 64,
            success=False,
            failure_code="INVALID_CREDENTIALS"
        )
        self.assertIsInstance(attempt.id, uuid.UUID)
        self.assertIsInstance(attempt.request_id, uuid.UUID)
        self.assertFalse(attempt.success)
        self.assertEqual(attempt.failure_code, "INVALID_CREDENTIALS")


class RolePolicyTestCase(TestCase):
    """Kiểm tra ma trận phân quyền RBAC, tính idempotent của seed_roles và quy tắc Single-role."""

    def setUp(self):
        # Đồng bộ vai trò và quyền hạn trước mỗi test
        call_command("seed_roles", verbosity=0)

        # Tạo sẵn một tài khoản Admin và một tài khoản Operator
        self.admin_user = User.objects.create_user(
            username="admin_rbac_test",
            full_name="Quản Trị Viên RBAC",
            password="AdminPassword@2026!"
        )
        assign_role(self.admin_user, ROLE_ADMIN)

        self.operator_user = User.objects.create_user(
            username="operator_rbac_test",
            full_name="Vận Hành Viên RBAC",
            password="OperatorPassword@2026!"
        )
        assign_role(self.operator_user, ROLE_OPERATOR)

    def test_seed_roles_idempotency(self):
        """Xác minh chạy seed_roles nhiều lần không làm trùng lặp nhóm hay quyền hạn."""
        call_command("seed_roles", verbosity=0)
        call_command("seed_roles", verbosity=0)
        self.assertEqual(Group.objects.filter(name__in=ALL_ROLES).count(), 3)

    def test_seed_roles_assigns_correct_permissions(self):
        """Xác minh ADMIN được gán đúng 7 quyền, OPERATOR và VIEWER rỗng quyền trong chặng 03."""
        admin_group = Group.objects.get(name=ROLE_ADMIN)
        operator_group = Group.objects.get(name=ROLE_OPERATOR)
        viewer_group = Group.objects.get(name=ROLE_VIEWER)

        admin_perms = set(admin_group.permissions.values_list("codename", flat=True))
        expected_admin_perms = {
            "add_user", "view_user", "change_user", "delete_user",
            "view_authsession", "change_authsession", "view_auditlog"
        }
        self.assertEqual(admin_perms, expected_admin_perms)
        self.assertEqual(operator_group.permissions.count(), 0)
        self.assertEqual(viewer_group.permissions.count(), 0)

    def test_seed_roles_raises_command_error_when_permission_missing(self):
        """Xác minh seed_roles ném CommandError và rollback khi thiếu permission trong DB."""
        perm = Permission.objects.filter(content_type__app_label="accounts", codename="add_user").first()
        self.assertIsNotNone(perm)
        perm.delete()

        with self.assertRaises(CommandError) as ctx:
            call_command("seed_roles", verbosity=0)
        self.assertIn("Thiếu permission bắt buộc 'accounts.add_user'", str(ctx.exception))

    def test_assign_role_and_get_user_role(self):
        """Xác minh get_user_role() và has_role() phản ánh chính xác vai trò được gán."""
        self.assertEqual(get_user_role(self.admin_user), ROLE_ADMIN)
        self.assertTrue(has_role(self.admin_user, ROLE_ADMIN))
        self.assertFalse(has_role(self.admin_user, ROLE_OPERATOR))

        self.assertEqual(get_user_role(self.operator_user), ROLE_OPERATOR)
        self.assertTrue(has_role(self.operator_user, ROLE_OPERATOR))
        self.assertFalse(has_role(self.operator_user, ROLE_ADMIN))

    def test_assign_role_switches_role_cleanly_enforcing_single_role(self):
        """
        Xác minh quy tắc Single-role: Khi đổi vai trò, nhóm vai trò cũ phải bị gỡ bỏ,
        và cờ is_staff được đồng bộ tự động.
        """
        user = User.objects.create_user(
            username="switch_role_user",
            full_name="Người Dùng Đổi Role",
            password="ValidPassword@2026!"
        )
        assign_role(user, ROLE_ADMIN)
        self.assertEqual(user.groups.count(), 1)
        self.assertEqual(get_user_role(user), ROLE_ADMIN)
        self.assertTrue(user.is_staff)

        # Chuyển sang OPERATOR -> nhóm ADMIN bị gỡ bỏ, is_staff chuyển thành False
        assign_role(user, ROLE_OPERATOR)
        user.refresh_from_db()
        self.assertEqual(user.groups.count(), 1)
        self.assertEqual(get_user_role(user), ROLE_OPERATOR)
        self.assertFalse(user.is_staff)

    def test_assign_role_clears_instance_permission_cache(self):
        """
        Xác minh đổi vai trò trên cùng một instance User lập tức có hiệu lực,
        xóa sạch permission cache trong bộ nhớ mà không giữ quyền cũ.
        """
        user = User.objects.create_user(
            username="cache_clear_user",
            full_name="Người Dùng Xóa Cache",
            password="ValidPassword@2026!"
        )
        assign_role(user, ROLE_ADMIN)
        # Nạp cache permission của Admin vào instance
        self.assertTrue(has_permission(user, ACTION_USER_CREATE))

        # Hạ vai trò sang OPERATOR trên CHÍNH INSTANCE ĐÓ
        assign_role(user, ROLE_OPERATOR)

        # Instance phải bị tước quyền ngay lập tức
        self.assertFalse(has_permission(user, ACTION_USER_CREATE))
        with self.assertRaises(PermissionDenied):
            require_permission(user, ACTION_USER_CREATE)

    def test_assign_role_atomic_rollback_on_save_failure(self):
        """Xác minh tính nguyên tử: Nếu save() thất bại, nhóm vai trò mới không được lưu."""
        user = User.objects.create_user(
            username="atomic_rollback_user",
            full_name="Người Dùng Atomic",
            password="ValidPassword@2026!"
        )
        assign_role(user, ROLE_OPERATOR)
        self.assertEqual(get_user_role(user), ROLE_OPERATOR)

        with patch.object(User, "save", side_effect=RuntimeError("Simulated DB Crash")):
            with self.assertRaises(RuntimeError):
                assign_role(user, ROLE_ADMIN)

        # Dữ liệu trong DB vẫn bảo toàn vai trò OPERATOR ban đầu
        user_db = User.objects.get(id=user.id)
        self.assertEqual(get_user_role(user_db), ROLE_OPERATOR)
        self.assertFalse(user_db.is_staff)

    def test_multi_role_conflict_returns_none_and_denies_permission(self):
        """Xác minh tài khoản có >= 2 role (xung đột) bị coi là None và từ chối mọi quyền."""
        user = User.objects.create_user(
            username="multi_role_conflict_user",
            full_name="Người Dùng Xung Đột Role",
            password="ValidPassword@2026!"
        )
        user.groups.add(Group.objects.get(name=ROLE_ADMIN))
        user.groups.add(Group.objects.get(name=ROLE_OPERATOR))

        self.assertIsNone(get_user_role(user))
        self.assertFalse(has_role(user, ROLE_ADMIN))
        self.assertFalse(has_role(user, ROLE_OPERATOR))
        self.assertFalse(has_permission(user, ACTION_USER_CREATE))
        with self.assertRaises(PermissionDenied):
            require_permission(user, ACTION_USER_CREATE)

    def test_direct_user_permissions_bypass_is_blocked(self):
        """Xác minh người dùng không có role nhưng được gán quyền trực tiếp vẫn bị từ chối."""
        user = User.objects.create_user(
            username="direct_permission_user",
            full_name="Người Dùng Gán Quyền Trực Tiếp",
            password="ValidPassword@2026!"
        )
        perm = Permission.objects.get(codename="add_user")
        user.user_permissions.add(perm)

        self.assertIsNone(get_user_role(user))
        self.assertFalse(has_permission(user, ACTION_USER_CREATE))
        with self.assertRaises(PermissionDenied):
            require_permission(user, ACTION_USER_CREATE)

    def test_admin_has_staff_true_and_superuser_false(self):
        """Xác minh Rule 3: ADMIN có is_staff=True nhưng is_superuser=False."""
        self.assertTrue(self.admin_user.is_staff)
        self.assertFalse(self.admin_user.is_superuser)

    def test_has_permission_and_require_permission_for_admin(self):
        """Xác minh ADMIN có đủ quyền thực thi các hành động quản trị User và xem AuditLog."""
        admin = User.objects.get(id=self.admin_user.id)
        self.assertTrue(has_permission(admin, ACTION_USER_CREATE))
        self.assertTrue(has_permission(admin, ACTION_AUDIT_VIEW))
        # require_permission không được ném exception
        require_permission(admin, ACTION_USER_CREATE)
        require_permission(admin, ACTION_AUDIT_VIEW)

    def test_require_permission_denied_for_operator(self):
        """Xác minh OPERATOR bị từ chối quyền quản trị User và AuditLog, ném PermissionDenied."""
        operator = User.objects.get(id=self.operator_user.id)
        self.assertFalse(has_permission(operator, ACTION_USER_CREATE))
        self.assertFalse(has_permission(operator, ACTION_AUDIT_VIEW))
        with self.assertRaises(PermissionDenied):
            require_permission(operator, ACTION_USER_CREATE)
        with self.assertRaises(PermissionDenied):
            require_permission(operator, ACTION_AUDIT_VIEW)

    def test_require_permission_denied_for_inactive_or_anonymous_user(self):
        """Xác minh tài khoản bị khóa (is_active=False) hoặc AnonymousUser đều bị từ chối quyền."""
        admin = User.objects.get(id=self.admin_user.id)
        admin.status = User.Status.LOCKED
        admin.save(update_fields=["status"])
        admin.refresh_from_db()
        self.assertFalse(admin.is_active)
        self.assertFalse(has_permission(admin, ACTION_USER_CREATE))
        with self.assertRaises(PermissionDenied):
            require_permission(admin, ACTION_USER_CREATE)

        anon = AnonymousUser()
        self.assertFalse(has_permission(anon, ACTION_USER_CREATE))
        with self.assertRaises(PermissionDenied):
            require_permission(anon, ACTION_USER_CREATE)


class BootstrapAdminCommandTestCase(TestCase):
    """Kiểm tra hoạt động của command bootstrap_admin, tính an toàn và tính idempotent."""

    def test_bootstrap_admin_creates_active_admin_successfully(self):
        """Xác minh khởi tạo thành công tài khoản admin với đầy đủ thuộc tính chuẩn."""
        with patch.dict(os.environ, {"BOOTSTRAP_ADMIN_PASSWORD": "SuperSecureAdminPassword@2026!"}):
            call_command("bootstrap_admin", username="new_admin_01", no_input=True)

        user = User.objects.get(username="new_admin_01")
        self.assertTrue(user.is_active)
        self.assertEqual(user.status, User.Status.ACTIVE)
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(get_user_role(user), ROLE_ADMIN)
        self.assertTrue(user.check_password("SuperSecureAdminPassword@2026!"))

    def test_bootstrap_admin_idempotency_does_not_overwrite_password(self):
        """Xác minh chạy lại bootstrap_admin khi user đã có sẵn sẽ không ghi đè mật khẩu."""
        with patch.dict(os.environ, {"BOOTSTRAP_ADMIN_PASSWORD": "OriginalPassword@2026!"}):
            call_command("bootstrap_admin", username="idempotent_admin", no_input=True)

        user_original = User.objects.get(username="idempotent_admin")
        original_hash = user_original.password

        # Chạy lại với mật khẩu mới giả lập
        with patch.dict(os.environ, {"BOOTSTRAP_ADMIN_PASSWORD": "ChangedPassword@2026!"}):
            call_command("bootstrap_admin", username="idempotent_admin", no_input=True)

        user_after = User.objects.get(username="idempotent_admin")
        self.assertEqual(user_after.password, original_hash)
        self.assertTrue(user_after.check_password("OriginalPassword@2026!"))
        self.assertFalse(user_after.check_password("ChangedPassword@2026!"))

    def test_bootstrap_admin_rejects_invalid_username(self):
        """Xác minh từ chối username không đạt chuẩn canonical (ngắn hơn 3 ký tự hoặc chứa ký tự cấm)."""
        with patch.dict(os.environ, {"BOOTSTRAP_ADMIN_PASSWORD": "SuperSecureAdminPassword@2026!"}):
            with self.assertRaises(CommandError) as ctx:
                call_command("bootstrap_admin", username="a", no_input=True)
            self.assertIn("không hợp lệ", str(ctx.exception))

            with self.assertRaises(CommandError) as ctx:
                call_command("bootstrap_admin", username="bad username with space", no_input=True)
            self.assertIn("không hợp lệ", str(ctx.exception))

    def test_bootstrap_admin_rejects_short_password(self):
        """Xác minh từ chối mật khẩu không đáp ứng chính sách bảo mật (ngắn hơn 12 ký tự)."""
        with patch.dict(os.environ, {"BOOTSTRAP_ADMIN_PASSWORD": "short"}):
            with self.assertRaises(CommandError) as ctx:
                call_command("bootstrap_admin", username="valid_admin_test", no_input=True)
            self.assertIn("Mật khẩu không đáp ứng chính sách bảo mật", str(ctx.exception))