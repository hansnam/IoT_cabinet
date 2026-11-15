"""
Tiện ích mã hóa và giải mã thông tin nhạy cảm (mật khẩu broker MQTT)
sử dụng cryptography.Fernet theo đặc tả UI-05.
Khóa bí mật được lấy từ biến môi trường MQTT_CONFIG_ENCRYPTION_KEY.
"""
from django.conf import settings
from cryptography.fernet import Fernet


def get_fernet() -> Fernet:
    """Khởi tạo Fernet cipher từ khóa trong settings."""
    key = getattr(settings, 'MQTT_CONFIG_ENCRYPTION_KEY', '') or ''
    key = key.strip()
    if not key:
        raise RuntimeError(
            "Biến môi trường MQTT_CONFIG_ENCRYPTION_KEY chưa được thiết lập. "
            "Vui lòng cấu hình khóa 32-byte urlsafe base64 trong backend/.env."
        )
    return Fernet(key.encode('utf-8'))


def encrypt_secret(plaintext: str) -> str:
    """Mã hóa chuỗi mật khẩu sang chuỗi ciphertext an toàn."""
    if not plaintext:
        return ''
    f = get_fernet()
    return f.encrypt(plaintext.encode('utf-8')).decode('utf-8')


def decrypt_secret(ciphertext: str) -> str:
    """Giải mã chuỗi ciphertext về plaintext."""
    if not ciphertext:
        return ''
    f = get_fernet()
    return f.decrypt(ciphertext.encode('utf-8')).decode('utf-8')
