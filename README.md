# IoT Giám Sát Nhiệt Độ, Độ Ẩm và Điều Khiển Thông Gió Tủ Thiết Bị Mạng

Dự án đồ án kết thúc học phần môn IoT — Đặc tả kỹ thuật phiên bản **V1.3** (Chốt ngày 09/10/2026).

---

## 1. Phân Công Thành Viên

| Vai trò | Phụ trách chính | Nhiệm vụ chính |
|---|---|---|
| **TV1** | Thiết bị & Giao thức | Mạch phần cứng Node 1 (ESP32 + DHT22 + Relay + Quạt DC), Firmware C++ CONTROLLER (AUTO hysteresis, safety fallback, MQTT v3). |
| **TV2** | Backend Thiết bị & Dữ liệu | Khung ứng dụng Flask, CSDL Thiết bị/Telemetry/Commands (14 bảng), `mqtt_worker.py` (ingest telemetry, transactional outbox). |
| **TV3** | Tài khoản & Bảo mật | CSDL IAM (8 bảng), `audit_logs`, `user_activity_logs`, Argon2id, JWT + Refresh Token cookie, Dynamic Security broker controller, UI Auth & Users. |
| **TV4** | Dashboard, Cảnh báo & Tích hợp | Web React + Vite (Dashboard, Lịch sử, Điều khiển, Cảnh báo, Màn hình Nhật ký làm việc), `alert_worker.py`, Docker Compose. |

---

## 2. Cấu Trúc Thư Mục

```text
├── Description/          # Hồ sơ đặc tả và đề bài đồ án
├── backend/              # Mã nguồn Flask Backend API & Workers
│   ├── app/              # Application Factory & Blueprints
│   │   ├── models/       # SQLAlchemy Models (IAM, Devices, Telemetry, Alerts, Logs)
│   │   ├── auth/         # Blueprint xác thực (Login, refresh, logout, session)
│   │   ├── users/        # Blueprint quản lý người dùng & vai trò
│   │   ├── permissions/  # PermissionService & RBAC decorators
│   │   ├── devices/      # Blueprint thiết bị & nhóm thiết bị
│   │   ├── telemetry/    # Blueprint dữ liệu cảm biến & export CSV
│   │   ├── commands/     # Blueprint tạo lệnh & outbox dispatch
│   │   ├── alerts/       # Blueprint quy tắc & sự cố cảnh báo
│   │   ├── audit/        # Blueprint audit logs bảo mật
│   │   ├── user_activity/# Blueprint nhật ký làm việc (user_activity_logs)
│   │   ├── mqtt/         # MQTT client helpers & message handlers
│   │   ├── shadows/      # Device shadow services
│   │   ├── broker_operations/ # Broker Dynamic Security sync controller
│   │   ├── outbox/       # Outbox queue service
│   │   └── extensions_optional/ # Điểm mở rộng P1 (Fans/Fusion) & P2 (AI)
│   ├── workers/          # Tiến trình nền độc lập
│   │   ├── mqtt_worker.py   # Worker ingest telemetry và dispatch lệnh
│   │   └── alert_worker.py  # Worker đánh giá cảnh báo định kỳ
│   ├── migrations/       # Flask-Migrate / Alembic migration scripts
│   └── requirements.txt  # Thư viện Python phụ thuộc
├── frontend/             # Ứng dụng Web (React + Vite + Recharts)
├── firmware/             # Mã nguồn nhúng ESP32 (TV1 phụ trách)
├── hardware/             # Sơ đồ nguyên lý, BOM, pinout mạch (TV1 phụ trách)
├── database/             # ERD, Data Dictionary, tài liệu SQL seed
├── deployment/           # Docker Compose, Mosquitto config, Nginx proxy
├── tests/                # Bộ kiểm thử tự động (Unit, API, Integration, Security)
└── docs/                 # Tài liệu báo cáo, kịch bản demo
```

---

## 3. Hướng Dẫn Chạy Môi Trường Cục Bộ (Pha 0)

### 3.1. Khởi chạy Database & MQTT Broker qua Docker
```bash
cd deployment
docker compose up -d
```
Dịch vụ khởi động:
- **PostgreSQL 16**: Port `5432` (Database: `iot_cabinet`, User: `postgres`)
- **Eclipse Mosquitto**: Port `1883` (MQTT dev), Port `8883` (MQTTS)

### 3.2. Cài đặt và chạy Backend (Python 3.11+)
```bash
cd backend
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
cp ../.env.example .env

# Chạy database migration
flask db upgrade

# Khởi chạy API Server
flask run --port 5000

# Khởi chạy MQTT Worker (ở một terminal riêng)
python workers/mqtt_worker.py

# Khởi chạy Alert Worker (ở một terminal riêng)
python workers/alert_worker.py
```
