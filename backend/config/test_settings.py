"""
Cấu hình kiểm thử chuyên dụng cho Django Backend.
Kế thừa toàn bộ cấu hình từ settings.py chuẩn và giữ nguyên các kiểm tra an toàn.
"""
from config.settings import *

# Đảm bảo DatabaseGuardTestRunner luôn được áp dụng
TEST_RUNNER = 'apps.core.test_runner.DatabaseGuardTestRunner'

# Giữ nguyên cấu hình Argon2PasswordHasher để test đúng thuật toán thực tế
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
]