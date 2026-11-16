# IoT Cabinet

Hệ thống giám sát nhiệt độ, độ ẩm, cảnh báo và điều khiển thông gió cho tủ thiết bị. Giao diện hỗ trợ quản lý tài khoản, phân quyền theo tủ, cấu hình thiết bị và kết nối MQTT.

## Công nghệ và cấu trúc

- Backend: Django + Django REST Framework, PostgreSQL, xác thực bằng session và CSRF.
- Frontend: React + Vite.
- Giao tiếp thiết bị: Mosquitto MQTT và worker Django; ESP32 chạy firmware riêng.

```text
backend/       Django: manage.py, config/, apps/, migrations và test
frontend/      React và cấu hình Vite
firmware/      Chương trình cho ESP32
deployment/    Docker Compose và cấu hình Mosquitto
database/      Tài liệu dữ liệu và SQL lịch sử
docs/          Tài liệu dự án
back/          Backend Flask cũ, không dùng để chạy bản hiện tại
```

Database Django được tạo bằng migration trong `backend/apps/`. Không import file SQL 27 bảng cũ để khởi tạo database Django.

## 1. Chuẩn bị môi trường

Hướng dẫn dưới đây dùng **Windows PowerShell**, chạy từ thư mục gốc `IoT_cabinet`.

Cần Python tương thích các phiên bản đã pin trong `backend/requirements.txt`, Node.js tương thích `frontend/package.json`, PostgreSQL và Mosquitto. Môi trường đã kiểm tra trong quá trình phát triển: Python 3.14, Django 5.2 và PostgreSQL 16. Dùng `npm ci` để cài đúng lockfile.

```powershell
python --version
node --version
npm.cmd --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm.cmd --prefix frontend ci
```

Các lệnh dùng trực tiếp Python trong `.venv`, không cần chạy `Activate.ps1`. Dùng `npm.cmd` để tránh lỗi PowerShell chặn `npm.ps1`.

## 2. Tạo cấu hình backend

Chỉ sao chép mẫu khi **chưa có** `backend/.env`; không ghi đè cấu hình đang dùng:

```powershell
if (!(Test-Path backend/.env)) {
    Copy-Item backend/.env.example backend/.env
}
```

Điền các giá trị phù hợp trong `backend/.env`:

```dotenv
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
DJANGO_SECRET_KEY=<khóa riêng của môi trường>
AUTH_LOG_HMAC_KEY=<khóa riêng của môi trường>

DB_NAME=iot_cabinet_django
DB_USER=postgres
DB_PASSWORD=<mật khẩu PostgreSQL>
DB_HOST=127.0.0.1
DB_PORT=5432
TEST_DB_NAME=iot_cabinet_django_test

COOKIE_SECURE=false
MQTT_BROKER_HOST=127.0.0.1
MQTT_BROKER_PORT=1883
MQTT_USERNAME=<tài khoản kết nối của backend và worker>
MQTT_PASSWORD=<mật khẩu tài khoản worker>
MQTT_ADMIN_USERNAME=<tài khoản quản trị broker>
MQTT_ADMIN_PASSWORD=<mật khẩu quản trị broker>
MQTT_CA_FILE=
MQTT_CONFIG_ENCRYPTION_KEY=<khóa Fernet tạo một lần>
```

Các chuỗi trong `<...>` là chỗ cần thay, không phải giá trị sử dụng được. Không commit `.env` thật.

Tạo khóa Django/HMAC bằng lệnh sau, chạy hai lần để lấy hai khóa khác nhau:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
```

Tạo khóa mã hóa mật khẩu MQTT:

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Giữ nguyên khóa Fernet sau khi đã lưu mật khẩu broker trên web. Đổi hoặc mất khóa sẽ làm các mật khẩu đã mã hóa không giải mã được. Các khóa môi trường được thiết lập một lần khi cài đặt; thông tin kết nối MQTT thường ngày có thể sửa trên trang Cấu hình kết nối.

## 3. Khởi động PostgreSQL và chuẩn bị database

Chọn **một** cách, không chạy hai PostgreSQL cùng cổng 5432.

**Đã cài PostgreSQL trên Windows:** bật dịch vụ PostgreSQL. Dùng pgAdmin hoặc psql để tạo database `iot_cabinet_django`, rồi điền thông tin đăng nhập vào `.env`.

```sql
CREATE DATABASE iot_cabinet_django;
```

**Dùng Docker Desktop:** bật Docker Desktop rồi chạy:

```powershell
docker compose -f deployment/docker-compose.yml up -d postgres
docker compose -f deployment/docker-compose.yml ps
```

Compose mặc định tạo `iot_cabinet_django`, user `postgres`, password `postgres123` khi volume mới được khởi tạo. Nếu volume đã tồn tại, đổi biến trong Compose không tự đổi database/mật khẩu cũ. Không xóa volume để sửa lỗi kết nối.

Sau khi database sẵn sàng:

```powershell
.\.venv\Scripts\python.exe backend\manage.py migrate
.\.venv\Scripts\python.exe backend\manage.py seed_roles
.\.venv\Scripts\python.exe backend\manage.py bootstrap_admin --username admin
```

Nhập mật khẩu khi được hỏi (12–128 ký tự). Không có mật khẩu admin mặc định. Nếu tài khoản đã tồn tại, bootstrap không ghi đè mật khẩu. Lần đăng nhập đầu cần đổi mật khẩu.

## 4. Khởi động Mosquitto

Broker là dịch vụ nhận/gửi dữ liệu MQTT; **worker không thay thế broker**. Chọn một cách, tránh hai dịch vụ cùng chiếm cổng 1883.

**Mosquitto cài trên Windows:** kiểm tra dịch vụ:

```powershell
Get-Service mosquitto
```

Nếu chưa chạy, mở PowerShell với quyền Administrator và chạy:

```powershell
Start-Service mosquitto
```

Bản cài mặc định có thể chỉ cho kết nối local và chưa có tài khoản/plugin quản trị. Tài khoản `django_worker` trong `.env` không được Mosquitto tự tạo. Để quản lý tài khoản từng tủ trên web, broker phải được cấu hình plugin **Dynamic Security**, tài khoản quản trị và tài khoản worker có quyền topic phù hợp. Không dùng file `deployment/mosquitto/config/mosquitto.conf` nguyên trạng cho Windows: file đó dùng đường dẫn plugin Linux bên trong Docker.

**Dùng Docker:** repo có cấu hình broker/plugin và dữ liệu demo sẵn:

```powershell
docker compose -f deployment/docker-compose.yml up -d mosquitto
docker compose -f deployment/docker-compose.yml logs --tail 60 mosquitto
```

Dùng credential tương ứng với cấu hình broker của nhóm, điền vào `.env` hoặc trang cấu hình MQTT. Không giả định mật khẩu mặc định và không ghi đè `dynamic-security.json` đang sử dụng để thử lại. Cấu hình/credential demo không dùng cho môi trường triển khai thật.

Với web/backend chạy trên cùng máy broker, host là `127.0.0.1`, port thường `1883`. ESP32 trên mạng LAN phải dùng **IPv4 LAN của máy broker**, không dùng `127.0.0.1`; máy broker phải cho phép kết nối LAN và firewall tương ứng. Xem IP bằng `ipconfig`.

Port 1883 trong cấu hình hiện tại không phải TLS. Chỉ bật TLS trên web khi broker đã có listener TLS và chứng chỉ phù hợp; bật checkbox không tự cấu hình dịch vụ Mosquitto.

## 5. Chạy ứng dụng hằng ngày

Đảm bảo PostgreSQL và broker đang chạy. Sau khi pull code có migration mới, chạy lại `migrate`; có thay đổi quyền thì chạy lại `seed_roles`.

Mở **3 terminal** tại thư mục gốc:

**Terminal 1 — API Django:**

```powershell
.\.venv\Scripts\python.exe backend\manage.py runserver
```

**Terminal 2 — giao diện web:**

```powershell
npm.cmd --prefix frontend run dev
```

**Terminal 3 — nhận số đo, ACK và gửi lệnh MQTT:**

```powershell
.\.venv\Scripts\python.exe backend\manage.py run_mqtt_worker
```

Mở **http://localhost:5173/** (hoặc địa chỉ Vite in ra nếu cổng đang bận). Đăng nhập bằng tài khoản đã tạo. Django chạy mặc định tại `http://127.0.0.1:8000`; Vite chuyển tiếp `/api`, `/admin` và `/static` sang Django.

- Web chính: giám sát, điều khiển, tài khoản, phân quyền và cấu hình MQTT.
- `/admin/`: Django Admin, công cụ quản trị phụ cho người có quyền ADMIN.
- Khi chưa bật worker hoặc chưa có ESP32 gửi dữ liệu, web vẫn mở được nhưng chưa có số đo mới/ACK. Không coi mở được giao diện là đã kết nối phần cứng.
- Dừng từng tiến trình bằng `Ctrl+C`. Sau khi sửa `.env`, khởi động lại backend và worker. Cấu hình MQTT lưu trên web được worker nhận trong vòng lặp runtime.
- Đây là cách chạy phát triển/demo local; `runserver` và Vite dev server không phải cấu hình triển khai production.

## 6. Kiểm tra và xử lý lỗi thường gặp

```powershell
.\.venv\Scripts\python.exe backend\manage.py check
.\.venv\Scripts\python.exe backend\manage.py makemigrations --check --dry-run
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run build
```

Chạy test backend bằng PostgreSQL và cấu hình test riêng:

```powershell
.\.venv\Scripts\python.exe backend\manage.py test apps --settings=config.test_settings --noinput
```

Test có thể tạo/xóa lại `iot_cabinet_django_test`; tài khoản DB cần quyền tạo database test. Không trỏ test vào DB chính, không tự tạo schema bằng SQL cũ để vượt lỗi migration.

| Triệu chứng | Cách xử lý |
| --- | --- |
| PowerShell chặn npm.ps1 | Dùng `npm.cmd` như các lệnh trên |
| Thiếu permission khi seed_roles | Chạy `migrate`, rồi `seed_roles` |
| PostgreSQL báo sai mật khẩu/không kết nối | Kiểm tra dịch vụ, DB_NAME, DB_USER, DB_PASSWORD và cổng trong `.env` |
| API MQTT settings lỗi 500 sau pull | Chạy migration mới, xem traceback backend; kiểm tra khóa Fernet nếu đã lưu credential |
| TCP 1883 bị từ chối | Broker chưa chạy, sai địa chỉ/cổng hoặc không có listener; worker chạy không giải quyết được lỗi này |
| Broker từ chối đăng nhập | Kiểm tra tài khoản đã được tạo thật trên đúng broker và credential tương ứng |
| Không quản lý được tài khoản MQTT | Kiểm tra plugin Dynamic Security và quyền tài khoản quản trị broker |
| Thiết bị không có số đo mới | Kiểm tra worker đã kết nối, IP/credential/topic của ESP32 và quyền truy cập tủ |
| Đã gửi lệnh nhưng chưa thực hiện | Kiểm tra ACK của thiết bị; broker nhận lệnh không đồng nghĩa thiết bị đã thực thi |

Quên mật khẩu admin:

```powershell
.\.venv\Scripts\python.exe backend\manage.py changepassword admin
```

Không chạy lệnh seed dữ liệu mẫu trên database đang dùng mà chưa đọc tác dụng của command. Dữ liệu demo không bắt buộc để khởi động hệ thống.
