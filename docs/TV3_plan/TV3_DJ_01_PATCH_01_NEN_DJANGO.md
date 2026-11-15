# TV3-DJ-01 — Nền Django, Custom User và Database An Toàn (Patch 01 — Bản Sửa)

Ngày cập nhật: **05/11/2026**  
Mã chặng: **TV3-DJ-01**  
Thuộc kế hoạch: [TV3_KE_HOACH_DJANGO_CHO_GEMINI.md](TV3_KE_HOACH_DJANGO_CHO_GEMINI.md)  
Người thực hiện: **TV3**  
Trạng thái: **CODE_PROPOSED (Bản sửa hoàn thiện theo review)**  

---

## 1. Mục tiêu, Prerequisites và Thay đổi trong bản sửa

### 1.1 Mục tiêu chặng 01
1. **Thiết lập hạ tầng Django 5.2 LTS**: Cấu hình chuẩn cho backend đặt trực tiếp tại `backend/`, nạp biến môi trường từ `backend/.env`.
2. **Khởi tạo Custom User Model (`accounts.User`) ngay từ migration đầu tiên**: Dùng `UUID` v4 làm Primary Key, username canonical (3–64 ký tự), trường `status` (`ACTIVE`, `LOCKED`, `ARCHIVED`), `must_change_password`, đồng bộ trạng thái `is_active` với `status`.
3. **Triển khai Registry phiên (`AuthSession`) và Bộ đệm thử đăng nhập (`LoginAttempt`)**: Tạo đủ 3 model của `accounts` một lần để không phải sửa migration chắp vá về sau.
4. **Áp dụng thuật toán băm Argon2id**: Kích hoạt `Argon2PasswordHasher` làm thuật toán băm mật khẩu mặc định qua thư viện `argon2-cffi`.
5. **Chốt chặn an toàn Database (`DatabaseGuardTestRunner`)**: Ngăn chặn việc chạy test nhầm vào database chính (`iot_cabinet_django`) hoặc database của Flask cũ (`iot_cabinet`, `iot_cabinet_test`).

### 1.2 Các điểm đã sửa và tối ưu theo review
1. **[P1 — Sửa Guard DB test]**: Bắt buộc tên test DB phải chính xác là `iot_cabinet_django_test`, bắt buộc khác DB chính (`default_db_name != test_db_name`), nếu thiếu cấu hình tên test DB thì chặn ngay.
2. **[P1 — Sửa đồng bộ khi khóa tài khoản]**: Trong `User.save()`, nếu caller gọi `save(update_fields=['status'])`, tự động bổ sung `'is_active'` vào `update_fields` để đảm bảo cột `is_active` được cập nhật xuống PostgreSQL. Bổ sung test kiểm tra bằng `refresh_from_db()`.
3. **[P2 — Gọi Validator đầy đủ trong `create_user`]**: Kiểm tra họ tên không rỗng/khoảng trắng, gọi `validate_canonical_username()`, gọi `validate_password()` của Django, và chạy `full_clean()` trước khi `save()`.
4. **[P2 — Ghi đè `get_full_name()` và `get_short_name()`]**: Trả về `full_name`, tránh lỗi hiển thị `"None None"` do đã loại bỏ `first_name` và `last_name`.
5. **[P2 — Chỉnh sửa hướng dẫn chạy test & số lượng test]**: Không yêu cầu tạo trước DB test (để Django tự quản lý vòng đời tạo/xóa test DB mà không bị prompt hỏi xóa DB cũ). Cập nhật chính xác số lượng test: **15 tests**.
6. **[Tối ưu đơn giản hóa]**:
   - Bỏ index thủ công của `username` trong `User.Meta.indexes` (vì `unique=True` đã tự tạo unique index).
   - Bỏ index thủ công của `session_key` trong `AuthSession.Meta.indexes` (vì `db_index=True` đã tự tạo index).
   - Đổi tên `LengthAndComplexityValidator` thành `PasswordLengthValidator` cho chuẩn xác chức năng.

---

## 2. Danh sách file theo thứ tự thao tác

| STT | Đường dẫn file | Trạng thái | Lý do / Trách nhiệm |
|---|---|---|---|
| 1 | `backend/requirements.txt` | **NEW** | Ghim chính xác danh sách thư viện Python của Django backend |
| 2 | `backend/.env.example` | **NEW** | Mẫu cấu hình môi trường chuẩn cho Django (DB mới, HMAC key) |
| 3 | `backend/config/settings.py` | **MODIFY** | Nạp dotenv, cấu hình PostgreSQL mới, Argon2, Custom User, Session, PasswordLengthValidator |
| 4 | `backend/config/test_settings.py` | **NEW** | Cấu hình test chuyên biệt, gắn DatabaseGuardTestRunner |
| 5 | `backend/apps/__init__.py` | **NEW** | Đánh dấu package chứa các ứng dụng nghiệp vụ Django |
| 6 | `backend/apps/core/__init__.py` | **NEW** | Đánh dấu package app `core` (hạ tầng dùng chung) |
| 7 | `backend/apps/core/apps.py` | **NEW** | Khai báo `CoreConfig` (app label: `core`) |
| 8 | `backend/apps/core/test_runner.py` | **NEW** | Lớp `DatabaseGuardTestRunner` bảo vệ database |
| 9 | `backend/apps/core/tests/__init__.py` | **NEW** | Đánh dấu package test của `core` |
| 10 | `backend/apps/core/tests/test_database_guard.py` | **NEW** | 5 test cases kiểm tra chốt chặn DatabaseGuard |
| 11 | `backend/apps/accounts/__init__.py` | **NEW** | Đánh dấu package app `accounts` (người dùng & xác thực) |
| 12 | `backend/apps/accounts/apps.py` | **NEW** | Khai báo `AccountsConfig` (app label: `accounts`) |
| 13 | `backend/apps/accounts/managers.py` | **NEW** | `CustomUserManager` kiểm tra dữ liệu và băm mật khẩu |
| 14 | `backend/apps/accounts/models/__init__.py` | **NEW** | Export `User`, `AuthSession`, `LoginAttempt` cho Django ORM |
| 15 | `backend/apps/accounts/models/user.py` | **NEW** | Model `User` UUID, đồng bộ `is_active` cả khi `update_fields` |
| 16 | `backend/apps/accounts/models/auth_session.py` | **NEW** | Model `AuthSession` lưu registry phiên đăng nhập |
| 17 | `backend/apps/accounts/models/login_attempt.py` | **NEW** | Model `LoginAttempt` lưu HMAC chống brute-force |
| 18 | `backend/apps/accounts/security/__init__.py` | **NEW** | Đánh dấu package bảo mật tài khoản |
| 19 | `backend/apps/accounts/security/password_validators.py` | **NEW** | `PasswordLengthValidator` kiểm tra độ dài mật khẩu 12–128 ký tự |
| 20 | `backend/apps/accounts/tests/__init__.py` | **NEW** | Đánh dấu package test của `accounts` |
| 21 | `backend/apps/accounts/tests/test_models_roles.py` | **NEW** | 10 test cases kiểm tra User, UUID, Argon2, validation, lock, session |

---

## 3. Luồng hoạt động & Kiến thức Django cần hiểu

### 3.1 Luồng gọi tạo người dùng an toàn trong `CustomUserManager.create_user()`
```text
Client / Caller gọi create_user(username, full_name, password)
  │
  ├── 1. Kiểm tra họ tên: Loại bỏ khoảng trắng thừa, cấm chuỗi rỗng/chỉ chứa space.
  │
  ├── 2. Chuẩn hóa & kiểm tra username:
  │      - username.strip().lower() -> canonical username
  │      - validate_canonical_username(): Regex ^[a-zA-Z0-9_.-]{3,64}$ (ném ValidationError nếu vi phạm)
  │
  ├── 3. Kiểm tra mật khẩu (Password Validation):
  │      - Khởi tạo instance User tạm thời trong bộ nhớ
  │      - django.contrib.auth.password_validation.validate_password(password, user=user)
  │        (Kích hoạt PasswordLengthValidator 12-128 ký tự, Similarity, Common...)
  │      - user.set_password(password): Băm bằng Argon2id
  │
  ├── 4. Kiểm tra toàn diện Model: user.full_clean()
  │
  └── 5. Lưu xuống DB: user.save(using=self._db)
```

### 3.2 Cơ chế đồng bộ `is_active` khi gọi `save(update_fields=['status'])`
- Mặc định trong Django, nếu gọi `save(update_fields=['status'])`, Django chỉ sinh câu lệnh SQL cập nhật duy nhất cột `status`.
- Dù hàm `clean()` đã tính toán `self.is_active = False` trong RAM, giá trị này sẽ bị bỏ rơi và không được ghi xuống PostgreSQL nếu `'is_active'` không có trong `update_fields`.
- Giải pháp: Trong `User.save()`, phát hiện nếu `'status'` nằm trong `update_fields` thì tự động thêm `'is_active'` vào danh sách cập nhật.

### 3.3 Cơ chế chốt chặn nghiêm ngặt của `DatabaseGuardTestRunner`
```text
Chạy lệnh test:
  │
  ▼
DatabaseGuardTestRunner.setup_databases()
  │
  ├── 1. Kiểm tra test_db_name có tồn tại?
  │      └── KHÔNG: Ném RuntimeError("Connection chưa được cấu hình TEST['NAME']")
  │
  ├── 2. test_db_name có đúng là 'iot_cabinet_django_test'?
  │      └── KHÔNG: Ném RuntimeError("Chỉ cho phép chạy test trên 'iot_cabinet_django_test'")
  │
  ├── 3. test_db_name có trùng với DB chính (default_db_name)?
  │      └── CÓ: Ném RuntimeError("Tên DB test trùng hoàn toàn với DB chính")
  │
  └── 4. test_db_name có nằm trong FORBIDDEN_TEST_DATABASES?
         └── CÓ: Ném RuntimeError("Database test nằm trong danh sách cấm")
```

---

## 4. Toàn bộ nội dung từng file

### File 1: `backend/requirements.txt`
**Vị trí:** `backend/requirements.txt`  

```text
Django==5.2.19
djangorestframework==3.18.3
psycopg[binary]==3.3.8
argon2-cffi==25.1.0
python-dotenv==1.2.4
```

---

### File 2: `backend/.env.example`
**Vị trí:** `backend/.env.example`  

```bash
# ==============================================================================
# IoT Cabinet Django Backend - Environment Configuration
# ==============================================================================

# --- Django Core Settings ---
DJANGO_SECRET_KEY=django-insecure-dev-only-secret-key-must-be-changed-in-production-at-least-50-chars
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost

# --- PostgreSQL Primary Database ---
# TUYỆT ĐỐI KHÔNG DÙNG DB 'iot_cabinet' CỦA BẢN FLASK CŨ
DB_NAME=iot_cabinet_django
DB_USER=postgres
DB_PASSWORD=postgres123
DB_HOST=127.0.0.1
DB_PORT=5432

# --- PostgreSQL Test Database ---
# TUYỆT ĐỐI KHÔNG DÙNG DB 'iot_cabinet_test' CỦA BẢN FLASK CŨ
TEST_DB_NAME=iot_cabinet_django_test

# --- Security HMAC Keys ---
# Khóa HMAC bí mật để băm định danh username/IP trong login_attempts
AUTH_LOG_HMAC_KEY=ecbf305c5a9a2b1fc6c6a1ad9a7f104eebf620c49b2cb17eebe10c08367cba2b
```

---

### File 3: `backend/config/settings.py`
**Vị trí:** `backend/config/settings.py`  

```python
"""
Django settings for IoT Cabinet Backend Project.
Tuân thủ đặc tả kỹ thuật V1.4 và kế hoạch TV3.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# BASE_DIR trỏ tới thư mục backend/
BASE_DIR = Path(__file__).resolve().parent.parent

# Chỉ nạp file .env nội bộ đặt ngay tại thư mục backend/
# Tránh tuyệt đối việc nạp nhầm file .env của Flask ở thư mục gốc
backend_env = BASE_DIR / '.env'
if backend_env.exists():
    load_dotenv(dotenv_path=backend_env)

# Khóa bí mật phục vụ mật mã học của Django
SECRET_KEY = os.getenv(
    'DJANGO_SECRET_KEY',
    'django-insecure-fallback-dev-secret-key-3b4f9a0c1e2d8e7a6b5c4d3e2f1a0b'
)

# Chế độ Debug (True khi phát triển, False khi chạy production)
DEBUG = os.getenv('DJANGO_DEBUG', 'True').lower() in ('true', '1', 'yes')

# Danh sách domain / IP được phép truy cập
allowed_hosts_env = os.getenv('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost')
ALLOWED_HOSTS = [h.strip() for h in allowed_hosts_env.split(',') if h.strip()]

# ------------------------------------------------------------------------------
# Khai báo các ứng dụng (Apps)
# ------------------------------------------------------------------------------
INSTALLED_APPS = [
    # Django Built-in Apps
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party Apps
    'rest_framework',

    # Dự án IoT Cabinet Apps
    'apps.core.apps.CoreConfig',
    'apps.accounts.apps.AccountsConfig',
]

# ------------------------------------------------------------------------------
# Middleware Pipeline
# ------------------------------------------------------------------------------
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ------------------------------------------------------------------------------
# Cấu hình Cơ sở dữ liệu PostgreSQL (iot_cabinet_django)
# ------------------------------------------------------------------------------
DB_NAME = os.getenv('DB_NAME', 'iot_cabinet_django')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres123')
DB_HOST = os.getenv('DB_HOST', '127.0.0.1')
DB_PORT = os.getenv('DB_PORT', '5432')
TEST_DB_NAME = os.getenv('TEST_DB_NAME', 'iot_cabinet_django_test')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': DB_NAME,
        'USER': DB_USER,
        'PASSWORD': DB_PASSWORD,
        'HOST': DB_HOST,
        'PORT': DB_PORT,
        'TEST': {
            'NAME': TEST_DB_NAME,
        },
    }
}

# ------------------------------------------------------------------------------
# Custom User Model & Password Hashers (Argon2id)
# ------------------------------------------------------------------------------
# Chỉ định model User tùy biến trước migration đầu tiên
AUTH_USER_MODEL = 'accounts.User'

# Ưu tiên Argon2id làm thuật toán băm mật khẩu chính
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
]

# Bộ kiểm tra độ dài mật khẩu (12–128 ký tự, không trim)
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'apps.accounts.security.password_validators.PasswordLengthValidator',
        'OPTIONS': {
            'min_length': 12,
            'max_length': 128,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
]

# ------------------------------------------------------------------------------
# Thời gian & Quốc tế hóa
# ------------------------------------------------------------------------------
LANGUAGE_CODE = 'vi'
TIME_ZONE = 'Asia/Ho_Chi_Minh'
USE_I18N = True
USE_TZ = True

# ------------------------------------------------------------------------------
# Cấu hình Session theo V1.4
# ------------------------------------------------------------------------------
# Sử dụng cơ chế lưu session trong Database
SESSION_ENGINE = 'django.contrib.sessions.backends.db'

# Thời hạn sống tuyệt đối 15 phút (900 giây)
SESSION_COOKIE_AGE = 900

# KHÔNG tự động gia hạn phiên (sliding expiration) mỗi khi có request
SESSION_SAVE_EVERY_REQUEST = False

# Bảo vệ Cookie chống truy cập từ JavaScript (chống XSS đánh cắp session)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = False  # Đặt False trong môi trường dev; True khi chạy HTTPS production

# Khóa bí mật băm HMAC cho định danh login_attempts
AUTH_LOG_HMAC_KEY = os.getenv(
    'AUTH_LOG_HMAC_KEY',
    'ecbf305c5a9a2b1fc6c6a1ad9a7f104eebf620c49b2cb17eebe10c08367cba2b'
)

# ------------------------------------------------------------------------------
# Test Runner Guard
# ------------------------------------------------------------------------------
TEST_RUNNER = 'apps.core.test_runner.DatabaseGuardTestRunner'

# Static files
STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
```

---

### File 4: `backend/config/test_settings.py`
**Vị trí:** `backend/config/test_settings.py`  

```python
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
```

---

### File 5: `backend/apps/__init__.py`
**Vị trí:** `backend/apps/__init__.py`  
*(Nội dung: file rỗng)*

---

### File 6: `backend/apps/core/__init__.py`
**Vị trí:** `backend/apps/core/__init__.py`  
*(Nội dung: file rỗng)*

---

### File 7: `backend/apps/core/apps.py`
**Vị trí:** `backend/apps/core/apps.py`  

```python
"""
AppConfig cho core application.
Quản lý các tiện ích hạ tầng chung: middleware, lỗi, test runner.
"""
from django.apps import AppConfig

class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'
    label = 'core'
    verbose_name = 'Hạ tầng hệ thống'
```

---

### File 8: `backend/apps/core/test_runner.py`
**Vị trí:** `backend/apps/core/test_runner.py`  

```python
"""
Database Guard Test Runner Module
Bảo vệ toàn vẹn dữ liệu: Chặn đứng quá trình kiểm thử nếu phát hiện nguy cơ
chạy test đè lên Database chính hoặc Database của bản Flask cũ.
"""
from django.test.runner import DiscoverRunner
from django.db import connections

# Tên database duy nhất được phép dùng cho môi trường kiểm thử
ALLOWED_TEST_DB_NAME = 'iot_cabinet_django_test'

# Danh sách tên Database tuyệt đối CẤM CHẠM VÀO trong môi trường kiểm thử
FORBIDDEN_TEST_DATABASES = {
    'iot_cabinet',           # Database nghiệp vụ Flask cũ
    'iot_cabinet_test',      # Database test Flask cũ
    'iot_cabinet_django',    # Database nghiệp vụ Django chính (dev / production)
}

class DatabaseGuardTestRunner(DiscoverRunner):
    """
    Test Runner mở rộng từ DiscoverRunner của Django.
    Trước khi tạo hoặc dọn dẹp (flush/drop) database test, setup_databases sẽ:
    1. Kiểm tra cấu hình database test có tồn tại hay không.
    2. Bắt buộc tên database test phải là 'iot_cabinet_django_test'.
    3. Bắt buộc tên database test phải KHÁC tên database chính (default connection).
    4. Chặn tuyệt đối nếu database test trùng với bất kỳ database nào trong FORBIDDEN_TEST_DATABASES.
    """

    def setup_databases(self, **kwargs):
        for alias in connections:
            connection = connections[alias]
            test_db_name = connection.settings_dict.get('TEST', {}).get('NAME')
            default_db_name = connection.settings_dict.get('NAME')

            # 1. Chặn nếu thiếu cấu hình tên database test
            if not test_db_name:
                raise RuntimeError(
                    f"[DATABASE GUARD ERROR] Connection '{alias}' chưa được cấu hình TEST['NAME']. "
                    f"Bắt buộc phải chỉ định tên database test cụ thể để tránh xóa nhầm dữ liệu."
                )

            # 2. Bắt buộc tên database test phải là iot_cabinet_django_test
            if test_db_name != ALLOWED_TEST_DB_NAME:
                raise RuntimeError(
                    f"[DATABASE GUARD ERROR] Tên database test '{test_db_name}' không hợp lệ. "
                    f"Hệ thống chỉ cho phép chạy test trên database được quy định: '{ALLOWED_TEST_DB_NAME}'."
                )

            # 3. Bắt buộc tên DB test phải khác DB chính
            if test_db_name == default_db_name:
                raise RuntimeError(
                    f"[DATABASE GUARD ERROR] Tên database test '{test_db_name}' trùng hoàn toàn với "
                    f"database chính '{default_db_name}'. Dừng ngay lập tức để chống xóa dữ liệu thật."
                )

            # 4. Chặn nếu nằm trong danh sách cấm
            if test_db_name in FORBIDDEN_TEST_DATABASES:
                raise RuntimeError(
                    f"[DATABASE GUARD ERROR] Nguy hiểm! Database test '{test_db_name}' "
                    f"nằm trong danh sách cấm ({FORBIDDEN_TEST_DATABASES})."
                )

        return super().setup_databases(**kwargs)
```

---

### File 9: `backend/apps/core/tests/__init__.py`
**Vị trí:** `backend/apps/core/tests/__init__.py`  
*(Nội dung: file rỗng)*

---

### File 10: `backend/apps/core/tests/test_database_guard.py`
**Vị trí:** `backend/apps/core/tests/test_database_guard.py`  

```python
"""
Test module kiểm tra chốt chặn an toàn DatabaseGuardTestRunner.
Chạy độc lập không cần kết nối database vật lý (SimpleTestCase).
"""
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase
from apps.core.test_runner import (
    DatabaseGuardTestRunner,
    ALLOWED_TEST_DB_NAME,
    FORBIDDEN_TEST_DATABASES
)

class DatabaseGuardTestCase(SimpleTestCase):
    """Xác minh cơ chế chặn nghiêm ngặt của DatabaseGuardTestRunner."""

    def setUp(self):
        self.runner = DatabaseGuardTestRunner()

    def test_missing_test_db_name_is_blocked(self):
        """Xác minh nếu thiếu cấu hình TEST['NAME'] thì bị chặn ngay."""
        mock_connection = MagicMock()
        mock_connection.settings_dict = {
            'NAME': 'iot_cabinet_django',
            'TEST': {'NAME': None}
        }
        with patch('apps.core.test_runner.connections', {'default': mock_connection}):
            with self.assertRaises(RuntimeError) as ctx:
                self.runner.setup_databases()
            self.assertIn("chưa được cấu hình TEST['NAME']", str(ctx.exception))

    def test_disallowed_test_db_name_is_blocked(self):
        """Xác minh tên database test khác ALLOWED_TEST_DB_NAME sẽ bị từ chối."""
        mock_connection = MagicMock()
        mock_connection.settings_dict = {
            'NAME': 'iot_cabinet_django',
            'TEST': {'NAME': 'other_test_db'}
        }
        with patch('apps.core.test_runner.connections', {'default': mock_connection}):
            with self.assertRaises(RuntimeError) as ctx:
                self.runner.setup_databases()
            self.assertIn("Hệ thống chỉ cho phép chạy test trên database được quy định", str(ctx.exception))

    def test_test_db_same_as_default_db_is_blocked(self):
        """Xác minh trường hợp tên DB test trùng với DB chính sẽ bị chặn đứng."""
        mock_connection = MagicMock()
        mock_connection.settings_dict = {
            'NAME': ALLOWED_TEST_DB_NAME,
            'TEST': {'NAME': ALLOWED_TEST_DB_NAME}
        }
        with patch('apps.core.test_runner.connections', {'default': mock_connection}):
            with self.assertRaises(RuntimeError) as ctx:
                self.runner.setup_databases()
            self.assertIn("trùng hoàn toàn với database chính", str(ctx.exception))

    def test_forbidden_databases_are_blocked(self):
        """Xác minh mọi database trong danh sách cấm đều bị chặn."""
        for forbidden_db in FORBIDDEN_TEST_DATABASES:
            mock_connection = MagicMock()
            mock_connection.settings_dict = {
                'NAME': 'some_safe_db',
                'TEST': {'NAME': forbidden_db}
            }
            with patch('apps.core.test_runner.connections', {'default': mock_connection}):
                with self.assertRaises(RuntimeError) as ctx:
                    self.runner.setup_databases()
                self.assertIn('[DATABASE GUARD ERROR]', str(ctx.exception))

    def test_allowed_test_database_proceeds_to_parent(self):
        """Xác minh cấu hình chuẩn (iot_cabinet_django_test khác iot_cabinet_django) vượt qua guard."""
        mock_connection = MagicMock()
        mock_connection.settings_dict = {
            'NAME': 'iot_cabinet_django',
            'TEST': {'NAME': ALLOWED_TEST_DB_NAME}
        }
        with patch('apps.core.test_runner.connections', {'default': mock_connection}):
            with patch('django.test.runner.DiscoverRunner.setup_databases', return_value=['mock_db']):
                result = self.runner.setup_databases()
                self.assertEqual(result, ['mock_db'])
```

---

### File 11: `backend/apps/accounts/__init__.py`
**Vị trí:** `backend/apps/accounts/__init__.py`  
*(Nội dung: file rỗng)*

---

### File 12: `backend/apps/accounts/apps.py`
**Vị trí:** `backend/apps/accounts/apps.py`  

```python
"""
AppConfig cho accounts application.
Chịu trách nhiệm quản lý User, AuthSession, LoginAttempt và chính sách phân quyền.
"""
from django.apps import AppConfig

class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.accounts'
    label = 'accounts'
    verbose_name = 'Tài khoản & Xác thực'
```

---

### File 13: `backend/apps/accounts/managers.py`
**Vị trí:** `backend/apps/accounts/managers.py`  

```python
"""
Custom User Manager Module
Quản lý việc tạo người dùng thông thường và quản trị viên (superuser),
chuẩn hóa tên đăng nhập (canonical lowercase), kiểm tra định dạng và băm mật khẩu.
"""
from django.contrib.auth.models import BaseUserManager
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

class CustomUserManager(BaseUserManager):
    """
    Manager tùy biến cho model User dùng UUID và canonical username.
    """

    def create_user(self, username: str, full_name: str, password: str = None, **extra_fields):
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

        if 'email' in extra_fields and extra_fields['email']:
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
            user.set_unusable_password()

        # Kiểm tra toàn diện model trước khi ghi xuống DB
        user.full_clean()
        user.save(using=self._db)
        return user

    def create_superuser(self, username: str, full_name: str, password: str = None, **extra_fields):
        """
        Tạo và lưu trữ một Superuser (phục vụ quản trị cấp cao).
        Bắt buộc gán is_staff=True và is_superuser=True.
        """
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('status', 'ACTIVE')

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser bắt buộc phải có is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser bắt buộc phải có is_superuser=True.')

        return self.create_user(username, full_name, password, **extra_fields)
```

---

### File 14: `backend/apps/accounts/models/__init__.py`
**Vị trí:** `backend/apps/accounts/models/__init__.py`  

```python
"""
Models package của apps.accounts.
Export đầy đủ ba model cốt lõi để Django ORM và hệ thống migrations phát hiện.
"""
from apps.accounts.models.user import User
from apps.accounts.models.auth_session import AuthSession
from apps.accounts.models.login_attempt import LoginAttempt

__all__ = ['User', 'AuthSession', 'LoginAttempt']
```

---

### File 15: `backend/apps/accounts/models/user.py`
**Vị trí:** `backend/apps/accounts/models/user.py`  

```python
"""
User Model Module
Kế thừa AbstractUser của Django nhưng thay thế ID số nguyên bằng UUID v4,
chuẩn hóa username canonical (3-64 ký tự), bổ sung các trường nghiệp vụ theo V1.4:
status (ACTIVE/LOCKED/ARCHIVED), must_change_password, failed_login_count, locked_until,
password_changed_at và liên kết người tạo created_by.
Đồng bộ trạng thái is_active cả khi gọi save(update_fields=['status']).
"""
import uuid
import re
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from apps.accounts.managers import CustomUserManager

# Biểu thức chính quy cho username canonical: 3-64 ký tự chữ cái, số, gạch dưới, gạch ngang, dấu chấm
USERNAME_REGEX = re.compile(r'^[a-zA-Z0-9_.-]{3,64}$')

def validate_canonical_username(value: str) -> None:
    """Kiểm tra định dạng canonical username từ 3 đến 64 ký tự hợp lệ."""
    if not isinstance(value, str) or not USERNAME_REGEX.match(value):
        raise ValidationError(
            'Username phải có độ dài từ 3 đến 64 ký tự, chỉ chứa chữ cái, chữ số, gạch dưới, gạch ngang hoặc dấu chấm.'
        )

class User(AbstractUser):
    """
    Model Người dùng cốt lõi của hệ thống IoT Cabinet.
    Kế thừa AbstractUser để tận dụng Group và Permission sẵn có của Django,
    đồng thời định nghĩa lại:
    - id: UUID v4 làm Primary Key (chống brute-force / enumeration ID số tuần tự).
    - first_name, last_name: Loại bỏ (đặt None) để dùng trường duy nhất full_name.
    - status: Quản lý vòng đời tài khoản (ACTIVE, LOCKED, ARCHIVED).
    """
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Hoạt động'
        LOCKED = 'LOCKED', 'Tạm khóa'
        ARCHIVED = 'ARCHIVED', 'Lưu trữ / Đã hủy'

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
        help_text='Thời điểm tạo tài khoản.'
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text='Thời điểm cập nhật bản ghi gần nhất.'
    )

    # Loại bỏ first_name, last_name mặc định của AbstractUser
    first_name = None
    last_name = None

    objects = CustomUserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['full_name']

    class Meta:
        db_table = 'accounts_user'
        verbose_name = 'Người dùng'
        verbose_name_plural = 'Danh sách người dùng'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status'], name='ix_users_status'),
            models.Index(fields=['created_at'], name='ix_users_created_at'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=['ACTIVE', 'LOCKED', 'ARCHIVED']),
                name='ck_users_status_valid'
            ),
            models.CheckConstraint(
                condition=models.Q(failed_login_count__gte=0),
                name='ck_users_failed_login_count_gte_0'
            ),
        ]

    def __str__(self) -> str:
        return f'{self.username} ({self.full_name})'

    def get_full_name(self) -> str:
        """Trả về họ và tên đầy đủ của người dùng."""
        return self.full_name

    def get_short_name(self) -> str:
        """Trả về tên hiển thị ngắn gọn."""
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
        self.clean()

        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            # Chuyển đổi sang set để thao tác nếu là list/tuple
            update_fields_set = set(update_fields)
            if 'status' in update_fields_set:
                update_fields_set.add('is_active')
            kwargs['update_fields'] = update_fields_set

        super().save(*args, **kwargs)
```

---

### File 16: `backend/apps/accounts/models/auth_session.py`
**Vị trí:** `backend/apps/accounts/models/auth_session.py`  

```python
"""
AuthSession Model Module
Quản lý Registry phiên làm việc đăng nhập của người dùng.
- Mỗi bản ghi đại diện cho một phiên có ID duy nhất (sid: UUID v4).
- Không ràng buộc Foreign Key cứng tới bảng django_session (tránh cascade delete hoặc lỗi khi chạy lệnh clearsessions).
- Thiết lập thời gian sống tuyệt đối (15 phút / 900 giây), hỗ trợ thu hồi (revoke) có lý do.
"""
import uuid
from django.db import models
from django.conf import settings
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
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at'], name='ix_auth_sessions_user_created'),
            models.Index(fields=['expires_at'], name='ix_auth_sessions_expires_at'),
        ]
        constraints = [
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
```

---

### File 17: `backend/apps/accounts/models/login_attempt.py`
**Vị trí:** `backend/apps/accounts/models/login_attempt.py`  

```python
"""
LoginAttempt Model Module
Ghi nhận lịch sử các lần thử đăng nhập phục vụ rate-limit và chống tấn công dò mật khẩu (brute-force).
- Tuyệt đối không lưu username hay IP ở dạng thô; thay vào đó sử dụng khóa băm HMAC SHA-256 (username_key, ip_key).
- Có liên kết tới request_id để đối chiếu (correlation) với nhật ký hệ thống.
"""
import uuid
from django.db import models
from django.conf import settings

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
        ordering = ['-occurred_at']
        indexes = [
            models.Index(fields=['username_key', 'occurred_at'], name='ix_login_attempts_user_time'),
            models.Index(fields=['ip_key', 'occurred_at'], name='ix_login_attempts_ip_time'),
        ]

    def __str__(self) -> str:
        status_text = 'SUCCESS' if self.success else f'FAILED({self.failure_code})'
        return f'LoginAttempt {self.id} [{status_text}] at {self.occurred_at}'
```

---

### File 18: `backend/apps/accounts/security/__init__.py`
**Vị trí:** `backend/apps/accounts/security/__init__.py`  
*(Nội dung: file rỗng)*

---

### File 19: `backend/apps/accounts/security/password_validators.py`
**Vị trí:** `backend/apps/accounts/security/password_validators.py`  

```python
"""
Password Validators Module
Hiện thực hóa quy tắc kiểm tra độ dài mật khẩu theo chuẩn V1.4:
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
                f'Mật khẩu quá ngắn. Độ dài tối thiểu là {self.min_length} ký tự (hiện tại: {length} ký tự).'
            )
        if length > self.max_length:
            raise ValidationError(
                f'Mật khẩu quá dài. Độ dài tối đa cho phép là {self.max_length} ký tự (hiện tại: {length} ký tự).'
            )

    def get_help_text(self) -> str:
        """Đoạn mô tả hiển thị trên giao diện Django Admin hoặc hướng dẫn người dùng."""
        return f'Mật khẩu phải chứa từ {self.min_length} đến {self.max_length} ký tự.'
```

---

### File 20: `backend/apps/accounts/tests/__init__.py`
**Vị trí:** `backend/apps/accounts/tests/__init__.py`  
*(Nội dung: file rỗng)*

---

### File 21: `backend/apps/accounts/tests/test_models_roles.py`
**Vị trí:** `backend/apps/accounts/tests/test_models_roles.py`  

```python
"""
Test module kiểm tra các Model của accounts:
- Khóa chính UUID v4 của User.
- Chuẩn hóa username chữ thường và kiểm tra độ dài.
- Thuật toán băm mật khẩu Argon2id qua set_password() và check_password().
- Đồng bộ trường is_active với status cả khi dùng update_fields.
- Ghi đè get_full_name() và get_short_name().
- Kiểm tra tính hợp lệ đầu vào khi create_user().
- Bộ kiểm tra mật khẩu PasswordLengthValidator.
- Model AuthSession và LoginAttempt.
"""
import uuid
from datetime import timedelta
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from apps.accounts.models import AuthSession, LoginAttempt
from apps.accounts.security.password_validators import PasswordLengthValidator

User = get_user_model()

class PasswordLengthValidatorTestCase(SimpleTestCase):
    """Kiểm tra đơn vị logic của PasswordLengthValidator không cần DB."""

    def setUp(self):
        self.validator = PasswordLengthValidator(min_length=12, max_length=128)

    def test_valid_password_passes(self):
        """Mật khẩu đủ từ 12 đến 128 ký tự phải vượt qua kiểm tra."""
        self.validator.validate("Password@123456")
        self.validator.validate("A" * 12)
        self.validator.validate("A" * 128)

    def test_too_short_password_fails(self):
        """Mật khẩu dưới 12 ký tự phải bị ném ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            self.validator.validate("Short@123")
        self.assertIn("Mật khẩu quá ngắn", str(ctx.exception))

    def test_too_long_password_fails(self):
        """Mật khẩu trên 128 ký tự phải bị ném ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            self.validator.validate("A" * 129)
        self.assertIn("Mật khẩu quá dài", str(ctx.exception))

    def test_password_is_not_trimmed(self):
        """Khoảng trắng đầu cuối không bị trim: '   123456   ' có độ dài 12 ký tự."""
        self.validator.validate("   123456   ")


class UserModelTestCase(TestCase):
    """Kiểm tra tích hợp Model User và các quan hệ trên Database."""

    def test_create_user_with_uuid_and_argon2(self):
        """Xác minh User được cấp khóa chính UUID và mật khẩu được băm bằng Argon2id."""
        user = User.objects.create_user(
            username="Test_Admin_01",
            full_name="Nguyễn Văn Quản Trị",
            password="SecurePassword@2026!"
        )

        # 1. Khóa chính phải là UUID v4 hợp lệ
        self.assertIsInstance(user.id, uuid.UUID)

        # 2. Username phải được chuyển thành chữ thường (canonicalized)
        self.assertEqual(user.username, "test_admin_01")

        # 3. Chuỗi băm mật khẩu phải sử dụng Argon2
        self.assertTrue(user.password.startswith("argon2"), f"Hash không dùng Argon2: {user.password}")
        self.assertTrue(user.check_password("SecurePassword@2026!"))
        self.assertFalse(user.check_password("WrongPassword@2026!"))

        # 4. Trạng thái ACTIVE thì is_active phải là True
        self.assertEqual(user.status, User.Status.ACTIVE)
        self.assertTrue(user.is_active)

    def test_get_full_name_and_short_name(self):
        """Xác minh get_full_name() và get_short_name() trả về đúng tên chứ không phải 'None None'."""
        user = User.objects.create_user(
            username="operator_demo",
            full_name="Nguyễn Văn An",
            password="OperatorPassword@2026!"
        )
        self.assertEqual(user.get_full_name(), "Nguyễn Văn An")
        self.assertEqual(user.get_short_name(), "Nguyễn")

    def test_create_user_rejects_invalid_username_length(self):
        """Xác minh create_user từ chối username ngắn hơn 3 ký tự."""
        with self.assertRaises(ValidationError):
            User.objects.create_user(
                username="a",
                full_name="Người Dùng Lỗi",
                password="ValidPassword@2026!"
            )

    def test_create_user_rejects_empty_or_whitespace_fullname(self):
        """Xác minh create_user từ chối họ tên toàn khoảng trắng."""
        with self.assertRaises(ValueError):
            User.objects.create_user(
                username="valid_user",
                full_name="    ",
                password="ValidPassword@2026!"
            )

    def test_create_user_rejects_short_password(self):
        """Xác minh create_user kích hoạt validator từ chối mật khẩu quá ngắn."""
        with self.assertRaises(ValidationError):
            User.objects.create_user(
                username="valid_user",
                full_name="Người Dùng Hợp Lệ",
                password="short"
            )

    def test_status_and_is_active_sync_with_update_fields_and_refresh(self):
        """
        Xác minh khóa tài khoản với save(update_fields=['status'])
        vẫn đồng bộ is_active=False xuống PostgreSQL và kiểm tra lại bằng refresh_from_db().
        """
        user = User.objects.create_user(
            username="operator_lock_test",
            full_name="Nhân viên vận hành",
            password="OperatorPassword@2026!"
        )
        self.assertTrue(user.is_active)

        # Khóa tài khoản sử dụng update_fields
        user.status = User.Status.LOCKED
        user.save(update_fields=['status'])

        # Nạp lại bản ghi từ PostgreSQL để chứng minh DB thật đã được cập nhật
        user.refresh_from_db()
        self.assertEqual(user.status, User.Status.LOCKED)
        self.assertFalse(user.is_active)

        # Mở khóa tài khoản lại
        user.status = User.Status.ACTIVE
        user.save(update_fields=['status'])
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_auth_session_lifecycle(self):
        """Xác minh vòng đời và cờ is_active của AuthSession."""
        user = User.objects.create_user(
            username="session_test_user",
            full_name="Người Dùng Test Session",
            password="ValidPassword@2026!"
        )
        now = timezone.now()
        session = AuthSession.objects.create(
            user=user,
            session_key="django_session_key_sample_123456",
            expires_at=now + timedelta(minutes=15),
            ip_address="127.0.0.1",
            user_agent="Mozilla/5.0 TestBrowser"
        )

        self.assertIsInstance(session.id, uuid.UUID)
        self.assertTrue(session.is_active)

        # Thu hồi session
        session.revoked_at = timezone.now()
        session.revoke_reason = "USER_LOGOUT"
        session.save()
        self.assertFalse(session.is_active)

    def test_login_attempt_record(self):
        """Xác minh tạo bản ghi LoginAttempt với HMAC key và UUID request ID."""
        attempt = LoginAttempt.objects.create(
            username_key="a" * 64,
            ip_key="b" * 64,
            success=False,
            failure_code="INVALID_CREDENTIALS"
        )
        self.assertIsInstance(attempt.id, uuid.UUID)
        self.assertIsInstance(attempt.request_id, uuid.UUID)
        self.assertFalse(attempt.success)
        self.assertEqual(attempt.failure_code, "INVALID_CREDENTIALS")
```

---

## 5. Bảng tổng hợp Hàm, Lớp, Biến và Fields

| Thành phần | Thuộc file | Kiểu / Loại | Ý nghĩa & Dữ liệu vào / ra |
|---|---|---|---|
| `User` | `accounts/models/user.py` | Model Class | Kế thừa `AbstractUser`, PK `UUIDField(default=uuid.uuid4)`. |
| `User.status` | `accounts/models/user.py` | Field (`CharField`) | Giá trị: `ACTIVE`, `LOCKED`, `ARCHIVED`. Ràng buộc `ck_users_status_valid`. |
| `User.clean()` | `accounts/models/user.py` | Method | Chuẩn hóa `username.strip().lower()`, đồng bộ `is_active = (status == 'ACTIVE')`. |
| `User.save()` | `accounts/models/user.py` | Method | Tự động thêm `'is_active'` vào `update_fields` nếu có `'status'`. |
| `User.get_full_name()` | `accounts/models/user.py` | Method | Trả về `self.full_name` (thay thế hàm mặc định của AbstractUser). |
| `CustomUserManager` | `accounts/managers.py` | Manager Class | `create_user` & `create_superuser`; kiểm tra fullname, canonical username, validate mật khẩu và `full_clean()`. |
| `AuthSession` | `accounts/models/auth_session.py` | Model Class | Registry phiên làm việc (sid UUID, user FK, `expires_at`, `revoked_at`). |
| `AuthSession.is_active` | `accounts/models/auth_session.py` | Property (bool) | Trả về `True` nếu phiên chưa bị thu hồi (`revoked_at IS NULL`) và chưa quá hạn. |
| `LoginAttempt` | `accounts/models/login_attempt.py` | Model Class | Lưu HMAC username/IP để đếm lỗi đăng nhập mà không lộ dữ liệu thô. |
| `PasswordLengthValidator` | `accounts/security/password_validators.py` | Validator Class | Kiểm tra mật khẩu thô từ 12 đến 128 ký tự; không trim khoảng trắng. |
| `DatabaseGuardTestRunner` | `core/test_runner.py` | Test Runner Class | Bắt buộc test DB là `iot_cabinet_django_test`, khác DB chính và cấm DB cũ. |

---

## 6. Lệnh PowerShell để bạn tự chạy kiểm tra

> [!IMPORTANT]
> Toàn bộ các lệnh dưới đây được chạy từ thư mục gốc của repository: `c:\Users\taday\OneDrive\Documents\PTIT\IOT\IoT_cabinet`.

### Bước 1: Tạo file cấu hình môi trường `.env` cho backend
Tạo file `.env` từ mẫu `backend/.env.example`:
```powershell
Copy-Item -Path .\backend\.env.example -Destination .\backend\.env
```

### Bước 2: Chuẩn bị Cơ sở dữ liệu phát triển PostgreSQL
> [!NOTE]
> **Chỉ tạo duy nhất database phát triển `iot_cabinet_django`.**  
> KHÔNG tạo trước database test `iot_cabinet_django_test`. Django Test Runner sẽ tự động tạo database test khi bắt đầu kiểm thử và xóa đi khi kết thúc mà không bị hỏi xác nhận xóa.

Khởi động PostgreSQL từ Compose:
```powershell
docker compose -f .\deployment\docker-compose.yml up -d postgres
```
Tạo database phát triển:
```powershell
docker exec -it iot_cabinet_postgres psql -U postgres -c "CREATE DATABASE iot_cabinet_django;"
```

### Bước 3: Tạo migration ban đầu cho app `accounts`
Chạy lệnh tạo migration từ thư mục gốc:
```powershell
.\.venv\Scripts\python.exe backend\manage.py makemigrations accounts
```
**Kết quả mong đợi:**
```text
Migrations for 'accounts':
  backend\apps\accounts\migrations\0001_initial.py
    + Create model User
    + Create model LoginAttempt
    + Create model AuthSession
    + Create constraint ck_users_status_valid on model user
    + Create constraint ck_users_failed_login_count_gte_0 on model user
    + Create constraint ck_auth_sessions_expires_after_created on model authsession
```

### Bước 4: Áp dụng migration vào PostgreSQL
```powershell
.\.venv\Scripts\python.exe backend\manage.py migrate
```
**Kết quả mong đợi:**
```text
Operations to perform:
  Apply all migrations: accounts, admin, auth, contenttypes, sessions
Running migrations:
  Applying contenttypes.0001_initial... OK
  ...
  Applying accounts.0001_initial... OK
  Applying admin.0001_initial... OK
  Applying sessions.0001_initial... OK
```

### Bước 5: Chạy bộ kiểm thử (15 Tests)
```powershell
.\.venv\Scripts\python.exe backend\manage.py test apps.core.tests apps.accounts.tests --settings=config.test_settings
```
**Kết quả mong đợi:**
```text
Creating test database for alias 'default' ('iot_cabinet_django_test')...
Found 15 test(s).
System check identified no issues (0 silenced).
...............
----------------------------------------------------------------------
Ran 15 tests in 0.650s

OK
Destroying test database for alias 'default' ('iot_cabinet_django_test')...
```

---

## 7. Acceptance Checklist & Câu hỏi tự kiểm tra

### Checklist nghiệm thu:
- [ ] File `backend/.env` đã được tạo và chứa đúng `DB_NAME=iot_cabinet_django`.
- [ ] File `backend/apps/accounts/migrations/0001_initial.py` đã được tạo và chứa `CreateModel(name='User')` với khóa chính UUID.
- [ ] Lệnh `migrate` thành công vào database PostgreSQL `iot_cabinet_django`.
- [ ] Toàn bộ **15 test cases** đều vượt qua (`Ran 15 tests ... OK`).
- [ ] Database của bản Flask cũ (`iot_cabinet`) hoàn toàn không bị thay đổi dữ liệu hay cấu trúc.

---

## 8. Gói gửi Reviewer

Sau khi copy và chạy thử nghiệm thành công, hãy gửi đoạn text sau cho reviewer:

```text
Review: TV3-DJ-01 — Nền Django, Custom User và Database An Toàn (Patch 01 — Bản sửa hoàn thiện)
Baseline: commit fe7b6a2
Đã tạo các file:
- backend/requirements.txt, backend/.env.example, backend/.env
- backend/config/settings.py, backend/config/test_settings.py
- backend/apps/core/ (apps.py, test_runner.py, tests/test_database_guard.py)
- backend/apps/accounts/ (apps.py, managers.py, security/password_validators.py)
- backend/apps/accounts/models/ (user.py, auth_session.py, login_attempt.py, __init__.py)
- backend/apps/accounts/tests/test_models_roles.py
- Migration tự sinh: backend/apps/accounts/migrations/0001_initial.py

Đã giải quyết theo review:
1. Sửa guard DB test: Bắt buộc đúng 'iot_cabinet_django_test', khác DB chính, cấm DB cũ.
2. Sửa đồng bộ is_active khi gọi save(update_fields=['status']) và kiểm tra refresh_from_db().
3. Kiểm tra họ tên, canonical username, validate_password() và full_clean() trong create_user().
4. Ghi đè get_full_name() và get_short_name() trả về full_name.
5. Loại bỏ index trùng lặp (username unique, session_key db_index), đổi tên PasswordLengthValidator.
6. Hướng dẫn chỉ tạo DB dev; bộ test thực tế gồm 15 tests.

Tôi tự chạy:
1. python backend/manage.py makemigrations accounts -> Sinh 0001_initial.py
2. python backend/manage.py migrate -> Áp dụng thành công vào iot_cabinet_django
3. python backend/manage.py test apps.core.tests apps.accounts.tests --settings=config.test_settings -> OK (15 tests)

Nhờ bạn đọc và review; không tự sửa source code.
```

---

> [!CAUTION]
> **DỪNG TẠI PHẦN TV3-DJ-01 (Bản sửa hoàn thiện).**  
> Chưa sang phần tiếp (TV3-DJ-02 — Hạ tầng lỗi và AuditLog).  
> Chờ bạn copy các file trên, chạy kiểm tra thực tế và gửi kết quả/review.
