"""
Test module kiểm tra các Model của accounts:
- Khóa chính UUID v4 của User.
- Chuẩn hóa username chữ thường và kiểm tra độ dài.
- Thuật toán băm mật khẩu Argon2id qua set_password() và check_password().
- Đồng bộ trường is_active với status cả khi dùng update_fields.
- Ghi đè get_full_name() và get_short_name().
- Kiểm tra tính hợp lệ đầu vào khi create_user().
- Bộ kiểm tra mật khẩu PasswordLengthValidator.
- Model AuthSession và LoginAttempt.
"""
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.accounts.models import AuthSession, LoginAttempt, User
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