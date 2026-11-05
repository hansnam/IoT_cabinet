"""
Audit Log Model Module
Hiện thực hóa bảng duy nhất `audit_logs` lưu trữ toàn bộ sự kiện kiểm toán bảo mật
và nhật ký làm việc theo đặc tả V1.4.
"""
import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone


class AuditLog(models.Model):
    """
    Bảng duy nhất lưu trữ dấu vết kiểm toán bảo mật và nhật ký chức năng.
    """
    class ActorType(models.TextChoices):
        USER = 'USER', 'Người dùng'
        SYSTEM = 'SYSTEM', 'Hệ thống tự động'
        SERVICE = 'SERVICE', 'Dịch vụ nền / Worker'

    class Outcome(models.TextChoices):
        SUCCESS = 'SUCCESS', 'Thành công'
        ACCEPTED = 'ACCEPTED', 'Đã tiếp nhận (Bất đồng bộ 202)'
        DENIED = 'DENIED', 'Bị từ chối quyền'
        FAILED = 'FAILED', 'Thất bại do lỗi hệ thống / dữ liệu'

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text='Khóa chính UUID v4 của bản ghi kiểm toán.'
    )

    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name='audit_logs',
        help_text='Tài khoản người dùng thực hiện (null nếu do hệ thống/worker).'
    )
    actor_session_id = models.UUIDField(
        null=True,
        blank=True,
        help_text='Session ID (sid) của phiên làm việc phát sinh thao tác.'
    )
    actor_type = models.CharField(
        max_length=20,
        choices=ActorType.choices,
        default=ActorType.USER,
        help_text='Phân loại tác nhân: USER, SYSTEM hoặc SERVICE.'
    )
    actor_name_snapshot = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text='Snapshot tên người dùng tại thời điểm xảy ra sự kiện.'
    )

    function_key = models.CharField(
        max_length=80,
        db_index=True,
        help_text='Mã phân loại chức năng (ví dụ: AUTH_LOGIN, USER_CREATE, DEVICE_CONTROL).'
    )
    action = models.CharField(
        max_length=32,
        db_index=True,
        help_text='Thao tác thực hiện (ví dụ: LOGIN, LOGOUT, CREATE, UPDATE, DELETE, VIEW, CONTROL).'
    )

    target_type = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text='Loại đối tượng đích (ví dụ: USER, DEVICE, SESSION, CONFIG).'
    )
    target_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text='Định danh đối tượng đích bị tác động.'
    )
    target_name_snapshot = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text='Snapshot tên đối tượng đích tại thời điểm thực hiện.'
    )
    device_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
        help_text='Snapshot mã thiết bị (độc lập với model Device để không phụ thuộc TV2).'
    )

    outcome = models.CharField(
        max_length=20,
        choices=Outcome.choices,
        db_index=True,
        help_text='Kết quả thực hiện: SUCCESS, ACCEPTED, DENIED hoặc FAILED.'
    )
    summary = models.TextField(
        help_text='Tóm tắt nội dung hành động theo câu mẫu phía server.'
    )
    error_code = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text='Mã lỗi nếu kết quả là DENIED hoặc FAILED.'
    )

    request_id = models.UUIDField(
        db_index=True,
        help_text='Request ID gắn liền với HTTP request gửi lên.'
    )
    correlation_id = models.UUIDField(
        null=True,
        blank=True,
        help_text='Correlation ID xâu chuỗi nhiều tác vụ liên quan.'
    )
    occurred_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        help_text='Thời điểm ghi nhận sự kiện.'
    )
    duration_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='Thời gian thực thi thao tác tính bằng mili-giây (ms).'
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text='Địa chỉ IP của client gửi yêu cầu.'
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text='Dữ liệu chi tiết bổ sung (đã được lọc allowlist và bảo đảm số hữu hạn).'
    )

    class Meta:
        db_table = 'audit_logs'
        verbose_name = 'Nhật ký kiểm toán'
        verbose_name_plural = 'Danh sách nhật ký kiểm toán'
        ordering:  ClassVar[list[str]] = ['-occurred_at']
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['actor_user', '-occurred_at'], name='idx_audit_actor_time'),
            models.Index(fields=['function_key', '-occurred_at'], name='idx_audit_func_time'),
            models.Index(fields=['action', '-occurred_at'], name='idx_audit_act_time'),
            models.Index(fields=['device_id', '-occurred_at'], name='idx_audit_dev_time'),
            models.Index(fields=['outcome', '-occurred_at'], name='idx_audit_out_time'),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(outcome__in=['SUCCESS', 'ACCEPTED', 'DENIED', 'FAILED']),
                name='ck_audit_logs_outcome_valid'
            ),
            models.CheckConstraint(
                condition=models.Q(actor_type__in=['USER', 'SYSTEM', 'SERVICE']),
                name='ck_audit_logs_actor_type_valid'
            ),
        ]

    def __str__(self) -> str:
        return f'[{self.occurred_at}] [{self.outcome}] {self.function_key}:{self.action} by {self.actor_type}'