"""
Custom User Manager Module
Quản lý việc tạo người dùng thông thường và quản trị viên (superuser),
chuẩn hóa tên đăng nhập (canonical lowercase), kiểm tra định dạng và băm mật khẩu.
"""
from django.contrib.auth.models import BaseUserManager
from django.contrib.auth.password_validation import validate_password


class CustomUserManager(BaseUserManager):
    """
    Manager tùy biến cho model User dùng UUID và canonical username.
    """
    def create_user(self, username: str, full_name: str, password: str | None = None, **extra_fields):
        """
        Tạo và lưu trữ một người dùng mới vào database:
        - Kiểm tra họ tên: bắt buộc, không được để trống hoặc chỉ có khoảng trắng.
        - Chuẩn hóa username: chuyển chữ thường, kiểm tra regex định dạng.
        - Kiểm tra mật khẩu qua hệ thống AUTH_PASSWORD_VALIDATORS của Django.
        - Chạy full_clean() để kiểm tra toàn bộ ràng buộc model trước khi save.
        """
        if not full_name or not isinstance(full_name, str) or not full_name.strip():
            raise ValueError('Họ và tên (full_name) không được để trống hoặc chỉ chứa khoảng trắng.')

        if not username or not isinstance(username, str) or not username.strip():
            raise ValueError('Username là thông tin bắt buộc.')

        # Canonicalize username: chuyển chữ thường và cắt khoảng trắng đầu cuối
        canonical_username = username.strip().lower()

        # Kiểm tra username qua hàm validate_canonical_username của model
        from apps.accounts.models.user import validate_canonical_username
        validate_canonical_username(canonical_username)

        cleaned_full_name = full_name.strip()

        if extra_fields.get('email'):
            extra_fields['email'] = self.normalize_email(extra_fields['email'])

        # Mặc định status là ACTIVE nếu chưa chỉ định
        status = extra_fields.pop('status', 'ACTIVE')
        is_active = (status == 'ACTIVE')

        user = self.model(
            username=canonical_username,
            full_name=cleaned_full_name,
            status=status,
            is_active=is_active,
            **extra_fields
        )

        if password:
            # Kiểm tra độ phức tạp và độ dài mật khẩu qua validators của Django
            validate_password(password, user=user)
            user.set_password(password)
        else:
            user.set_unusable_password()  # Nếu không có mật khẩu, đánh dấu là không thể đăng nhập bằng mật khẩu

        # Kiểm tra toàn diện model trước khi ghi xuống DB
        user.full_clean()
        user.save(using=self._db)
        return user

    def create_superuser(self, username: str, full_name: str, password: str | None = None, **extra_fields):
        """
        Tạo và lưu trữ một Superuser (phục vụ quản trị cấp cao).
        Bắt buộc gán is_staff=True và is_superuser=True.
        """
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('status', 'ACTIVE')  # Superuser luôn ở trạng thái ACTIVE

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser phải có is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser phải có is_superuser=True.')

        return self.create_user(username, full_name, password, **extra_fields)
