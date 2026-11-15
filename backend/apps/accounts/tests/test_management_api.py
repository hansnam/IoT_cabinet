"""
Kiểm thử REST API Quản trị Tài khoản và Phân quyền Thiết bị (UI-03).
Bao gồm:
- Kiểm tra quyền truy cập: 401 unauthenticated, 403 non-admin, 200/201 ADMIN
- Bảo vệ last-admin khi cố tình khóa hoặc hạ quyền admin hoạt động duy nhất
- Quy trình cấp quyền và thu hồi quyền DeviceAccess
"""
from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.policies.roles import assign_role
from apps.devices.models import Device, DeviceAccess

PASSWORD = "CabinetSecureTest@2026!"


def make_test_user(username, role="ADMIN", **fields):
    user = User.objects.create_user(
        username=username, full_name="Test User", password=PASSWORD, **fields
    )
    assign_role(user, role)
    return user


def login_client(client, user):
    csrf = client.get("/api/v1/auth/csrf/").json()["csrf_token"]
    response = client.post(
        "/api/v1/auth/login/",
        {"username": user.username, "password": PASSWORD},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 200
    return client.cookies["csrftoken"].value


class ManagementApiTestCase(TestCase):
    def setUp(self):
        call_command("seed_roles", verbosity=0)
        self.admin = make_test_user("admin_lead", "ADMIN")
        self.operator = make_test_user("operator_user", "OPERATOR")
        self.viewer = make_test_user("viewer_user", "VIEWER")

        self.device1 = Device.objects.create(
            id="CABINET-TEST-01",
            name="Tủ thử nghiệm 01",
            status="ACTIVE",
        )

        self.admin_client = Client(enforce_csrf_checks=True)
        self.admin_csrf = login_client(self.admin_client, self.admin)

        self.operator_client = Client(enforce_csrf_checks=True)
        self.operator_csrf = login_client(self.operator_client, self.operator)

    def test_unauthenticated_request_rejected(self):
        anon_client = Client()
        res = anon_client.get("/api/v1/management/users/")
        self.assertIn(res.status_code, (401, 403))

    def test_operator_forbidden_from_management_api(self):
        res = self.operator_client.get("/api/v1/management/users/")
        self.assertEqual(res.status_code, 403)

    def test_admin_can_list_and_create_user(self):
        # 1. List users
        res = self.admin_client.get("/api/v1/management/users/")
        self.assertEqual(res.status_code, 200)
        data = res.json()["data"]
        self.assertGreaterEqual(len(data), 3)

        # 2. Create new user
        new_user_payload = {
            "username": "technician_new",
            "full_name": "Kỹ thuật viên mới",
            "email": "tech@example.com",
            "role": "OPERATOR",
            "status": "ACTIVE",
            "temporary_password": "NewUserPassword@2026!",
        }
        res_post = self.admin_client.post(
            "/api/v1/management/users/",
            new_user_payload,
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.admin_csrf,
        )
        self.assertEqual(res_post.status_code, 201)
        created = res_post.json()["data"]
        self.assertEqual(created["username"], "technician_new")
        self.assertTrue(created["must_change_password"])

    def test_admin_cannot_lock_last_admin(self):
        # Admin duy nhất không được tự khóa chính mình
        res = self.admin_client.patch(
            f"/api/v1/management/users/{self.admin.pk}/",
            {"status": "LOCKED"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.admin_csrf,
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("cuối cùng", res.json()["error"]["message"])

    def test_admin_can_grant_and_revoke_device_access(self):
        # 1. Cấp quyền vận hành tủ
        res_grant = self.admin_client.post(
            "/api/v1/device-access/",
            {
                "user_id": str(self.operator.pk),
                "device_id": self.device1.id,
                "role": "OPERATE",
            },
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.admin_csrf,
        )
        self.assertEqual(res_grant.status_code, 201)
        grant_id = res_grant.json()["data"]["id"]

        # Kiểm tra bản ghi đã lưu vào DB
        self.assertTrue(
            DeviceAccess.objects.filter(
                user=self.operator, device=self.device1, role="OPERATE"
            ).exists()
        )

        # 2. Thu hồi quyền
        res_revoke = self.admin_client.delete(
            f"/api/v1/device-access/{grant_id}/",
            HTTP_X_CSRFTOKEN=self.admin_csrf,
        )
        self.assertEqual(res_revoke.status_code, 200)
        self.assertFalse(DeviceAccess.objects.filter(pk=grant_id).exists())
