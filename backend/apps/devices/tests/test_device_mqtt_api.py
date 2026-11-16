"""
Bộ kiểm thử tự động cho UI-06 (Tài khoản MQTT của từng tủ mạng và thao tác vòng đời).
Kiểm tra chi tiết:
- Phân quyền (Chỉ ADMIN được truy cập, chặn OPERATOR và VIEWER).
- Khởi tạo tài khoản MQTT an toàn với Mosquitto Dynamic Security.
- Kích hoạt (Enable), Tạm khóa (Disable), Đặt lại mật khẩu (Reset-password).
- Không bao giờ rò rỉ mật khẩu trong GET, URL hoặc AuditLog.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.policies.roles import assign_role
from apps.audit.models import AuditLog
from apps.devices.models import Device

User = get_user_model()


class DeviceMqttApiUI06Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username='admin_mqtt_ui06',
            password='AdminPassword123!',
            full_name='Quản trị viên MQTT',
            status='ACTIVE'
        )
        assign_role(self.admin, 'ADMIN')

        self.operator = User.objects.create_user(
            username='op_mqtt_ui06',
            password='OperatorPassword123!',
            full_name='Người vận hành',
            status='ACTIVE'
        )
        assign_role(self.operator, 'OPERATOR')

        self.viewer = User.objects.create_user(
            username='viewer_mqtt_ui06',
            password='ViewerPassword123!',
            full_name='Người xem',
            status='ACTIVE'
        )
        assign_role(self.viewer, 'VIEWER')

        self.device = Device.objects.create(
            id='CABINET-MQTT-01',
            name='Tủ mạng Kiểm thử MQTT',
            status='REGISTERED'
        )

    def test_metadata_permission_guards(self):
        """Chỉ ADMIN mới có quyền truy cập thông tin tài khoản MQTT của tủ."""
        # Unauthenticated -> 401
        res_anon = self.client.get(f'/api/v1/devices/{self.device.id}/mqtt/')
        self.assertEqual(res_anon.status_code, status.HTTP_401_UNAUTHORIZED)

        # OPERATOR -> 403
        self.client.force_authenticate(user=self.operator)
        res_op = self.client.get(f'/api/v1/devices/{self.device.id}/mqtt/')
        self.assertEqual(res_op.status_code, status.HTTP_403_FORBIDDEN)

        # VIEWER -> 403
        self.client.force_authenticate(user=self.viewer)
        res_view = self.client.get(f'/api/v1/devices/{self.device.id}/mqtt/')
        self.assertEqual(res_view.status_code, status.HTTP_403_FORBIDDEN)

    @patch('apps.devices.device_mqtt_service.broker_command')
    def test_metadata_get_success_for_admin(self, mock_broker_cmd):
        """ADMIN xem thông tin cấu hình và ACL topic; không chứa trường secret."""
        mock_broker_cmd.return_value = {
            'client': {
                'username': self.device.id,
                'disabled': False,
                'roles': [{'rolename': f'device-{self.device.id}'}]
            }
        }
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(f'/api/v1/devices/{self.device.id}/mqtt/')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['device_id'], self.device.id)
        self.assertEqual(res.data['username'], self.device.id)
        self.assertTrue(res.data['account_exists'])
        self.assertFalse(res.data['is_disabled'])
        self.assertIn('topics', res.data)
        self.assertEqual(res.data['topics']['telemetry'], f'cabinet/v3/devices/{self.device.id}/telemetry')
        self.assertEqual(res.data['topics']['commands'], f'cabinet/v3/devices/{self.device.id}/commands')

        # Đảm bảo không để lộ mật khẩu trong metadata
        self.assertNotIn('password', res.data)
        self.assertNotIn('secret', res.data)

    @patch('apps.devices.device_mqtt_service.broker_command')
    def test_provision_device_mqtt_success(self, mock_broker_cmd):
        """Khởi tạo tài khoản MQTT: tuần tự Mosquitto 2.0.18, mật khẩu 1 lần, trạng thái REGISTERED."""
        mock_broker_cmd.return_value = {'responses': [{'command': 'ok'}]}
        self.client.force_authenticate(user=self.admin)

        res = self.client.post(f'/api/v1/devices/{self.device.id}/mqtt/provision/', data={}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data['success'])
        self.assertEqual(res.data['username'], self.device.id)
        self.assertEqual(res.data['client_id'], self.device.id)
        self.assertIn('password', res.data)
        self.assertGreaterEqual(len(res.data['password']), 16)
        self.assertEqual(res.data['status'], 'REGISTERED')
        self.assertTrue(res.data['is_disabled'])

        # Xác nhận trạng thái Device trong DB được lưu là REGISTERED
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, 'REGISTERED')

        # Xác nhận AuditLog được ghi lại và KHÔNG chứa mật khẩu
        audit_event = AuditLog.objects.filter(
            target_type='DEVICE',
            target_id=self.device.id,
            action='MQTT_PROVISIONED'
        ).latest('occurred_at')
        self.assertIsNotNone(audit_event)
        self.assertNotIn('password', str(audit_event.metadata))

    @patch('apps.devices.device_mqtt_service.broker_command')
    def test_provision_with_invalid_password_length(self, mock_broker_cmd):
        """Mật khẩu tự nhập nếu dưới 12 ký tự phải bị từ chối."""
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(
            f'/api/v1/devices/{self.device.id}/mqtt/provision/',
            data={'password': 'short'},
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('PROVISION_FAILED', res.data['error']['code'])

    @patch('apps.devices.device_mqtt_service.broker_command')
    def test_enable_device_mqtt(self, mock_broker_cmd):
        """Kích hoạt tài khoản MQTT: gọi enableClient và chuyển Device sang ACTIVE."""
        mock_broker_cmd.return_value = {'responses': [{'command': 'ok'}]}
        self.client.force_authenticate(user=self.admin)

        res = self.client.post(f'/api/v1/devices/{self.device.id}/mqtt/enable/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'ACTIVE')
        self.assertFalse(res.data['is_disabled'])

        self.device.refresh_from_db()
        self.assertEqual(self.device.status, 'ACTIVE')

    @patch('apps.devices.device_mqtt_service.broker_command')
    def test_disable_device_mqtt(self, mock_broker_cmd):
        """Vô hiệu hóa tài khoản MQTT: gọi disableClient trên Broker và chuyển Device sang LOCKED."""
        mock_broker_cmd.return_value = {'responses': [{'command': 'ok'}]}
        self.client.force_authenticate(user=self.admin)

        res = self.client.post(f'/api/v1/devices/{self.device.id}/mqtt/disable/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'LOCKED')
        self.assertTrue(res.data['is_disabled'])

        self.device.refresh_from_db()
        self.assertEqual(self.device.status, 'LOCKED')

    @patch('apps.devices.device_mqtt_service.broker_command')
    def test_cannot_enable_decommissioned_device(self, mock_broker_cmd):
        """Tủ đã ngừng sử dụng (DECOMMISSIONED) không được phép kích hoạt lại tài khoản MQTT."""
        self.device.status = 'DECOMMISSIONED'
        self.device.save(update_fields=['status'])
        self.client.force_authenticate(user=self.admin)

        res = self.client.post(f'/api/v1/devices/{self.device.id}/mqtt/enable/')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['error']['code'], 'ENABLE_FAILED')

    @patch('apps.devices.device_mqtt_service.broker_command')
    def test_reset_password_mqtt(self, mock_broker_cmd):
        """Đặt lại mật khẩu MQTT trả về mật khẩu mới và ghi audit log an toàn."""
        mock_broker_cmd.return_value = {'responses': [{'command': 'ok'}]}
        self.client.force_authenticate(user=self.admin)

        res = self.client.post(f'/api/v1/devices/{self.device.id}/mqtt/reset-password/', data={}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('password', res.data)
        self.assertGreaterEqual(len(res.data['password']), 16)

        # Audit log ghi nhận nhưng không lưu password
        audit_event = AuditLog.objects.filter(
            target_type='DEVICE',
            target_id=self.device.id,
            action='MQTT_PASSWORD_RESET'
        ).latest('occurred_at')
        self.assertIsNotNone(audit_event)
        self.assertNotIn('password', str(audit_event.metadata))
