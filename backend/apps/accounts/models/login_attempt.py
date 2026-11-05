"""
LoginAttempt Model Module
Ghi nhận lịch sử các lần thử đăng nhập phục vụ rate-limit và chống tấn công dò mật khẩu (brute-force).
- Tuyệt đối không lưu username hay IP ở dạng thô; thay vào đó sử dụng khóa băm HMAC SHA-256 (username_key, ip_key).
- Có liên kết tới request_id để đối chiếu (correlation) với nhật ký hệ thống.
"""
import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models


class LoginAttempt(models.Model):
    """
    Bảng accounts_login_attempt: Lịch sử nỗ lực đăng nhập.
    """
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text='Khóa chính UUID v4.'
    )
    username_key = models.CharField(
        max_length=64,
        db_index=True,
        help_text='HMAC-SHA256 của username (chống lộ thông tin tài khoản thô).'
    )
    ip_key = models.CharField(
        max_length=64,
        db_index=True,
        help_text='HMAC-SHA256 của IP client (chống lộ địa chỉ IP thô).'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name='login_attempts',
        help_text='Người dùng tương ứng nếu username tồn tại trong hệ thống.'
    )
    success = models.BooleanField(
        help_text='Kết quả đăng nhập: True nếu thành công, False nếu thất bại.'
    )
    failure_code = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text='Mã phân loại nguyên nhân thất bại (ví dụ: INVALID_CREDENTIALS, ACCOUNT_LOCKED).'
    )
    occurred_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text='Thời điểm ghi nhận nỗ lực đăng nhập.'
    )
    request_id = models.UUIDField(
        default=uuid.uuid4,
        help_text='Request ID gắn liền với HTTP request gửi lên.'
    )

    class Meta:
        db_table = 'accounts_login_attempt'
        verbose_name = 'Lịch sử đăng nhập'
        verbose_name_plural = 'Danh sách lịch sử đăng nhập'
        ordering: ClassVar[list[str]] = ['-occurred_at']
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['username_key', 'occurred_at'], name='ix_login_attempts_user_time'),
            models.Index(fields=['ip_key', 'occurred_at'], name='ix_login_attempts_ip_time'),
        ]

    def __str__(self) -> str:
        status_text = 'SUCCESS' if self.success else f'FAILED({self.failure_code})'
        return f'LoginAttempt {self.id} [{status_text}] at {self.occurred_at}'