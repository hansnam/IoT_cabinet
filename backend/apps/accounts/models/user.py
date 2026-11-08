"""
User Model Module
Kế thừa AbstractUser của Django nhưng thay thế ID số nguyên bằng UUID v4,
chuẩn hóa username canonical (3-64 ký tự), bổ sung các trường nghiệp vụ:
status (ACTIVE/LOCKED/ARCHIVED), must_change_password, failed_login_count, locked_until,
password_changed_at và liên kết người tạo created_by.
Đồng bộ trạng thái is_active cả khi gọi save(update_fields=['status']).
"""
import re
import uuid
from typing import ClassVar

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models

from apps.accounts.managers import CustomUserManager

# Biểu thức chính quy cho username canonical: 3-64 ký tự chữ cái, số, gạch dưới, gạch ngang, dấu chấm
USERNAME_REGEX = re.compile(r'^[a-z0-9._-]{3,64}$')

def validate_canonical_username(value: str) -> None:
    """Kiểm tra định dạng canonical username từ 3 đến 64 ký tự hợp lệ."""
    if not USERNAME_REGEX.match(value):
        raise ValidationError(
            'Username phải có độ dài từ 3 đến 64 ký tự, chỉ chứa chữ cái, chữ số, gạch dưới, gạch ngang hoặc dấu chấm.'
        )

class UserStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Hoạt động'
    LOCKED = 'LOCKED', 'Tạm khóa'
    ARCHIVED = 'ARCHIVED', 'Lưu trữ / Đã hủy'


class User(AbstractUser):
    """
    Model Người dùng cốt lõi của hệ thống IoT Cabinet.
    Kế thừa AbstractUser để tận dụng Group và Permission sẵn có của Django,
    đồng thời định nghĩa lại:
    - id: UUID v4 làm Primary Key (chống brute-force / enumeration ID số tuần tự).
    - first_name, last_name: Loại bỏ (đặt None) để dùng trường duy nhất full_name.
    - status: Quản lý vòng đời tài khoản (ACTIVE, LOCKED, ARCHIVED).
    """
    Status = UserStatus

    # 1. Định danh chính bằng UUID v4
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text='Khóa chính UUID v4 định danh duy nhất người dùng.'
    )

    # 2. Username canonical hóa (3-64 ký tự) — unique=True đã tự tạo index trên DB
    username = models.CharField(
        max_length=64,
        unique=True,
        validators=[validate_canonical_username],
        help_text='Tên đăng nhập duy nhất (3-64 ký tự).'
    )

    # 3. Thông tin người dùng
    full_name = models.CharField(
        max_length=128,
        help_text='Họ và tên đầy đủ của người dùng.'
    )
    email = models.EmailField(
        max_length=254,
        null=True,
        blank=True,   
        help_text='Địa chỉ email (tùy chọn, duy nhất nếu có).'
    )

    # 4. Trạng thái và bảo mật mật khẩu
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        help_text='Trạng thái tài khoản: ACTIVE, LOCKED hoặc ARCHIVED.'
    )
    must_change_password = models.BooleanField(
        default=False,
        help_text='Cờ bắt buộc phải đổi mật khẩu ở lần đăng nhập tiếp theo.'
    )
    password_changed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Thời điểm thay đổi mật khẩu lần gần nhất.'
    )
    failed_login_count = models.PositiveIntegerField(
        default=0,
        help_text='Số lần đăng nhập thất bại liên tiếp (phục vụ khóa tạm thời).'
    )
    locked_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Thời điểm tài khoản được tự động mở khóa nếu bị tạm khóa có thời hạn.'
    )

    # 5. Dấu vết quản trị (Audit trace)
    created_by = models.ForeignKey(
        'self',
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name='created_users',
        help_text='Người dùng quản trị đã tạo tài khoản này.'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text='Thời điểm tài khoản được tạo.'
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text='Thời điểm tài khoản được cập nhật lần cuối.'
    )

    # Loại bỏ first_name, last_name mặc định của AbstractUser
    first_name = None
    last_name = None

    objects = CustomUserManager()  # pyright: ignore[reportAssignmentType]

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS: ClassVar[list[str]] = ['full_name']

    class Meta:
        db_table = 'accounts_user'
        verbose_name = 'Người dùng'
        verbose_name_plural = 'Danh sách người dùng'
        ordering: ClassVar[list[str]] = ['-created_at']
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['status'], name='idx_user_status'),
            models.Index(fields=['created_at'], name='idx_user_created_at'),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(status__in=[choice[0] for choice in UserStatus.choices]),
                name='chk_user_status_valid'
            ),
            models.CheckConstraint(
                condition=models.Q(failed_login_count__gte=0),
                name='ck_users_failed_login_count_gte_0'
            ),
        ]

    def __str__(self) -> str:
        return f'{self.username} ({self.full_name})'

    def get_full_name(self) -> str:
        return self.full_name

    def get_short_name(self) -> str:
        return self.full_name.split()[0] if self.full_name else self.username

    def clean(self) -> None:
        """
        Chuẩn hóa và đồng bộ trạng thái:
        - Đồng bộ trường is_active của Django theo status nghiệp vụ:
          is_active chỉ True khi và chỉ khi status == ACTIVE.
        - Canonicalize username về dạng chữ thường.
        """
        super().clean()
        if self.username:
            self.username = self.username.strip().lower()
        # Đồng bộ is_active với status nghiệp vụ
        self.is_active = (self.status == self.Status.ACTIVE)

    def save(self, *args, **kwargs) -> None:
        """
        Ghi đè save() để bảo đảm dữ liệu luôn được chuẩn hóa qua clean() trước khi ghi DB.
        Đặc biệt xử lý trường hợp caller gọi save(update_fields=['status']):
        Tự động bổ sung 'is_active' vào update_fields để cập nhật xuống DB đồng thời.
        """
        self.clean()  # Chuẩn hóa dữ liệu trước khi lưu

        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            # Chuyển đổi sang set để thao tác nếu là list/tuple
            update_fields_set = set(update_fields)
            if 'status' in update_fields_set:
                update_fields_set.add('is_active')  # Đồng bộ is_active khi status thay đổi
                kwargs['update_fields'] = update_fields_set

        super().save(*args, **kwargs)