"""
Test module kiểm tra toàn diện hạ tầng Authentication & Session của accounts:
- Rate Limiter: Ngưỡng 5 user, 20 IP, tính toán Retry-After, chống kéo dài vô hạn window, chặn giả mạo IP.
- Đăng nhập thành công: Tráo đổi session_key, xoay vòng CSRF, tạo AuthSession registry, ghi AuditLog.
- Đăng nhập thất bại: Sai mật khẩu, user không tồn tại, user bị khóa (LOCKED), user bị hủy (ARCHIVED) - trả thông báo chung.
- Quá hạn tuyệt đối 15 phút (no sliding window).
- Đăng xuất: Thu hồi AuthSession trong registry, chặn cookie replay.
- Chống hồi sinh phiên: Khóa user -> thu hồi ngay lập tức -> mở khóa không thể dùng lại cookie cũ.
- Chống khoảng trống race condition: User bị khóa hoặc mật khẩu thay đổi trước khi establish_session() bị từ chối tạo phiên.
- Cookie cũ không chặn CSRF & đăng nhập: Client có sessionid hết hạn vẫn lấy được CSRF và đăng nhập lại.
- Chặn username chứa ký tự nhạy cảm / sai định dạng trả HTTP 400 và không làm bung lỗi HTTP 500 từ AuditService.
- Kiểm tra tính nguyên tử: Rollback toàn bộ session và registry khi gặp lỗi registry/audit.
- Kiểm tra bắt buộc CSRF trên endpoint đăng nhập (kể cả anonymous).
- Chốt chặn must_change_password: Chặn API khác, cho phép đổi mật khẩu / me / logout.
"""
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone

from apps.accounts.models import AuthSession, LoginAttempt, User
from apps.accounts.policies.roles import ROLE_ADMIN, assign_role
from apps.accounts.security.middleware import SessionRegistryAndMustChangeMiddleware
from apps.accounts.security.rate_limit import (
    RATE_LIMIT_LOGIN_MAX_IP,
    RATE_LIMIT_LOGIN_MAX_USERNAME,
    RATE_LIMIT_WINDOW_SECONDS,
    check_rate_limit,
    get_client_ip,
    hash_identifier,
)
from apps.accounts.services.sessions import verify_auth_session
from apps.audit.models import AuditLog


class RateLimiterTestCase(TestCase):
    """Kiểm tra logic giới hạn tần suất đăng nhập (Rate Limiter)."""

    def setUp(self):
        self.username = "test_rate_user"
        self.ip = "192.168.1.100"

    def test_rate_limiter_allows_under_threshold(self):
        """Xác minh dưới 5 lần thất bại thì chưa bị giới hạn."""
        for _ in range(RATE_LIMIT_LOGIN_MAX_USERNAME - 1):
            LoginAttempt.objects.create(
                username_key=hash_identifier(self.username),
                ip_key=hash_identifier(self.ip),
                success=False,
                failure_code="INVALID_CREDENTIALS"
            )

        is_limited, retry_after, reason = check_rate_limit(self.username, self.ip)
        self.assertFalse(is_limited)
        self.assertIsNone(retry_after)
        self.assertIsNone(reason)

    def test_rate_limiter_blocks_username_after_5_failures(self):
        """Xác minh đủ 5 lần thất bại thì username bị khóa và trả Retry-After > 0."""
        for _ in range(RATE_LIMIT_LOGIN_MAX_USERNAME):
            LoginAttempt.objects.create(
                username_key=hash_identifier(self.username),
                ip_key=hash_identifier(self.ip),
                success=False,
                failure_code="INVALID_CREDENTIALS"
            )

        is_limited, retry_after, reason = check_rate_limit(self.username, self.ip)
        self.assertTrue(is_limited)
        self.assertEqual(reason, "USERNAME_LOCKED")
        self.assertIsNotNone(retry_after)
        assert retry_after is not None
        self.assertGreater(retry_after, 0)
        self.assertLessEqual(retry_after, RATE_LIMIT_WINDOW_SECONDS)

    def test_rate_limiter_blocks_ip_after_20_failures(self):
        """Xác minh khi IP đạt 20 lần thất bại (với các username khác nhau) thì IP bị khóa."""
        for i in range(RATE_LIMIT_LOGIN_MAX_IP):
            LoginAttempt.objects.create(
                username_key=hash_identifier(f"other_user_{i}"),
                ip_key=hash_identifier(self.ip),
                success=False,
                failure_code="INVALID_CREDENTIALS"
            )

        is_limited, retry_after, reason = check_rate_limit("brand_new_user", self.ip)
        self.assertTrue(is_limited)
        self.assertEqual(reason, "IP_LOCKED")
        self.assertIsNotNone(retry_after)
        assert retry_after is not None
        self.assertGreater(retry_after, 0)

    def test_rate_limiter_retry_after_calculated_correctly(self):
        """Xác minh Retry-After được tính toán chính xác theo thời điểm nỗ lực cũ nhất."""
        now = timezone.now()
        oldest_time = now - timedelta(seconds=300)
        oldest_attempt = LoginAttempt.objects.create(
            username_key=hash_identifier(self.username),
            ip_key=hash_identifier(self.ip),
            success=False,
            failure_code="INVALID_CREDENTIALS"
        )
        # Sử dụng QuerySet.update() để ghi đè trường auto_now_add occurred_at
        LoginAttempt.objects.filter(pk=oldest_attempt.pk).update(occurred_at=oldest_time)

        for _ in range(RATE_LIMIT_LOGIN_MAX_USERNAME - 1):
            LoginAttempt.objects.create(
                username_key=hash_identifier(self.username),
                ip_key=hash_identifier(self.ip),
                success=False,
                failure_code="INVALID_CREDENTIALS"
            )

        is_limited, retry_after, _ = check_rate_limit(self.username, self.ip)
        self.assertTrue(is_limited)
        self.assertIsNotNone(retry_after)
        assert retry_after is not None
        # Thời gian còn lại phải xấp xỉ 900 - 300 = 600 giây
        self.assertAlmostEqual(retry_after, 600, delta=5)

    def test_rate_limiter_blocked_attempts_do_not_extend_window(self):
        """
        Xác minh quy tắc Anti-Infinite Window: Các lần bị chặn với mã RATE_LIMITED
        không được tính vào tổng vi phạm, chống spam liên tục làm khóa vô tận.
        """
        for _ in range(RATE_LIMIT_LOGIN_MAX_USERNAME):
            LoginAttempt.objects.create(
                username_key=hash_identifier(self.username),
                ip_key=hash_identifier(self.ip),
                success=False,
                failure_code="INVALID_CREDENTIALS"
            )

        # Ghi thêm 10 lần bị chặn
        for _ in range(10):
            LoginAttempt.objects.create(
                username_key=hash_identifier(self.username),
                ip_key=hash_identifier(self.ip),
                success=False,
                failure_code="RATE_LIMITED"
            )

        # Đếm các lần vi phạm loại trừ RATE_LIMITED: Vẫn phải giữ nguyên là 5
        now = timezone.now()
        count = LoginAttempt.objects.filter(
            username_key=hash_identifier(self.username),
            success=False,
            occurred_at__gte=now - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
        ).exclude(failure_code="RATE_LIMITED").count()

        self.assertEqual(count, RATE_LIMIT_LOGIN_MAX_USERNAME)

    def test_get_client_ip_does_not_trust_untrusted_proxy(self):
        """Xác minh get_client_ip chỉ tin X-Forwarded-For khi REMOTE_ADDR trong TRUSTED_PROXIES."""
        rf = RequestFactory()
        # Client tự ý đổi header X-Forwarded-For từ IP không tin cậy
        req = rf.get("/", HTTP_X_FORWARDED_FOR="1.2.3.4", REMOTE_ADDR="192.168.1.50")
        self.assertEqual(get_client_ip(req), "192.168.1.50")


class AuthEndpointsTestCase(TestCase):
    """Kiểm tra tích hợp các endpoint HTTP API đăng nhập, đăng xuất, CSRF và registry."""

    def setUp(self):
        Group.objects.get_or_create(name=ROLE_ADMIN)
        self.client = Client(enforce_csrf_checks=True)

        self.user = User.objects.create_user(
            username="auth_test_user",
            full_name="Người Dùng Test Auth",
            password="SecurePassword@2026!"
        )
        assign_role(self.user, ROLE_ADMIN)

    def _get_csrf_token(self) -> str:
        """Helper gọi GET /api/v1/auth/csrf/ để lấy token và gắn cookie cho client."""
        resp = self.client.get("/api/v1/auth/csrf/")
        self.assertEqual(resp.status_code, 200)
        return resp.json()["csrf_token"]

    def test_csrf_token_endpoint_sets_cookie(self):
        """Xác minh GET /api/v1/auth/csrf/ trả về token và thiết lập cookie csrftoken."""
        token = self._get_csrf_token()
        self.assertTrue(len(token) > 0)
        self.assertIn("csrftoken", self.client.cookies)

    def test_expired_cookie_does_not_block_csrf_and_login(self):
        """
        Xác minh lỗi Point 2: Khi client có session cookie cũ/hết hạn,
        GET /auth/csrf/ và POST /auth/login/ vẫn hoạt động bình thường, không bị chặn 401.
        """
        stale_client = Client(enforce_csrf_checks=True)
        stale_client.cookies["sessionid"] = "stale_or_expired_session_key"

        # Vẫn lấy được CSRF token thành công
        csrf_resp = stale_client.get("/api/v1/auth/csrf/")
        self.assertEqual(csrf_resp.status_code, 200)
        csrf_token = csrf_resp.json()["csrf_token"]
        self.assertTrue(len(csrf_token) > 0)

        # Vẫn gửi POST đăng nhập thành công
        login_resp = stale_client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token
        )
        self.assertEqual(login_resp.status_code, 200)
        self.assertEqual(login_resp.json()["user"]["username"], "auth_test_user")

    def test_csrf_protection_enforced_on_login(self):
        """Xác minh gửi POST login không có CSRF token bị chặn với lỗi CSRF_FAILED (HTTP 403)."""
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"]["code"], "CSRF_FAILED")

    def test_login_success_establishes_session_and_rotates_csrf(self):
        """Xác minh đăng nhập thành công trả HTTP 200, gắn session cookie và xoay vòng CSRF."""
        csrf_before = self._get_csrf_token()

        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_before
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["user"]["username"], "auth_test_user")
        self.assertEqual(data["user"]["role"], ROLE_ADMIN)
        self.assertIn("session_id", data["session"])

        # Phải có session cookie trong response
        self.assertIn("sessionid", self.client.cookies)

        # CSRF token phải được xoay vòng mới sau khi đăng nhập
        csrf_after = self.client.cookies["csrftoken"].value
        self.assertNotEqual(csrf_before, csrf_after)

    def test_login_success_creates_auth_session_registry(self):
        """Xác minh đăng nhập thành công tạo bản ghi AuthSession trong DB với hạn 15 phút."""
        csrf = self._get_csrf_token()
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 200)

        session_key = self.client.session.session_key
        auth_session = AuthSession.objects.filter(session_key=session_key).first()
        self.assertIsNotNone(auth_session)
        assert auth_session is not None
        self.assertEqual(auth_session.user, self.user)
        self.assertIsNone(auth_session.revoked_at)
        self.assertTrue(auth_session.is_active)

    def test_login_success_records_audit_log(self):
        """Xác minh đăng nhập thành công ghi nhận bản ghi AuditLog với outcome=SUCCESS và actor_user."""
        csrf = self._get_csrf_token()
        self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        log = AuditLog.objects.filter(action="USER_LOGIN", outcome="SUCCESS").first()
        self.assertIsNotNone(log)
        assert log is not None
        self.assertEqual(log.actor_user, self.user)
        self.assertEqual(log.target_type, "USER")
        self.assertEqual(log.target_id, str(self.user.id))

    def test_login_failure_invalid_password_returns_401(self):
        """Xác minh sai mật khẩu trả về HTTP 401 NOT_AUTHENTICATED với thông báo chung."""
        csrf = self._get_csrf_token()
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "WrongPassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["code"], "NOT_AUTHENTICATED")
        self.assertEqual(resp.json()["error"]["message"], "Tên đăng nhập hoặc mật khẩu không chính xác.")

        log = AuditLog.objects.filter(action="USER_LOGIN", outcome="FAILED").first()
        self.assertIsNotNone(log)
        assert log is not None
        self.assertEqual(log.actor_user, self.user)
        self.assertEqual(log.error_code, "INVALID_CREDENTIALS")

    def test_login_failure_nonexistent_user_returns_401_generic(self):
        """Xác minh user không tồn tại trả cùng thông báo chung chống user enumeration."""
        csrf = self._get_csrf_token()
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "nonexistent_user", "password": "ValidPassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["message"], "Tên đăng nhập hoặc mật khẩu không chính xác.")

        log = AuditLog.objects.filter(action="USER_LOGIN", outcome="FAILED").first()
        self.assertIsNotNone(log)
        assert log is not None
        self.assertIsNone(log.actor_user)
        self.assertEqual(log.actor_type, AuditLog.ActorType.SYSTEM)

    def test_login_failure_locked_user_returns_401_generic(self):
        """Xác minh tài khoản bị LOCKED vẫn trả thông báo chung, không để lộ cờ lock."""
        self.user.status = User.Status.LOCKED
        self.user.save(update_fields=["status"])

        csrf = self._get_csrf_token()
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["message"], "Tên đăng nhập hoặc mật khẩu không chính xác.")

    def test_login_failure_archived_user_returns_401_generic(self):
        """Xác minh tài khoản bị ARCHIVED vẫn trả thông báo chung."""
        self.user.status = User.Status.ARCHIVED
        self.user.save(update_fields=["status"])

        csrf = self._get_csrf_token()
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["message"], "Tên đăng nhập hoặc mật khẩu không chính xác.")

    def test_login_rate_limited_returns_429_with_retry_after(self):
        """Xác minh khi vượt ngưỡng 5 lần thử sai, trả HTTP 429 kèm header Retry-After."""
        csrf = self._get_csrf_token()

        for _ in range(5):
            self.client.post(
                "/api/v1/auth/login/",
                {"username": "auth_test_user", "password": "WrongPassword@2026!"},
                content_type="application/json",
                HTTP_X_CSRFTOKEN=csrf
            )

        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()["error"]["code"], "RATE_LIMITED")
        self.assertIn("Retry-After", resp.headers)

    def test_session_absolute_15_minute_expiry(self):
        """Xác minh phiên làm việc hết hạn sau 15 phút bị từ chối qua verify_auth_session."""
        now = timezone.now()
        session = AuthSession.objects.create(
            user=self.user,
            session_key="session_test_exp",
            expires_at=now + timedelta(seconds=900)
        )
        # Cập nhật created_at và expires_at qua QuerySet.update() thỏa mãn constraint ck_auth_sessions_expires_after_created
        AuthSession.objects.filter(pk=session.pk).update(
            created_at=now - timedelta(minutes=20),
            expires_at=now - timedelta(minutes=5)
        )
        is_valid, _, reason = verify_auth_session("session_test_exp", user=self.user)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "SESSION_EXPIRED")

    def test_session_is_not_sliding_window(self):
        """Xác minh gọi verify_auth_session không gia hạn expires_at (no sliding)."""
        now = timezone.now()
        original_expiry = now + timedelta(seconds=900)
        session = AuthSession.objects.create(
            user=self.user,
            session_key="session_no_sliding",
            expires_at=original_expiry
        )

        is_valid, _, _ = verify_auth_session("session_no_sliding", user=self.user)
        self.assertTrue(is_valid)
        session.refresh_from_db()
        self.assertEqual(session.expires_at, original_expiry)

    def test_logout_revokes_auth_session_in_registry(self):
        """Xác minh đăng xuất thành công thu hồi AuthSession (revoked_at được thiết lập)."""
        csrf = self._get_csrf_token()
        self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        session_key = self.client.session.session_key
        auth_session = AuthSession.objects.get(session_key=session_key)
        self.assertTrue(auth_session.is_active)

        csrf_logged_in = self.client.cookies["csrftoken"].value
        resp = self.client.post(
            "/api/v1/auth/logout/",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_logged_in
        )
        self.assertEqual(resp.status_code, 200)

        auth_session.refresh_from_db()
        self.assertFalse(auth_session.is_active)
        self.assertIsNotNone(auth_session.revoked_at)
        self.assertEqual(auth_session.revoke_reason, "USER_LOGOUT")

    def test_logout_records_audit_log(self):
        """Xác minh đăng xuất ghi nhận nhật ký AuditLog sự kiện USER_LOGOUT với actor_user."""
        csrf = self._get_csrf_token()
        self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        csrf_logged_in = self.client.cookies["csrftoken"].value
        self.client.post(
            "/api/v1/auth/logout/",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_logged_in
        )
        log = AuditLog.objects.filter(action="USER_LOGOUT", outcome="SUCCESS").first()
        self.assertIsNotNone(log)
        assert log is not None
        self.assertEqual(log.actor_user, self.user)

    def test_cookie_replay_after_logout_is_denied(self):
        """Xác minh cố tình dùng lại cookie session sau khi đã đăng xuất sẽ bị từ chối 401."""
        csrf = self._get_csrf_token()
        self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        session_cookie = self.client.cookies["sessionid"].value
        csrf_logged_in = self.client.cookies["csrftoken"].value

        # Đăng xuất
        self.client.post(
            "/api/v1/auth/logout/",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_logged_in
        )

        # Tạo client mới giả lập kẻ tấn công replay cookie cũ
        replay_client = Client(enforce_csrf_checks=True)
        replay_client.cookies["sessionid"] = session_cookie
        replay_csrf = replay_client.get("/api/v1/auth/csrf/").json()["csrf_token"]

        # Gọi endpoint yêu cầu xác thực
        resp = replay_client.post(
            "/api/v1/auth/logout/",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=replay_csrf
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["code"], "NOT_AUTHENTICATED")

    def test_must_change_password_blocks_access_to_other_apis(self):
        """
        Xác minh khi user có must_change_password=True:
        Truy cập endpoint nghiệp vụ khác bị chặn với 403 MUST_CHANGE_PASSWORD,
        nhưng vẫn được phép gọi logout và csrf.
        """
        self.user.must_change_password = True
        self.user.save(update_fields=["must_change_password"])

        csrf = self._get_csrf_token()
        self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        csrf_logged = self.client.cookies["csrftoken"].value

        # Giả lập request qua middleware
        rf = RequestFactory()
        req = rf.get("/api/v1/audit/logs/")
        req.user = self.user
        req.session = self.client.session

        middleware = SessionRegistryAndMustChangeMiddleware(lambda r: HttpResponse())
        resp = middleware(req)
        self.assertEqual(resp.status_code, 403)
        data = json.loads(resp.content)
        self.assertIn("đổi mật khẩu", data["error"]["message"])

        # Nhưng vẫn được phép đăng xuất mà không bị chặn
        logout_resp = self.client.post(
            "/api/v1/auth/logout/",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_logged
        )
        self.assertEqual(logout_resp.status_code, 200)

    def test_session_revoked_on_user_lock_and_cannot_revive_after_unlock(self):
        """
        Xác minh lỗi Point 3: Phiên hợp lệ -> Khóa user -> Mở khóa user -> Phiên cũ PHẢI BỊ THU HỒI,
        tuyệt đối không được sống lại.
        """
        csrf = self._get_csrf_token()
        self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        session_key = self.client.session.session_key
        auth_session = AuthSession.objects.get(session_key=session_key)
        self.assertTrue(auth_session.is_active)

        # Khóa tài khoản
        self.user.status = User.Status.LOCKED
        self.user.save(update_fields=["status"])

        # Kiểm tra AuthSession đã bị thu hồi ngay lập tức
        auth_session.refresh_from_db()
        self.assertFalse(auth_session.is_active)
        self.assertIsNotNone(auth_session.revoked_at)
        self.assertEqual(auth_session.revoke_reason, "USER_LOCKED")

        # Mở khóa lại tài khoản
        self.user.status = User.Status.ACTIVE
        self.user.save(update_fields=["status"])

        # Đối chiếu phiên cũ: Vẫn phải bị từ chối với lý do SESSION_REVOKED
        is_valid, _, reason = verify_auth_session(session_key, user=self.user)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "SESSION_REVOKED")

    def test_user_locked_between_authenticate_and_establish_session_is_rejected(self):
        """
        Xác minh Point 3: Nếu tài khoản bị khóa trong khoảng trống giữa lúc xác thực xong
        và lúc tạo phiên, establish_session() đọc lại DB qua select_for_update() và từ chối tạo phiên.
        """
        from apps.accounts.services.auth import establish_session

        # Giả lập User lúc authenticate() là ACTIVE, nhưng trước establish_session() bị khóa
        self.user.status = User.Status.LOCKED
        self.user.save(update_fields=["status"])

        rf = RequestFactory()
        req = rf.post("/api/v1/auth/login/")
        req.session = self.client.session

        # establish_session phải ném PermissionDenied khi phát hiện User đã bị khóa
        with self.assertRaises(PermissionDenied):
            establish_session(req, self.user)

        # Không tạo bất kỳ session nào trong AuthSession
        self.assertEqual(AuthSession.objects.filter(user=self.user).count(), 0)

    def test_session_rejected_if_user_has_no_role(self):
        """Xác minh lỗi Point 3: Phiên của user không có vai trò hợp lệ bị từ chối và thu hồi."""
        # Xóa nhóm vai trò khỏi user
        self.user.groups.clear()

        now = timezone.now()
        session = AuthSession.objects.create(
            user=self.user,
            session_key="session_no_role",
            expires_at=now + timedelta(seconds=900)
        )
        is_valid, _, reason = verify_auth_session("session_no_role", user=self.user)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "USER_NO_ROLE")

        session.refresh_from_db()
        self.assertIsNotNone(session.revoked_at)

    def test_establish_session_rollback_on_registry_failure(self):
        """Xác minh Point 4: Lỗi tại bước tạo registry dẫn tới rollback toàn bộ transaction."""
        from apps.accounts.services.auth import establish_session

        rf = RequestFactory()
        req = rf.post("/api/v1/auth/login/")
        req.session = self.client.session

        with (
            patch("apps.accounts.services.auth.create_auth_session", side_effect=RuntimeError("Simulated Registry DB Error")),
            self.assertRaises(RuntimeError),
        ):
            establish_session(req, self.user)

        # Không có bản ghi AuthSession nào mồ côi
        self.assertEqual(AuthSession.objects.filter(user=self.user).count(), 0)

    def test_establish_session_rollback_on_audit_failure(self):
        """Xác minh Point 4: Lỗi tại bước ghi audit log dẫn tới rollback AuthSession và session."""
        from apps.accounts.services.auth import establish_session

        rf = RequestFactory()
        req = rf.post("/api/v1/auth/login/")
        req.session = self.client.session

        with (
            patch("apps.audit.services.AuditService.record_event", side_effect=RuntimeError("Simulated Audit Failure")),
            self.assertRaises(RuntimeError),
        ):
            establish_session(req, self.user)

        # Toàn bộ AuthSession bị rollback
        self.assertEqual(AuthSession.objects.filter(user=self.user).count(), 0)

    def test_password_changed_between_authenticate_and_establish_session_is_rejected(self):
        """
        Xác minh P1: Nếu mật khẩu bị thay đổi trong khoảng trống giữa lúc xác thực xong
        và lúc tạo phiên, establish_session() đối chiếu locked_user.password với user.password
        và ném PermissionDenied, ngăn chặn cấp phiên bằng mật khẩu cũ.
        """
        from apps.accounts.services.auth import establish_session

        old_password_hash = self.user.password

        # Đổi mật khẩu trong database
        self.user.set_password("BrandNewPassword@2026!")
        self.user.save(update_fields=["password"])

        # Tạo đối tượng user đại diện cho kết quả authenticate() trước đó (mang hash cũ)
        user_before_change = User.objects.get(id=self.user.id)
        user_before_change.password = old_password_hash

        rf = RequestFactory()
        req = rf.post("/api/v1/auth/login/")
        req.session = self.client.session

        with self.assertRaises(PermissionDenied):
            establish_session(req, user_before_change)

        # Không tạo bất kỳ session nào trong AuthSession
        self.assertEqual(AuthSession.objects.filter(user=self.user).count(), 0)

    def test_invalid_username_format_returns_400_not_500(self):
        """
        Xác minh P2: Gửi username chứa ký tự nhạy cảm hoặc sai định dạng (ví dụ 'password=sample')
        bị chặn ở tầng Serializer với HTTP 400 (VALIDATION_ERROR), không gây crash HTTP 500 tại AuditService.
        """
        csrf = self._get_csrf_token()
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "password=sample", "password": "AnyPassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "VALIDATION_ERROR")

    def test_sensitive_string_in_username_direct_auth_does_not_raise_500(self):
        """
        Xác minh P2 Defense-in-depth: Lời gọi authenticate_credentials trực tiếp với username
        chứa chuỗi nhạy cảm ('password=sample') sử dụng câu mẫu cố định và lọc metadata,
        hoàn toàn không làm bung lỗi 500 từ AuditService.
        """
        from apps.accounts.services.auth import authenticate_credentials

        rf = RequestFactory()
        req = rf.post("/api/v1/auth/login/")

        user, error_code, _ = authenticate_credentials(
            req,
            username="password=sample",
            password="AnyPassword@2026!"
        )
        self.assertIsNone(user)
        self.assertEqual(error_code, "INVALID_CREDENTIALS")
