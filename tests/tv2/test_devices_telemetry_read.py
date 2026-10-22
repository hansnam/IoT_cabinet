"""
TV2 Priority 1 Test Suite: Devices, Latest, Shadow, Telemetry History, Statistics & CSV Export
Kiểm thử toàn diện các yêu cầu của Đặc tả V1.3 (Mục 6.4, 6.10, 7.2, 8.1, 15.1, 15.3):
- IAM-02: Người dùng không được cấp grant trên sim-unassigned nhận 404 ở mọi endpoint chi tiết
  (/devices/{id}, /latest, /shadow, /telemetry, /statistics, /telemetry/export) và không lộ count trong /devices.
- DATA-01: Khi cảm biến lỗi (temperature_c=None, humidity_pct=None), /latest, /telemetry, /statistics
  và /telemetry/export giữ nguyên null/rỗng, tuyệt đối không biến thành 0.
- CSV Formula Injection: Các ô văn bản bắt đầu bằng '=', '+', '-', '@', '\t', '\r' được thêm "'"
  ở đầu; số âm hợp lệ (-12.5) giữ nguyên dạng số.
- LOG-01 & LOG-03: Các thao tác đọc/poll (/devices, /latest, /shadow, /telemetry, /statistics)
  không sinh audit_logs hay user_activity_logs; riêng /telemetry/export sinh đầy đủ cả 2 log.
- Validation khoảng thời gian [from, to) tối đa 31 ngày, page_size 1..500, allowlist metric/time_field.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest

from backend.app.auth.tokens import issue_access_token
from backend.app.models.device import Device, DeviceAccess, DeviceConfig
from backend.app.models.iam import AuthSession, Permission, Role, RolePermission, User, UserRole
from backend.app.models.logs import AuditLog, UserActivityLog
from backend.app.models.telemetry import DeviceShadow, Telemetry
from backend.app.telemetry.schemas import sanitize_csv_cell


@pytest.fixture
def tv2_setup(transaction_session):
    """Khởi tạo dữ liệu kiểm thử cho TV2 Priority 1 trong SAVEPOINT transaction."""
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
        ("telemetry.export", "DEVICE"),
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
        ("ADMIN", ["device.read", "telemetry.read", "telemetry.export", "device.control", "device.configure"]),
        ("OPERATOR", ["device.read", "telemetry.read", "telemetry.export", "device.control", "device.configure"]),
        ("VIEWER", ["device.read", "telemetry.read", "telemetry.export"]),
    ]:
        role_id = roles[role_code].id
        for code in granted_codes:
            pid = perms[code].id
            if not transaction_session.query(RolePermission).filter_by(role_id=role_id, permission_id=pid).first():
                transaction_session.add(RolePermission(role_id=role_id, permission_id=pid))

    transaction_session.flush()

    now_utc = datetime.now(timezone.utc)

    # Thiết bị 1: cabinet-001 (REAL, ONLINE)
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
            last_seen_at=now_utc,
            last_telemetry_at=now_utc,
        )
        transaction_session.add(dev1)
    else:
        dev1.connection_state = "ONLINE"
        dev1.health_state = "OK"
        dev1.last_seen_at = now_utc
        dev1.last_telemetry_at = now_utc

    # Thiết bị 2: sim-unassigned (SIMULATED, không gán quyền cho operator/viewer)
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

    # Cấu hình và Shadow cho cabinet-001
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
            canonical_json='{"config_version":1,"profile":"CONTROLLER"}',
            content_hash="3520a5738ade36fecf0a3f72ecf34e9f53fb2b2fe23e0a20245393e6a57a91cb",
        )
        transaction_session.add(cfg1)
        transaction_session.flush()

    dev1.desired_config_version = 1
    dev1.applied_config_version = 1

    shadow1 = transaction_session.query(DeviceShadow).filter_by(device_id="cabinet-001").first()
    if not shadow1:
        shadow1 = DeviceShadow(
            device_id="cabinet-001",
            desired={"fan_on_c": 30.0, "fan_off_c": 28.0},
            reported={"mode": "AUTO", "fan_output": True},
            desired_version=1,
            reported_version=1,
            desired_at=now_utc - timedelta(minutes=5),
            reported_at=now_utc - timedelta(seconds=2),
            drift_status="IN_SYNC",
        )
        transaction_session.add(shadow1)

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
        "create_user": create_user_with_token,
        "dev1": dev1,
        "dev2": dev2,
    }


def test_list_devices_sql_scope_and_no_leak_iam_02(client, tv2_setup):
    """IAM-02: Viewer chỉ được gán cabinet-001 chỉ thấy đúng 1 thiết bị (meta.total=1),
    tuyệt đối không lộ sim-unassigned; Admin thấy đủ cả hai thiết bị."""
    _, viewer_token = tv2_setup["create_user"]("VIEWER", [("cabinet-001", "VIEW")])
    _, admin_token = tv2_setup["create_user"]("ADMIN")

    resp_viewer = client.get(
        "/api/v1/devices",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp_viewer.status_code == 200
    body_v = resp_viewer.get_json()
    assert body_v["meta"]["total"] == 1
    assert [d["device_id"] for d in body_v["data"]] == ["cabinet-001"]

    resp_admin = client.get(
        "/api/v1/devices?data_source=SIMULATED",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp_admin.status_code == 200
    body_a = resp_admin.get_json()
    assert any(d["device_id"] == "sim-unassigned" for d in body_a["data"])


def test_out_of_scope_device_endpoints_return_404_iam_02(client, tv2_setup):
    """IAM-02: Người dùng không có grant trên sim-unassigned gọi bất kỳ endpoint nào của
    sim-unassigned đều nhận 404 NOT_FOUND."""
    _, operator_token = tv2_setup["create_user"]("OPERATOR", [("cabinet-001", "OPERATE")])
    headers = {"Authorization": f"Bearer {operator_token}"}

    for path in [
        "/api/v1/devices/sim-unassigned",
        "/api/v1/devices/sim-unassigned/latest",
        "/api/v1/devices/sim-unassigned/shadow",
        "/api/v1/devices/sim-unassigned/telemetry",
        "/api/v1/devices/sim-unassigned/statistics",
        "/api/v1/devices/sim-unassigned/telemetry/export",
    ]:
        resp = client.get(path, headers=headers)
        assert resp.status_code == 404, f"Expected 404 for {path}, got {resp.status_code}"
        assert resp.get_json()["error"]["code"] == "NOT_FOUND"


def test_latest_and_shadow_preserve_null_on_sensor_fault_data_01(client, tv2_setup):
    """DATA-01: Khi cảm biến DHT22 lỗi (temperature_c=None, humidity_pct=None, sensor_ok=False),
    /latest trả về null chứ không biến thành 0."""
    session = tv2_setup["session"]
    now_utc = tv2_setup["now"]
    _, viewer_token = tv2_setup["create_user"]("VIEWER", [("cabinet-001", "VIEW")])
    headers = {"Authorization": f"Bearer {viewer_token}"}

    boot_id = uuid4()
    conn_id = uuid4()
    # Mẫu 1: bình thường
    t1 = Telemetry(
        device_id="cabinet-001",
        profile="CONTROLLER",
        boot_id=boot_id,
        connection_id=conn_id,
        seq=1,
        measured_at=now_utc - timedelta(seconds=10),
        received_at=now_utc - timedelta(seconds=10),
        uptime_ms=10000,
        clock_synced=True,
        temperature_c=31.5,
        humidity_pct=64.0,
        sensor_ok=True,
        fan_output=True,
        mode="AUTO",
        config_version=1,
        config_hash="3520a5738ade36fecf0a3f72ecf34e9f53fb2b2fe23e0a20245393e6a57a91cb",
    )
    # Mẫu 2 (mới nhất): lỗi cảm biến DHT -> temperature_c và humidity_pct là None
    t2 = Telemetry(
        device_id="cabinet-001",
        profile="CONTROLLER",
        boot_id=boot_id,
        connection_id=conn_id,
        seq=2,
        measured_at=now_utc - timedelta(seconds=2),
        received_at=now_utc - timedelta(seconds=2),
        uptime_ms=15000,
        clock_synced=True,
        temperature_c=None,
        humidity_pct=None,
        sensor_ok=False,
        fan_output=True,
        mode="AUTO",
        fault_code="DHT_READ_FAIL",
    )
    session.add_all([t1, t2])
    session.flush()

    resp_latest = client.get("/api/v1/devices/cabinet-001/latest", headers=headers)
    assert resp_latest.status_code == 200
    latest_data = resp_latest.get_json()["data"]
    assert latest_data["device_id"] == "cabinet-001"
    assert latest_data["seq"] == 2
    assert latest_data["temperature_c"] is None
    assert latest_data["humidity_pct"] is None
    assert latest_data["sensor_ok"] is False
    assert latest_data["fan_output"] is True
    assert latest_data["stale"] is False

    resp_shadow = client.get("/api/v1/devices/cabinet-001/shadow", headers=headers)
    assert resp_shadow.status_code == 200
    shadow_data = resp_shadow.get_json()["data"]
    assert shadow_data["device_id"] == "cabinet-001"
    assert shadow_data["desired_version"] == 1
    assert shadow_data["drift_status"] == "IN_SYNC"


def test_telemetry_history_statistics_and_validation(client, tv2_setup):
    """Kiểm tra /telemetry, /statistics và các quy tắc giới hạn 31 ngày, [from, to), null không biến 0."""
    session = tv2_setup["session"]
    now_utc = tv2_setup["now"]
    user, viewer_token = tv2_setup["create_user"]("VIEWER", [("cabinet-001", "VIEW")])
    headers = {"Authorization": f"Bearer {viewer_token}"}

    boot_id = uuid4()
    conn_id = uuid4()
    session.add_all([
        Telemetry(
            device_id="cabinet-001",
            profile="CONTROLLER",
            boot_id=boot_id,
            connection_id=conn_id,
            seq=10,
            measured_at=now_utc - timedelta(minutes=10),
            received_at=now_utc - timedelta(minutes=10),
            uptime_ms=10000,
            clock_synced=True,
            temperature_c=28.0,
            humidity_pct=60.0,
            sensor_ok=True,
            fan_output=False,
            mode="AUTO",
        ),
        Telemetry(
            device_id="cabinet-001",
            profile="CONTROLLER",
            boot_id=boot_id,
            connection_id=conn_id,
            seq=11,
            measured_at=now_utc - timedelta(minutes=5),
            received_at=now_utc - timedelta(minutes=5),
            uptime_ms=15000,
            clock_synced=True,
            temperature_c=32.0,
            humidity_pct=70.0,
            sensor_ok=True,
            fan_output=True,
            mode="AUTO",
        ),
        Telemetry(
            device_id="cabinet-001",
            profile="CONTROLLER",
            boot_id=boot_id,
            connection_id=conn_id,
            seq=12,
            measured_at=now_utc - timedelta(minutes=1),
            received_at=now_utc - timedelta(minutes=1),
            uptime_ms=20000,
            clock_synced=True,
            temperature_c=None,
            humidity_pct=None,
            sensor_ok=False,
            fan_output=True,
            mode="AUTO",
        ),
    ])
    session.flush()

    # 1. History query
    resp_hist = client.get("/api/v1/devices/cabinet-001/telemetry?page=1&page_size=10", headers=headers)
    assert resp_hist.status_code == 200
    hist_body = resp_hist.get_json()
    assert hist_body["meta"]["total"] == 3
    assert hist_body["data"][0]["seq"] == 12
    assert hist_body["data"][0]["temperature_c"] is None

    # 2. Statistics query: count(temperature_c) == 2 (bỏ qua mẫu NULL), avg == 30.0
    resp_stats = client.get("/api/v1/devices/cabinet-001/statistics", headers=headers)
    assert resp_stats.status_code == 200
    stats_data = resp_stats.get_json()["data"]
    assert stats_data["total_samples"] == 3
    assert stats_data["sensor_fault_count"] == 1
    assert stats_data["fan_on_count"] == 2
    temp_stats = stats_data["metrics"]["temperature_c"]
    assert temp_stats["count"] == 2
    assert temp_stats["min"] == 28.0
    assert temp_stats["max"] == 32.0
    assert temp_stats["avg"] == 30.0

    # 3. Validation: khoảng thời gian > 31 ngày bị chặn bằng 400
    from_too_old = (now_utc - timedelta(days=32)).isoformat().replace("+00:00", "Z")
    to_now = now_utc.isoformat().replace("+00:00", "Z")
    resp_bad_window = client.get(
        f"/api/v1/devices/cabinet-001/telemetry?from={from_too_old}&to={to_now}",
        headers=headers,
    )
    assert resp_bad_window.status_code == 400

    # 4. LOG-03: Kiểm tra không có log đọc/poll nào được ghi cho user này
    act_count = session.query(UserActivityLog).filter_by(actor_user_id=user.id).count()
    assert act_count == 0


def test_telemetry_csv_export_formula_injection_and_activity_log(client, tv2_setup):
    """Kiểm tra xuất CSV (/telemetry/export):
    - Chống CSV Formula Injection (=, +, -, @) trên các cột chuỗi nhưng giữ nguyên số âm hợp lệ (-5.5).
    - Ghi nhận đầy đủ AuditLog và UserActivityLog (function_key='telemetry.export', action='EXPORT').
    """
    assert sanitize_csv_cell("=1+1") == "'=1+1"
    assert sanitize_csv_cell("  +cmd|' /C calc'!A0") == "'  +cmd|' /C calc'!A0"
    assert sanitize_csv_cell("@SUM(A1:A2)") == "'@SUM(A1:A2)"
    assert sanitize_csv_cell(-5.5) == "-5.5"
    assert sanitize_csv_cell(None) == ""

    session = tv2_setup["session"]
    now_utc = tv2_setup["now"]
    user, operator_token = tv2_setup["create_user"]("OPERATOR", [("cabinet-001", "OPERATE")])
    headers = {"Authorization": f"Bearer {operator_token}"}

    session.add(
        Telemetry(
            device_id="cabinet-001",
            profile="CONTROLLER",
            boot_id=uuid4(),
            connection_id=uuid4(),
            seq=99,
            measured_at=now_utc - timedelta(minutes=2),
            received_at=now_utc - timedelta(minutes=2),
            uptime_ms=50000,
            clock_synced=True,
            temperature_c=-5.5,
            humidity_pct=55.2,
            sensor_ok=True,
            fan_output=False,
            mode="AUTO",
            fault_code="=CMD|' /C calc'!A0",
        )
    )
    session.flush()

    resp = client.get("/api/v1/devices/cabinet-001/telemetry/export", headers=headers)
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert resp.headers.get("Cache-Control") == "no-store"

    csv_text = resp.get_data(as_text=True)
    lines = [line for line in csv_text.strip().split("\n") if line]
    assert len(lines) == 2
    assert "temperature_unit" in lines[0]
    assert "clock_synced" in lines[0]
    assert "is_backfill" in lines[0]
    # Kiểm tra số âm hợp lệ -5.5 được giữ nguyên và chuỗi độc hại bị thêm dấu nháy đơn "'"
    assert ",-5.5,C,55.2,%RH," in lines[1]
    assert "'=CMD|' /C calc'!A0" in lines[1]

    # Kiểm tra UserActivityLog & AuditLog đã được ghi nhận (LOG-01)
    act_log = (
        session.query(UserActivityLog)
        .filter_by(actor_user_id=user.id, function_key="telemetry.export")
        .one_or_none()
    )
    assert act_log is not None
    assert act_log.action == "EXPORT"
    assert act_log.outcome == "SUCCESS"
    assert act_log.device_id == "cabinet-001"
    assert act_log.related_audit_log_id is not None

    audit_log = session.query(AuditLog).filter_by(id=act_log.related_audit_log_id).one_or_none()
    assert audit_log is not None
    assert audit_log.action == "TELEMETRY_EXPORT"
    assert audit_log.success is True

