"""
Test module kiểm tra toàn diện API Profile & Change Password (TV3-DJ-05):
1. GET /api/v1/profile/me/: Đọc hồ sơ, vai trò, quyền, phiên. Không ghi AuditLog.
2. PATCH /api/v1/profile/me/: Cập nhật full_name, ghi AuditLog.
3. Chặn field lạ / mass-assignment trên PATCH (HTTP 400).
4. POST /api/v1/profile/change-password/: Đổi mật khẩu thành công, thu hồi session cũ, logout và yêu cầu đăng nhập lại.
5. Sai mật khẩu cũ trả về HTTP 401 NOT_AUTHENTICATED và ghi AuditLog FAILED (không log secret).
6. Mật khẩu mới trùng mật khẩu cũ trả về HTTP 400 VALIDATION_ERROR.
7. Mật khẩu mới vi phạm độ dài (<12) hoặc validators trả về HTTP 400.
8. Rate limiting riêng biệt cho CHANGE_PASSWORD (5 lần sai -> 429), không làm khóa đăng nhập chính.
9. Tái kiểm tra phiên trong lock: Phiên bị revoke trước khi service thực hiện bị từ chối và không đổi hash.
10. Luồng tài khoản must_change_password=True: Vẫn xem được profile, sửa được tên, đổi mật khẩu xong gỡ cờ.
11. CSRF enforcement trên cả PATCH profile và POST change-password.
12. Cookie replay sau khi đổi mật khẩu bị từ chối 401.
13. Tính nguyên tử: Rollback toàn bộ khi ghi AuditLog thất bại (kiểm tra trực tiếp tầng service).
14. Các alias /api/v1/auth/ hoạt động tương đồng.
"""
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone

from apps.accounts.models import AuthSession, LoginAttempt, User
from apps.accounts.policies.roles import ROLE_ADMIN, assign_role
from apps.accounts.security.rate_limit import hash_identifier
from apps.accounts.services.users import change_password, update_profile
from apps.audit.models import AuditLog


class ProfilePasswordTestCase(TestCase):
    """Kiểm tra tích hợp endpoint Profile Me và Change Password."""

    def setUp(self):
        # Đồng bộ đầy đủ các nhóm vai trò và quyền hạn vào DB test
        call_command("seed_roles")
        self.client = Client(enforce_csrf_checks=True)

        self.raw_password = "SecurePassword@2026!"
        self.user = User.objects.create_user(
            username="profile_user",
            full_name="Nguyễn Văn A",
            email="nguyenvana@example.com",
            password=self.raw_password
        )
        assign_role(self.user, ROLE_ADMIN)

    def _login_and_get_csrf(self, username: str | None = None, password: str | None = None) -> str:
        u = username or self.user.username
        p = password or self.raw_password
        csrf_init = self.client.get("/api/v1/auth/csrf/").json()["csrf_token"]
        login_resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": u, "password": p},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_init
        )
        self.assertEqual(login_resp.status_code, 200)
        return self.client.cookies["csrftoken"].value

    def test_get_profile_me_success(self):
        """Xác minh GET profile trả về đủ thông tin, đúng quyền đã seed, không ghi AuditLog."""
        self._login_and_get_csrf()
        initial_log_count = AuditLog.objects.count()

        resp = self.client.get("/api/v1/profile/me/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]

        self.assertEqual(data["username"], self.user.username)
        self.assertEqual(data["full_name"], "Nguyễn Văn A")
        self.assertEqual(data["role"], ROLE_ADMIN)
        self.assertIn("USER_CREATE", data["permissions"])
        self.assertIn("USER_VIEW", data["permissions"])
        self.assertIsNotNone(data["session"]["session_id"])
        self.assertEqual(data["expires_at"], data["session"]["expires_at"])
        self.assertNotIn("password", data)

        # GET không tạo thêm AuditLog
        self.assertEqual(AuditLog.objects.count(), initial_log_count)

    def test_get_profile_me_unauthenticated_returns_401(self):
        """Xác minh chưa đăng nhập gọi GET trả về 401 NOT_AUTHENTICATED."""
        unauthed = Client(enforce_csrf_checks=True)
        resp = unauthed.get("/api/v1/profile/me/")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["code"], "NOT_AUTHENTICATED")

    def test_patch_profile_me_success(self):
        """Xác minh PATCH cập nhật full_name thành công và ghi AuditLog."""
        csrf = self._login_and_get_csrf()

        resp = self.client.patch(
            "/api/v1/profile/me/",
            {"full_name": "  Đỗ Mạnh Hùng (Kỹ Sư Vận Hành)  "},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["full_name"], "Đỗ Mạnh Hùng (Kỹ Sư Vận Hành)")

        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, "Đỗ Mạnh Hùng (Kỹ Sư Vận Hành)")

        log = AuditLog.objects.filter(action="USER_UPDATE", target_id=str(self.user.id)).first()
        self.assertIsNotNone(log)
        assert log is not None
        self.assertEqual(log.outcome, "SUCCESS")
        self.assertEqual(log.metadata.get("fields"), ["full_name"])

    def test_patch_profile_me_records_audit_log(self):
        """Xác minh PATCH ghi đúng summary và metadata allowlist."""
        csrf = self._login_and_get_csrf()
        self.client.patch(
            "/api/v1/profile/me/",
            {"full_name": "Lê Văn B"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        log = AuditLog.objects.filter(action="USER_UPDATE", target_id=str(self.user.id)).first()
        self.assertIsNotNone(log)
        assert log is not None
        self.assertEqual(log.actor_user, self.user)
        self.assertEqual(log.summary, "Cập nhật thông tin hồ sơ cá nhân thành công")

    def test_patch_profile_me_rejects_mass_assignment(self):
        """Xác minh gửi kèm trường lạ hoặc đặc quyền bị từ chối 400 VALIDATION_ERROR."""
        csrf = self._login_and_get_csrf()

        for field in ["role", "is_staff", "status", "password", "id"]:
            with self.subTest(field=field):
                resp = self.client.patch(
                    "/api/v1/profile/me/",
                    {"full_name": "Tên Mới", field: "hack_value"},
                    content_type="application/json",
                    HTTP_X_CSRFTOKEN=csrf
                )
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json()["error"]["code"], "VALIDATION_ERROR")

    def test_patch_profile_me_validation_empty_or_invalid_name(self):
        """Xác minh tên trống hoặc quá dài bị từ chối 400."""
        csrf = self._login_and_get_csrf()
        for payload in [{"full_name": "   "}, {"full_name": ""}, {"full_name": "A" * 129}, {}]:
            with self.subTest(payload=payload):
                resp = self.client.patch(
                    "/api/v1/profile/me/",
                    payload,
                    content_type="application/json",
                    HTTP_X_CSRFTOKEN=csrf
                )
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json()["error"]["code"], "VALIDATION_ERROR")

    def test_change_password_success_revokes_all_sessions_and_requires_login(self):
        """Xác minh đổi mật khẩu thành công: phiên cũ bị thu hồi, logout ngay, client phải đăng nhập lại bằng mật khẩu mới."""
        csrf = self._login_and_get_csrf()
        old_session_cookie = self.client.cookies["sessionid"].value

        new_pass = "BrandNewSecurePassword@2026!"
        resp = self.client.post(
            "/api/v1/profile/change-password/",
            {"old_password": self.raw_password, "new_password": new_pass},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["data"]["login_required"])

        # Mật khẩu mới đã được cập nhật trong DB
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_pass))
        self.assertFalse(self.user.must_change_password)
        self.assertIsNotNone(self.user.password_changed_at)

        # AuditLog thành công được ghi nhận
        log = AuditLog.objects.filter(action="PASSWORD_CHANGE", target_id=str(self.user.id), outcome="SUCCESS").first()
        self.assertIsNotNone(log)
        assert log is not None
        self.assertEqual(log.outcome, "SUCCESS")

        # Client hiện tại đã bị logout: Gọi API tiếp theo trả về 401
        me_resp = self.client.get("/api/v1/profile/me/")
        self.assertEqual(me_resp.status_code, 401)

        # Replay cookie cũ qua client khác -> Phải bị từ chối 401
        replay_client = Client(enforce_csrf_checks=True)
        replay_client.cookies["sessionid"] = old_session_cookie
        replay_resp = replay_client.get("/api/v1/profile/me/")
        self.assertEqual(replay_resp.status_code, 401)

        # Đăng nhập lại bằng mật khẩu cũ bị từ chối 401
        login_csrf = self.client.get("/api/v1/auth/csrf/").json()["csrf_token"]
        fail_login = self.client.post(
            "/api/v1/auth/login/",
            {"username": self.user.username, "password": self.raw_password},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=login_csrf
        )
        self.assertEqual(fail_login.status_code, 401)

        # Đăng nhập lại bằng mật khẩu mới thành công 200
        ok_login = self.client.post(
            "/api/v1/auth/login/",
            {"username": self.user.username, "password": new_pass},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=login_csrf
        )
        self.assertEqual(ok_login.status_code, 200)

    def test_change_password_wrong_old_password_returns_401_and_audits_failure(self):
        """Xác minh sai mật khẩu cũ trả về HTTP 401, ghi AuditLog FAILED (không log secret) và ghi attempt thất bại."""
        csrf = self._login_and_get_csrf()

        resp = self.client.post(
            "/api/v1/profile/change-password/",
            {"old_password": "WrongOldPassword@2026!", "new_password": "BrandNewSecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["code"], "NOT_AUTHENTICATED")

        # Mật khẩu DB không đổi
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.raw_password))

        # Ghi nhận AuditLog outcome=FAILED
        fail_log = AuditLog.objects.filter(
            action="PASSWORD_CHANGE",
            target_id=str(self.user.id),
            outcome="FAILED"
        ).first()
        self.assertIsNotNone(fail_log)
        assert fail_log is not None
        self.assertEqual(fail_log.error_code, "INVALID_CREDENTIALS")
        self.assertNotIn("WrongOldPassword", fail_log.summary)
        self.assertNotIn("password", fail_log.metadata)

        # Ghi nhận LoginAttempt thất bại cho mục đích CHANGE_PASSWORD
        user_key = hash_identifier(self.user.username, "CHANGE_PASSWORD")
        self.assertTrue(LoginAttempt.objects.filter(username_key=user_key, success=False).exists())

    def test_change_password_same_as_old_rejected_400(self):
        """Xác minh mật khẩu mới trùng mật khẩu cũ bị từ chối 400 VALIDATION_ERROR."""
        csrf = self._login_and_get_csrf()

        resp = self.client.post(
            "/api/v1/profile/change-password/",
            {"old_password": self.raw_password, "new_password": self.raw_password},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "VALIDATION_ERROR")

    def test_change_password_too_short_rejected_400(self):
        """Xác minh mật khẩu mới dưới 12 ký tự bị từ chối 400 VALIDATION_ERROR."""
        csrf = self._login_and_get_csrf()

        resp = self.client.post(
            "/api/v1/profile/change-password/",
            {"old_password": self.raw_password, "new_password": "Short1!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "VALIDATION_ERROR")

    def test_change_password_rate_limiting_isolated(self):
        """Xác minh Rate Limiting của CHANGE_PASSWORD (5 lần sai -> 429) độc lập, không làm khóa đăng nhập."""
        csrf = self._login_and_get_csrf()

        for _ in range(5):
            self.client.post(
                "/api/v1/profile/change-password/",
                {"old_password": "WrongPassword@2026!", "new_password": "ValidNewPassword@2026!"},
                content_type="application/json",
                HTTP_X_CSRFTOKEN=csrf
            )

        # Lần thứ 6 bị khóa 429
        resp = self.client.post(
            "/api/v1/profile/change-password/",
            {"old_password": self.raw_password, "new_password": "ValidNewPassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()["error"]["code"], "RATE_LIMITED")
        self.assertIn("Retry-After", resp.headers)

        # Đăng nhập bằng mật khẩu đúng vẫn thành công (Rate limit tách biệt domain)
        new_client = Client(enforce_csrf_checks=True)
        login_csrf = new_client.get("/api/v1/auth/csrf/").json()["csrf_token"]
        login_resp = new_client.post(
            "/api/v1/auth/login/",
            {"username": self.user.username, "password": self.raw_password},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=login_csrf
        )
        self.assertEqual(login_resp.status_code, 200)

    def test_change_password_rejected_if_session_revoked_before_service(self):
        """Xác minh nếu phiên bị thu hồi trong registry trước khi đổi mật khẩu, service từ chối và không đổi hash."""
        csrf = self._login_and_get_csrf()
        session_key = self.client.session.session_key

        # Thu hồi phiên trong registry trước
        AuthSession.objects.filter(session_key=session_key).update(
            revoked_at=timezone.now(),
            revoke_reason="ADMIN_LOCKED"
        )

        resp = self.client.post(
            "/api/v1/profile/change-password/",
            {"old_password": self.raw_password, "new_password": "BrandNewPassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        # Bị từ chối
        self.assertIn(resp.status_code, (401, 403))

        # Mật khẩu trong DB tuyệt đối không đổi
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.raw_password))

    def test_must_change_password_user_journey(self):
        """Xác minh hành trình tài khoản must_change_password=True: xem me -> sửa tên -> đổi mật khẩu -> hết must-change."""
        self.user.must_change_password = True
        self.user.save(update_fields=["must_change_password"])

        csrf = self._login_and_get_csrf()

        # 1. GET profile thành công
        get_res = self.client.get("/api/v1/profile/me/")
        self.assertEqual(get_res.status_code, 200)
        self.assertTrue(get_res.json()["data"]["must_change_password"])

        # 2. PATCH profile thành công
        patch_res = self.client.patch(
            "/api/v1/profile/me/",
            {"full_name": "Tên Mới Khi Đổi Pass"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(patch_res.status_code, 200)

        # 3. Đổi mật khẩu thành công
        new_pass = "PermanentSecurePass@2026!"
        change_res = self.client.post(
            "/api/v1/profile/change-password/",
            {"old_password": self.raw_password, "new_password": new_pass},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(change_res.status_code, 200)

        # 4. Kiểm tra cờ must_change_password đã tắt
        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)

    def test_csrf_protection_on_both_endpoints(self):
        """Xác minh cả PATCH profile và POST change-password đều bắt buộc kiểm tra CSRF."""
        self._login_and_get_csrf()

        patch_no_csrf = self.client.patch(
            "/api/v1/profile/me/",
            {"full_name": "Tên Không CSRF"},
            content_type="application/json"
        )
        self.assertEqual(patch_no_csrf.status_code, 403)
        self.assertEqual(patch_no_csrf.json()["error"]["code"], "CSRF_FAILED")

        post_no_csrf = self.client.post(
            "/api/v1/profile/change-password/",
            {"old_password": self.raw_password, "new_password": "ValidNewPassword@2026!"},
            content_type="application/json"
        )
        self.assertEqual(post_no_csrf.status_code, 403)
        self.assertEqual(post_no_csrf.json()["error"]["code"], "CSRF_FAILED")

    def test_update_profile_rollback_on_audit_failure(self):
        """Xác minh tính nguyên tử tại tầng service: Lỗi ghi AuditLog kéo theo rollback cập nhật full_name."""
        rf = RequestFactory()
        req = rf.patch("/api/v1/profile/me/")
        req.user = self.user
        req.session = self.client.session

        old_name = self.user.full_name

        with (
            patch("apps.audit.services.AuditService.record_event", side_effect=RuntimeError("Simulated DB Audit Failure")),
            self.assertRaises(RuntimeError),
        ):
            update_profile(user=self.user, full_name="Tên Bị Rollback", request=req)

        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, old_name)

    def test_change_password_rollback_on_audit_failure(self):
        """Xác minh tính nguyên tử tại tầng service: Lỗi ghi AuditLog kéo theo rollback đổi mật khẩu."""
        self._login_and_get_csrf()
        rf = RequestFactory()
        req = rf.post("/api/v1/profile/change-password/")
        req.user = self.user
        req.session = self.client.session

        with (
            patch("apps.audit.services.AuditService.record_event", side_effect=RuntimeError("Simulated DB Audit Failure")),
            self.assertRaises(RuntimeError),
        ):
            change_password(
                user=self.user,
                old_password=self.raw_password,
                new_password="BrandNewPassword@2026!",
                request=req
            )

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.raw_password))

    def test_auth_aliases_work_identically(self):
        """Xác minh cả 2 alias /api/v1/auth/me/ và /api/v1/auth/change-password/ hoạt động hoàn toàn tương đồng."""
        csrf = self._login_and_get_csrf()

        get_res = self.client.get("/api/v1/auth/me/")
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.json()["data"]["username"], self.user.username)

        new_pass = "AliasNewPassword@2026!"
        change_res = self.client.post(
            "/api/v1/auth/change-password/",
            {"old_password": self.raw_password, "new_password": new_pass},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(change_res.status_code, 200)