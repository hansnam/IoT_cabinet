"""
Seed Data Script - IoT Network Cabinet Monitoring & Ventilation Control
Đặc tả V1.3: Mục 5, 6.2, 6.3, 6.5, 6.7, 8.5
========================================================================

Khởi tạo dữ liệu nền ban đầu (Idempotent - an toàn khi chạy lại nhiều lần):
1. 3 Vai trò hệ thống: ADMIN, OPERATOR, VIEWER.
2. ~22 Quyền hạn hệ thống chuẩn (GLOBAL, DEVICE, SELF).
3. Gán quyền cho từng vai trò (Role-Permission).
4. 3 Tài khoản demo: admin_demo, operator_demo, viewer_demo (băm Argon2id).
5. Vị trí lắp đặt: rack-lab, lab-cabinets.
6. Thiết bị mẫu:
   - cabinet-001 (CONTROLLER, REAL) + DeviceCredential + DeviceConfig v1 (hash fixture 3520...1cb).
   - sim-unassigned (CONTROLLER, SIMULATED) để test kiểm soát phạm vi (scope isolation).
7. Phân quyền truy cập thiết bị (device_access):
   - operator_demo -> cabinet-001: OPERATE
   - viewer_demo -> cabinet-001: VIEW
   - sim-unassigned không grant cho operator/viewer (test 404/deny).
8. 4 Quy tắc cảnh báo mặc định cho cabinet-001:
   - HIGH_TEMPERATURE, HIGH_HUMIDITY, OFFLINE, SENSOR_FAULT.
"""

import sys
import os
import json
import hashlib
from pathlib import Path

# Đảm bảo import được module từ thư mục gốc
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Đảm bảo in tiếng Việt chuẩn trên Windows console
try:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

from backend.app import create_app
from backend.app.extensions import db, get_password_hasher
from backend.app.models import (
    User, Role, Permission, RolePermission, UserRole,
    Location, Device, DeviceAccess, DeviceCredential, DeviceConfig, DeviceShadow,
    AlertRule,
)

def run_seed():
    app = create_app()
    with app.app_context():
        print("=" * 70)
        print("BẮT ĐẦU NẠP SEED DATA NỀN TẢNG (P0 CORE)")
        print("=" * 70)

        # ----------------------------------------------------------------------
        # 1. SEED ROLES (Mục 5.1 & 6.2)
        # ----------------------------------------------------------------------
        roles_data = [
            ("ADMIN", "Quản trị viên hệ thống", "Toàn quyền quản trị hệ thống, người dùng và thiết bị"),
            ("OPERATOR", "Người vận hành", "Theo dõi và điều khiển thiết bị trong phạm vi được cấp quyền"),
            ("VIEWER", "Người theo dõi", "Chỉ có quyền xem thông tin và dữ liệu giám sát"),
        ]
        roles_map = {}
        for code, name, desc in roles_data:
            role = Role.query.filter_by(code=code).first()
            if not role:
                role = Role(code=code, name=name, description=desc, is_system=True)
                db.session.add(role)
                db.session.flush()
                print(f" [+] Tạo Role: {code}")
            else:
                print(f" [.] Role đã tồn tại: {code}")
            roles_map[code] = role

        # ----------------------------------------------------------------------
        # 2. SEED PERMISSIONS (Mục 5.1 & 6.2)
        # ----------------------------------------------------------------------
        permissions_data = [
            # Device Scope Permissions (VIEW / OPERATE)
            ("device.read", "DEVICE", "Xem thông tin cơ bản của thiết bị"),
            ("telemetry.read", "DEVICE", "Xem số đo cảm biến và dữ liệu telemetry"),
            ("telemetry.export", "DEVICE", "Xuất dữ liệu lịch sử telemetry ra CSV"),
            ("command.read", "DEVICE", "Xem lịch sử lệnh điều khiển và trạng thái ACK"),
            ("alert.read", "DEVICE", "Xem danh sách quy tắc và sự cố cảnh báo"),
            ("activity.read", "DEVICE", "Xem dòng thời gian sự kiện của thiết bị"),
            ("device.control", "DEVICE", "Gửi lệnh điều khiển bật tắt quạt và chuyển chế độ"),
            ("device.configure", "DEVICE", "Thay đổi ngưỡng nhiệt/ẩm và cấu hình hoạt động"),
            ("alert.acknowledge", "DEVICE", "Xác nhận đã tiếp nhận sự cố cảnh báo"),
            ("alert.resolve", "DEVICE", "Đóng sự cố cảnh báo khi điều kiện đã an toàn"),

            # Global Administrative Permissions
            ("user.read", "GLOBAL", "Xem danh sách tài khoản người dùng"),
            ("user.create", "GLOBAL", "Tạo tài khoản người dùng mới"),
            ("user.update", "GLOBAL", "Cập nhật thông tin tài khoản người dùng"),
            ("user.lock", "GLOBAL", "Khóa hoặc mở khóa tài khoản người dùng"),
            ("user.role.assign", "GLOBAL", "Gán hoặc thay đổi vai trò của người dùng"),
            ("user.password.reset", "GLOBAL", "Đặt lại mật khẩu tạm thời cho người dùng"),
            ("device.register", "GLOBAL", "Đăng ký thiết bị mới vào hệ thống"),
            ("device.provision", "GLOBAL", "Cấp thông tin xác thực ban đầu cho thiết bị"),
            ("device.activate", "GLOBAL", "Kích hoạt thiết bị sau khi cấu hình"),
            ("device.access.manage", "GLOBAL", "Phân quyền truy cập thiết bị cho người dùng"),
            ("device.lifecycle.manage", "GLOBAL", "Quản lý vòng đời (bảo trì, khóa, giải phóng thiết bị)"),
            ("device.credential.rotate", "GLOBAL", "Xoay vòng thông tin xác thực MQTT của thiết bị"),
            ("location.manage", "GLOBAL", "Quản lý danh mục vị trí tủ thiết bị"),
            ("device_group.manage", "GLOBAL", "Quản lý danh mục nhóm thiết bị"),
            ("audit.read", "GLOBAL", "Xem nhật ký kiểm toán bảo mật của hệ thống"),
            ("user_activity.read", "GLOBAL", "Xem toàn bộ nhật ký làm việc của mọi người dùng"),
            ("session.admin.revoke", "GLOBAL", "Thu hồi phiên đăng nhập của người dùng khác"),

            # Self Permissions
            ("user_activity.self.read", "SELF", "Xem nhật ký làm việc của chính bản thân"),
            ("profile.update", "SELF", "Cập nhật thông tin cá nhân của bản thân"),
            ("password.change", "SELF", "Tự đổi mật khẩu của bản thân"),
            ("session.self.manage", "SELF", "Xem và thu hồi các phiên đăng nhập của bản thân"),
        ]

        perms_map = {}
        for code, scope, desc in permissions_data:
            perm = Permission.query.filter_by(code=code).first()
            if not perm:
                perm = Permission(code=code, scope_type=scope, description=desc)
                db.session.add(perm)
                db.session.flush()
            perms_map[code] = perm
        print(f" [+] Đã sẵn sàng {len(perms_map)} quyền hạn hệ thống (Permissions)")

        # ----------------------------------------------------------------------
        # 3. GÁN ROLE - PERMISSIONS (Mục 5.1)
        # ----------------------------------------------------------------------
        operator_perms = [
            "device.read", "telemetry.read", "telemetry.export", "command.read", 
            "alert.read", "activity.read", "device.control", "device.configure", 
            "alert.acknowledge", "alert.resolve", "user_activity.self.read", 
            "profile.update", "password.change", "session.self.manage"
        ]

        viewer_perms = [
            "device.read", "telemetry.read", "telemetry.export", "command.read", 
            "alert.read", "activity.read", "user_activity.self.read", 
            "profile.update", "password.change", "session.self.manage"
        ]

        # ADMIN nhận tất cả quyền
        for perm in perms_map.values():
            if not RolePermission.query.filter_by(role_id=roles_map["ADMIN"].id, permission_id=perm.id).first():
                db.session.add(RolePermission(role_id=roles_map["ADMIN"].id, permission_id=perm.id))

        # OPERATOR
        for code in operator_perms:
            if code in perms_map:
                perm = perms_map[code]
                if not RolePermission.query.filter_by(role_id=roles_map["OPERATOR"].id, permission_id=perm.id).first():
                    db.session.add(RolePermission(role_id=roles_map["OPERATOR"].id, permission_id=perm.id))

        # VIEWER
        for code in viewer_perms:
            if code in perms_map:
                perm = perms_map[code]
                if not RolePermission.query.filter_by(role_id=roles_map["VIEWER"].id, permission_id=perm.id).first():
                    db.session.add(RolePermission(role_id=roles_map["VIEWER"].id, permission_id=perm.id))

        db.session.flush()
        print(" [+] Gán quyền hoàn tất: ADMIN (Full), OPERATOR (Operate + Read), VIEWER (Read-only)")

        # ----------------------------------------------------------------------
        # 4. SEED TÀI KHOẢN DEMO (Mục 5.2, 6.2, 6.7)
        # ----------------------------------------------------------------------
        hasher = get_password_hasher(
            memory_cost=app.config["ARGON2_MEMORY_COST_KIB"],
            time_cost=app.config["ARGON2_TIME_COST"],
            parallelism=app.config["ARGON2_PARALLELISM"]
        )

        users_data = [
            (
                os.getenv("ADMIN_BOOTSTRAP_USERNAME", "admin_demo"),
                os.getenv("ADMIN_BOOTSTRAP_PASSWORD", "Admin@Cabinet2026!"),
                os.getenv("ADMIN_BOOTSTRAP_FULLNAME", "System Administrator"),
                "admin@cabinet.local",
                "ADMIN"
            ),
            (
                os.getenv("OPERATOR_BOOTSTRAP_USERNAME", "operator_demo"),
                os.getenv("OPERATOR_BOOTSTRAP_PASSWORD", "Operator@Cabinet2026!"),
                os.getenv("OPERATOR_BOOTSTRAP_FULLNAME", "Network Cabinet Operator"),
                "operator@cabinet.local",
                "OPERATOR"
            ),
            (
                os.getenv("VIEWER_BOOTSTRAP_USERNAME", "viewer_demo"),
                os.getenv("VIEWER_BOOTSTRAP_PASSWORD", "Viewer@Cabinet2026!"),
                os.getenv("VIEWER_BOOTSTRAP_FULLNAME", "Auditor Viewer"),
                "viewer@cabinet.local",
                "VIEWER"
            ),
        ]

        users_map = {}
        for username, raw_password, fullname, email, role_code in users_data:
            user = User.query.filter_by(username=username).first()
            if not user:
                pwd_hash = hasher.hash(raw_password)
                user = User(
                    username=username,
                    password_hash=pwd_hash,
                    full_name=fullname,
                    email=email,
                    status="ACTIVE",
                    must_change_password=False,
                    token_version=1
                )
                db.session.add(user)
                db.session.flush()

                # Gán vai trò
                user_role = UserRole(user_id=user.id, role_id=roles_map[role_code].id)
                db.session.add(user_role)
                print(f" [+] Tạo User: {username} ({role_code})")
            else:
                print(f" [.] User đã tồn tại: {username}")
            users_map[username] = user

        # ----------------------------------------------------------------------
        # 5. SEED VỊ TRÍ (Mục 6.3)
        # ----------------------------------------------------------------------
        loc_lab = Location.query.filter_by(code="rack-lab").first()
        if not loc_lab:
            loc_lab = Location(
                code="rack-lab",
                name="Phòng Thí Nghiệm Tủ Mạng",
                description="Khu vực thử nghiệm mô hình tủ mạng Lab IoT"
            )
            db.session.add(loc_lab)
            db.session.flush()
            print(" [+] Tạo Location: rack-lab")

        loc_central = Location.query.filter_by(code="lab-cabinets").first()
        if not loc_central:
            loc_central = Location(
                code="lab-cabinets",
                name="Cụm Tủ Mạng Trung Tâm",
                parent_id=loc_lab.id,
                description="Nhánh con của phòng thí nghiệm"
            )
            db.session.add(loc_central)
            db.session.flush()
            print(" [+] Tạo Location: lab-cabinets")

        # ----------------------------------------------------------------------
        # 6. SEED THIẾT BỊ MẪU: cabinet-001 (REAL) & sim-unassigned (SIMULATED)
        # ----------------------------------------------------------------------
        admin_user = users_map.get("admin_demo")

        # Chuẩn bị Config Snapshot chuẩn v1 cho CONTROLLER (Mục 8.5)
        config_snapshot_v1 = {
            "schema_version": 3,
            "config_version": 1,
            "profile": "CONTROLLER",
            "fan_binding_id": None,
            "fan_on_c": 30,
            "fan_off_c": 28,
            "temp_alert_c": 35,
            "humidity_alert_pct": 80,
            "temp_clear_margin_c": 1,
            "humidity_clear_margin_pct": 3,
            "telemetry_interval_ms": 5000,
            "manual_timeout_s": 60,
            "extra": {}
        }
        canonical_json_v1 = json.dumps(config_snapshot_v1, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        content_hash_v1 = hashlib.sha256(canonical_json_v1.encode("utf-8")).hexdigest()
        assert content_hash_v1 == "3520a5738ade36fecf0a3f72ecf34e9f53fb2b2fe23e0a20245393e6a57a91cb", "Hash fixture v1 không khớp!"

        # 6.1 Thiết bị cabinet-001 (REAL)
        dev_real = Device.query.filter_by(device_id="cabinet-001").first()
        if not dev_real:
            dev_real = Device(
                device_id="cabinet-001",
                name="Tủ Mạng Phòng Lab 001",
                location_id=loc_lab.id,
                owner_user_id=admin_user.id if admin_user else None,
                device_type="CONTROLLER",
                data_source="REAL",
                lifecycle_state="ACTIVE",
                connection_state="UNKNOWN",
                health_state="OK",
                hardware_version="esp32-wroom32-dht22-relay-controller-v1",
                firmware_version="1.0.0",
                capabilities={"thermal": True, "humidity": True, "relay": True, "mode_control": True},
                desired_config_version=1,
                applied_config_version=1,
                broker_desired_enabled=True,
                broker_actual_enabled=True,
                broker_sync_state="SYNCED",
                state_version=1
            )
            db.session.add(dev_real)
            db.session.flush()

            # Config v1
            cfg_real = DeviceConfig(
                device_id="cabinet-001",
                version=1,
                profile="CONTROLLER",
                fan_binding_id=None,
                fan_on_c=30.0,
                fan_off_c=28.0,
                temp_alert_c=35.0,
                humidity_alert_pct=80.0,
                temp_clear_margin_c=1.0,
                humidity_clear_margin_pct=3.0,
                manual_timeout_s=60,
                telemetry_interval_ms=5000,
                schema_version=3,
                extra={},
                canonical_json=canonical_json_v1,
                content_hash=content_hash_v1,
                created_by=admin_user.id if admin_user else None
            )
            db.session.add(cfg_real)

            # Credential
            cred_real = DeviceCredential(
                device_id="cabinet-001",
                credential_version=1,
                mqtt_username="cabinet-001",
                client_id="cabinet-001",
                broker_client_ref="cabinet-001-client",
                status="ACTIVE",
                created_by=admin_user.id if admin_user else None
            )
            db.session.add(cred_real)

            # Shadow
            shadow_real = DeviceShadow(
                device_id="cabinet-001",
                desired={"mode": "AUTO", "config_version": 1},
                reported={"mode": "AUTO", "config_version": 1},
                desired_version=1,
                reported_version=1,
                drift_status="IN_SYNC"
            )
            db.session.add(shadow_real)
            print(" [+] Tạo Device: cabinet-001 (CONTROLLER, REAL, Hash fixture v1)")
        else:
            print(" [.] Device đã tồn tại: cabinet-001")

        # 6.2 Thiết bị sim-unassigned (SIMULATED để test scope / 404 IDOR)
        dev_sim = Device.query.filter_by(device_id="sim-unassigned").first()
        if not dev_sim:
            dev_sim = Device(
                device_id="sim-unassigned",
                name="Tủ Giả Lập Ngoài Phạm Vi",
                location_id=loc_lab.id,
                owner_user_id=admin_user.id if admin_user else None,
                device_type="CONTROLLER",
                data_source="SIMULATED",
                lifecycle_state="ACTIVE",
                connection_state="OFFLINE",
                health_state="OK",
                hardware_version="esp32-simulator-v1",
                firmware_version="1.0.0",
                capabilities={"thermal": True, "humidity": True, "relay": True, "simulated": True},
                desired_config_version=1,
                applied_config_version=1,
                broker_desired_enabled=True,
                broker_actual_enabled=True,
                broker_sync_state="SYNCED",
                state_version=1
            )
            db.session.add(dev_sim)
            db.session.flush()

            cfg_sim = DeviceConfig(
                device_id="sim-unassigned",
                version=1,
                profile="CONTROLLER",
                fan_binding_id=None,
                fan_on_c=30.0,
                fan_off_c=28.0,
                temp_alert_c=35.0,
                humidity_alert_pct=80.0,
                temp_clear_margin_c=1.0,
                humidity_clear_margin_pct=3.0,
                manual_timeout_s=60,
                telemetry_interval_ms=5000,
                schema_version=3,
                extra={},
                canonical_json=canonical_json_v1,
                content_hash=content_hash_v1,
                created_by=admin_user.id if admin_user else None
            )
            db.session.add(cfg_sim)

            cred_sim = DeviceCredential(
                device_id="sim-unassigned",
                credential_version=1,
                mqtt_username="sim-unassigned",
                client_id="sim-unassigned",
                status="ACTIVE",
                created_by=admin_user.id if admin_user else None
            )
            db.session.add(cred_sim)

            shadow_sim = DeviceShadow(
                device_id="sim-unassigned",
                desired={"mode": "AUTO", "config_version": 1},
                reported={"mode": "AUTO", "config_version": 1},
                desired_version=1,
                reported_version=1,
                drift_status="IN_SYNC"
            )
            db.session.add(shadow_sim)
            print(" [+] Tạo Device: sim-unassigned (CONTROLLER, SIMULATED)")
        else:
            print(" [.] Device đã tồn tại: sim-unassigned")

        # ----------------------------------------------------------------------
        # 7. PHÂN QUYỀN TRUY CẬP THIẾT BỊ (device_access - Mục 5.1 & 6.3)
        # ----------------------------------------------------------------------
        op_user = users_map.get("operator_demo")
        vw_user = users_map.get("viewer_demo")

        if op_user and admin_user:
            acc_op = DeviceAccess.query.filter_by(device_id="cabinet-001", user_id=op_user.id).first()
            if not acc_op:
                db.session.add(DeviceAccess(
                    device_id="cabinet-001",
                    user_id=op_user.id,
                    access_level="OPERATE",
                    granted_by=admin_user.id
                ))
                print(" [+] Cấp quyền device_access: operator_demo -> cabinet-001 [OPERATE]")

        if vw_user and admin_user:
            acc_vw = DeviceAccess.query.filter_by(device_id="cabinet-001", user_id=vw_user.id).first()
            if not acc_vw:
                db.session.add(DeviceAccess(
                    device_id="cabinet-001",
                    user_id=vw_user.id,
                    access_level="VIEW",
                    granted_by=admin_user.id
                ))
                print(" [+] Cấp quyền device_access: viewer_demo -> cabinet-001 [VIEW]")

        print(" [!] sim-unassigned không được grant cho operator/viewer để test 404 IDOR scope block.")

        # ----------------------------------------------------------------------
        # 8. CẢNH BÁO MẶC ĐỊNH (alert_rules - Mục 6.5 & 9.2)
        # ----------------------------------------------------------------------
        rules_data = [
            ("HIGH_TEMPERATURE", "CRITICAL", "APPLIED_CONFIG"),
            ("HIGH_HUMIDITY", "WARNING", "APPLIED_CONFIG"),
            ("OFFLINE", "CRITICAL", "CONNECTION"),
            ("SENSOR_FAULT", "CRITICAL", "HEALTH"),
        ]

        for code, severity, source in rules_data:
            rule = AlertRule.query.filter_by(device_id="cabinet-001", code=code).first()
            if not rule:
                db.session.add(AlertRule(
                    device_id="cabinet-001",
                    code=code,
                    severity=severity,
                    condition_source=source,
                    enabled=True,
                    created_by=admin_user.id if admin_user else None
                ))
                print(f" [+] Tạo AlertRule cho cabinet-001: {code} [{severity}]")
            else:
                print(f" [.] AlertRule đã tồn tại: {code}")

        # Commit toàn bộ
        db.session.commit()
        print("=" * 70)
        print("HOÀN TẤT NẠP SEED DATA THÀNH CÔNG VÀ AN TOÀN!")
        print("=" * 70)

if __name__ == "__main__":
    run_seed()
