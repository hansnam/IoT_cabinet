import json
import uuid
from datetime import timedelta
from apps.accounts.models import AuthSession
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
        self.client.force_login(self.user)
        session = self.client.session
        registry = AuthSession.objects.create(user=self.user, session_key=session.session_key,
            expires_at=timezone.now() + timedelta(minutes=15))
        session['auth_session_id'] = str(registry.pk)
        session.save()

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
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        cmd = Command.objects.filter(device=self.device, command_type='APPLY_CONFIG').latest('created_at')
        params = cmd.payload['params']
        self.assertEqual(cmd.payload['schema_version'], 3)
        self.assertEqual(cmd.payload['type'], 'APPLY_CONFIG')
        expected_hash = hashlib.sha256(params['config_json'].encode('utf-8')).hexdigest()
        self.assertEqual(params['content_hash'], expected_hash)

    @patch('apps.devices.views.publish_command_to_broker')
    def test_post_device_control_set_mode_and_config_bounds(self, mock_publish):
        """Kiểm tra validate duration_s [10..300] cho SET_MODE và ngưỡng config V1.4"""
        mock_publish.return_value = True

        bad_mode = self.client.post(
            f'/api/devices/{self.device.id}/control/',
            {'mode': 'MANUAL', 'duration_s': 5},
            format='json',
        )
        self.assertEqual(bad_mode.status_code, status.HTTP_400_BAD_REQUEST)

        ok_mode = self.client.post(
            f'/api/devices/{self.device.id}/control/',
            {'mode': 'MANUAL', 'duration_s': 120},
            format='json',
        )
        self.assertEqual(ok_mode.status_code, status.HTTP_202_ACCEPTED)
        cmd = Command.objects.get(id=ok_mode.json()['command_id'])
        self.assertEqual(cmd.command_type, 'SET_MODE')
        self.assertEqual(cmd.payload['params']['duration_s'], 120)

        bad_cfg = self.client.post(
            f'/api/devices/{self.device.id}/config/',
            {'desired_config': {'fan_on_c': 28.0, 'fan_off_c': 30.0}},
            format='json',
        )
        self.assertEqual(bad_cfg.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('apps.devices.views.sync_device_mqtt_access', return_value=True)
    def test_patch_device_status_syncs_mqtt_dynsec(self, mock_sync):
        """OPERATOR bị chặn PATCH status (403); ADMIN đổi status -> gọi sync_device_mqtt_access và ghi AuditLog"""
        denied = self.client.patch(
            f'/api/devices/{self.device.id}/',
            {'status': 'LOCKED'},
            format='json',
        )
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        mock_sync.assert_not_called()

        admin_user = User.objects.create_user(
            username='admin_dynsec_test',
            full_name='Admin Dynsec Test',
            password='TestPassword123!',
        )
        assign_role(admin_user, ROLE_ADMIN)
        admin_client = APIClient()
        admin_client.force_login(admin_user)
        admin_session = admin_client.session
        registry = AuthSession.objects.create(
            user=admin_user,
            session_key=admin_session.session_key,
            expires_at=timezone.now() + timedelta(minutes=15),
        )
        admin_session['auth_session_id'] = str(registry.pk)
        admin_session.save()

        res = admin_client.patch(
            f'/api/devices/{self.device.id}/',
            {'status': 'LOCKED'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mock_sync.assert_called_once_with(self.device.id, 'LOCKED')
        self.assertTrue(
            AuditLog.objects.filter(
                target_id=self.device.id,
                function_key='DEVICE_MANAGEMENT',
                action='DEVICE_STATUS_UPDATED',
            ).exists()
        )

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
        self.assertIsNone(self.device.latest_temperature)
        self.assertIsNone(self.device.latest_humidity)
        fault_tele = Telemetry.objects.filter(device=self.device, seq=15).first()
        self.assertIsNotNone(fault_tele)
        self.assertIsNone(fault_tele.temperature)
        self.assertIsNone(fault_tele.humidity)
        self.assertEqual(fault_tele.uptime, 75)
        self.assertEqual(fault_tele.measured_at.year, 2026)
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
        """Thiết bị không gửi viễn trắc quá 45 giây kích hoạt DEVICE_OFFLINE"""
        from datetime import timedelta
        self.device.last_seen_at = timezone.now() - timedelta(seconds=46)
        self.device.save(update_fields=['last_seen_at'])

        self.worker.check_offline_devices()

        offline_alert = Alert.objects.filter(device=self.device, alert_type='DEVICE_OFFLINE', status='OPEN').first()
        self.assertIsNotNone(offline_alert)
        self.assertEqual(offline_alert.severity, 'WARNING')

    def test_process_ack_apply_config_updates_applied_config(self):
        """Nhận ACK thành công cho APPLY_CONFIG cập nhật device.applied_config, DeviceConfig và evaluate_alerts V1.4"""
        new_cfg = {
            'temp_alert_c': 32.0,
            'humidity_alert_pct': 70.0,
            'temp_clear_margin_c': 2.0,
            'humidity_clear_margin_pct': 5.0,
        }
        DeviceConfig.objects.create(device=self.device, version=2, config_data=new_cfg, applied=False)
        cmd_id = str(uuid.uuid4())
        Command.objects.create(
            device=self.device,
            command_type='APPLY_CONFIG',
            payload={
                'schema_version': 3,
                'command_id': cmd_id,
                'type': 'APPLY_CONFIG',
                'config_version': 2,
                'config': new_cfg,
            },
            status='SENT',
            correlation_id=cmd_id,
        )

        self.worker.process_ack(self.device.id, {'command_id': cmd_id, 'status': 'APPLIED'})
        self.device.refresh_from_db()
        self.assertEqual(self.device.applied_config, new_cfg)
        cfg_row = DeviceConfig.objects.get(device=self.device, version=2)
        self.assertTrue(cfg_row.applied)
        self.assertIsNotNone(cfg_row.applied_at)

        # Kiểm tra evaluate_alerts dùng khóa V1.4 (temp_alert_c=32.0, temp_clear_margin_c=2.0)
        self.worker.process_telemetry(self.device.id, {'temperature_c': 33.0, 'humidity_pct': 50.0, 'seq': 201})
        self.assertTrue(Alert.objects.filter(device=self.device, alert_type='HIGH_TEMP', status='OPEN').exists())

        # Hạ còn 31.0 (> 32 - 2 = 30): vẫn giữ OPEN
        self.worker.process_telemetry(self.device.id, {'temperature_c': 31.0, 'humidity_pct': 50.0, 'seq': 202})
        self.assertTrue(Alert.objects.filter(device=self.device, alert_type='HIGH_TEMP', status='OPEN').exists())

        # Hạ về 30.0 (<= 32 - 2): tự động RESOLVED
        self.worker.process_telemetry(self.device.id, {'temperature_c': 30.0, 'humidity_pct': 50.0, 'seq': 203})
        self.assertFalse(Alert.objects.filter(device=self.device, alert_type='HIGH_TEMP', status='OPEN').exists())

    def test_sweep_pending_outbox_checks_rc_and_retries_at_3s_6s(self):
        """Worker kiểm tra rc qua dispatch_outbox và retry ở mốc 3s / 6s cho lệnh SENT chưa ACK"""
        from unittest.mock import MagicMock

        user = User.objects.create_user(username='retry_op', full_name='Retry Op', password='TestPassword123!')
        assign_role(user, ROLE_OPERATOR)
        DeviceAccess.objects.create(user=user, device=self.device, role='OPERATE')
        session = AuthSession.objects.create(
            user=user,
            session_key='retry-session-key',
            expires_at=timezone.now() + timedelta(minutes=15),
        )
        corr_id = str(uuid.uuid4())
        cmd = Command.objects.create(
            device=self.device,
            actor=user,
            command_type='SET_FAN',
            payload={'command_id': corr_id, 'on': True, '_auth_session_id': str(session.pk)},
            status='PENDING',
            correlation_id=corr_id,
        )
        outbox = OutboxMessage.objects.create(
            command=cmd,
            topic=f"cabinet/v3/devices/{self.device.id}/commands",
            payload=cmd.payload,
            status='PENDING',
        )

        # 1. Broker trả về rc=4 -> dispatch_outbox giữ nguyên PENDING
        bad_client = MagicMock()
        bad_client.publish.return_value = MagicMock(rc=4)
        self.worker.sweep_pending_outbox(bad_client)
        outbox.refresh_from_db()
        cmd.refresh_from_db()
        self.assertEqual(outbox.status, 'PENDING')
        self.assertEqual(cmd.status, 'PENDING')

        # Đặt lại retry_count=0 rồi publish thành công -> PUBLISHED, SENT, retry_count=1
        outbox.retry_count = 0
        outbox.save(update_fields=['retry_count'])
        ok_client = MagicMock()
        ok_client.publish.return_value = MagicMock(rc=0)
        self.worker.sweep_pending_outbox(ok_client)
        outbox.refresh_from_db()
        cmd.refresh_from_db()
        self.assertEqual(outbox.status, 'PUBLISHED')
        self.assertEqual(cmd.status, 'SENT')
        self.assertEqual(outbox.retry_count, 1)

        # 2. Sau 4 giây chưa ACK -> retry lần 1 (retry_count = 2)
        cmd.sent_at = timezone.now() - timedelta(seconds=4)
        cmd.save(update_fields=['sent_at'])
        ok_client.publish.reset_mock()
        self.worker.sweep_pending_outbox(ok_client)
        outbox.refresh_from_db()
        self.assertEqual(outbox.retry_count, 2)
        self.assertTrue(ok_client.publish.called)

        # 3. Sau 7 giây chưa ACK -> retry lần 2 (retry_count = 3)
        cmd.sent_at = timezone.now() - timedelta(seconds=7)
        cmd.save(update_fields=['sent_at'])
        ok_client.publish.reset_mock()
        self.worker.sweep_pending_outbox(ok_client)
        outbox.refresh_from_db()
        self.assertEqual(outbox.retry_count, 3)
        self.assertTrue(ok_client.publish.called)

    def test_build_dynsec_payload_and_sync_publishes_to_control_topic(self):
        """Kiểm tra helper tạo payload Dynamic Security và publish tới $CONTROL/dynamic-security/v1"""
        from django.test import override_settings
        from apps.devices.mqtt_client import (
            DYNSEC_CONTROL_TOPIC,
            build_dynsec_client_status_payload,
            sync_device_mqtt_access,
        )

        self.assertEqual(
            build_dynsec_client_status_payload('cabinet-001', 'LOCKED'),
            {'commands': [{'command': 'disableClient', 'username': 'cabinet-001'}]},
        )
        self.assertEqual(
            build_dynsec_client_status_payload('cabinet-001', 'DECOMMISSIONED'),
            {'commands': [{'command': 'disableClient', 'username': 'cabinet-001'}]},
        )
        self.assertEqual(
            build_dynsec_client_status_payload('cabinet-001', 'ACTIVE'),
            {'commands': [{'command': 'enableClient', 'username': 'cabinet-001'}]},
        )
        self.assertIsNone(build_dynsec_client_status_payload('cabinet-001', 'MAINTENANCE'))

        with override_settings(MQTT_ADMIN_USERNAME='', MQTT_ADMIN_PASSWORD=''):
            with patch('apps.devices.mqtt_client._create_connected_mqtt_client') as mock_conn:
                fake_client = mock_conn.return_value
                fake_client.publish.return_value.rc = 0
                fake_client.publish.return_value.is_published.return_value = True
                ok = sync_device_mqtt_access('cabinet-001', 'LOCKED')
                self.assertTrue(ok)
                args, _ = fake_client.publish.call_args
                self.assertEqual(args[0], DYNSEC_CONTROL_TOPIC)
                self.assertEqual(json.loads(args[1]), {'commands': [{'command': 'disableClient', 'username': 'cabinet-001'}]})

        with override_settings(MQTT_ADMIN_USERNAME='admin', MQTT_ADMIN_PASSWORD='secret'):
            with patch('apps.devices.broker_security.broker_command', return_value={}) as mock_broker_cmd:
                ok = sync_device_mqtt_access('cabinet-001', 'LOCKED')
                self.assertTrue(ok)
                mock_broker_cmd.assert_called_once_with('disableClient', username='cabinet-001')

    def test_process_telemetry_deduplicates_msg_id_and_boot_seq(self):
        """Gói tin telemetry trùng msg_id hoặc trùng (boot_id, seq) chỉ được lưu 1 lần"""
        payload_with_msg_id = {
            'msg_id': 'cabinet-msg-001',
            'temperature_c': 30.2,
            'humidity_pct': 61.5,
            'fan_output': False,
            'seq': 10,
        }
        self.worker.process_telemetry(self.device.id, payload_with_msg_id)
        self.worker.process_telemetry(self.device.id, payload_with_msg_id)
        self.assertEqual(Telemetry.objects.filter(device=self.device, msg_id='cabinet-msg-001').count(), 1)

        # Gói tin từ firmware ESP32 có (boot_id, seq) -> tự tạo khóa chống trùng boot_id:seq
        esp32_payload = {
            'boot_id': 'boot-uuid-1234',
            'seq': 5,
            'temperature_c': 31.0,
            'humidity_pct': 63.0,
            'fan_output': True,
        }
        self.worker.process_telemetry(self.device.id, esp32_payload)
        self.worker.process_telemetry(self.device.id, esp32_payload)
        self.assertEqual(Telemetry.objects.filter(device=self.device, msg_id='boot-uuid-1234:5').count(), 1)

        # Payload legacy không có msg_id và không có boot_id vẫn lưu bình thường (msg_id=None)
        legacy_payload = {'temperature': 29.0, 'humidity': 59.0}
        self.worker.process_telemetry(self.device.id, legacy_payload)
        self.worker.process_telemetry(self.device.id, legacy_payload)
        self.assertEqual(Telemetry.objects.filter(device=self.device, msg_id__isnull=True).count(), 2)
