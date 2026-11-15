from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.alerts.models import Alert
from apps.devices.models import Device, DeviceAccess, DeviceConfig
from apps.telemetry.models import Telemetry

User = get_user_model()


class Command(BaseCommand):
    help = "Khởi tạo dữ liệu mẫu IoT: Thiết bị tủ mạng, phân quyền DeviceAccess, chuỗi viễn trắc và cảnh báo."

    def handle(self, *args, **options):
        self.stdout.write("Bắt đầu khởi tạo dữ liệu mẫu IoT Cabinet...")

        now = timezone.now()

        # 1. Tạo 2 thiết bị tủ mạng (1 node thật và 1 node mô phỏng)
        cab1, created1 = Device.objects.get_or_create(
            id='CABINET-01',
            defaults={
                'name': 'Tủ Mạng Phòng Server Lab 01',
                'source': 'REAL',
                'status': 'ACTIVE',
                'hardware_version': 'ESP32-WROOM-32D',
                'firmware_version': 'v1.4.0',
                'ip_address': '192.168.1.150',
                'mac_address': '24:6F:28:B1:A2:30',
                'desired_config': {'max_temp': 35.0, 'max_hum': 75.0, 'auto_cool': True},
                'applied_config': {'max_temp': 35.0, 'max_hum': 75.0, 'auto_cool': True},
                'last_seen_at': now,
                'latest_temperature': 31.5,
                'latest_humidity': 68.2,
                'latest_fan_status': 'ON',
                'latest_door_status': 'CLOSED',
                'latest_mode': 'AUTO',
            }
        )
        if created1:
            self.stdout.write(self.style.SUCCESS("  + Tạo mới thiết bị: CABINET-01 (REAL)"))
        else:
            self.stdout.write("  - Thiết bị CABINET-01 đã tồn tại.")

        cab2, created2 = Device.objects.get_or_create(
            id='CABINET-02',
            defaults={
                'name': 'Tủ Mạng Tầng 2 Viễn Thông (Mô phỏng)',
                'source': 'SIMULATED',
                'status': 'ACTIVE',
                'hardware_version': 'SIMULATOR-X86',
                'firmware_version': 'v1.4.0',
                'ip_address': '192.168.1.151',
                'desired_config': {'max_temp': 36.0, 'max_hum': 80.0, 'auto_cool': True},
                'applied_config': {'max_temp': 36.0, 'max_hum': 80.0, 'auto_cool': True},
                'last_seen_at': now - timedelta(minutes=2),
                'latest_temperature': 29.8,
                'latest_humidity': 62.0,
                'latest_fan_status': 'OFF',
                'latest_door_status': 'CLOSED',
                'latest_mode': 'AUTO',
            }
        )
        if created2:
            self.stdout.write(self.style.SUCCESS("  + Tạo mới thiết bị: CABINET-02 (SIMULATED)"))

        # 2. Cấu hình ban đầu
        DeviceConfig.objects.get_or_create(
            device=cab1,
            version=1,
            defaults={
                'config_data': cab1.desired_config,
                'applied': True,
                'applied_at': now,
            }
        )

        # 3. Phân quyền DeviceAccess cho các user sẵn có trong hệ thống
        users = User.objects.all()
        for u in users:
            DeviceAccess.objects.get_or_create(
                user=u,
                device=cab1,
                defaults={'role': 'OPERATE', 'granted_at': now}
            )
            DeviceAccess.objects.get_or_create(
                user=u,
                device=cab2,
                defaults={'role': 'VIEW', 'granted_at': now}
            )
        self.stdout.write(self.style.SUCCESS(f"  + Đã đồng bộ phân quyền DeviceAccess cho {users.count()} người dùng."))

        # 4. Tạo dữ liệu viễn trắc mẫu cho CABINET-01
        telemetry_count = Telemetry.objects.filter(device=cab1).count()
        if telemetry_count < 10:
            for i in range(15):
                t_point = now - timedelta(minutes=(15 - i) * 2)
                temp = round(28.0 + (i * 0.3) % 4.5, 1)
                hum = round(65.0 + (i * 0.4) % 6.0, 1)
                fan = 'ON' if temp > 31.0 else 'OFF'
                Telemetry.objects.create(
                    device=cab1,
                    seq=i + 1,
                    temperature=temp,
                    humidity=hum,
                    fan_status=fan,
                    door_status='CLOSED',
                    mode='AUTO',
                    uptime=100 + i * 120,
                    measured_at=t_point,
                    raw_data={'source': 'seed'}
                )
            self.stdout.write(self.style.SUCCESS("  + Đã khởi tạo 15 bản ghi viễn trắc mẫu cho CABINET-01."))

        # 5. Khởi tạo cảnh báo mẫu
        alert_exists = Alert.objects.filter(device=cab1).exists()
        if not alert_exists:
            Alert.objects.create(
                device=cab1,
                alert_type='HIGH_TEMP',
                severity='WARNING',
                status='RESOLVED',
                message="Nhiệt độ tủ CABINET-01 đạt 36.2°C, vượt ngưỡng an toàn (35.0°C)!",
                threshold=35.0,
                current_value=36.2,
                resolved_at=now - timedelta(minutes=5)
            )
            Alert.objects.create(
                device=cab1,
                alert_type='DOOR_OPEN',
                severity='INFO',
                status='OPEN',
                message="Cửa tủ mạng CABINET-01 đang ở trạng thái MỞ!"
            )
            self.stdout.write(self.style.SUCCESS("  + Đã tạo 2 cảnh báo mẫu (1 OPEN, 1 RESOLVED)."))

        self.stdout.write(self.style.SUCCESS("Hoàn tất khởi tạo dữ liệu mẫu IoT Cabinet thành công!"))
