"""
Test module kiểm tra AuditLog và AuditService:
- Ghi nhận sự kiện thành công với metadata hợp lệ.
- Chặn đứng các từ khóa bí mật (password, token, secret, database_url, sessionid, csrftoken).
- Chặn đứng các chuỗi DSN kết nối, Bearer/Basic token, sessionid markers.
- Chặn số không hữu hạn (NaN, Infinity).
- Chặn nested dictionary trong metadata.
- Hợp đồng bất đồng bộ (Async 202 Contract): Cấm outcome SUCCESS cho tác vụ 202.
- Spoofing guard: USER thiếu actor_user hoặc SYSTEM truyền actor_user.
- TransactionTestCase: Chứng minh rollback trong atomic block hủy bỏ log SUCCESS,
  và caller ghi log FAILED ngoài atomic block được commit thành công xuống DB.
"""
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import SimpleTestCase, TransactionTestCase

from apps.audit.models import AuditLog
from apps.audit.services import AuditService, sanitize_metadata

User = get_user_model()

class AuditSanitizerTestCase(SimpleTestCase):
    """Kiểm tra đơn vị logic hàm sanitize_metadata và kiểm tra secret không cần DB."""

    def test_valid_metadata_passes(self):
        """Metadata chứa các khóa hợp lệ được giữ nguyên."""
        meta = {
            'username': 'operator_demo',
            'device_id': 'CABINET-01',
            'failure_count': 3,
            'fields': ['full_name', 'status']
        }
        clean = sanitize_metadata(meta)
        self.assertEqual(clean['username'], 'operator_demo')
        self.assertEqual(clean['device_id'], 'CABINET-01')
        self.assertEqual(clean['failure_count'], 3)
        self.assertEqual(clean['fields'], ['full_name', 'status'])

    def test_forbidden_keys_raise_error(self):
        """Bất kỳ khóa nào thuộc FORBIDDEN_METADATA_KEYS đều phải bị ném ValueError."""
        for bad_key in ['password', 'secret', 'token', 'database_url', 'sessionid', 'csrftoken']:
            with self.assertRaises(ValueError) as ctx:
                sanitize_metadata({bad_key: 'sensitive_val'})
            self.assertIn("Khóa nhạy cảm bị cấm", str(ctx.exception))

    def test_sensitive_dsn_string_raises_error(self):
        """Giá trị chuỗi chứa connection string DSN phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'reason': 'Connecting to postgresql://user:pass@localhost:5432/db'})
        self.assertIn("Phát hiện dữ liệu nhạy cảm", str(ctx.exception))

    def test_bearer_and_basic_token_string_raises_error(self):
        """Giá trị chuỗi chứa Bearer hoặc Basic auth token phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'reason': 'Header Bearer eyJhbGciOiJIUzI1Ni...'})
        self.assertIn("Phát hiện dữ liệu nhạy cảm", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'reason': 'Header Basic dXNlcjpwYXNz'})
        self.assertIn("Phát hiện dữ liệu nhạy cảm", str(ctx.exception))

    def test_session_cookie_string_raises_error(self):
        """Giá trị chuỗi chứa sessionid= hoặc csrftoken= phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'reason': 'Cookie sessionid=demo-cookie-value'})
        self.assertIn("Phát hiện dữ liệu nhạy cảm", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'reason': 'Cookie csrftoken=demo-csrf-token'})
        self.assertIn("Phát hiện dữ liệu nhạy cảm", str(ctx.exception))

    def test_nan_and_infinity_raise_error(self):
        """Giá trị float không hữu hạn (NaN, Inf) phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'failure_count': float('nan')})
        self.assertIn("không chấp nhận NaN hoặc Infinity", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'failure_count': float('inf')})
        self.assertIn("không chấp nhận NaN hoặc Infinity", str(ctx.exception))

    def test_nested_dict_raises_error(self):
        """Cấu trúc dictionary lồng nhau bị cấm tuyệt đối."""
        with self.assertRaises(TypeError) as ctx:
            sanitize_metadata({'error': {'nested_key': 'nested_val'}})
        self.assertIn("không được chứa dictionary lồng nhau", str(ctx.exception))

    def test_unallowed_keys_raise_error(self):
        """Khóa không nằm trong ALLOWED_METADATA_KEYS phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'unregistered_custom_key': 'value'})
        self.assertIn("Khóa không được phép", str(ctx.exception))

    def test_summary_sensitive_string_raises_error(self):
        """summary chứa DSN, Basic auth hoặc password marker phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            AuditService.record_event(
                function_key="AUTH_LOGIN",
                action="LOGIN",
                outcome="FAILED",
                summary="Lỗi kết nối postgresql://user:pass@localhost:5432/db",
                request_id=uuid.uuid4(),
                actor_type=AuditLog.ActorType.SYSTEM
            )
        self.assertIn("Phát hiện dữ liệu nhạy cảm", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            AuditService.record_event(
                function_key="AUTH_LOGIN",
                action="LOGIN",
                outcome="FAILED",
                summary="Header Authorization: Basic dXNlcjpwYXNz",
                request_id=uuid.uuid4(),
                actor_type=AuditLog.ActorType.SYSTEM
            )
        self.assertIn("Phát hiện dữ liệu nhạy cảm", str(ctx.exception))


class AuditServiceTransactionTestCase(TransactionTestCase):
    """Kiểm tra tích hợp AuditService và Transaction thực tế trên Database."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="audit_actor",
            full_name="Người Thao Tác",
            password="SecurePassword@2026!"
        )
        self.request_id = uuid.uuid4()

    def test_record_event_success(self):
        """Xác minh ghi nhận sự kiện thành công vào bảng audit_logs."""
        log = AuditService.record_event(
            function_key="AUTH_LOGIN",
            action="LOGIN",
            outcome="SUCCESS",
            summary="Đăng nhập thành công vào hệ thống",
            request_id=self.request_id,
            actor_user=self.user,
            actor_type=AuditLog.ActorType.USER,
            metadata={'username': 'audit_actor'}
        )

        self.assertIsInstance(log.id, uuid.UUID)
        self.assertEqual(log.actor_user, self.user)
        self.assertEqual(log.outcome, AuditLog.Outcome.SUCCESS)
        self.assertEqual(log.actor_name_snapshot, "Người Thao Tác")

        saved_log = AuditLog.objects.get(id=log.id)
        self.assertEqual(saved_log.function_key, "AUTH_LOGIN")
        self.assertEqual(saved_log.metadata['username'], 'audit_actor')

    def test_async_contract_rejects_success_outcome(self):
        """Xác minh thao tác bất đồng bộ (is_async=True) cấm outcome là SUCCESS."""
        with self.assertRaises(ValueError) as ctx:
            AuditService.record_event(
                function_key="DEVICE_DISPATCH",
                action="CONTROL",
                outcome="SUCCESS",
                summary="Lệnh đã gửi tới hàng đợi",
                request_id=self.request_id,
                actor_user=self.user,
                is_async=True
            )
        self.assertIn("không được phép ghi nhận outcome là SUCCESS", str(ctx.exception))

    def test_spoofing_guard_user_without_actor_raises(self):
        """actor_type là USER nhưng không truyền actor_user phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            AuditService.record_event(
                function_key="AUTH_LOGIN",
                action="LOGIN",
                outcome="DENIED",
                summary="Từ chối đăng nhập",
                request_id=self.request_id,
                actor_user=None,
                actor_type=AuditLog.ActorType.USER
            )
        self.assertIn("bắt buộc phải cung cấp đối tượng actor_user", str(ctx.exception))

    def test_spoofing_guard_system_with_user_raises(self):
        """actor_type là SYSTEM nhưng lại truyền actor_user phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            AuditService.record_event(
                function_key="SYSTEM_MAINTENANCE",
                action="UPDATE",
                outcome="SUCCESS",
                summary="Bảo trì hệ thống",
                request_id=self.request_id,
                actor_user=self.user,
                actor_type=AuditLog.ActorType.SYSTEM
            )
        self.assertIn("không được phép đính kèm actor_user", str(ctx.exception))

    def test_atomic_rollback_cancels_audit_log_inside_block(self):
        """
        Xác minh quy tắc transaction atomic: Khi khối nghiệp vụ bị lỗi rollback,
        bản ghi log trong khối đó cũng bị hủy bỏ hoàn toàn.
        """
        initial_count = AuditLog.objects.count()

        try:
            with transaction.atomic():
                AuditService.record_event(
                    function_key="USER_PASSWORD_CHANGE",
                    action="UPDATE",
                    outcome="SUCCESS",
                    summary="Đổi mật khẩu thành công",
                    request_id=self.request_id,
                    actor_user=self.user
                )
                raise RuntimeError("Lỗi mô phỏng trong bước lưu dữ liệu!")
        except RuntimeError:
            pass

        self.assertEqual(AuditLog.objects.count(), initial_count)

    def test_caller_outside_failed_atomic_persists_failure_log(self):
        """
        Xác minh kiến trúc chuẩn: Caller thoát khỏi khối atomic bị lỗi
        rồi ghi log FAILED thì bản ghi được commit thật sự xuống DB.
        """
        initial_count = AuditLog.objects.count()

        try:
            with transaction.atomic():
                raise RuntimeError("Lỗi nghiệp vụ cần rollback!")
        except RuntimeError:
            # Caller ghi nhận vết lỗi FAILED bên ngoài khối transaction bị lỗi
            AuditService.record_event(
                function_key="USER_PASSWORD_CHANGE",
                action="UPDATE",
                outcome="FAILED",
                summary="Đổi mật khẩu thất bại do lỗi hệ thống",
                request_id=self.request_id,
                actor_user=self.user,
                error_code="INTERNAL_ERROR"
            )

        self.assertEqual(AuditLog.objects.count(), initial_count + 1)
        last_log = AuditLog.objects.latest('occurred_at')
        self.assertEqual(last_log.outcome, AuditLog.Outcome.FAILED)
        self.assertEqual(last_log.error_code, "INTERNAL_ERROR")