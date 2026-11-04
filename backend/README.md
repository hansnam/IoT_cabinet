# Backend IoT Network Cabinet Monitoring & Ventilation Control (Django Version)

Hệ thống Backend tinh gọn cho đồ án Giám sát nhiệt độ, độ ẩm và điều khiển thông gió tủ thiết bị mạng.

---

## 1. Cấu Trúc Thư Mục

```text
backend/
├── manage.py                   # Lệnh quản trị Django
├── db.sqlite3                  # Cơ sở dữ liệu SQLite3 (mặc định cực nhẹ, không cần cài PostgreSQL)
├── requirements.txt            # Thư viện phụ thuộc
├── iot_cabinet/                # Cấu hình dự án Django (Settings, URLs)
│   ├── settings.py
│   └── urls.py
└── cabinet/                    # Ứng dụng IoT Cabinet
    ├── models.py               # 4 Bảng CSDL cốt lõi (Device, Telemetry, Alert, CommandLog)
    ├── admin.py                # Giao diện Quản trị Django Admin
    ├── serializers.py          # Bộ tuần tự hóa dữ liệu REST API
    ├── views.py                # REST API ViewSets
    ├── urls.py                 # Định tuyến API
    ├── mqtt_client.py          # Client gửi lệnh MQTT xuống thiết bị
    ├── tests.py                # Bộ kiểm thử tự động
    └── management/commands/
        ├── run_mqtt_worker.py  # Tiến trình nền nhận tin cảm biến từ ESP32
        └── seed_data.py        # Nạp dữ liệu mẫu ban đầu
```

---

## 2. Hướng Dẫn Chạy Nhanh (Quick Start)

Mở terminal tại thư mục `backend/`:

### Bước 1: Kích hoạt môi trường ảo
```powershell
.\venv\Scripts\activate
```

### Bước 2: Nạp dữ liệu mẫu (Tạo Admin & Tủ cabinet-001)
```powershell
python manage.py seed_data
```
- **Tài khoản Admin:** `admin` / Mật khẩu: `admin123`

### Bước 3: Chạy Web Server API & Django Admin
```powershell
python manage.py runserver 127.0.0.1:5000
```
- **Trang Quản trị Django Admin:** http://127.0.0.1:5000/admin/
- **Danh sách Thiết bị:** http://127.0.0.1:5000/api/devices/
- **Lịch sử Cảm biến:** http://127.0.0.1:5000/api/telemetry/
- **Danh sách Cảnh báo:** http://127.0.0.1:5000/api/alerts/

### Bước 4: Chạy tiến trình nền MQTT Worker (nhận tin từ ESP32)
Mở một cửa sổ terminal khác:
```powershell
python manage.py run_mqtt_worker
```

---

## 3. Chạy Kiểm Thử Tự Động
```powershell
python manage.py test
```
Kỳ vọng: `OK` (5/5 tests passed).
