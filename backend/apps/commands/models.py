import uuid
from django.conf import settings
from django.db import models


class Command(models.Model):
    """
    Model lưu vết và quản lý vòng đời lệnh điều khiển thiết bị (Command Lifecycle):
    PENDING -> SENT -> ACKED (hoặc TIMEOUT / FAILED).
    Đảm bảo tính bất biến (idempotency) và xác nhận hai chiều (Application ACK).
    """
    STATUS_CHOICES = (
        ('PENDING', 'Chờ xử lý / gửi đi'),
        ('SENT', 'Đã xuất bản tới Broker MQTT'),
        ('APPLIED', 'Thiết bị đã thực thi và phản hồi ACK thành công'),
        ('APPLIED_LATE', 'Thiết bị thực thi và phản hồi ACK sau khi đã timeout'),
        ('ACKED', 'Đã nhận phản hồi ACK thành công (tương đương APPLIED)'),
        ('FAILED', 'Thực thi thất bại trên thiết bị'),
        ('TIMEOUT', 'Quá thời gian chờ phản hồi ACK'),
        ('CANCELLED', 'Đã hủy lệnh'),
    )

    COMMAND_TYPE_CHOICES = (
        ('SET_FAN', 'Điều khiển bật/tắt quạt thông gió'),
        ('SET_MODE', 'Chuyển đổi chế độ AUTO / MANUAL'),
        ('APPLY_CONFIG', 'Đồng bộ cấu hình tham số'),
        ('REBOOT', 'Khởi động lại node ESP32'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey(
        'devices.Device',
        on_delete=models.CASCADE,
        related_name='commands',
        verbose_name="Thiết bị tủ mạng"
    )
    command_type = models.CharField(
        max_length=32,
        choices=COMMAND_TYPE_CHOICES,
        default='SET_FAN',
        verbose_name="Loại lệnh"
    )
    payload = models.JSONField(default=dict, verbose_name="Tham số lệnh (JSON Payload)")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='PENDING',
        verbose_name="Trạng thái thực thi"
    )
    correlation_id = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name="Mã đối chiếu tương quan (Correlation ID / Idempotency Key)"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='issued_commands',
        verbose_name="Người thực hiện phát lệnh"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Thời điểm tạo lệnh")
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name="Thời điểm gửi tới MQTT Broker")
    acked_at = models.DateTimeField(null=True, blank=True, verbose_name="Thời điểm nhận ACK")
    error_message = models.TextField(blank=True, default='', verbose_name="Thông tin lỗi nếu có")

    class Meta:
        verbose_name = "Lệnh điều khiển"
        verbose_name_plural = "Nhật ký lệnh điều khiển"
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.status}] {self.device.id} - {self.command_type} (corr: {self.correlation_id[:8]})"


class OutboxMessage(models.Model):
    """
    Transactional Outbox: Đảm bảo tính nhất quán (Reliable Messaging) giữa Database và MQTT Broker.
    Lệnh được ghi vào outbox cùng transaction, sau đó background worker quét và publish.
    """
    STATUS_CHOICES = (
        ('PENDING', 'Chờ phát hành'),
        ('LEASED', 'Worker đang phát hành'),
        ('PUBLISHED', 'Đã phát hành thành công sang Broker'),
        ('FAILED', 'Phát hành thất bại'),
    )

    id = models.BigAutoField(primary_key=True)
    command = models.OneToOneField(
        Command,
        on_delete=models.CASCADE,
        related_name='outbox_message',
        verbose_name="Lệnh tương ứng"
    )
    topic = models.CharField(max_length=255, verbose_name="Topic MQTT đích")
    payload = models.JSONField(verbose_name="Nội dung tin nhắn")
    qos = models.PositiveSmallIntegerField(default=1, verbose_name="Mức QoS MQTT")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='PENDING')
    retry_count = models.PositiveIntegerField(default=0, verbose_name="Số lần thử lại")
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = "Hộp thư gửi đi (Outbox)"
        verbose_name_plural = "Hộp thư gửi đi (Outbox Messages)"
        ordering = ['created_at']

    def __str__(self):
        return f"Outbox #{self.id} -> {self.topic} [{self.status}]"
