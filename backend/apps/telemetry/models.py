from django.db import models
from django.utils import timezone


class Telemetry(models.Model):
    """
    Model lưu trữ chuỗi dữ liệu thời gian (Time-series) các thông số viễn trắc từ cảm biến tủ mạng.
    """
    device = models.ForeignKey(
        'devices.Device',
        on_delete=models.CASCADE,
        related_name='telemetries',
        verbose_name="Thiết bị tủ mạng"
    )
    seq = models.BigIntegerField(default=0, verbose_name="Số thứ tự gói tin (Sequence)")
    temperature = models.FloatField(verbose_name="Nhiệt độ (°C)")
    humidity = models.FloatField(verbose_name="Độ ẩm không khí (%)")
    fan_status = models.CharField(
        max_length=16,
        default='OFF',
        choices=(('OFF', 'Tắt'), ('ON', 'Bật'), ('FAULT', 'Lỗi')),
        verbose_name="Trạng thái quạt"
    )
    door_status = models.CharField(
        max_length=16,
        default='CLOSED',
        choices=(('CLOSED', 'Đóng'), ('OPEN', 'Mở')),
        verbose_name="Trạng thái cửa"
    )
    mode = models.CharField(
        max_length=16,
        default='AUTO',
        choices=(('AUTO', 'Tự động'), ('MANUAL', 'Thủ công')),
        verbose_name="Chế độ"
    )
    uptime = models.BigIntegerField(default=0, verbose_name="Thời gian chạy của node (giây)")
    measured_at = models.DateTimeField(default=timezone.now, verbose_name="Thời điểm đo tại node")
    received_at = models.DateTimeField(auto_now_add=True, verbose_name="Thời điểm hệ thống nhận")
    raw_data = models.JSONField(default=dict, blank=True, verbose_name="Dữ liệu thô JSON")

    class Meta:
        verbose_name = "Dữ liệu viễn trắc"
        verbose_name_plural = "Lịch sử viễn trắc"
        ordering = ['-measured_at']
        indexes = [
            models.Index(fields=['device', '-measured_at'], name='telemetry_dev_time_idx'),
        ]

    def __str__(self):
        return f"{self.device.id} | {self.measured_at.strftime('%Y-%m-%d %H:%M:%S')} | {self.temperature}°C, {self.humidity}% | Fan: {self.fan_status}"
