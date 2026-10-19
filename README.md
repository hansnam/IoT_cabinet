# Hệ Thống IoT Giám Sát Nhiệt Độ, Độ Ẩm và Điều Khiển Thông Gió Tủ Thiết Bị Mạng

Dự án phát triển hệ thống nhúng và phần mềm quản lý, giám sát môi trường và tự động điều khiển quạt thông gió trong tủ mạng viễn thông / máy chủ (P0 Scope).

---

## 1. Cấu Trúc Thư Mục Dự Án

```text
├── Description/          # Hồ sơ đặc tả và đề bài đồ án (V1.3 P0/P1/P2)
├── backend/              # Mã nguồn Flask Backend API & Workers
│   ├── app/              # Application Factory, Security & Blueprints
│   │   ├── security/     # [TV3] Module an ninh: Catalog lỗi, Envelope, Context, Validation
│   │   ├── models/       # SQLAlchemy Models (27 bảng: IAM, Devices, Telemetry, Alerts, Logs)
│   │   ├── auth/         # Blueprint xác thực (JWT 15m, Session DB, Login/Logout)
│   │   ├── users/        # Blueprint quản lý người dùng & phân quyền RBAC
│   │   ├── devices/      # Blueprint thiết bị & nhóm tủ
│   │   ├── telemetry/    # Blueprint dữ liệu cảm biến & export CSV
│   │   ├── commands/     # Blueprint tạo lệnh & outbox dispatch
│   │   ├── alerts/       # Blueprint sự cố & quy tắc cảnh báo
│   │   ├── audit/        # Blueprint audit logs bảo mật
│   │   └── extensions.py # Khởi tạo extensions: DB, Migrate, JWT, CORS
│   ├── workers/          # Tiến trình nền độc lập
│   │   ├── mqtt_worker.py   # Ingest telemetry từ ESP32 & dispatch lệnh ra broker
│   │   └── alert_worker.py  # Đánh giá cảnh báo định kỳ theo chu kỳ 10 giây
│   ├── migrations/       # Flask-Migrate / Alembic migration scripts (fcb8c798de10)
│   └── requirements.txt  # Thư viện Python phụ thuộc
├── frontend/             # Ứng dụng Web (React + Vite + Recharts)
├── firmware/             # Mã nguồn nhúng ESP32 (Node Controller & Node Fan)
├── hardware/             # Sơ đồ nguyên lý, BOM, pinout mạch phần cứng
├── database/             # ERD, Data Dictionary, tài liệu SQL seed
├── deployment/           # Docker Compose, Mosquitto config, Nginx proxy
├── tests/                # Bộ kiểm thử tự động (Unit, API, Integration, Security)
│   └── tv3/              # Bộ test nền tảng bảo mật TV3 (test_foundation.py)
└── docs/                 # Tài liệu kỹ thuật, hướng dẫn cài đặt DB local
```

---

## 2. Hướng Dẫn Từng Bước: Khởi Tạo & Khởi Chạy Hệ Thống

Tất cả các lệnh dưới đây được thiết kế chạy từ **thư mục gốc dự án** (`E:\Python\IoT_cabinet`) trên môi trường **Windows PowerShell**.

---

### Bước 1: Kích Hoạt Môi Trường Ảo Python (`.venv`)

Dự án sử dụng môi trường ảo đặt tại thư mục `.venv` ở thư mục gốc.

```powershell
# 1. Kích hoạt môi trường ảo (nếu dùng PowerShell)
.\.venv\Scripts\Activate.ps1

# (Nếu gặp lỗi "Execution Policy", chạy lệnh này một lần để cấp quyền):
# Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# 2. Kiểm tra Python và Pip đang trỏ đúng vào .venv
python --version
# Kỳ vọng: Python 3.11+ (Môi trường cục bộ khuyến nghị 3.13.x)

# 3. Cài đặt các gói phụ thuộc (nếu chưa cài)
pip install -r backend/requirements.txt
```

---

### Bước 2: Thiết Lập File Biến Môi Trường (`.env`)

Tạo file `.env` từ file mẫu `.env.example`:

```powershell
Copy-Item .env.example .env
```

#### 2.1. LỆNH SINH MẬT KHẨU & KHÓA BẢO MẬT TỰ ĐỘNG (Copy & Dán)

Các biến bảo mật (`SECRET_KEY`, `JWT_SECRET_KEY`, `AUTH_LOG_HMAC_KEY`) bắt buộc phải là chuỗi ngẫu nhiên có độ dài **tối thiểu 32 bytes (64 ký tự hex)** và **hoàn toàn khác nhau**.

👉 **Chạy ngay lệnh Python 1-dòng dưới đây trong PowerShell để sinh toàn bộ các khóa & mật khẩu ngẫu nhiên:**

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print('\n' + '='*65); print('--- COPY CÁC DÒNG DƯỚI ĐÂY DÁN THAY THẾ VÀO FILE .env ---'); print('='*65); print(f'SECRET_KEY={secrets.token_hex(32)}'); print(f'JWT_SECRET_KEY={secrets.token_hex(32)}'); print(f'AUTH_LOG_HMAC_KEY={secrets.token_hex(32)}'); print(f'MQTT_WORKER_PASSWORD={secrets.token_urlsafe(16)}'); print(f'MQTT_CONTROLLER_PASSWORD={secrets.token_urlsafe(16)}'); print(f'ADMIN_BOOTSTRAP_PASSWORD={secrets.token_urlsafe(16)}'); print(f'OPERATOR_BOOTSTRAP_PASSWORD={secrets.token_urlsafe(16)}'); print(f'VIEWER_BOOTSTRAP_PASSWORD={secrets.token_urlsafe(16)}'); print('='*65 + '\n')"
```

#### 2.2. Xử Lý Mật Khẩu PostgreSQL Có Ký Tự Đặc Biệt (URL-Encoding)

Trong chuỗi `DATABASE_URL` và `TEST_DATABASE_URL`, nếu mật khẩu PostgreSQL của bạn có chứa các ký tự đặc biệt như `@`, `#`, `:`, `/`, `$`, `%`,... thì phải mã hóa URL.

👉 **Chạy lệnh sau để lấy mật khẩu đã mã hóa URL:**

```powershell
.\.venv\Scripts\python.exe -c "import urllib.parse; p = input('Nhập mật khẩu Postgres của bạn: '); print('\nMật khẩu đã mã hóa URL để ghép vào DATABASE_URL:\n' + urllib.parse.quote_plus(p) + '\n')"
```

#### 2.3. Bảng Kiểm Tra Các Giá Trị Cần Điền Trong `.env`

Mở file `.env` bằng trình biên soạn (VS Code, Notepad,...) và kiểm tra các dòng sau:

| Tên biến                                | Ví dụ / Hướng dẫn điền                                                      |
| --------------------------------------- | --------------------------------------------------------------------------- |
| `FLASK_ENV`                             | `development` (khi code và debug cục bộ)                                    |
| `SECRET_KEY`                            | Dán chuỗi 64 ký tự hex từ lệnh sinh ở Mục 2.1                               |
| `JWT_SECRET_KEY`                        | Dán chuỗi 64 ký tự hex từ lệnh sinh ở Mục 2.1                               |
| `AUTH_LOG_HMAC_KEY`                     | Dán chuỗi 64 ký tự hex từ lệnh sinh ở Mục 2.1                               |
| `DATABASE_URL`                          | `postgresql://postgres:<MAT_KHAU_POSTGRES>@127.0.0.1:5432/iot_cabinet`      |
| `TEST_DATABASE_URL`                     | `postgresql://postgres:<MAT_KHAU_POSTGRES>@127.0.0.1:5432/iot_cabinet_test` |
| `DB_PASSWORD`                           | Mật khẩu Postgres thật của bạn                                              |
| `JWT_ACCESS_TOKEN_EXPIRES_SECONDS`      | `900` (15 phút theo chuẩn P0)                                               |
| `TEST_JWT_ACCESS_TOKEN_EXPIRES_SECONDS` | `5` (thời hạn 5 giây khi chạy test)                                         |
| `AUTH_ALLOWED_ORIGINS`                  | `http://localhost:5173,http://127.0.0.1:5173`                               |
| `CORS_ALLOWED_ORIGINS`                  | `http://localhost:5173,http://127.0.0.1:5173`                               |
| `MQTT_WORKER_PASSWORD`                  | Dán mật khẩu ngẫu nhiên từ lệnh ở Mục 2.1                                   |
| `MQTT_CONTROLLER_PASSWORD`              | Dán mật khẩu ngẫu nhiên từ lệnh ở Mục 2.1                                   |
| `ADMIN_BOOTSTRAP_PASSWORD`              | Dán mật khẩu ngẫu nhiên từ lệnh ở Mục 2.1                                   |
| `OPERATOR_BOOTSTRAP_PASSWORD`           | Dán mật khẩu ngẫu nhiên từ lệnh ở Mục 2.1                                   |
| `VIEWER_BOOTSTRAP_PASSWORD`             | Dán mật khẩu ngẫu nhiên từ lệnh ở Mục 2.1                                   |

> [!WARNING]
> Tuyệt đối không để giá trị placeholder dạng `REPLACE_WITH_...` trong file `.env`. Nếu còn placeholder hoặc các khóa bảo mật bị trùng nhau, hệ thống sẽ chặn không cho ứng dụng khởi động để bảo vệ an toàn.

---

### Bước 3: Khởi Tạo Cơ Sở Dữ Liệu PostgreSQL

Đảm bảo dịch vụ PostgreSQL đang chạy (PostgreSQL 18 hoặc 16 tại cổng mặc định `5432`).

Mở công cụ `psql` để kiểm tra và tạo 2 database độc lập:

```powershell
# Kết nối psql vào máy chủ PostgreSQL cục bộ
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -X -W -h 127.0.0.1 -p 5432 -U postgres -d postgres
```

Khi vào giao diện `postgres=#`, chạy các câu lệnh SQL sau:

```sql
-- 1. Kiểm tra xem 2 database đã tồn tại chưa:
SELECT datname FROM pg_database WHERE datname IN ('iot_cabinet', 'iot_cabinet_test');

-- 2. Tạo database chính và database test (nếu chưa có):
CREATE DATABASE iot_cabinet WITH ENCODING 'UTF8';
CREATE DATABASE iot_cabinet_test WITH ENCODING 'UTF8';

-- 3. Thoát khỏi psql:
\q
```

---

### Bước 4: Chạy Database Migrations (Nạp 27 Bảng Nghiệp Vụ)

Chạy Alembic migration để tạo toàn bộ 27 bảng nghiệp vụ + 1 bảng `alembic_version` trên cả 2 database:

```powershell
# 1. Nạp bảng cho database chính (iot_cabinet)
$env:FLASK_ENV='development'
.\.venv\Scripts\python.exe -m flask --app backend.app:create_app db upgrade --directory backend/migrations

# 2. Nạp bảng cho database kiểm thử (iot_cabinet_test)
$env:FLASK_ENV='testing'
.\.venv\Scripts\python.exe -m flask --app backend.app:create_app db upgrade --directory backend/migrations

# 3. Trả lại biến môi trường về development
$env:FLASK_ENV='development'
```

> [!NOTE]
> **Không chạy lệnh `flask seed` ở bước này.** Dữ liệu tài khoản mẫu đang được chuẩn hóa mật khẩu và liên kết Dynamic Security ở gói TV3-01B.

---

### Bước 5: Chạy Kiểm Thử Tự Động (Pytest)

Trước khi chạy server, hãy chạy bộ kiểm thử để đảm bảo mọi cấu hình và module an ninh đều hợp lệ:

```powershell
# 1. Chạy Unit tests (không cần kết nối Database):
.\.venv\Scripts\python.exe -m pytest tests/tv3/test_foundation.py -v -k "not postgres"

# 2. Chạy toàn bộ kiểm thử (gồm cả Database Guard & Health Readiness trên PostgreSQL):
.\.venv\Scripts\python.exe -m pytest tests/tv3/test_foundation.py -v
```

Kỳ vọng: Tất cả các bài kiểm thử đều hiển thị màu xanh `PASSED`.

---

### Bước 6: Khởi Chạy Backend API Server

Khởi động Flask API Server tại địa chỉ `127.0.0.1:5000`:

```powershell
.\.venv\Scripts\python.exe -m flask --app backend.app:create_app run --host 127.0.0.1 --port 5000
```

#### Kiểm Tra Sức Khỏe Máy Chủ (Health Endpoints)

Mở một cửa sổ PowerShell hoặc Terminal khác để kiểm tra phản hồi:

1. **Kiểm tra Liveness (Máy chủ đang chạy):**

   ```powershell
   curl http://127.0.0.1:5000/health/live
   ```

   _Kết quả kỳ vọng:_

   ```json
   { "status": "live" }
   ```

2. **Kiểm tra Readiness (Kết nối cơ sở dữ liệu sẵn sàng):**
   ```powershell
   curl http://127.0.0.1:5000/health/ready
   ```
   _Kết quả kỳ vọng:_
   ```json
   { "database": "connected", "status": "ready" }
   ```

---

### Bước 7: Khởi Chạy Các Tiến Trình Nền (Workers)

Khi cần thử nghiệm tương tác cảm biến hoặc cảnh báo, mở 2 cửa sổ terminal riêng biệt:

1. **MQTT Worker (Ingest dữ liệu cảm biến & dispatch lệnh):**

   ```powershell
   .\.venv\Scripts\python.exe backend/workers/mqtt_worker.py
   ```

2. **Alert Worker (Đánh giá luật cảnh báo nhiệt độ/độ ẩm định kỳ 10s):**
   ```powershell
   .\.venv\Scripts\python.exe backend/workers/alert_worker.py
   ```

---

## 3. Xử Lý Sự Cố Thường Gặp (Troubleshooting)

- **Lỗi `ValueError: Invalid configuration: ...` khi khởi động:**
  - _Nguyên nhân:_ Một trong các biến `SECRET_KEY`, `JWT_SECRET_KEY`, `AUTH_LOG_HMAC_KEY` bị thiếu, ngắn hơn 32 ký tự, bị trùng nhau, hoặc vẫn giữ chuỗi mẫu `REPLACE_WITH_...`.
  - _Khắc phục:_ Chạy lại lệnh sinh khóa 1-dòng ở **Mục 2.1** và dán đè vào `.env`.
- **Lỗi `Database is unavailable` (HTTP 503 tại `/health/ready`):**
  - _Nguyên nhân:_ Mật khẩu PostgreSQL trong `.env` chưa đúng, service Postgres chưa bật, hoặc chưa chạy migration.
  - _Khắc phục:_ Mở `services.msc` kiểm tra dịch vụ `postgresql-x64-18` đang chạy, thử đăng nhập bằng psql và kiểm tra lại chuỗi `DATABASE_URL`.
- **Lỗi `Negative test fail` hoặc `Target database must be in allowlist`:**
  - _Nguyên nhân:_ Database kiểm thử trong `TEST_DATABASE_URL` bắt buộc phải có tên là `iot_cabinet_test` (không được trùng tên với `iot_cabinet` và không dùng database khác).
