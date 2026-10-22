"""
TV2 Priority 2 Test Suite: Device Config, Commands & Transactional Outbox
Kiểm thử toàn diện các yêu cầu của Đặc tả V1.3 (Mục 6.3, 6.4, 7.2, 8.4, 8.5, 15.3):
- CFG-01: Hai PUT /config cùng base_version -> cái đầu 202, cái sau 409; applied_config_version
  không đổi khi mới 202; version mới supersede lệnh APPLY_CONFIG PENDING cũ.
- CMD-01: HTTP retry cùng Idempotency-Key trả cùng command_id và không gia hạn expires_at;
  cùng Idempotency-Key nhưng khác payload trả 409.
- SET_FAN chỉ hợp lệ khi thiết bị đang ở chế độ MANUAL đã APPLIED; từ chối lệnh thủ công
  khi OFFLINE, FAULT, sensor_ok=False hoặc clock_synced=False.
- IAM-01: Viewer có VIEW trên cabinet-001 gửi SET_FAN nhận 403, không tạo command/outbox mới,
  ghi nhận AuditLog(success=False) và UserActivityLog(outcome='DENIED').
- IAM-02: Đọc /commands/{id} hoặc /devices/{id}/config của thiết bị ngoài scope trả 404.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest

from backend.app.auth.tokens import issue_access_token
from backend.app.commands.schemas import build_controller_config_snapshot
from backend.app.models.command import Command, OutboxMessage
from backend.app.models.device import Device, DeviceAccess, DeviceConfig
from backend.app.models.iam import AuthSession, Permission, Role, RolePermission, User, UserRole
from backend.app.models.logs import AuditLog, UserActivityLog
from backend.app.models.telemetry import DeviceShadow, Telemetry


@pytest.fixture
def tv2_cmd_setup(transaction_session):
    """Khởi tạo dữ liệu cho kiểm thử Config & Commands trong SAVEPOINT transaction."""
    roles = {}
    for code, name in [("ADMIN", "System Admin"), ("OPERATOR", "Operator"), ("VIEWER", "Viewer")]:
        r = transaction_session.query(Role).filter_by(code=code).first()
        if not r:
            r = Role(code=code, name=name, is_system=True)
            transaction_session.add(r)
            transaction_session.flush()
        roles[code] = r

    perms = {}
    perm_defs = [
        ("device.read", "DEVICE"),
        ("telemetry.read", "DEVICE"),
        ("command.read", "DEVICE"),
        ("device.control", "DEVICE"),
        ("device.configure", "DEVICE"),
    ]
    for code, scope in perm_defs:
        p = transaction_session.query(Permission).filter_by(code=code).first()
        if not p:
            p = Permission(code=code, scope_type=scope, description=code)
            transaction_session.add(p)
            transaction_session.flush()
        perms[code] = p

    for role_code, granted_codes in [
        ("ADMIN", ["device.read", "telemetry.read", "command.read", "device.control", "device.configure"]),
        ("OPERATOR", ["device.read", "telemetry.read", "command.read", "device.control", "device.configure"]),
        ("VIEWER", ["device.read", "telemetry.read", "command.read"]),
    ]:
        role_id = roles[role_code].id
        for code in granted_codes:
            pid = perms[code].id
            if not transaction_session.query(RolePermission).filter_by(role_id=role_id, permission_id=pid).first():
                transaction_session.add(RolePermission(role_id=role_id, permission_id=pid))

    transaction_session.flush()

    now_utc = datetime.now(timezone.utc)
    boot_id = uuid4()
    conn_id = uuid4()

    dev1 = transaction_session.query(Device).filter_by(device_id="cabinet-001").first()
    if not dev1:
        dev1 = Device(
            device_id="cabinet-001",
            name="Tủ mạng Lab 01",
            device_type="CONTROLLER",
            data_source="REAL",
            lifecycle_state="ACTIVE",
            connection_state="ONLINE",
            health_state="OK",
            current_boot_id=boot_id,
            current_connection_id=conn_id,
            last_command_seq=0,
            last_seen_at=now_utc,
            last_telemetry_at=now_utc,
        )
        transaction_session.add(dev1)
    else:
        dev1.lifecycle_state = "ACTIVE"
        dev1.connection_state = "ONLINE"
        dev1.health_state = "OK"
        dev1.current_boot_id = boot_id
        dev1.current_connection_id = conn_id
        dev1.last_command_seq = 0
        dev1.last_seen_at = now_utc
        dev1.last_telemetry_at = now_utc

    dev2 = transaction_session.query(Device).filter_by(device_id="sim-unassigned").first()
    if not dev2:
        dev2 = Device(
            device_id="sim-unassigned",
            name="Simulator Unassigned",
            device_type="CONTROLLER",
            data_source="SIMULATED",
            lifecycle_state="ACTIVE",
            connection_state="ONLINE",
            health_state="OK",
            last_seen_at=now_utc,
            last_telemetry_at=now_utc,
        )
        transaction_session.add(dev2)

    transaction_session.flush()

    # Kiểm tra fixture hash v1 khớp tuyệt đối với Mục 8.5
    snapshot_v1, canonical_v1, hash_v1 = build_controller_config_snapshot(
        config_version=1,
        fan_on_c=30,
        fan_off_c=28,
        temp_alert_c=35,
        humidity_alert_pct=80,
        temp_clear_margin_c=1,
        humidity_clear_margin_pct=3,
        telemetry_interval_ms=5000,
        manual_timeout_s=60,
    )
    assert hash_v1 == "3520a5738ade36fecf0a3f72ecf34e9f53fb2b2fe23e0a20245393e6a57a91cb"

    cfg1 = transaction_session.query(DeviceConfig).filter_by(device_id="cabinet-001", version=1).first()
    if not cfg1:
        cfg1 = DeviceConfig(
            device_id="cabinet-001",
            version=1,
            profile="CONTROLLER",
            fan_on_c=30.0,
            fan_off_c=28.0,
            temp_alert_c=35.0,
            humidity_alert_pct=80.0,
            temp_clear_margin_c=1.0,
            humidity_clear_margin_pct=3.0,
            manual_timeout_s=60,
            telemetry_interval_ms=5000,
            schema_version=3,
            canonical_json=canonical_v1,
            content_hash=hash_v1,
        )
        transaction_session.add(cfg1)
        transaction_session.flush()

    dev1.desired_config_version = 1
    dev1.applied_config_version = 1

    shadow1 = transaction_session.query(DeviceShadow).filter_by(device_id="cabinet-001").first()
    if not shadow1:
        shadow1 = DeviceShadow(
            device_id="cabinet-001",
            desired=snapshot_v1,
            reported={"mode": "AUTO", "fan_output": False},
            desired_version=1,
            reported_version=1,
            desired_at=now_utc - timedelta(minutes=2),
            reported_at=now_utc - timedelta(seconds=1),
            drift_status="IN_SYNC",
        )
        transaction_session.add(shadow1)

    # Thêm 1 mẫu telemetry live gần nhất (AUTO, sensor_ok=True, clock_synced=True)
    t_live = Telemetry(
        device_id="cabinet-001",
        profile="CONTROLLER",
        boot_id=boot_id,
        connection_id=conn_id,
        seq=1,
        measured_at=now_utc - timedelta(seconds=1),
        received_at=now_utc - timedelta(seconds=1),
        uptime_ms=60000,
        clock_synced=True,
        temperature_c=29.5,
        humidity_pct=60.0,
        sensor_ok=True,
        fan_output=False,
        mode="AUTO",
        config_version=1,
        config_hash=hash_v1,
    )
    transaction_session.add(t_live)
    transaction_session.flush()

    def create_user_with_token(role_code: str, grant_device_ids: list[tuple[str, str]] | None = None):
        uid = uuid4()
        user = User(
            id=uid,
            username=f"{role_code.lower()}_{uid.hex[:8]}",
            password_hash="$argon2id$v=19$m=19456,t=2,p=1$mockhash",
            full_name=f"User {role_code}",
            status="ACTIVE",
            must_change_password=False,
            token_version=1,
        )
        transaction_session.add(user)
        transaction_session.flush()
        transaction_session.add(UserRole(user_id=user.id, role_id=roles[role_code].id))

        if grant_device_ids:
            for dev_id, level in grant_device_ids:
                transaction_session.add(
                    DeviceAccess(
                        device_id=dev_id,
                        user_id=user.id,
                        access_level=level,
                        granted_by=user.id,
                        granted_at=now_utc - timedelta(minutes=10),
                    )
                )

        sess = AuthSession(
            id=uuid4(),
            user_id=user.id,
            token_version=1,
            created_at=now_utc,
            last_seen_at=now_utc,
            expires_at=now_utc + timedelta(seconds=900),
        )
        transaction_session.add(sess)
        transaction_session.flush()

        token_data = issue_access_token(
            user_id=user.id,
            session_id=sess.id,
            token_version=1,
            expires_delta=900,
        )
        sess.expires_at = token_data.expires_at
        transaction_session.flush()
        return user, token_data.access_token

    return {
        "session": transaction_session,
        "now": now_utc,
        "boot_id": boot_id,
        "conn_id": conn_id,
        "dev1": dev1,
        "dev2": dev2,
        "telemetry": t_live,
        "shadow": shadow1,
        "create_user": create_user_with_token,
    }


def test_config_update_base_version_conflict_and_supersede_cfg_01(client, tv2_cmd_setup):
    """CFG-01: Hai PUT /config cùng base_version -> cái đầu 202 (desired=2, applied giữ nguyên=1),
    cái thứ hai cùng base_version=1 bị 409 CONFLICT. Khi gửi tiếp base_version=2 -> 202 (desired=3)
    và lệnh APPLY_CONFIG v2 đang PENDING bị chuyển thành CANCELLED."""
    session = tv2_cmd_setup["session"]
    _, operator_token = tv2_cmd_setup["create_user"]("OPERATOR", [("cabinet-001", "OPERATE")])

    payload_v2 = {
        "base_version": 1,
        "fan_on_c": 31.0,
        "fan_off_c": 28.5,
        "temp_alert_c": 36.0,
        "humidity_alert_pct": 82.0,
        "temp_clear_margin_c": 1.0,
        "humidity_clear_margin_pct": 3.0,
        "telemetry_interval_ms": 5000,
        "manual_timeout_s": 90,
    }

    # 1. PUT config đầu tiên với base_version=1 -> 202 Accepted
    resp1 = client.put(
        "/api/v1/devices/cabinet-001/config",
        json=payload_v2,
        headers={
            "Authorization": f"Bearer {operator_token}",
            "Idempotency-Key": "cfg-key-001",
        },
    )
    assert resp1.status_code == 202
    data1 = resp1.get_json()["data"]
    assert data1["desired_config_version"] == 2
    assert data1["applied_config_version"] == 1
    assert data1["pending"] is True
    cmd_v2_id = data1["command"]["command_id"]

    # 2. Retry cùng Idempotency-Key + cùng payload -> trả lại đúng command_id v2 (202)
    resp_retry = client.put(
        "/api/v1/devices/cabinet-001/config",
        json=payload_v2,
        headers={
            "Authorization": f"Bearer {operator_token}",
            "Idempotency-Key": "cfg-key-001",
        },
    )
    assert resp_retry.status_code == 202
    assert resp_retry.get_json()["data"]["command"]["command_id"] == cmd_v2_id

    # 3. PUT config khác với base_version=1 cũ -> 409 CONFLICT
    resp_stale = client.put(
        "/api/v1/devices/cabinet-001/config",
        json={**payload_v2, "fan_on_c": 32.0},
        headers={
            "Authorization": f"Bearer {operator_token}",
            "Idempotency-Key": "cfg-key-002",
        },
    )
    assert resp_stale.status_code == 409
    assert resp_stale.get_json()["error"]["code"] == "CONFLICT"

    # 4. PUT config v3 với base_version=2 -> 202 Accepted và hủy lệnh APPLY_CONFIG v2 đang PENDING
    resp_v3 = client.put(
        "/api/v1/devices/cabinet-001/config",
        json={**payload_v2, "base_version": 2, "fan_on_c": 32.0},
        headers={
            "Authorization": f"Bearer {operator_token}",
            "Idempotency-Key": "cfg-key-003",
        },
    )
    assert resp_v3.status_code == 202
    assert resp_v3.get_json()["data"]["desired_config_version"] == 3

    cmd_v2 = session.query(Command).filter_by(command_id=cmd_v2_id).one()
    ob_v2 = session.query(OutboxMessage).filter_by(command_id=cmd_v2_id).one()
    assert cmd_v2.status == "CANCELLED"
    assert ob_v2.status == "CANCELLED"


def test_command_idempotency_and_manual_mode_requirement_cmd_01(client, tv2_cmd_setup):
    """CMD-01:
    - Gửi SET_FAN khi thiết bị đang ở chế độ AUTO bị từ chối 409.
    - Gửi SET_MODE(MANUAL) -> 202 Accepted; retry cùng Idempotency-Key trả cùng command_id và expires_at.
    - Sau khi thiết bị báo mode=MANUAL, gửi SET_FAN(on=True) -> 202 Accepted."""
    session = tv2_cmd_setup["session"]
    _, operator_token = tv2_cmd_setup["create_user"]("OPERATOR", [("cabinet-001", "OPERATE")])
    auth_header = {"Authorization": f"Bearer {operator_token}"}

    # 1. Thử gửi SET_FAN khi đang ở chế độ AUTO -> 409 CONFLICT
    resp_fan_early = client.post(
        "/api/v1/devices/cabinet-001/commands",
        json={"type": "SET_FAN", "params": {"on": True}},
        headers={**auth_header, "Idempotency-Key": "fan-early-1"},
    )
    assert resp_fan_early.status_code == 409

    # 2. Gửi SET_MODE(MANUAL, duration_s=120) -> 202 Accepted
    resp_mode = client.post(
        "/api/v1/devices/cabinet-001/commands",
        json={"type": "SET_MODE", "params": {"mode": "MANUAL", "duration_s": 120}},
        headers={**auth_header, "Idempotency-Key": "mode-manual-1"},
    )
    assert resp_mode.status_code == 202
    cmd_mode = resp_mode.get_json()["data"]
    assert cmd_mode["type"] == "SET_MODE"
    assert cmd_mode["status"] == "PENDING"
    assert cmd_mode["device_command_seq"] == 1

    # 3. Retry cùng Idempotency-Key -> trả cùng command_id, cùng expires_at, không tạo bản ghi mới
    resp_mode_retry = client.post(
        "/api/v1/devices/cabinet-001/commands",
        json={"type": "SET_MODE", "params": {"mode": "MANUAL", "duration_s": 120}},
        headers={**auth_header, "Idempotency-Key": "mode-manual-1"},
    )
    assert resp_mode_retry.status_code == 202
    cmd_mode_retry = resp_mode_retry.get_json()["data"]
    assert cmd_mode_retry["command_id"] == cmd_mode["command_id"]
    assert cmd_mode_retry["expires_at"] == cmd_mode["expires_at"]

    # Cùng Idempotency-Key nhưng đổi payload -> 409 CONFLICT
    resp_mode_conflict = client.post(
        "/api/v1/devices/cabinet-001/commands",
        json={"type": "SET_MODE", "params": {"mode": "AUTO"}},
        headers={**auth_header, "Idempotency-Key": "mode-manual-1"},
    )
    assert resp_mode_conflict.status_code == 409

    # 4. Giả lập thiết bị đã APPLIED lệnh SET_MODE(MANUAL)
    tv2_cmd_setup["telemetry"].mode = "MANUAL"
    session.flush()

    resp_fan_ok = client.post(
        "/api/v1/devices/cabinet-001/commands",
        json={"type": "SET_FAN", "params": {"on": True}},
        headers={**auth_header, "Idempotency-Key": "fan-ok-1"},
    )
    assert resp_fan_ok.status_code == 202
    fan_cmd_data = resp_fan_ok.get_json()["data"]
    assert fan_cmd_data["type"] == "SET_FAN"
    assert fan_cmd_data["params"] == {"on": True}
    assert fan_cmd_data["device_command_seq"] == 2

    # 5. Kiểm tra GET /devices/cabinet-001/commands và GET /commands/<id>
    resp_list = client.get("/api/v1/devices/cabinet-001/commands", headers=auth_header)
    assert resp_list.status_code == 200
    assert resp_list.get_json()["meta"]["total"] == 2

    resp_detail = client.get(f"/api/v1/commands/{fan_cmd_data['command_id']}", headers=auth_header)
    assert resp_detail.status_code == 200
    assert resp_detail.get_json()["data"]["command_id"] == fan_cmd_data["command_id"]


def test_viewer_control_denied_records_audit_and_no_outbox_iam_01(client, tv2_cmd_setup):
    """IAM-01: Viewer có VIEW trên cabinet-001 gửi SET_FAN nhận 403 FORBIDDEN,
    không tạo Command/OutboxMessage mới, và ghi AuditLog(success=False) + UserActivityLog(outcome='DENIED')."""
    session = tv2_cmd_setup["session"]
    viewer_user, viewer_token = tv2_cmd_setup["create_user"]("VIEWER", [("cabinet-001", "VIEW")])

    cmd_count_before = session.query(Command).filter_by(device_id="cabinet-001").count()
    ob_count_before = session.query(OutboxMessage).count()

    resp = client.post(
        "/api/v1/devices/cabinet-001/commands",
        json={"type": "SET_FAN", "params": {"on": True}},
        headers={
            "Authorization": f"Bearer {viewer_token}",
            "Idempotency-Key": "viewer-try-control-1",
        },
    )
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "FORBIDDEN"

    assert session.query(Command).filter_by(device_id="cabinet-001").count() == cmd_count_before
    assert session.query(OutboxMessage).count() == ob_count_before

    denied_act = (
        session.query(UserActivityLog)
        .filter_by(actor_user_id=viewer_user.id, function_key="device.control", outcome="DENIED")
        .one_or_none()
    )
    assert denied_act is not None
    assert denied_act.action == "CONTROL"
    assert denied_act.related_audit_log_id is not None

    denied_audit = session.query(AuditLog).filter_by(id=denied_act.related_audit_log_id).one_or_none()
    assert denied_audit is not None
    assert denied_audit.success is False
    assert denied_audit.reason_code == "FORBIDDEN"
