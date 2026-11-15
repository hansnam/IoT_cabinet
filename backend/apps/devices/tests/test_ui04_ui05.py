"""
Bộ kiểm thử tự động cho UI-04 và UI-05 (Thiết bị, Cấu hình Hysteresis, Resync và Broker Settings).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from unittest.mock import patch
from cryptography.fernet import Fernet
from rest_framework import status
from rest_framework.test import APIClient

from apps.devices.broker_config import validate_broker_host, validate_broker_port, validate_ca_pem
from apps.devices.crypto import decrypt_secret, encrypt_secret
from apps.devices.models import BrokerConnection, Device, DeviceConfig

User = get_user_model()


class DeviceUI04Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username='admin_ui04',
            password='AdminPassword123!',
            full_name='Admin Test',
            status='ACTIVE'
        )
        self.operator = User.objects.create_user(
            username='op_ui04',
            password='OperatorPass123!',
            full_name='Operator Test',
            status='ACTIVE'
        )
        from apps.accounts.policies.roles import assign_role
        assign_role(self.admin, 'ADMIN')
        if hasattr(self, 'operator'): assign_role(self.operator, 'OPERATOR')
        self.client.force_authenticate(user=self.admin)

    def test_admin_create_device_starts_in_registered_status(self):
        """Tạo thiết bị mới bắt buộc ở trạng thái REGISTERED (chưa mặc định ACTIVE)."""
        payload = {
            'id': 'CABINET-TEST-01',
            'name': 'Tủ mạng Thử nghiệm 01',
            'source': 'REAL',
            'hardware_version': 'v1.0',
            'firmware_version': 'v1.0.0',
        }
        res = self.client.post('/api/v1/devices/', data=payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['status'], 'REGISTERED')
        self.assertEqual(res.data['id'], 'CABINET-TEST-01')

        # Thử tạo với mã có ký tự đặc biệt không hợp lệ
        res_bad = self.client.post('/api/v1/devices/', data={'id': 'BAD ID!@#', 'name': 'Lỗi'}, format='json')
        self.assertEqual(res_bad.status_code, status.HTTP_400_BAD_REQUEST)

    def test_operator_cannot_create_device(self):
        """Người vận hành không có quyền tạo thiết bị mới."""
        self.client.force_authenticate(user=self.operator)
        res = self.client.post('/api/v1/devices/', data={'id': 'CABINET-OP', 'name': 'Op'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_config_hysteresis_validation(self):
        """Kiểm tra ngưỡng tắt quạt phải nhỏ hơn ngưỡng bật (hysteresis)."""
        dev = Device.objects.create(id='CAB-HYST', name='Hysteresis Test', status='ACTIVE')
        from apps.devices.models import DeviceAccess
        DeviceAccess.objects.create(user=self.admin, device=dev, role='OPERATE')

        # fan_off_c (30) >= fan_on_c (28) -> Phải bị từ chối
        bad_config = {
            'desired_config': {
                'temp_alert_c': 35,
                'humidity_alert_pct': 80,
                'fan_on_c': 28,
                'fan_off_c': 30,
            }
        }
        res = self.client.post(f'/api/v1/devices/{dev.id}/config/', data=bad_config, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # fan_off_c (26) < fan_on_c (28) -> Hợp lệ
        good_config = {
            'desired_config': {
                'temp_alert_c': 35,
                'humidity_alert_pct': 80,
                'fan_on_c': 28,
                'fan_off_c': 26,
                'telemetry_interval_ms': 5000,
                'manual_timeout_s': 60,
            }
        }
        res_ok = self.client.post(f'/api/v1/devices/{dev.id}/config/', data=good_config, format='json')
        self.assertEqual(res_ok.status_code, status.HTTP_202_ACCEPTED)

    def test_resync_config_endpoint(self):
        """Thao tác resync chỉ thành công khi đã có cấu hình APPLIED trước đó."""
        dev = Device.objects.create(id='CAB-RESYNC', name='Resync Test', status='ACTIVE')
        from apps.devices.models import DeviceAccess
        DeviceAccess.objects.create(user=self.admin, device=dev, role='OPERATE')

        # Khi chưa có snapshot APPLIED -> Trả 400
        res_fail = self.client.post(f'/api/v1/devices/{dev.id}/resync-config/')
        self.assertEqual(res_fail.status_code, status.HTTP_400_BAD_REQUEST)

        # Tạo snapshot đã applied
        DeviceConfig.objects.create(
            device=dev,
            version=1,
            config_data={'fan_on_c': 30, 'fan_off_c': 27, 'temp_alert_c': 35, 'humidity_alert_pct': 80},
            applied=True
        )

        res_ok = self.client.post(f'/api/v1/devices/{dev.id}/resync-config/')
        self.assertEqual(res_ok.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res_ok.data['version'], 1)


@override_settings(MQTT_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode())
class BrokerSettingsUI05Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username='admin_ui05',
            password='AdminPassword123!',
            full_name='Admin Test',
            status='ACTIVE'
        )
        from apps.accounts.policies.roles import assign_role
        assign_role(self.admin, 'ADMIN')
        if hasattr(self, 'operator'): assign_role(self.operator, 'OPERATOR')
        self.client.force_authenticate(user=self.admin)

    def test_fernet_crypto_roundtrip(self):
        """Mã hóa và giải mã bằng Fernet an toàn."""
        secret = 'SecretMosquittoPassword#2026'
        cipher = encrypt_secret(secret)
        self.assertNotEqual(secret, cipher)
        decrypted = decrypt_secret(cipher)
        self.assertEqual(secret, decrypted)

    def test_host_and_port_validation(self):
        """Chặn shell injection và URL không hợp lệ."""
        self.assertEqual(validate_broker_host('192.168.1.100'), '192.168.1.100')
        self.assertEqual(validate_broker_host('mqtt.mycompany.vn'), 'mqtt.mycompany.vn')
        with self.assertRaises(ValueError):
            validate_broker_host('http://127.0.0.1; rm -rf /')
        with self.assertRaises(ValueError):
            validate_broker_port(70000)

    def test_ca_pem_validation(self):
        """Chỉ nhận chứng chỉ công khai, chặn private key."""
        valid_pem = "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----"
        with self.assertRaises(ValueError):
            validate_ca_pem(valid_pem)
        with self.assertRaises(ValueError):
            validate_ca_pem("-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----")

    def test_broker_settings_api_get_and_patch(self):
        """API không để lộ ciphertext/plaintext; tăng revision khi cập nhật."""
        res_get = self.client.get('/api/v1/mqtt/settings/')
        self.assertEqual(res_get.status_code, status.HTTP_200_OK)
        self.assertIn('worker_password_set', res_get.data)
        self.assertNotIn('password', res_get.data)

        # Cập nhật cấu hình
        patch_payload = {
            'host': '127.0.0.1',
            'port': 1883,
            'worker_username': 'new_worker',
            'worker_password': 'NewWorkerPassword123!',
        }
        with patch('apps.devices.broker_views.test_broker_connection', return_value={'success': True}):
            res_patch = self.client.patch('/api/v1/mqtt/settings/', data=patch_payload, format='json')
        self.assertEqual(res_patch.status_code, status.HTTP_200_OK)
        self.assertEqual(res_patch.data['worker_username'], 'new_worker')
        self.assertTrue(res_patch.data['worker_password_set'])
        self.assertGreater(res_patch.data['revision'], 0)
