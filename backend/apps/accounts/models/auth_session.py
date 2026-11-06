"""
AuthSession Model Module
Quản lý Registry phiên làm việc đăng nhập của người dùng.
- Mỗi bản ghi đại diện cho một phiên có ID duy nhất (sid: UUID v4).
- Không ràng buộc Foreign Key cứng tới bảng django_session (tránh cascade delete hoặc lỗi khi chạy lệnh clearsessions).
- Thiết lập thời gian sống tuyệt đối (15 phút / 900 giây), hỗ trợ thu hồi (revoke) có lý do.
"""
import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone


class AuthSession(models.Model):
    """
    Bảng accounts_auth_session: Registry phiên làm việc.
    Lưu trữ metadata bảo mật của phiên (sid, user, IP, User-Agent, expires_at, revoked_at).
    """
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text='Session ID (sid) định danh UUID v4 duy nhất của phiên.'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name='auth_sessions',
        help_text='Người dùng sở hữu phiên làm việc này.'
    )
    session_key = models.CharField(
        max_length=40,
        db_index=True,
        help_text='Khóa tham chiếu session Django (db_index=True đã tự tạo index trên DB).'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text='Thời điểm khởi tạo phiên.'
    )
    last_seen_at = models.DateTimeField(
        auto_now=True,
        help_text='Thời điểm ghi nhận hoạt động gần nhất của phiên.'
    )
    expires_at = models.DateTimeField(
        help_text='Thời điểm hết hạn tuyệt đối của phiên (mặc định created_at + 15 phút).'
    )
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Thời điểm phiên bị thu hồi (đăng xuất, đổi mật khẩu, hoặc admin khóa).'
    )
    revoke_reason = models.TextField(
        null=True,
        blank=True,
        help_text='Lý do thu hồi phiên (ví dụ: USER_LOGOUT, PASSWORD_CHANGED, ADMIN_LOCKED).'
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text='Địa chỉ IP của client tại thời điểm đăng nhập.'
    )
    user_agent = models.CharField(
        max_length=512,
        null=True,
        blank=True,
        help_text='Chuỗi User-Agent của client.'
    )

    class Meta:
        db_table = 'accounts_auth_session'
        verbose_name = 'Phiên làm việc'
        verbose_name_plural = 'Danh sách phiên làm việc'
        ordering: ClassVar[list[str]] = ['-created_at']
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['user', '-created_at'], name='ix_auth_sessions_user_created'),
            models.Index(fields=['expires_at'], name='ix_auth_sessions_expires_at'),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F('created_at')),
                name='ck_auth_sessions_expires_after_created'
            ),
        ]

    def __str__(self) -> str:
        return f'Session {self.id} (User: {self.user.username})'

    @property
    def is_active(self) -> bool:
        """Kiểm tra phiên có đang còn hiệu lực hay không (chưa revoked và chưa hết hạn)."""
        return (self.revoked_at is None) and (self.expires_at > timezone.now())