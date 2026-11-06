from django.conf import settings
from django.db import models


class Alert(models.Model):
    """
    Model quản lý các cảnh báo và sự cố trong tủ mạng (Incident & Alerting):
    Hỗ trợ 4 loại cảnh báo cốt lõi theo V1.4:
    - HIGH_TEMP: Nhiệt độ vượt ngưỡng cho phép
    - HIGH_HUMIDITY: Độ ẩm vượt ngưỡng cho phép
    - FAN_FAULT: Quạt gặp sự cố / không quay khi kích hoạt
    - DOOR_OPEN: Cửa tủ mạng bị mở trái phép hoặc quá lâu
    """
    ALERT_TYPE_CHOICES = (
        ('HIGH_TEMP', 'Nhiệt độ cao vượt ngưỡng'),
        ('HIGH_HUMIDITY', 'Độ ẩm cao vượt ngưỡng'),
        ('FAN_FAULT', 'Sự cố quạt thông gió'),
        ('DOOR_OPEN', 'Cửa tủ mạng đang mở'),
    )

    SEVERITY_CHOICES = (
        ('INFO', 'Thông tin'),
        ('WARNING', 'Cảnh báo'),
        ('CRITICAL', 'Nghiêm trọng'),
    )

    STATUS_CHOICES = (
        ('OPEN', 'Đang xảy ra (Chưa xử lý)'),
        ('ACKNOWLEDGED', 'Đã ghi nhận (Đang xử lý)'),
        ('RESOLVED', 'Đã khắc phục xong'),
    )

    device = models.ForeignKey(
        'devices.Device',
        on_delete=models.CASCADE,
        related_name='alerts',
        verbose_name="Thiết bị tủ mạng"
    )
    alert_type = models.CharField(
        max_length=32,
        choices=ALERT_TYPE_CHOICES,
        verbose_name="Loại cảnh báo"
    )
    severity = models.CharField(
        max_length=16,
        choices=SEVERITY_CHOICES,
        default='WARNING',
        verbose_name="Mức độ nghiêm trọng"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='OPEN',
        verbose_name="Trạng thái cảnh báo"
    )
    message = models.CharField(max_length=255, verbose_name="Nội dung thông báo cảnh báo")
    threshold = models.FloatField(null=True, blank=True, verbose_name="Ngưỡng kích hoạt")
    current_value = models.FloatField(null=True, blank=True, verbose_name="Giá trị đo được tại thời điểm cảnh báo")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Thời điểm kích hoạt")
    acknowledged_at = models.DateTimeField(null=True, blank=True, verbose_name="Thời điểm ghi nhận")
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='acknowledged_alerts',
        verbose_name="Người ghi nhận"
    )
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name="Thời điểm khắc phục")

    class Meta:
        verbose_name = "Cảnh báo sự cố"
        verbose_name_plural = "Danh sách cảnh báo sự cố"
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.severity}] {self.device.id} - {self.alert_type} ({self.status})"
