import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class Device(models.Model):
    """
    Model đại diện cho Tủ mạng / Thiết bị IoT (Cabinet / Edge Node).
    Lưu trữ thông tin định danh, cấu hình và trạng thái chiếu (projection) số đo mới nhất.
    """
    SOURCE_CHOICES = (
        ('REAL', 'Phần cứng vật lý (ESP32)'),
        ('SIMULATED', 'Mô phỏng phần mềm'),
    )

    STATUS_CHOICES = (
        ('REGISTERED', 'Đã đăng ký (Chờ kích hoạt)'),
        ('ACTIVE', 'Đang hoạt động'),
        ('LOCKED', 'Đã tạm khóa'),
        ('DECOMMISSIONED', 'Đã loại biên'),
    )

    id = models.CharField(
        max_length=64,
        primary_key=True,
        help_text="Mã định danh duy nhất của tủ (ví dụ: CABINET-01)"
    )
    name = models.CharField(max_length=128, verbose_name="Tên tủ mạng")
    source = models.CharField(
        max_length=16,
        choices=SOURCE_CHOICES,
        default='REAL',
        verbose_name="Nguồn thiết bị"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='ACTIVE',
        verbose_name="Trạng thái vòng đời"
    )
    hardware_version = models.CharField(max_length=32, default='v1.0', verbose_name="Phiên bản phần cứng")
    firmware_version = models.CharField(max_length=32, default='v1.0.0', verbose_name="Phiên bản firmware")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="Địa chỉ IP")
    mac_address = models.CharField(max_length=32, blank=True, default='', verbose_name="Địa chỉ MAC")

    # Cấu hình thiết bị (Desired và Applied theo V1.4)
    desired_config = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Cấu hình mong muốn (Desired)"
    )
    applied_config = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Cấu hình thực tế đã áp dụng (Applied)"
    )

    # Chiếu trạng thái viễn trắc mới nhất (Latest Projection) phục vụ dashboard tức thời
    last_seen_at = models.DateTimeField(null=True, blank=True, verbose_name="Thời điểm nhìn thấy gần nhất")
    latest_temperature = models.FloatField(null=True, blank=True, verbose_name="Nhiệt độ hiện tại (°C)")
    latest_humidity = models.FloatField(null=True, blank=True, verbose_name="Độ ẩm hiện tại (%)")
    latest_fan_status = models.CharField(
        max_length=16,
        default='OFF',
        choices=(('OFF', 'Tắt'), ('ON', 'Bật'), ('FAULT', 'Lỗi')),
        verbose_name="Trạng thái quạt"
    )
    latest_door_status = models.CharField(
        max_length=16,
        default='CLOSED',
        choices=(('CLOSED', 'Đóng'), ('OPEN', 'Mở')),
        verbose_name="Trạng thái cửa"
    )
    latest_mode = models.CharField(
        max_length=16,
        default='AUTO',
        choices=(('AUTO', 'Tự động'), ('MANUAL', 'Thủ công')),
        verbose_name="Chế độ làm việc"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Thời điểm tạo")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Thời điểm cập nhật")

    class Meta:
        verbose_name = "Thiết bị tủ mạng"
        verbose_name_plural = "Danh sách thiết bị tủ mạng"
        ordering = ['id']

    def __str__(self):
        return f"{self.id} - {self.name} ({self.status})"

    def is_online(self, threshold_seconds=60) -> bool:
        """Kiểm tra thiết bị có đang online dựa trên last_seen_at"""
        if not self.last_seen_at:
            return False
        return (timezone.now() - self.last_seen_at).total_seconds() < threshold_seconds


class DeviceAccess(models.Model):
    """
    Phân quyền truy cập thiết bị theo người dùng (Device Scope / RBAC):
    - Ngăn chặn triệt để lỗi IDOR (người dùng xem/điều khiển tủ ngoài phạm vi được cấp).
    - Phối hợp trực tiếp với app accounts của TV3.
    """
    ROLE_CHOICES = (
        ('VIEW', 'Chỉ xem số đo và cảnh báo'),
        ('OPERATE', 'Xem số đo và gửi lệnh điều khiển'),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='device_accesses',
        verbose_name="Người dùng"
    )
    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
        related_name='access_grants',
        verbose_name="Thiết bị tủ mạng"
    )
    role = models.CharField(
        max_length=16,
        choices=ROLE_CHOICES,
        default='VIEW',
        verbose_name="Mức quyền truy cập"
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='granted_device_accesses',
        verbose_name="Người cấp quyền"
    )
    granted_at = models.DateTimeField(auto_now_add=True, verbose_name="Thời điểm cấp")
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="Thời điểm hết hạn")

    class Meta:
        verbose_name = "Quyền truy cập thiết bị"
        verbose_name_plural = "Danh sách phân quyền thiết bị"
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'device'],
                name='unique_user_device_access'
            )
        ]

    def __str__(self):
        return f"{self.user.username} -> {self.device.id} ({self.role})"

    def is_valid(self) -> bool:
        """Kiểm tra quyền còn hạn sử dụng hay không"""
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True


class DeviceConfig(models.Model):
    """
    Lưu trữ lịch sử các phiên bản cấu hình đã được tạo/áp dụng cho tủ mạng.
    """
    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
        related_name='config_versions',
        verbose_name="Thiết bị tủ mạng"
    )
    version = models.PositiveIntegerField(default=1, verbose_name="Phiên bản cấu hình")
    config_data = models.JSONField(default=dict, verbose_name="Nội dung cấu hình JSON")
    applied = models.BooleanField(default=False, verbose_name="Đã áp dụng trên phần cứng")
    applied_at = models.DateTimeField(null=True, blank=True, verbose_name="Thời điểm áp dụng")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Thời điểm tạo")

    class Meta:
        verbose_name = "Phiên bản cấu hình"
        verbose_name_plural = "Lịch sử cấu hình thiết bị"
        constraints = [
            models.UniqueConstraint(
                fields=['device', 'version'],
                name='unique_device_config_version'
            )
        ]
        ordering = ['-version']

    def __str__(self):
        return f"{self.device.id} - Config v{self.version} ({'APPLIED' if self.applied else 'PENDING'})"
