from django.db import models


class Device(models.Model):
    device_id = models.CharField(max_length=64, primary_key=True, verbose_name="Mã thiết bị")
    name = models.CharField(max_length=128, default="Tủ Mạng Phòng Lab 001", verbose_name="Tên tủ mạng")
    device_type = models.CharField(max_length=32, default="CONTROLLER", verbose_name="Loại thiết bị")
    location = models.CharField(max_length=128, default="Phòng Thí Nghiệm Lab IoT", verbose_name="Vị trí")
    
    # Trạng thái vận hành
    connection_state = models.CharField(
        max_length=16,
        choices=[("online", "Online"), ("offline", "Offline")],
        default="offline",
        verbose_name="Kết nối"
    )
    health_state = models.CharField(
        max_length=16,
        choices=[("normal", "Bình thường"), ("warning", "Cảnh báo"), ("fault", "Sự cố")],
        default="normal",
        verbose_name="Sức khỏe"
    )
    mode = models.CharField(
        max_length=16,
        choices=[("AUTO", "Tự động (AUTO)"), ("MANUAL", "Thủ công (MANUAL)")],
        default="AUTO",
        verbose_name="Chế độ quạt"
    )
    fan_output = models.BooleanField(default=False, verbose_name="Đầu ra quạt (Bật/Tắt)")
    
    # Ngưỡng nhiệt độ & độ ẩm cấu hình
    temp_threshold_on = models.FloatField(default=30.0, verbose_name="Nhiệt độ bật quạt (°C)")
    temp_threshold_off = models.FloatField(default=28.0, verbose_name="Nhiệt độ tắt quạt (°C)")
    temp_alert_thresh = models.FloatField(default=35.0, verbose_name="Ngưỡng cảnh báo nhiệt (°C)")
    humidity_alert_thresh = models.FloatField(default=80.0, verbose_name="Ngưỡng cảnh báo độ ẩm (%)")
    
    last_seen_at = models.DateTimeField(null=True, blank=True, verbose_name="Lần cuối hoạt động")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Ngày tạo")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Cập nhật cuối")

    class Meta:
        verbose_name = "Thiết bị / Tủ mạng"
        verbose_name_plural = "Danh sách Thiết bị / Tủ mạng"
        ordering = ["device_id"]

    def __str__(self):
        return f"{self.name} ({self.device_id})"


class Telemetry(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="telemetry", verbose_name="Tủ thiết bị")
    temperature_c = models.FloatField(null=True, blank=True, verbose_name="Nhiệt độ (°C)")
    humidity_pct = models.FloatField(null=True, blank=True, verbose_name="Độ ẩm (%)")
    fan_output = models.BooleanField(default=False, verbose_name="Trạng thái quạt")
    mode = models.CharField(max_length=16, default="AUTO", verbose_name="Chế độ")
    received_at = models.DateTimeField(auto_now_add=True, verbose_name="Thời gian nhận")

    class Meta:
        verbose_name = "Bản ghi Telemetry"
        verbose_name_plural = "Lịch sử Telemetry"
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.device.device_id} | {self.temperature_c}°C - {self.humidity_pct}% ({self.received_at.strftime('%H:%M:%S %d/%m')})"


class Alert(models.Model):
    SEVERITY_CHOICES = [
        ("critical", "Khẩn cấp (Critical)"),
        ("high", "Cao (High)"),
        ("medium", "Trung bình (Medium)"),
        ("low", "Thấp (Low)"),
    ]
    STATUS_CHOICES = [
        ("open", "Đang mở"),
        ("acknowledged", "Đã tiếp nhận"),
        ("resolved", "Đã giải quyết"),
    ]

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="alerts", verbose_name="Tủ thiết bị")
    alert_type = models.CharField(max_length=64, verbose_name="Loại cảnh báo")
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES, default="high", verbose_name="Mức độ")
    message = models.TextField(verbose_name="Nội dung cảnh báo")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="open", verbose_name="Trạng thái")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Thời gian kích hoạt")
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name="Thời gian giải quyết")

    class Meta:
        verbose_name = "Cảnh báo"
        verbose_name_plural = "Danh sách Cảnh báo"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.severity.upper()}] {self.device.device_id}: {self.message}"


class CommandLog(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Đang chờ gửi"),
        ("SENT", "Đã gửi ra Broker"),
        ("APPLIED", "Thiết bị đã thực thi (ACK)"),
        ("FAILED", "Thất bại"),
    ]

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="commands", verbose_name="Tủ thiết bị")
    command_type = models.CharField(max_length=32, verbose_name="Loại lệnh")
    params = models.JSONField(default=dict, blank=True, verbose_name="Tham số lệnh")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="PENDING", verbose_name="Trạng thái")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Thời gian tạo lệnh")
    executed_at = models.DateTimeField(null=True, blank=True, verbose_name="Thời gian phản hồi ACK")

    class Meta:
        verbose_name = "Nhật ký lệnh"
        verbose_name_plural = "Lịch sử Lệnh điều khiển"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.device.device_id} -> {self.command_type} [{self.status}]"
