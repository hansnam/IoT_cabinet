import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.policies.roles import assign_role
from apps.alerts.models import Alert
from apps.commands.models import Command as DeviceCommand
from apps.devices.models import BrokerConnection, Device, DeviceAccess, DeviceConfig
from apps.telemetry.models import Telemetry

User = get_user_model()


class Command(BaseCommand):
    help = "Khởi tạo dữ liệu mẫu phong phú trong CSDL để kiểm thử toàn diện mọi chức năng giao diện."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("=== BẮT ĐẦU KHỞI TẠO DỮ LIỆU KIỂM THỬ HỆ THỐNG ==="))

        now = timezone.now()

        # -------------------------------------------------------------
        # 1. ĐỒNG BỘ VAI TRÒ & KHỞI TẠO TÀI KHOẢN NGƯỜI DÙNG CÁC LOẠI
        # -------------------------------------------------------------
        self.stdout.write("1. Khởi tạo vai trò và các tài khoản người dùng mẫu...")
        call_command('seed_roles')

        # Tài khoản ADMIN
        admin_user, _ = User.objects.get_or_create(
            username='admin',
            defaults={
                'full_name': 'Hệ Thống Quản Trị Viên',
                'email': 'admin@iotcabinet.vn',
                'status': 'ACTIVE',
                'is_staff': True,
                'is_active': True,
                'must_change_password': False,
            }
        )
        if not admin_user.has_usable_password():
            admin_user.set_password('AdminPassword123!')
        admin_user.status = 'ACTIVE'
        admin_user.is_active = True
        admin_user.is_staff = True
        admin_user.must_change_password = False
        admin_user.save()
        assign_role(admin_user, 'ADMIN')

        # Tài khoản OPERATOR (Người vận hành)
        operator_user, op_created = User.objects.get_or_create(
            username='operator1',
            defaults={
                'full_name': 'Nguyễn Văn Vận Hành',
                'email': 'operator1@iotcabinet.vn',
                'status': 'ACTIVE',
                'is_staff': False,
                'is_active': True,
                'must_change_password': False,
            }
        )
        operator_user.set_password('Operator123456!')
        operator_user.status = 'ACTIVE'
        operator_user.is_active = True
        operator_user.must_change_password = False
        operator_user.save()
        assign_role(operator_user, 'OPERATOR')
        self.stdout.write(f"  + Tài khoản Người vận hành: 'operator1' (Mật khẩu: Operator123456!)")

        # Tài khoản VIEWER (Người chỉ xem)
        viewer_user, vw_created = User.objects.get_or_create(
            username='viewer1',
            defaults={
                'full_name': 'Trần Thị Người Xem',
                'email': 'viewer1@iotcabinet.vn',
                'status': 'ACTIVE',
                'is_staff': False,
                'is_active': True,
                'must_change_password': False,
            }
        )
        viewer_user.set_password('Viewer123456!')
        viewer_user.status = 'ACTIVE'
        viewer_user.is_active = True
        viewer_user.must_change_password = False
        viewer_user.save()
        assign_role(viewer_user, 'VIEWER')
        self.stdout.write(f"  + Tài khoản Người xem: 'viewer1' (Mật khẩu: Viewer123456!)")

        # Tài khoản bị tạm khóa (LOCKED) để test trạng thái hiển thị
        locked_user, _ = User.objects.get_or_create(
            username='user_locked',
            defaults={
                'full_name': 'Lê Văn Tạm Khóa',
                'email': 'locked@iotcabinet.vn',
                'status': 'LOCKED',
                'is_staff': False,
                'is_active': False,
                'must_change_password': False,
            }
        )
        locked_user.set_password('Operator123456!')
        locked_user.status = 'LOCKED'
        locked_user.is_active = False
        locked_user.save()
        assign_role(locked_user, 'OPERATOR')
        self.stdout.write(f"  + Tài khoản Tạm khóa: 'user_locked'")

        # -------------------------------------------------------------
        # 2. KHỞI TẠO DANH SÁCH THIẾT BỊ TỦ MẠNG ĐA DẠNG TRẠNG THÁI
        # -------------------------------------------------------------
        self.stdout.write("2. Khởi tạo danh sách 5 tủ mạng mẫu...")

        default_cfg = {
            'profile': 'CONTROLLER',
            'fan_on_c': 30.0,
            'fan_off_c': 28.0,
            'temp_alert_c': 35.0,
            'humidity_alert_pct': 80.0,
            'telemetry_interval_ms': 5000,
            'manual_timeout_s': 60,
        }

        # Tủ 1: cabinet-001 (Node thực tế / ESP32 Wokwi) - Đang kết nối, bình thường
        cab_fw, _ = Device.objects.update_or_create(
            id='cabinet-001',
            defaults={
                'name': 'Tủ Mạng ESP32 Wokwi / Node Thực Tế',
                'source': 'REAL',
                'status': 'ACTIVE',
                'hardware_version': 'ESP32-WROOM-32D',
                'firmware_version': 'v1.4.0',
                'ip_address': '192.168.1.100',
                'desired_config': default_cfg,
                'applied_config': default_cfg,
                'last_seen_at': now,
                'latest_temperature': 29.5,
                'latest_humidity': 62.0,
                'latest_fan_status': 'OFF',
                'latest_door_status': 'CLOSED',
                'latest_mode': 'AUTO',
            }
        )

        # Tủ 2: CABINET-01 (Phòng Server Lab 01) - Đang kết nối, nhiệt độ cao, quạt đang bật
        cab1, _ = Device.objects.update_or_create(
            id='CABINET-01',
            defaults={
                'name': 'Tủ Mạng Phòng Server Lab 01',
                'source': 'REAL',
                'status': 'ACTIVE',
                'hardware_version': 'ESP32-WROOM-32D',
                'firmware_version': 'v1.4.0',
                'ip_address': '192.168.1.150',
                'mac_address': '24:6F:28:B1:A2:30',
                'desired_config': {**default_cfg, 'fan_on_c': 31.0, 'fan_off_c': 29.0},
                'applied_config': {**default_cfg, 'fan_on_c': 31.0, 'fan_off_c': 29.0},
                'last_seen_at': now,
                'latest_temperature': 35.8,
                'latest_humidity': 71.0,
                'latest_fan_status': 'ON',
                'latest_door_status': 'CLOSED',
                'latest_mode': 'AUTO',
            }
        )

        # Tủ 3: CABINET-02 (Tủ Tầng 2 Viễn Thông) - MẤT KẾT NỐI (Offline)
        cab2, _ = Device.objects.update_or_create(
            id='CABINET-02',
            defaults={
                'name': 'Tủ Mạng Tầng 2 Viễn Thông (Mô phỏng)',
                'source': 'SIMULATED',
                'status': 'ACTIVE',
                'hardware_version': 'SIMULATOR-X86',
                'firmware_version': 'v1.4.0',
                'ip_address': '192.168.1.151',
                'desired_config': default_cfg,
                'applied_config': default_cfg,
                'last_seen_at': now - timedelta(minutes=25),  # Mất kết nối 25 phút
                'latest_temperature': 28.0,
                'latest_humidity': 58.0,
                'latest_fan_status': 'OFF',
                'latest_door_status': 'CLOSED',
                'latest_mode': 'AUTO',
            }
        )

        # Tủ 4: CABINET-03 (Tủ Dự Phòng Mới Nhập Kho) - Trạng thái REGISTERED (chưa kích hoạt / chưa có MQTT)
        cab3, _ = Device.objects.update_or_create(
            id='CABINET-03',
            defaults={
                'name': 'Tủ Mạng Dự Phòng Mới Nhập Kho',
                'source': 'REAL',
                'status': 'REGISTERED',
                'hardware_version': 'ESP32-S3',
                'firmware_version': 'v2.0.0-rc1',
                'desired_config': default_cfg,
                'applied_config': {},
                'last_seen_at': None,
                'latest_temperature': None,
                'latest_humidity': None,
                'latest_fan_status': 'OFF',
                'latest_door_status': 'CLOSED',
                'latest_mode': 'AUTO',
            }
        )

        # Tủ 5: CABINET-04 (Tủ Khu Vực Bảo Trì) - Trạng thái LOCKED (Tạm khóa)
        cab4, _ = Device.objects.update_or_create(
            id='CABINET-04',
            defaults={
                'name': 'Tủ Khu Vực Bảo Trì Kỹ Thuật',
                'source': 'REAL',
                'status': 'LOCKED',
                'hardware_version': 'ESP32-WROOM-32D',
                'firmware_version': 'v1.4.0',
                'desired_config': default_cfg,
                'applied_config': default_cfg,
                'last_seen_at': now - timedelta(hours=2),
                'latest_temperature': 27.2,
                'latest_humidity': 55.0,
                'latest_fan_status': 'OFF',
                'latest_door_status': 'CLOSED',
                'latest_mode': 'MANUAL',
            }
        )

        # Lưu bản ghi cấu hình DeviceConfig
        for dev in (cab_fw, cab1, cab2, cab4):
            DeviceConfig.objects.get_or_create(
                device=dev,
                version=1,
                defaults={
                    'config_data': dev.desired_config,
                    'applied': True,
                    'applied_at': now,
                }
            )

        # -------------------------------------------------------------
        # 3. PHÂN QUYỀN TRUY CẬP TỦ (DEVICE ACCESS SCOPING)
        # -------------------------------------------------------------
        self.stdout.write("3. Thiết lập phân quyền truy cập tủ mạng...")

        # ADMIN: có quyền vận hành toàn bộ 5 tủ
        for dev in (cab_fw, cab1, cab2, cab3, cab4):
            DeviceAccess.objects.update_or_create(
                user=admin_user,
                device=dev,
                defaults={'role': 'OPERATE', 'granted_at': now}
            )

        # OPERATOR1: được cấp quyền vận hành cabinet-001, CABINET-01 và chỉ xem CABINET-02
        DeviceAccess.objects.update_or_create(
            user=operator_user,
            device=cab_fw,
            defaults={'role': 'OPERATE', 'granted_at': now}
        )
        DeviceAccess.objects.update_or_create(
            user=operator_user,
            device=cab1,
            defaults={'role': 'OPERATE', 'granted_at': now}
        )
        DeviceAccess.objects.update_or_create(
            user=operator_user,
            device=cab2,
            defaults={'role': 'VIEW', 'granted_at': now}
        )
        # Thu hồi các tủ còn lại để chứng minh phân quyền scope thiết bị
        DeviceAccess.objects.filter(user=operator_user, device__in=[cab3, cab4]).delete()

        # VIEWER1: chỉ xem cabinet-001, CABINET-01, CABINET-02
        for dev in (cab_fw, cab1, cab2):
            DeviceAccess.objects.update_or_create(
                user=viewer_user,
                device=dev,
                defaults={'role': 'VIEW', 'granted_at': now}
            )
        DeviceAccess.objects.filter(user=viewer_user, device__in=[cab3, cab4]).delete()

        # -------------------------------------------------------------
        # 4. KHỞI TẠO DỮ LIỆU SỐ ĐO VIỄN TRẮC (TELEMETRY HISTORY)
        # -------------------------------------------------------------
        self.stdout.write("4. Khởi tạo chuỗi số đo viễn trắc 2 giờ gần nhất...")

        Telemetry.objects.filter(device__in=[cab_fw, cab1, cab2]).delete()

        # 30 bản ghi cho cabinet-001 (Nhiệt độ dao động nhẹ 28.5 - 30.5°C)
        telemetries_cab_fw = []
        for i in range(30):
            t_point = now - timedelta(minutes=(30 - i) * 4)
            temp = round(28.5 + (i * 0.15) % 2.0, 1)
            hum = round(60.0 + (i * 0.25) % 5.0, 1)
            telemetries_cab_fw.append(Telemetry(
                device=cab_fw,
                seq=i + 1,
                temperature=temp,
                humidity=hum,
                fan_status='OFF',
                door_status='CLOSED',
                mode='AUTO',
                uptime=200 + i * 240,
                measured_at=t_point,
                raw_data={'source': 'seed'}
            ))
        Telemetry.objects.bulk_create(telemetries_cab_fw)

        # 30 bản ghi cho CABINET-01 (Nhiệt độ tăng dần từ 31.0 lên 35.8°C, quạt bật khi vượt 31°C)
        telemetries_cab1 = []
        for i in range(30):
            t_point = now - timedelta(minutes=(30 - i) * 4)
            temp = round(31.0 + (i * 0.18), 1)
            hum = round(66.0 + (i * 0.2) % 6.0, 1)
            fan = 'ON' if temp >= 31.0 else 'OFF'
            telemetries_cab1.append(Telemetry(
                device=cab1,
                seq=i + 1,
                temperature=temp,
                humidity=hum,
                fan_status=fan,
                door_status='CLOSED',
                mode='AUTO',
                uptime=500 + i * 240,
                measured_at=t_point,
                raw_data={'source': 'seed'}
            ))
        Telemetry.objects.bulk_create(telemetries_cab1)

        # 10 bản ghi cho CABINET-02 trước khi mất kết nối
        telemetries_cab2 = []
        for i in range(10):
            t_point = now - timedelta(minutes=25 + (10 - i) * 5)
            telemetries_cab2.append(Telemetry(
                device=cab2,
                seq=i + 1,
                temperature=round(27.5 + i * 0.1, 1),
                humidity=round(57.0 + i * 0.2, 1),
                fan_status='OFF',
                door_status='CLOSED',
                mode='AUTO',
                uptime=100 + i * 300,
                measured_at=t_point,
                raw_data={'source': 'seed'}
            ))
        Telemetry.objects.bulk_create(telemetries_cab2)
        self.stdout.write(f"  + Đã tạo 70 bản ghi viễn trắc cho 3 tủ.")

        # -------------------------------------------------------------
        # 5. KHỞI TẠO CẢNH BÁO HỆ THỐNG ĐA DẠNG MỨC ĐỘ & TRẠNG THÁI
        # -------------------------------------------------------------
        self.stdout.write("5. Khởi tạo cảnh báo mẫu (Critical, Warning, Info, Resolved)...")

        Alert.objects.all().delete()

        # Cảnh báo 1: Nhiệt độ cao nguy cấp (CRITICAL, OPEN) trên CABINET-01 -> để test nút Xác nhận và Đóng
        Alert.objects.create(
            device=cab1,
            alert_type='HIGH_TEMP',
            severity='CRITICAL',
            status='OPEN',
            message="Nhiệt độ tủ CABINET-01 đạt 36.8°C, vượt ngưỡng an toàn (35.0°C)!",
            threshold=35.0,
            current_value=36.8,
            created_at=now - timedelta(minutes=18),
        )

        # Cảnh báo 2: Độ ẩm cao (WARNING, ACKNOWLEDGED) trên cabinet-001 -> đang được xử lý
        Alert.objects.create(
            device=cab_fw,
            alert_type='HUMIDITY_HIGH',
            severity='WARNING',
            status='ACKNOWLEDGED',
            message="Độ ẩm không khí trong tủ cabinet-001 đạt 82.5%, vượt ngưỡng cài đặt (80.0%)!",
            threshold=80.0,
            current_value=82.5,
            created_at=now - timedelta(minutes=45),
            acknowledged_at=now - timedelta(minutes=30),
            acknowledged_by=operator_user,
        )

        # Cảnh báo 3: Tủ mất kết nối (WARNING, OPEN) trên CABINET-02
        Alert.objects.create(
            device=cab2,
            alert_type='DEVICE_OFFLINE',
            severity='WARNING',
            status='OPEN',
            message="Tủ mạng CABINET-02 mất kết nối với máy chủ hơn 20 phút!",
            created_at=now - timedelta(minutes=22),
        )

        # Cảnh báo 4: Mở cửa tủ định kỳ (INFO, RESOLVED) trên CABINET-01
        Alert.objects.create(
            device=cab1,
            alert_type='DOOR_OPEN',
            severity='INFO',
            status='RESOLVED',
            message="Cửa tủ mạng CABINET-01 đã được đóng an toàn sau khi kỹ thuật viên kiểm tra định kỳ.",
            created_at=now - timedelta(hours=3),
            acknowledged_at=now - timedelta(hours=2, minutes=50),
            acknowledged_by=admin_user,
            resolved_at=now - timedelta(hours=2, minutes=45),
        )

        # -------------------------------------------------------------
        # 6. KHỞI TẠO LỊCH SỬ LỆNH ĐIỀU KHIỂN (COMMANDS)
        # -------------------------------------------------------------
        self.stdout.write("6. Khởi tạo lịch sử phát lệnh điều khiển...")

        DeviceCommand.objects.all().delete()

        # Lệnh 1: Bật quạt (APPLIED) do operator1 thực hiện
        DeviceCommand.objects.create(
            device=cab1,
            command_type='SET_FAN',
            payload={'command': 'SET_FAN', 'fan': 'ON'},
            status='APPLIED',
            correlation_id=str(uuid.uuid4()),
            actor=operator_user,
            sent_at=now - timedelta(minutes=15),
            acked_at=now - timedelta(minutes=14),
        )

        # Lệnh 2: Chuyển chế độ Tự động (APPLIED) do admin thực hiện
        DeviceCommand.objects.create(
            device=cab1,
            command_type='SET_MODE',
            payload={'command': 'SET_MODE', 'mode': 'AUTO'},
            status='APPLIED',
            correlation_id=str(uuid.uuid4()),
            actor=admin_user,
            sent_at=now - timedelta(minutes=30),
            acked_at=now - timedelta(minutes=29),
        )

        # Lệnh 3: Đồng bộ cấu hình ngưỡng (APPLIED) do admin thực hiện
        DeviceCommand.objects.create(
            device=cab_fw,
            command_type='APPLY_CONFIG',
            payload={'command': 'APPLY_CONFIG', 'config': default_cfg},
            status='APPLIED',
            correlation_id=str(uuid.uuid4()),
            actor=admin_user,
            sent_at=now - timedelta(hours=1),
            acked_at=now - timedelta(hours=1, seconds=-5),
        )

        # Lệnh 4: Tắt quạt trên tủ mất kết nối (TIMEOUT)
        DeviceCommand.objects.create(
            device=cab2,
            command_type='SET_FAN',
            payload={'command': 'SET_FAN', 'fan': 'OFF'},
            status='TIMEOUT',
            correlation_id=str(uuid.uuid4()),
            actor=operator_user,
            sent_at=now - timedelta(minutes=20),
            error_message="Quá thời gian chờ phản hồi ACK từ thiết bị (Timeout 10s)",
        )

        # Lệnh 5: Lệnh đang gửi (SENT)
        DeviceCommand.objects.create(
            device=cab_fw,
            command_type='SET_FAN',
            payload={'command': 'SET_FAN', 'fan': 'ON'},
            status='SENT',
            correlation_id=str(uuid.uuid4()),
            actor=admin_user,
            sent_at=now - timedelta(seconds=10),
        )

        # -------------------------------------------------------------
        # 7. KHỞI TẠO CẤU HÌNH BROKER CONNECTION RUNTIME
        # -------------------------------------------------------------
        self.stdout.write("7. Khởi tạo cấu hình Broker runtime...")

        broker_conn, _ = BrokerConnection.objects.get_or_create(id=1)
        broker_conn.host = '127.0.0.1'
        broker_conn.port = 1883
        broker_conn.use_tls = False
        broker_conn.worker_username = 'iot_worker'
        broker_conn.admin_username = 'admin_dynsec'
        broker_conn.worker_status = 'CONNECTED'
        broker_conn.worker_heartbeat_at = now
        broker_conn.revision = 1
        broker_conn.worker_last_seen_revision = 1
        broker_conn.save()

        self.stdout.write(self.style.SUCCESS("\n=== HOÀN TẤT KHỞI TẠO DỮ LIỆU KIỂM THỬ THÀNH CÔNG! ==="))
        self.stdout.write("""
----------------------------------------------------------------------
DANH SÁCH TÀI KHOẢN ĐỂ TEST ĐĂNG NHẬP TRÊN WEB:
  1. Quản trị viên (ADMIN):
     - Tên đăng nhập: admin
     - Mật khẩu: AdminPassword123!
     - Quyền: Toàn quyền truy cập mọi trang (Tổng quan, Thiết bị, Cảnh báo, Lệnh, Tài khoản, Cấu hình Broker MQTT)

  2. Người vận hành (OPERATOR):
     - Tên đăng nhập: operator1
     - Mật khẩu: Operator123456!
     - Quyền: Xem & Vận hành tủ 'cabinet-001', 'CABINET-01'; Xem tủ 'CABINET-02'; Xử lý Cảnh báo.
     - Giới hạn: Bị ẩn menu Quản trị tài khoản & Cấu hình Broker.

  3. Người xem (VIEWER):
     - Tên đăng nhập: viewer1
     - Mật khẩu: Viewer123456!
     - Quyền: Chỉ xem số đo và cảnh báo của các tủ được cấp ('cabinet-001', 'CABINET-01', 'CABINET-02').
     - Giới hạn: Không được gửi lệnh điều khiển quạt/chế độ hay cấu hình.

DANH SÁCH TỦ MẠNG SẴN CÓ:
  - cabinet-001 (Node thực tế / ESP32 Wokwi): Đang kết nối (Online), 29.5°C, 62%
  - CABINET-01  (Phòng Server Lab 01): Đang kết nối (Online), 35.8°C (Quạt ON), có cảnh báo Critical
  - CABINET-02  (Tầng 2 Viễn Thông): Mất kết nối (Offline 25 phút), để kiểm tra trạng thái mất mạng
  - CABINET-03  (Tủ Dự Phòng Mới Nhập Kho): Trạng thái REGISTERED, để test cấp tài khoản MQTT (Provision)
  - CABINET-04  (Khu Vực Bảo Trì): Trạng thái LOCKED, để test mở khóa
----------------------------------------------------------------------
""")
