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

    def test_anonymous_user_denied_access(self):
        """Người dùng ẩn danh bị từ chối truy cập (401/403) vào toàn bộ API thiết bị"""
        anon_client = APIClient()
        res_list = anon_client.get('/api/devices/')
        self.assertIn(res_list.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

        res_control = anon_client.post(
            f'/api/devices/{self.device.id}/control/',
            {'command': 'SET_FAN', 'fan': 'ON'},
            format='json'
        )
        self.assertIn(res_control.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_disallow_destructive_http_methods(self):
        """Cấm xóa cứng thiết bị và cảnh báo qua REST API (405 Method Not Allowed)"""
        # Cấm DELETE thiết bị
        del_device_res = self.client.delete(f'/api/devices/{self.device.id}/')
        self.assertEqual(del_device_res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

        # Cấm DELETE cảnh báo
        alert = Alert.objects.create(
            device=self.device,
            alert_type='HIGH_TEMP',
            severity='WARNING',
            status='OPEN'
        )
        del_alert_res = self.client.delete(f'/api/alerts/{alert.id}/')
        self.assertEqual(del_alert_res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

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

        # Kiểm tra Command đã được lưu trong DB (kèm trường chuẩn V1.4 cho ESP32)
        cmd = Command.objects.get(id=data['command_id'])
        self.assertEqual(cmd.command_type, 'SET_FAN')
        self.assertEqual(cmd.payload['fan'], 'ON')
        self.assertEqual(cmd.payload['schema_version'], 3)
        self.assertEqual(cmd.payload['type'], 'SET_FAN')
        self.assertEqual(cmd.payload['params']['on'], True)
        self.assertEqual(cmd.status, 'SENT')

        # Kiểm tra OutboxMessage
        outbox = OutboxMessage.objects.get(command=cmd)
        self.assertEqual(outbox.status, 'PUBLISHED')

        # Kiểm tra AuditLog
        audit = AuditLog.objects.filter(target_id=self.device.id, function_key='DEVICE_CONTROL').first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.outcome, 'ACCEPTED')

    @patch('apps.devices.views.publish_command_to_broker')
    def test_maintenance_device_blocks_control_and_config(self, mock_publish):
        """Thiết bị ở trạng thái MAINTENANCE bị chặn gửi lệnh điều khiển và đẩy cấu hình"""
        self.device.status = 'MAINTENANCE'
        self.device.save(update_fields=['status'])

        res_ctrl = self.client.post(
            f'/api/devices/{self.device.id}/control/',
            {'command': 'SET_FAN', 'fan': 'ON'},
            format='json'
        )
        self.assertEqual(res_ctrl.status_code, status.HTTP_400_BAD_REQUEST)

        res_cfg = self.client.post(
            f'/api/devices/{self.device.id}/config/',
            {'desired_config': {'max_temp': 34.0, 'max_hum': 70.0}},
            format='json'
        )
        self.assertEqual(res_cfg.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('apps.devices.views.publish_command_to_broker')
    def test_post_device_config_generates_sha256_v3_payload(self, mock_publish):
        """POST /api/devices/{id}/config/ sinh chuỗi config_json và mã băm SHA-256 khớp ESP32"""
        import hashlib
        mock_publish.return_value = True

        res = self.client.post(
            f'/api/devices/{self.device.id}/config/',
            {'desired_config': {'max_temp': 34.0, 'max_hum': 72.0, 'fan_on_c': 30.0, 'fan_off_c': 28.0}},
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        cmd = Command.objects.filter(device=self.device, command_type='APPLY_CONFIG').latest('created_at')
        params = cmd.payload['params']
        self.assertEqual(cmd.payload['schema_version'], 3)
        self.assertEqual(cmd.payload['type'], 'APPLY_CONFIG')
        expected_hash = hashlib.sha256(params['config_json'].encode('utf-8')).hexdigest()
        self.assertEqual(params['content_hash'], expected_hash)

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

    def test_csv_export_and_formula_injection_defense(self):
        """Kiểm tra xuất dữ liệu CSV viễn trắc với UTF-8 BOM và chống CSV Injection"""
        Telemetry.objects.create(
            device=self.device,
            seq=1,
            temperature=31.5,
            humidity=62.0,
            fan_status='=CMD|dir',  # Ký tự nguy hiểm kiểm thử CSV injection
            mode='AUTO',
            measured_at=timezone.now()
        )

        # Test endpoint /api/devices/{id}/export/
        res = self.client.get(f'/api/devices/{self.device.id}/export/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res['Content-Type'].startswith('text/csv'))
        # Kiểm tra UTF-8 BOM (\xef\xbb\xbf)
        self.assertTrue(res.content.startswith(b'\xef\xbb\xbf'))
        content_text = res.content.decode('utf-8')
        # Kiểm tra tiêu đề cột tiếng Việt
        self.assertIn('Nhiệt độ (°C)', content_text)
        # Kiểm tra giá trị '=CMD|dir' được thêm tiền tố dấu nháy đơn "'"
        self.assertIn("'=CMD|dir", content_text)

        # Test alias endpoint /api/telemetry/export/?device_id={id}
        tele_res = self.client.get(f'/api/telemetry/export/?device_id={self.device.id}')
        self.assertEqual(tele_res.status_code, status.HTTP_200_OK)


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
        """Nhận ACK từ node cập nhật trạng thái Command thành APPLIED"""
        corr_id = str(uuid.uuid4())
        cmd = Command.objects.create(
            device=self.device,
            command_type='SET_FAN',
            payload={'fan': 'ON', 'on': True},
            status='SENT',
            correlation_id=corr_id
        )
        outbox = OutboxMessage.objects.create(
            command=cmd,
            topic=f"cabinet/v3/devices/{self.device.id}/commands",
            payload={'fan': 'ON', 'on': True},
            status='PENDING'
        )

        ack_payload = {
            'correlation_id': corr_id,
            'status': 'SUCCESS',
            'message': 'Fan relay closed successfully.'
        }
        self.worker.process_ack(self.device.id, ack_payload)

        cmd.refresh_from_db()
        self.assertEqual(cmd.status, 'APPLIED')
        self.assertIsNotNone(cmd.acked_at)

        outbox.refresh_from_db()
        self.assertEqual(outbox.status, 'PUBLISHED')

    def test_process_late_ack_transitions_to_applied_late(self):
        """ACK đến muộn sau khi lệnh đã timeout chuyển trạng thái sang APPLIED_LATE"""
        corr_id = str(uuid.uuid4())
        cmd = Command.objects.create(
            device=self.device,
            command_type='SET_FAN',
            payload={'fan': 'ON', 'on': True},
            status='TIMEOUT',
            correlation_id=corr_id
        )

        ack_payload = {
            'correlation_id': corr_id,
            'status': 'SUCCESS',
            'message': 'Late ACK from ESP32 after reconnect.'
        }
        self.worker.process_ack(self.device.id, ack_payload)

        cmd.refresh_from_db()
        self.assertEqual(cmd.status, 'APPLIED_LATE')

    def test_process_v3_telemetry_payload_and_sensor_fault(self):
        """Hỗ trợ payload V1.4 (temperature_c, humidity_pct, fan_output: bool, sensor_ok)"""
        v3_payload = {
            'temperature_c': 29.8,
            'humidity_pct': 58.2,
            'fan_output': True,
            'sensor_ok': True,
            'boot_id': 'boot-test-123',
            'seq': 42
        }
        self.worker.process_telemetry(self.device.id, v3_payload)

        self.device.refresh_from_db()
        self.assertEqual(self.device.latest_temperature, 29.8)
        self.assertEqual(self.device.latest_humidity, 58.2)
        self.assertEqual(self.device.latest_fan_status, 'ON')

        # Khi sensor_ok = False -> Kích hoạt SENSOR_FAULT
        fault_payload = {
            'temperature_c': 0.0,
            'humidity_pct': 0.0,
            'sensor_ok': False,
            'seq': 43
        }
        self.worker.process_telemetry(self.device.id, fault_payload)
        fault_alert = Alert.objects.filter(device=self.device, alert_type='SENSOR_FAULT', status='OPEN').first()
        self.assertIsNotNone(fault_alert)
        self.assertEqual(fault_alert.severity, 'CRITICAL')

    def test_tv1_firmware_exact_payload_contract(self):
        """Kiểm tra tương thích 100% với định dạng bản tin firmware ESP32 của TV1"""
        # 1. Telemetry khi DHT22 lỗi (temperature_c=None, humidity_pct=None, sensor_ok=False)
        tv1_fault_telemetry = {
            "schema_version": 3,
            "device_id": self.device.id,
            "profile": "CONTROLLER",
            "fan_binding_id": None,
            "boot_id": str(uuid.uuid4()),
            "connection_id": str(uuid.uuid4()),
            "seq": 15,
            "measured_at": "2026-10-09T13:00:15Z",
            "clock_synced": True,
            "uptime_ms": 75000,
            "temperature_c": None,
            "humidity_pct": None,
            "sensor_ok": False,
            "fan_output": True,
            "mode": "AUTO",
            "config_version": 1,
            "config_hash": "3520a5738ade36fecf0a3f72ecf34e9f53fb2b2fe23e0a20245393e6a57a91cb",
            "fault_code": "DHT_READ_ERROR",
            "extra": {},
        }
        self.worker.process_telemetry(self.device.id, tv1_fault_telemetry)
        self.device.refresh_from_db()
        self.assertEqual(self.device.latest_fan_status, 'ON')
        alert = Alert.objects.filter(device=self.device, alert_type='SENSOR_FAULT', status='OPEN').first()
        self.assertIsNotNone(alert)
        self.assertEqual(alert.severity, 'CRITICAL')

        # 2. ACK payload chuẩn V1.4 do ESP32 phát ra
        cmd_id = str(uuid.uuid4())
        cmd = Command.objects.create(
            device=self.device,
            command_type='SET_FAN',
            payload={'fan': 'ON', 'on': True},
            status='SENT',
            correlation_id=cmd_id
        )
        tv1_ack = {
            "schema_version": 3,
            "device_id": self.device.id,
            "command_id": cmd_id,
            "status": "APPLIED",
            "type": "SET_FAN",
            "boot_id": str(uuid.uuid4()),
            "connection_id": str(uuid.uuid4()),
            "device_command_seq": 8,
            "executed_at": "2026-10-09T13:00:01Z",
            "result": {
                "fan_output": True,
                "mode": "MANUAL",
                "config_version": 1,
            },
        }
        self.worker.process_ack(self.device.id, tv1_ack)
        cmd.refresh_from_db()
        self.assertEqual(cmd.status, 'APPLIED')
        self.assertIsNotNone(cmd.acked_at)

    def test_check_offline_devices_creates_alert(self):
        """Thiết bị không gửi viễn trắc quá 30 giây kích hoạt DEVICE_OFFLINE"""
        from datetime import timedelta
        self.device.last_seen_at = timezone.now() - timedelta(seconds=45)
        self.device.save(update_fields=['last_seen_at'])

        self.worker.check_offline_devices()

        offline_alert = Alert.objects.filter(device=self.device, alert_type='DEVICE_OFFLINE', status='OPEN').first()
        self.assertIsNotNone(offline_alert)
        self.assertEqual(offline_alert.severity, 'WARNING')
