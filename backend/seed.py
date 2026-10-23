"""
Seed Data Script - IoT Network Cabinet Monitoring & Ventilation Control
Đặc tả V1.3: Mục 5, 6.2, 6.3, 6.5, 6.7, 8.5
========================================================================

Khởi tạo dữ liệu nền ban đầu (Idempotent - an toàn khi chạy lại nhiều lần):
1. 3 Vai trò hệ thống: ADMIN, OPERATOR, VIEWER (is_system=True).
2. ~31 Quyền hạn hệ thống chuẩn (GLOBAL, DEVICE, SELF).
3. Gán quyền cho từng vai trò (Role-Permission).
4. 3 Tài khoản quản trị/vận hành từ biến môi trường (không fallback, không placeholder):
   - Kiểm tra định dạng canonical và phát hiện collision với dữ liệu cũ.
   - Nếu user đã tồn tại: kiểm tra status ACTIVE và đúng role tương ứng; không đổi mật khẩu/vai trò.
5. Vị trí lắp đặt: rack-lab, lab-cabinets.
6. Thiết bị mẫu:
   - cabinet-001 (CONTROLLER, REAL): lifecycle REGISTERED, applied=None, broker=PENDING.
   - sim-unassigned (CONTROLLER, SIMULATED): dùng kiểm tra cách ly phạm vi (404/deny).
7. Phân quyền truy cập thiết bị (device_access):
   - Chỉ cấp grant ban đầu khi cabinet-001 vừa được tạo mới.
   - Không tự động cấp lại các grant đã bị admin thu hồi khi seed chạy lại.
8. 4 Quy tắc cảnh báo mặc định cho cabinet-001.
"""

import sys
import os
import json
import re
import hashlib
from pathlib import Path
from flask import current_app, has_app_context # type: ignore
from sqlalchemy import func # type: ignore


# Đảm bảo import được module từ thư mục gốc
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

try:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

from backend.app import create_app
from backend.app.extensions import db, get_password_hasher
from backend.app.security.messages import ErrorMessage
from backend.app.security.config_validation import _PLACEHOLDERS
from backend.app.models import (
    User, Role, Permission, RolePermission, UserRole,
    Location, Device, DeviceAccess, DeviceCredential, DeviceConfig, DeviceShadow,
    AlertRule,
)

USERNAME_REGEX = re.compile(r"^[a-z0-9_.-]{3,64}$")


def _validate_and_normalize_username(raw_username: str) -> str:
    """Validate canonical username contract: [a-z0-9_.-], 3-64 chars, lowercase."""
    if not isinstance(raw_username, str):
        raise ValueError(ErrorMessage.BOOTSTRAP_USERNAME_INVALID.format(username=str(raw_username)))
    trimmed = raw_username.strip()
    if raw_username != trimmed or raw_username != raw_username.lower():
        raise ValueError(ErrorMessage.BOOTSTRAP_USERNAME_INVALID.format(username=raw_username))
    if not USERNAME_REGEX.match(trimmed):
        raise ValueError(ErrorMessage.BOOTSTRAP_USERNAME_INVALID.format(username=raw_username))
    return trimmed


def _validate_bootstrap_password(raw_password: str, role_code: str) -> str:
    """Ensure password is provided, not placeholder, length 12-128. Never echo secret."""
    if not raw_password or not isinstance(raw_password, str):
        raise ValueError(ErrorMessage.BOOTSTRAP_PASSWORD_REQUIRED.format(role=role_code))
    if not (12 <= len(raw_password) <= 128):
        raise ValueError(ErrorMessage.BOOTSTRAP_PASSWORD_INVALID)
    lower = raw_password.lower()
    if any(marker in lower for marker in _PLACEHOLDERS):
        raise ValueError(ErrorMessage.BOOTSTRAP_PASSWORD_INVALID)
    return raw_password


def _seed_all(app, session):
    print("=" * 70)
    print("BẮT ĐẦU NẠP SEED DATA NỀN TẢNG (P0 CORE - V1.3)")
    print("=" * 70)

    try:
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
            role = session.query(Role).filter_by(code=code).first()
            if not role:
                # pyrefly: ignore [unexpected-keyword]
                role = Role(code=code, name=name, description=desc, is_system=True)
                session.add(role)
                session.flush()
                print(f" [+] Tạo Role: {code}")
            else:
                print(f" [.] Role đã tồn tại: {code}")
            roles_map[code] = role

        # ----------------------------------------------------------------------
        # 2. SEED PERMISSIONS (Mục 5.1 & 6.2 - Đủ 31 mã quyền)
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
            perm = session.query(Permission).filter_by(code=code).first()
            if not perm:
                # pyrefly: ignore [unexpected-keyword]
                perm = Permission(code=code, scope_type=scope, description=desc)
                session.add(perm)
                session.flush()
            perms_map[code] = perm

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
            if not session.query(RolePermission).filter_by(role_id=roles_map["ADMIN"].id, permission_id=perm.id).first():
                session.add(RolePermission(role_id=roles_map["ADMIN"].id, permission_id=perm.id))

        # OPERATOR
        for code in operator_perms:
            if code in perms_map:
                perm = perms_map[code]
                if not session.query(RolePermission).filter_by(role_id=roles_map["OPERATOR"].id, permission_id=perm.id).first():
                    session.add(RolePermission(role_id=roles_map["OPERATOR"].id, permission_id=perm.id))

        # VIEWER
        for code in viewer_perms:
            if code in perms_map:
                perm = perms_map[code]
                if not session.query(RolePermission).filter_by(role_id=roles_map["VIEWER"].id, permission_id=perm.id).first():
                    session.add(RolePermission(role_id=roles_map["VIEWER"].id, permission_id=perm.id))

        session.flush()

        # ----------------------------------------------------------------------
        # 4. SEED TÀI KHOẢN TỪ BIẾN MÔI TRƯỜNG (Mục 5.2 & 6.2)
        # ----------------------------------------------------------------------
        hasher = get_password_hasher(
            memory_cost=app.config.get("ARGON2_MEMORY_COST_KIB", 19456),
            time_cost=app.config.get("ARGON2_TIME_COST", 2),
            parallelism=app.config.get("ARGON2_PARALLELISM", 1)
        )

        bootstrap_specs = [
            ("ADMIN", "ADMIN_BOOTSTRAP_USERNAME", "admin_demo", "ADMIN_BOOTSTRAP_PASSWORD", "ADMIN_BOOTSTRAP_FULLNAME", "System Administrator"),
            ("OPERATOR", "OPERATOR_BOOTSTRAP_USERNAME", "operator_demo", "OPERATOR_BOOTSTRAP_PASSWORD", "OPERATOR_BOOTSTRAP_FULLNAME", "Network Cabinet Operator"),
            ("VIEWER", "VIEWER_BOOTSTRAP_USERNAME", "viewer_demo", "VIEWER_BOOTSTRAP_PASSWORD", "VIEWER_BOOTSTRAP_FULLNAME", "Auditor Viewer"),
        ]

        # 4.1 Kiểm tra trùng lặp canonical username giữa các biến môi trường
        seen_usernames = {}
        for role_code, user_env, default_user, _, _, _ in bootstrap_specs:
            raw_user = os.getenv(user_env, default_user)
            canonical = _validate_and_normalize_username(raw_user)
            if canonical in seen_usernames:
                raise ValueError(ErrorMessage.BOOTSTRAP_USERNAME_COLLISION.format(username=canonical))
            seen_usernames[canonical] = role_code

        # 4.2 Kiểm tra dữ liệu hiện có trong DB (phát hiện collision / noncanonical cũ)
        users_by_role = {}
        for role_code, user_env, default_user, pwd_env, fn_env, default_fn in bootstrap_specs:
            canonical_username = _validate_and_normalize_username(os.getenv(user_env, default_user))

            # Kiểm tra xem trong DB có bản ghi nào trùng lower(username) không
            existing_matches = session.query(User).filter(
                func.lower(User.username) == canonical_username.lower()
            ).all()

            if existing_matches:
                for existing in existing_matches:
                    if existing.username != canonical_username:
                        raise ValueError(
                            ErrorMessage.BOOTSTRAP_USERNAME_COLLISION.format(username=existing.username)
                        )
                user = existing_matches[0]
            else:
                user = None

            if not user:
                # Tạo user mới
                raw_pwd = _validate_bootstrap_password(os.getenv(pwd_env), role_code)
                pwd_hash = hasher.hash(raw_pwd)
                fullname = os.getenv(fn_env, default_fn).strip()
                user = User(
                    username=canonical_username,
                    password_hash=pwd_hash,
                    full_name=fullname,
                    email=None,  # P0 không dùng email; để None tránh xung đột unique email khi đổi username
                    status="ACTIVE",
                    must_change_password=False,
                    token_version=1
                )
                session.add(user)
                session.flush()

                # Gán duy nhất 1 vai trò (UQ user_id)
                session.add(UserRole(user_id=user.id, role_id=roles_map[role_code].id))
                session.flush()
                print(f" [+] Tạo User mới: {canonical_username} ({role_code})")
            else:
                # Xác minh user đã tồn tại: phải ACTIVE và đúng role tương ứng
                if user.status != "ACTIVE":
                    raise ValueError(
                        ErrorMessage.BOOTSTRAP_USER_INACTIVE.format(
                            username=user.username, status=user.status
                        )
                    )
                user_roles_list = session.query(Role).join(UserRole).filter(UserRole.user_id == user.id).all()
                if len(user_roles_list) != 1 or user_roles_list[0].code != role_code:
                    actual_role = user_roles_list[0].code if len(user_roles_list) == 1 else ("NONE" if not user_roles_list else "MULTIPLE")
                    raise ValueError(
                        ErrorMessage.BOOTSTRAP_USER_ROLE_MISMATCH.format(
                            username=user.username, expected=role_code, actual=actual_role
                        )
                    )
                print(f" [.] User đã tồn tại hợp lệ: {canonical_username} (Role: {role_code}, Status: ACTIVE. Giữ nguyên mật khẩu và vai trò hiện có)")

            users_by_role[role_code] = user

        admin_user = users_by_role["ADMIN"]
        operator_user = users_by_role["OPERATOR"]
        viewer_user = users_by_role["VIEWER"]

        # ----------------------------------------------------------------------
        # 5. SEED VỊ TRÍ (Mục 6.3)
        # ----------------------------------------------------------------------
        loc_lab = session.query(Location).filter_by(code="rack-lab").first()
        if not loc_lab:
            loc_lab = Location(
                code="rack-lab",
                name="Phòng Thí Nghiệm Tủ Mạng",
                description="Khu vực thử nghiệm mô hình tủ mạng Lab IoT"
            )
            session.add(loc_lab)
            session.flush()
            print(" [+] Tạo Location: rack-lab")

        loc_central = session.query(Location).filter_by(code="lab-cabinets").first()
        if not loc_central:
            loc_central = Location(
                code="lab-cabinets",
                name="Cụm Tủ Mạng Trung Tâm",
                parent_id=loc_lab.id,
                description="Nhánh con của phòng thí nghiệm"
            )
            session.add(loc_central)
            session.flush()
            print(" [+] Tạo Location: lab-cabinets")

        # ----------------------------------------------------------------------
        # 6. SEED THIẾT BỊ MẪU: cabinet-001 (REAL) & sim-unassigned (SIMULATED)
        # ----------------------------------------------------------------------
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

        # 6.1 Thiết bị cabinet-001 (REAL) - Trạng thái ban đầu không fake ACTIVE/SYNCED
        dev_real = session.query(Device).filter_by(device_id="cabinet-001").first()
        is_new_cabinet = False
        if not dev_real:
            is_new_cabinet = True
            dev_real = Device(
                device_id="cabinet-001",
                name="Tủ Mạng Phòng Lab 001",
                location_id=loc_lab.id,
                owner_user_id=admin_user.id,
                device_type="CONTROLLER",
                data_source="REAL",
                lifecycle_state="REGISTERED",
                connection_state="UNKNOWN",
                health_state="UNKNOWN",
                hardware_version="esp32-wroom32-dht22-relay-controller-v1",
                firmware_version="1.0.0",
                capabilities={"thermal": True, "humidity": True, "relay": True, "mode_control": True},
                desired_config_version=1,
                applied_config_version=None,
                broker_desired_enabled=True,
                broker_actual_enabled=None,
                broker_sync_state="PENDING",
                state_version=1
            )
            session.add(dev_real)
            session.flush()

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
                created_by=admin_user.id
            )
            session.add(cfg_real)

            cred_real = DeviceCredential(
                device_id="cabinet-001",
                credential_version=1,
                mqtt_username="cabinet-001",
                client_id="cabinet-001",
                broker_client_ref="cabinet-001-client",
                status="REGISTERED",
                created_by=admin_user.id
            )
            session.add(cred_real)

            shadow_real = DeviceShadow(
                device_id="cabinet-001",
                desired={"mode": "AUTO", "config_version": 1},
                reported={},
                desired_version=1,
                reported_version=0,
                drift_status="PENDING"
            )
            session.add(shadow_real)
            print(" [+] Tạo Device: cabinet-001 (CONTROLLER, REAL, state=REGISTERED)")
        else:
            print(" [.] Device đã tồn tại: cabinet-001")

        # 6.2 Thiết bị sim-unassigned (SIMULATED để test scope / 404 IDOR)
        dev_sim = session.query(Device).filter_by(device_id="sim-unassigned").first()
        if not dev_sim:
            dev_sim = Device(
                device_id="sim-unassigned",
                name="Tủ Giả Lập Ngoài Phạm Vi",
                location_id=loc_lab.id,
                owner_user_id=admin_user.id,
                device_type="CONTROLLER",
                data_source="SIMULATED",
                lifecycle_state="REGISTERED",
                connection_state="OFFLINE",
                health_state="UNKNOWN",
                hardware_version="esp32-simulator-v1",
                firmware_version="1.0.0",
                capabilities={"thermal": True, "humidity": True, "relay": True, "simulated": True},
                desired_config_version=1,
                applied_config_version=None,
                broker_desired_enabled=True,
                broker_actual_enabled=None,
                broker_sync_state="PENDING",
                state_version=1
            )
            session.add(dev_sim)
            session.flush()

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
                created_by=admin_user.id
            )
            session.add(cfg_sim)

            cred_sim = DeviceCredential(
                device_id="sim-unassigned",
                credential_version=1,
                mqtt_username="sim-unassigned",
                client_id="sim-unassigned",
                status="REGISTERED",
                created_by=admin_user.id
            )
            session.add(cred_sim)

            shadow_sim = DeviceShadow(
                device_id="sim-unassigned",
                desired={"mode": "AUTO", "config_version": 1},
                reported={},
                desired_version=1,
                reported_version=0,
                drift_status="PENDING"
            )
            session.add(shadow_sim)
            print(" [+] Tạo Device: sim-unassigned (CONTROLLER, SIMULATED, state=REGISTERED)")
        else:
            print(" [.] Device đã tồn tại: sim-unassigned")

        # ----------------------------------------------------------------------
        # 7. PHÂN QUYỀN TRUY CẬP THIẾT BỊ (device_access - Mục 5.1 & 6.3)
        # ----------------------------------------------------------------------
        # CHỈ cấp quyền demo ban đầu khi cabinet-001 VỪA ĐƯỢC TẠO MỚI (is_new_cabinet == True).
        # Nếu thiết bị đã tồn tại trong DB, giữ nguyên phân quyền hiện có;
        # KHÔNG tự ý cấp lại grant nếu admin đã thu hồi hoặc đã chỉnh sửa mức quyền.
        if is_new_cabinet:
            acc_op = session.query(DeviceAccess).filter_by(device_id="cabinet-001", user_id=operator_user.id).first()
            if not acc_op:
                session.add(DeviceAccess(
                    device_id="cabinet-001",
                    user_id=operator_user.id,
                    access_level="OPERATE",
                    granted_by=admin_user.id
                ))
                print(f" [+] Cấp quyền device_access ban đầu: {operator_user.username} -> cabinet-001 [OPERATE]")

            acc_vw = session.query(DeviceAccess).filter_by(device_id="cabinet-001", user_id=viewer_user.id).first()
            if not acc_vw:
                session.add(DeviceAccess(
                    device_id="cabinet-001",
                    user_id=viewer_user.id,
                    access_level="VIEW",
                    granted_by=admin_user.id
                ))
                print(f" [+] Cấp quyền device_access ban đầu: {viewer_user.username} -> cabinet-001 [VIEW]")
        else:
            print(" [.] Thiết bị cabinet-001 đã tồn tại từ trước: Giữ nguyên phân quyền device_access hiện có, không cấp lại grant đã thu hồi.")

        # sim-unassigned cố tình KHÔNG cấp grant cho operator/viewer để phục vụ test IDOR/404

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
            rule = session.query(AlertRule).filter_by(device_id="cabinet-001", code=code).first()
            if not rule:
                session.add(AlertRule(
                    device_id="cabinet-001",
                    code=code,
                    severity=severity,
                    condition_source=source,
                    enabled=True,
                    created_by=admin_user.id
                ))
                print(f" [+] Tạo AlertRule: cabinet-001 - {code} [{severity}]")

        # Commit toàn bộ phiên
        session.commit()
        print("=" * 70)
        print("HOÀN TẤT NẠP SEED DATA THÀNH CÔNG VÀ AN TOÀN!")
        print("=" * 70)
    except Exception:
        session.rollback()
        raise


def run_seed(app=None, session=None):
    """Entry point an toàn cho CLI, Script và Pytest fixtures."""
    if app is None:
        app = current_app if has_app_context() else create_app()
    sess = session if session is not None else db.session

    if not has_app_context():
        with app.app_context():
            return _seed_all(app, sess)
    return _seed_all(app, sess)


if __name__ == "__main__":
    run_seed()
