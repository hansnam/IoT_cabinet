from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
import random
from cabinet.models import Device, Telemetry, Alert


class Command(BaseCommand):
    help = "Khởi tạo dữ liệu mẫu cho hệ thống (Superuser, Thiết bị, Telemetry, Cảnh báo)"

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("[*] Bắt đầu khởi tạo dữ liệu mẫu..."))

        # 1. Tạo tài khoản Admin mặc định
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "admin@cabinet.local", "admin123")
            self.stdout.write(self.style.SUCCESS("[+] Đã tạo tài khoản Admin: admin / mật khẩu: admin123"))
        else:
            self.stdout.write("[.] Tài khoản admin đã tồn tại.")

        # 2. Tạo thiết bị mẫu
        dev1, _ = Device.objects.get_or_create(
            device_id="cabinet-001",
            defaults={
                "name": "Tủ Mạng Phòng Lab 001",
                "device_type": "CONTROLLER",
                "location": "Phòng Thí Nghiệm Lab IoT",
                "connection_state": "online",
                "health_state": "normal",
                "mode": "AUTO",
                "fan_output": True,
                "temp_threshold_on": 30.0,
                "temp_threshold_off": 28.0,
                "temp_alert_thresh": 35.0,
                "humidity_alert_thresh": 80.0,
                "last_seen_at": timezone.now(),
            }
        )

        dev2, _ = Device.objects.get_or_create(
            device_id="cabinet-002",
            defaults={
                "name": "Tủ Thiết Bị Máy Chủ 002",
                "device_type": "CONTROLLER",
                "location": "Phòng Server Tầng 2",
                "connection_state": "online",
                "health_state": "warning",
                "mode": "AUTO",
                "fan_output": False,
                "temp_threshold_on": 30.0,
                "temp_threshold_off": 28.0,
                "temp_alert_thresh": 35.0,
                "humidity_alert_thresh": 80.0,
                "last_seen_at": timezone.now(),
            }
        )
        self.stdout.write(self.style.SUCCESS(f"[+] Đã tạo 2 thiết bị: {dev1.device_id}, {dev2.device_id}"))

        # 3. Tạo dữ liệu Telemetry mẫu (trong 2 giờ qua)
        now = timezone.now()
        telemetry_count = Telemetry.objects.filter(device=dev1).count()
        if telemetry_count < 10:
            for i in range(15):
                t_time = now - timedelta(minutes=(15 - i) * 5)
                temp = round(28.0 + random.uniform(0.5, 5.5), 1)
                hum = round(60.0 + random.uniform(-5.0, 15.0), 1)
                fan = temp >= 30.0
                Telemetry.objects.create(
                    device=dev1,
                    temperature_c=temp,
                    humidity_pct=hum,
                    fan_output=fan,
                    mode="AUTO",
                )
            self.stdout.write(self.style.SUCCESS("[+] Đã tạo 15 bản ghi Telemetry lịch sử cho cabinet-001"))

        # 4. Tạo cảnh báo mẫu
        if not Alert.objects.filter(device=dev1).exists():
            Alert.objects.create(
                device=dev1,
                alert_type="HIGH_TEMPERATURE",
                severity="high",
                message="Nhiệt độ tủ vượt ngưỡng cho phép: 35.4 °C (Ngưỡng: 35.0 °C)",
                status="open",
            )
            Alert.objects.create(
                device=dev2,
                alert_type="HIGH_HUMIDITY",
                severity="medium",
                message="Độ ẩm tủ máy chủ tăng cao: 82.1 % (Ngưỡng: 80.0 %)",
                status="open",
            )
            self.stdout.write(self.style.SUCCESS("[+] Đã tạo 2 cảnh báo mẫu."))

        self.stdout.write(self.style.SUCCESS("=== KHỞI TẠO DỮ LIỆU THÀNH CÔNG ==="))
