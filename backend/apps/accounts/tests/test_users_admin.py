from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.utils import timezone

from apps.accounts.models import AuthSession, User
from apps.accounts.policies.roles import assign_role, get_user_role
from apps.accounts.services.administration import manage_user
from apps.audit.models import AuditLog

PASSWORD = "CabinetSecureTest@2026!"


def make_user(username, role="ADMIN", **fields):
    user = User.objects.create_user(username=username, full_name="Test User", password=PASSWORD, **fields)
    assign_role(user, role)
    return user


def login_api(client, user):
    csrf = client.get("/api/v1/auth/csrf/").json()["csrf_token"]
    response = client.post("/api/v1/auth/login/", {"username": user.username, "password": PASSWORD},
                           content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
    assert response.status_code == 200
    return client.cookies["csrftoken"].value


class UsersAdminTestCase(TestCase):
    def setUp(self):
        call_command("seed_roles", verbosity=0)
        self.admin = make_user("admin_manager")
        self.client = Client(enforce_csrf_checks=True)
        self.csrf = login_api(self.client, self.admin)
        self.target = make_user("operator_target", "OPERATOR")

    def form_data(self, **changes):
        return {"username": self.target.username, "full_name": "Updated Name", "email": "",
                "role": "OPERATOR", "status": "ACTIVE", "temporary_password": "",
                "confirm_password": "", "_save": "Save", **changes}

    def post_user(self, **changes):
        return self.client.post(f"/admin/accounts/user/{self.target.pk}/change/",
                                self.form_data(**changes), HTTP_X_CSRFTOKEN=self.csrf)

    def service_request(self):
        request = RequestFactory().post("/admin/accounts/user/")
        request.user, request.session = self.admin, self.client.session
        return request

    def test_admin_login_creates_registry_and_audit(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get("/admin/login/").status_code, 200)
        response = client.post("/admin/login/", {"username": self.admin.username, "password": PASSWORD,
                                               "next": "/admin/"}, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AuthSession.objects.filter(session_key=client.session.session_key, revoked_at=None).exists())
        self.assertTrue(AuditLog.objects.filter(action="USER_LOGIN", outcome="SUCCESS").exists())

    def test_admin_login_csrf_and_shared_limit(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post("/admin/login/", {"username": self.admin.username, "password": PASSWORD}).status_code, 403)
        csrf = client.get("/api/v1/auth/csrf/").json()["csrf_token"]
        for _ in range(5):
            response = client.post("/api/v1/auth/login/", {"username": self.admin.username, "password": "WrongPassword@2026!"},
                                   content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
            self.assertEqual(response.status_code, 401)
        response = client.post("/admin/login/", {"username": self.admin.username, "password": PASSWORD}, HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

    def test_staff_operator_cannot_enter_admin(self):
        User.objects.filter(pk=self.target.pk).update(is_staff=True)
        client = Client(enforce_csrf_checks=True)
        login_api(client, self.target)
        self.assertEqual(client.get("/admin/").status_code, 302)

    def test_create_form_hashes_password_and_requires_change(self):
        response = self.client.post("/admin/accounts/user/add/", self.form_data(
            username="new_viewer", role="VIEWER", temporary_password=PASSWORD, confirm_password=PASSWORD,
        ), HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username="new_viewer")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertTrue(user.must_change_password)
        self.assertFalse(user.is_superuser)
        self.assertEqual(get_user_role(user), "VIEWER")
        self.assertTrue(AuditLog.objects.filter(action="USER_CREATE", target_id=str(user.pk)).exists())

    def test_password_confirmation_and_weak_password_rejected(self):
        for password, confirmation in [(PASSWORD, "Mismatch@2026!"), ("short", "short")]:
            response = self.client.post("/admin/accounts/user/add/", self.form_data(
                username="invalid_account", temporary_password=password, confirm_password=confirmation,
            ), HTTP_X_CSRFTOKEN=self.csrf)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(User.objects.filter(username="invalid_account").exists())

    def test_change_form_ignores_privileged_fields(self):
        response = self.post_user(is_superuser="on", is_staff="on", groups=["999"], user_permissions=["999"])
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertEqual(self.target.full_name, "Updated Name")
        self.assertFalse(self.target.is_superuser)
        self.assertFalse(self.target.is_staff)
        self.assertEqual(get_user_role(self.target), "OPERATOR")

    def test_lock_unlock_and_archive_do_not_revive_cookie(self):
        client = Client(enforce_csrf_checks=True)
        login_api(client, self.target)
        cookie = client.cookies["sessionid"].value
        for status in ("LOCKED", "ACTIVE", "ARCHIVED"):
            self.assertEqual(self.post_user(status=status).status_code, 302)
            replay = Client()
            replay.cookies["sessionid"] = cookie
            self.assertEqual(replay.get("/api/v1/profile/me/").status_code, 401)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, "ARCHIVED")

    def test_role_change_and_reset_revoke_existing_sessions(self):
        client = Client(enforce_csrf_checks=True)
        login_api(client, self.target)
        key = client.session.session_key
        self.assertEqual(self.post_user(role="VIEWER").status_code, 302)
        self.assertIsNotNone(AuthSession.objects.get(session_key=key).revoked_at)
        old_key = key
        assert old_key is not None
        login_api(client, self.target)
        key = client.session.session_key
        self.assertNotEqual(key, old_key)
        replay = Client()
        replay.cookies["sessionid"] = old_key
        self.assertEqual(replay.get("/api/v1/profile/me/").status_code, 401)
        self.assertEqual(self.post_user(role="VIEWER", temporary_password="TemporaryNewPass@2026!",
                                       confirm_password="TemporaryNewPass@2026!").status_code, 302)
        self.target.refresh_from_db()
        self.assertTrue(self.target.must_change_password)
        self.assertTrue(self.target.check_password("TemporaryNewPass@2026!"))
        self.assertIsNotNone(AuthSession.objects.get(session_key=key).revoked_at)

    def test_last_admin_form_change_is_rejected(self):
        self.target = self.admin
        response = self.post_user(role="VIEWER")
        self.assertEqual(response.status_code, 302)
        self.admin.refresh_from_db()
        self.assertEqual(get_user_role(self.admin), "ADMIN")

    def test_last_admin_service_guard_and_rollback(self):
        request = self.service_request()
        with self.assertRaises(ValidationError):
            manage_user(request, target_id=self.admin.pk, status="LOCKED")
        with patch("apps.audit.services.AuditService.record_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                manage_user(request, target_id=self.target.pk, full_name="Not Committed", status="LOCKED")
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, "ACTIVE")
        self.assertEqual(self.target.full_name, "Test User")

    def test_service_rejects_revoked_actor(self):
        request = self.service_request()
        AuthSession.objects.filter(session_key=request.session.session_key).update(revoked_at=timezone.now())
        with self.assertRaises(PermissionDenied):
            manage_user(request, target_id=self.target.pk, status="LOCKED")

    def test_audit_readonly_and_no_group_editor(self):
        log = AuditLog.objects.first()
        assert log is not None
        self.assertEqual(self.client.get("/admin/audit/auditlog/").status_code, 200)
        self.assertEqual(self.client.post("/admin/audit/auditlog/add/", {}, HTTP_X_CSRFTOKEN=self.csrf).status_code, 403)
        self.assertEqual(self.client.post(f"/admin/audit/auditlog/{log.pk}/change/", {}, HTTP_X_CSRFTOKEN=self.csrf).status_code, 403)
        self.assertEqual(self.client.post(f"/admin/audit/auditlog/{log.pk}/delete/", {}, HTTP_X_CSRFTOKEN=self.csrf).status_code, 403)
        self.assertEqual(self.client.get("/admin/auth/group/").status_code, 404)
        self.assertFalse(Group.objects.filter(name="injected").exists())

    def test_admin_logout_revokes_registry(self):
        key = self.client.session.session_key
        response = self.client.post("/admin/logout/", HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(AuthSession.objects.get(session_key=key).revoked_at)

    def test_temporary_admin_can_change_password_but_not_browse(self):
        self.admin.must_change_password = True
        self.admin.save(update_fields=["must_change_password"])
        self.assertEqual(self.client.get("/admin/").headers["Location"], "/admin/password_change/")
        self.assertEqual(self.client.get("/admin/password_change/").status_code, 200)
        response = self.client.post("/admin/password_change/", {
            "old_password": PASSWORD, "new_password1": "PermanentAdminPass@2026!",
            "new_password2": "PermanentAdminPass@2026!",
        }, HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 302)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.must_change_password)
        self.assertTrue(self.admin.check_password("PermanentAdminPass@2026!"))
        self.assertEqual(self.client.get("/api/v1/profile/me/").status_code, 401)

    def test_admin_wrong_old_password_is_limited(self):
        payload = {"old_password": "WrongPassword@2026!", "new_password1": "PermanentAdminPass@2026!",
                   "new_password2": "PermanentAdminPass@2026!"}
        for _ in range(5):
            self.assertEqual(self.client.post("/admin/password_change/", payload, HTTP_X_CSRFTOKEN=self.csrf).status_code, 200)
        response = self.client.post("/admin/password_change/", payload, HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)


@skipUnless(connection.vendor == "postgresql", "Cần PostgreSQL để kiểm advisory lock thật")
class LastAdminRaceTestCase(TransactionTestCase):
    def test_two_admins_cannot_demote_each_other_to_zero(self):
        call_command("seed_roles", verbosity=0)
        users = [make_user("race_admin_1"), make_user("race_admin_2")]
        requests = []
        for user in users:
            client = Client(enforce_csrf_checks=True)
            login_api(client, user)
            request = RequestFactory().post("/admin/accounts/user/")
            request.user, request.session = user, client.session
            requests.append(request)
        barrier = Barrier(2)

        def demote(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                try:
                    manage_user(requests[index], target_id=users[index].pk, role="VIEWER")
                    return "changed"
                except ValidationError:
                    return "denied"
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(demote, range(2)))
        self.assertCountEqual(results, ["changed", "denied"])
        self.assertEqual(User.objects.filter(status="ACTIVE", groups__name="ADMIN").count(), 1)
