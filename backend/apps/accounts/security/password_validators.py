"""
Password Validators Module
Hiện thực hóa quy tắc kiểm tra độ dài mật khẩu:
- Mật khẩu phải có độ dài từ 12 đến 128 ký tự.
- Không tự ý trim khoảng trắng đầu cuối (giữ nguyên chuỗi người dùng nhập).
- Không echo hoặc ghi log mật khẩu khi phát sinh lỗi.
"""
from django.core.exceptions import ValidationError


class PasswordLengthValidator:
    """
    Validator kiểm tra độ dài mật khẩu nằm trong khoảng [min_length, max_length].
    Tương thích với cơ chế AUTH_PASSWORD_VALIDATORS của Django.
    """
    def __init__(self, min_length: int = 12, max_length: int = 128):
        self.min_length = min_length
        self.max_length = max_length

    def validate(self, password: str, user=None) -> None:
        """
        Kiểm tra mật khẩu thô.
        Ném ValidationError nếu vi phạm quy định về độ dài.
        """
        if not isinstance(password, str):
            raise ValidationError('Mật khẩu phải là chuỗi ký tự.')

        length = len(password)
        if length < self.min_length:
            raise ValidationError(
                f'Mật khẩu quá ngắn. Phải có ít nhất {self.min_length} ký tự.'
            )

        if length > self.max_length:
            raise ValidationError(
                f'Mật khẩu quá dài. Không được vượt quá {self.max_length} ký tự.'
            )

    def get_help_text(self) -> str:
        """Đoạn mô tả hiển thị trên giao diện Django Admin hoặc hướng dẫn người dùng."""
        return f'Mật khẩu phải chứa từ {self.min_length} đến {self.max_length} ký tự.'