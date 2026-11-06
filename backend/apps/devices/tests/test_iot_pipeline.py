import json
import uuid
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.policies.roles import assign_role, ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER
from apps.alerts.models import Alert
from apps.audit.models import AuditLog
from apps.commands.models import Command, OutboxMessage
from apps.devices.models import Device, DeviceAccess, DeviceConfig
from apps.devices.policies import require_device_permission, visible_devices
from apps.telemetry.models import Telemetry

User = get_user_model()


class DeviceScopeAndPolicyTestCase(TestCase):
    """Kiểm tra phân quyền và phạm vi truy cập thiết bị (Device Scope / IDOR Defense)"""

    def setUp(self):
        self.admin_user = User.objects.create_user(
            username='admin_tv2_test',
            full_name='Admin Test TV2',
            password='TestPassword123!'
        )
        assign_role(self.admin_user, ROLE_ADMIN)

        self.operator_user = User.objects.create_user(
            username='operator_tv2_test',
            full_name='Operator Test TV2',
            password='TestPassword123!'
        )
        assign_role(self.operator_user, ROLE_OPERATOR)

        self.viewer_user = User.objects.create_user(
            username='viewer_tv2_test',
            full_name='Viewer Test TV2',
            password='TestPassword123!'
        )
        assign_role(self.viewer_user, ROLE_VIEWER)

        self.cab1 = Device.objects.create(
            id='CAB-TEST-01',
            name='Tủ Thử Nghiệm 01',
            status='ACTIVE'
        )
        self.cab2 = Device.objects.create(
            id='CAB-TEST-02',
            name='Tủ Thử Nghiệm 02',
            status='ACTIVE'
        )

        # Gán quyền: Operator có OPERATE cab1; Viewer có VIEW cab1; cab2 không gán cho ai
        DeviceAccess.objects.create(user=self.operator_user, device=self.cab1, role='OPERATE')
        DeviceAccess.objects.create(user=self.viewer_user, device=self.cab1, role='VIEW')

    def test_admin_sees_all_devices(self):
        """Admin nhìn thấy toàn bộ thiết bị trong hệ thống"""
        visible = visible_devices(self.admin_user)
        self.assertEqual(visible.count(), 2)

    def test_operator_sees_only_granted_devices(self):
        """Operator chỉ nhìn thấy thiết bị đã được cấp DeviceAccess"""
        visible = visible_devices(self.operator_user)
        self.assertEqual(visible.count(), 1)
        self.assertEqual(visible.first().id, 'CAB-TEST-01')

    def test_require_device_permission_operate(self):
        """Kiểm tra quyền điều khiển quạt (OPERATE)"""
        # Admin được phép điều khiển
        self.assertTrue(require_device_permission(self.admin_user, self.cab1, 'OPERATE'))
        # Operator được phép điều khiển
        self.assertTrue(require_device_permission(self.operator_user, self.cab1, 'OPERATE'))
        # Viewer bị từ chối điều khiển
        with self.assertRaises(PermissionDenied):
            require_device_permission(self.viewer_user, self.cab1, 'OPERATE')

    def test_require_device_permission_unassigned(self):
        """Từ chối truy cập đối với thiết bị ngoài phạm vi được cấp"""
        with self.assertRaises(PermissionDenied):
            require_device_permission(self.operator_user, self.cab2, 'VIEW')


class DeviceAPITestCase(TestCase):
    """Kiểm tra toàn diện các API Thiết bị, Viễn trắc, Lệnh và Cảnh báo"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='operator_api_user',
            full_name='Operator API User',
            password='TestPassword123!'
        )
        assign_role(self.user, ROLE_OPERATOR)
        self.client.force_authenticate(user=self.user)

        self.device = Device.objects.create(
            id='CAB-01',
            name='Tủ Test API',
            status='ACTIVE',
            desired_config={'max_temp': 35.0, 'max_hum': 75.0},
            applied_config={'max_temp': 35.0, 'max_hum': 75.0},
            latest_temperature=30.5,
            latest_humidity=65.0,
            latest_fan_status='OFF',
            last_seen_at=timezone.now()
        )
        DeviceAccess.objects.create(user=self.user, device=self.device, role='OPERATE')

    def test_get_devices_list(self):
        """GET /api/devices/ trả về danh sách thiết bị"""
        res = self.client.get('/api/devices/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        results = data if isinstance(data, list) else data.get('results', [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], 'CAB-01')

    def test_get_device_latest(self):
        """GET /api/devices/{id}/latest/ trả về số đo projection mới nhất"""
        res = self.client.get(f'/api/devices/{self.device.id}/latest/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        self.assertEqual(data['temperature'], 30.5)
        self.assertEqual(data['humidity'], 65.0)
        self.assertEqual(data['fan_status'], 'OFF')

    @patch('apps.devices.views.publish_command_to_broker')
    def test_post_device_control_fan(self, mock_publish):
        """POST /api/devices/{id}/control/ phát lệnh bật quạt và ghi AuditLog"""
        mock_publish.return_value = True

        res = self.client.post(
            f'/api/devices/{self.device.id}/control/',
            {'command': 'SET_FAN', 'fan': 'ON'},
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        data = res.json()
        self.assertEqual(data['status'], 'ACCEPTED')
        self.assertTrue('command_id' in data)
        self.assertTrue('correlation_id' in data)

        # Kiểm tra Command đã được lưu trong DB
        cmd = Command.objects.get(id=data['command_id'])
        self.assertEqual(cmd.command_type, 'SET_FAN')
        self.assertEqual(cmd.payload['fan'], 'ON')
        self.assertEqual(cmd.status, 'SENT')

        # Kiểm tra OutboxMessage
        outbox = OutboxMessage.objects.get(command=cmd)
        self.assertEqual(outbox.status, 'PUBLISHED')

        # Kiểm tra AuditLog
        audit = AuditLog.objects.filter(target_id=self.device.id, function_key='DEVICE_CONTROL').first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.outcome, 'ACCEPTED')

    def test_telemetry_history_and_stats_api(self):
        """Kiểm tra API lịch sử viễn trắc và thống kê min/max/avg"""
        now = timezone.now()
        for i in range(5):
            Telemetry.objects.create(
                device=self.device,
                seq=i + 1,
                temperature=28.0 + i,
                humidity=60.0 + i,
                fan_status='OFF',
                measured_at=now
            )

        # Lấy danh sách viễn trắc
        res = self.client.get(f'/api/telemetry/?device_id={self.device.id}')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        results = data if isinstance(data, list) else data.get('results', [])
        self.assertEqual(len(results), 5)

        # Thống kê stats
        stats_res = self.client.get(f'/api/telemetry/stats/?device_id={self.device.id}')
        self.assertEqual(stats_res.status_code, status.HTTP_200_OK)
        stats_data = stats_res.json()
        self.assertEqual(stats_data['count'], 5)
        self.assertEqual(stats_data['temperature']['min'], 28.0)
        self.assertEqual(stats_data['temperature']['max'], 32.0)

    def test_alert_lifecycle_api(self):
        """Kiểm tra API xử lý cảnh báo: List -> Acknowledge -> Resolve"""
        alert = Alert.objects.create(
            device=self.device,
            alert_type='HIGH_TEMP',
            severity='WARNING',
            status='OPEN',
            message='Nhiệt độ cao thử nghiệm'
        )

        # 1. Ghi nhận cảnh báo (Acknowledge)
        ack_res = self.client.post(f'/api/alerts/{alert.id}/acknowledge/')
        self.assertEqual(ack_res.status_code, status.HTTP_200_OK)
        alert.refresh_from_db()
        self.assertEqual(alert.status, 'ACKNOWLEDGED')
        self.assertIsNotNone(alert.acknowledged_at)

        # 2. Khắc phục cảnh báo (Resolve)
        resolve_res = self.client.post(f'/api/alerts/{alert.id}/resolve/')
        self.assertEqual(resolve_res.status_code, status.HTTP_200_OK)
        alert.refresh_from_db()
        self.assertEqual(alert.status, 'RESOLVED')
        self.assertIsNotNone(alert.resolved_at)


class MQTTWorkerProcessingTestCase(TestCase):
    """Kiểm tra logic xử lý của MQTT Worker: Telemetry Ingest, Alerting, và Command ACK"""

    def setUp(self):
        from apps.devices.management.commands.run_mqtt_worker import Command as WorkerCommand
        self.worker = WorkerCommand()
        self.device = Device.objects.create(
            id='CAB-WORKER-01',
            name='Tủ Test Worker',
            status='ACTIVE',
            desired_config={'max_temp': 35.0, 'max_hum': 75.0}
        )

    def test_process_telemetry_normal(self):
        """Xử lý gói tin viễn trắc bình thường: cập nhật projection và lưu Telemetry"""
        payload = {
            'temperature': 32.5,
            'humidity': 64.0,
            'fan_status': 'ON',
            'door_status': 'CLOSED',
            'mode': 'AUTO',
            'uptime': 3600,
            'seq': 100
        }
        self.worker.process_telemetry(self.device.id, payload)

        self.device.refresh_from_db()
        self.assertEqual(self.device.latest_temperature, 32.5)
        self.assertEqual(self.device.latest_humidity, 64.0)
        self.assertEqual(self.device.latest_fan_status, 'ON')
        self.assertIsNotNone(self.device.last_seen_at)

        # Kiểm tra bản ghi Telemetry
        tele = Telemetry.objects.filter(device=self.device).first()
        self.assertIsNotNone(tele)
        self.assertEqual(tele.seq, 100)
        self.assertEqual(tele.temperature, 32.5)

    def test_process_telemetry_triggers_high_temp_alert(self):
        """Nhiệt độ vượt 35°C kích hoạt cảnh báo HIGH_TEMP"""
        payload = {
            'temperature': 37.2,
            'humidity': 60.0,
            'fan_status': 'ON',
            'door_status': 'CLOSED',
            'mode': 'AUTO'
        }
        self.worker.process_telemetry(self.device.id, payload)

        alert = Alert.objects.filter(device=self.device, alert_type='HIGH_TEMP', status='OPEN').first()
        self.assertIsNotNone(alert)
        self.assertEqual(alert.current_value, 37.2)

        # Nhiệt độ hạ về 33°C tự động RESOLVE cảnh báo
        normal_payload = {
            'temperature': 33.0,
            'humidity': 60.0,
            'fan_status': 'OFF',
            'door_status': 'CLOSED'
        }
        self.worker.process_telemetry(self.device.id, normal_payload)
        alert.refresh_from_db()
        self.assertEqual(alert.status, 'RESOLVED')

    def test_process_ack_updates_command_status(self):
        """Nhận ACK từ node cập nhật trạng thái Command thành ACKED"""
        corr_id = str(uuid.uuid4())
        cmd = Command.objects.create(
            device=self.device,
            command_type='SET_FAN',
            payload={'fan': 'ON'},
            status='SENT',
            correlation_id=corr_id
        )
        outbox = OutboxMessage.objects.create(
            command=cmd,
            topic=f"cabinet/{self.device.id}/command",
            payload={'fan': 'ON'},
            status='PENDING'
        )

        ack_payload = {
            'correlation_id': corr_id,
            'status': 'SUCCESS',
            'message': 'Fan relay closed successfully.'
        }
        self.worker.process_ack(self.device.id, ack_payload)

        cmd.refresh_from_db()
        self.assertEqual(cmd.status, 'ACKED')
        self.assertIsNotNone(cmd.acked_at)

        outbox.refresh_from_db()
        self.assertEqual(outbox.status, 'PUBLISHED')
