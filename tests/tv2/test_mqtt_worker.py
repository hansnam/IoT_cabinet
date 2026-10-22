"""
Kiểm thử tích hợp cho Ưu tiên 3 của TV2:
- MqttIngestService (/telemetry, /status, /acks) - DATA-01, DATA-02, CMD-01, CMD-02
- MqttDispatcherService (Outbox lease, retry +3s/+6s, timeout +10s, offline sweeper 45s, permission recheck) - CMD-03, CMD-04, OPS-01
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.app.commands.schemas import build_controller_config_snapshot
from backend.app.models.command import Command, CommandAttempt, OutboxMessage
from backend.app.models.device import Device, DeviceAccess, DeviceConfig
from backend.app.models.iam import AuthSession, Permission, Role, RolePermission, User, UserRole
from backend.app.models.telemetry import DeviceEvent, DeviceShadow, Telemetry
from backend.app.mqtt.dispatcher import MqttDispatcherService
from backend.app.mqtt.ingest import MqttIngestService
from backend.app.mqtt.topics import (
    build_acks_topic,
    build_status_topic,
    build_telemetry_topic,
)
from backend.app.outbox.service import OutboxService


def _enqueue_test_command(
    session,
    *,
    device: Device,
    user: User,
    auth_session: AuthSession,
    cmd_type: str,
    params: dict,
    idempotency_key: str,
    now_utc: datetime,
) -> Command:
    next_seq = int(device.last_command_seq or 0) + 1
    device.last_command_seq = next_seq
    cmd = Command(
        command_id=uuid.uuid4(),
        device_id=device.device_id,
        requested_by=user.id,
        source="USER",
        requesting_session_id=auth_session.id,
        type=cmd_type,
        params=params,
        expected_boot_id=device.current_boot_id,
        expected_connection_id=device.current_connection_id,
        device_command_seq=next_seq,
        idempotency_key=idempotency_key,
        request_hash="a" * 64,
        status="PENDING",
        requested_at=now_utc,
        expires_at=now_utc + timedelta(seconds=15),
        attempts=0,
    )
    session.add(cmd)
    session.flush()
    OutboxService.enqueue_command(cmd, db_session=session, available_at=now_utc)
    return cmd


def _seed_mqtt_test_env(session):
    """Khởi tạo dữ liệu nền cho kiểm thử MQTT Worker (Cabinet-001 + Sim-002 + Operator)."""
    now = datetime.now(timezone.utc)

    role_op = session.execute(select(Role).where(Role.code == "OPERATOR")).scalars().first()
    if not role_op:
        role_op = Role(id=uuid.uuid4(), code="OPERATOR", name="Operator", is_system=True)
        session.add(role_op)
        session.flush()

    for code, scope in [
        ("device.read", "DEVICE"),
        ("device.control", "DEVICE"),
        ("device.configure", "DEVICE"),
        ("command.read", "DEVICE"),
    ]:
        perm = session.execute(select(Permission).where(Permission.code == code)).scalars().first()
        if not perm:
            perm = Permission(id=uuid.uuid4(), code=code, scope_type=scope, description=code)
            session.add(perm)
            session.flush()
        rp = (
            session.execute(
                select(RolePermission).where(
                    RolePermission.role_id == role_op.id,
                    RolePermission.permission_id == perm.id,
                )
            )
            .scalars()
            .first()
        )
        if not rp:
            session.add(RolePermission(role_id=role_op.id, permission_id=perm.id))

    op_user = User(
        id=uuid.uuid4(),
        username=f"op_mqtt_{uuid.uuid4().hex[:6]}",
        password_hash="dummy",
        full_name="Operator MQTT Test",
        status="ACTIVE",
        must_change_password=False,
        token_version=1,
        password_changed_at=now,
        failed_login_count=0,
    )
    session.add(op_user)
    session.flush()
    session.add(UserRole(user_id=op_user.id, role_id=role_op.id))

    op_sess = AuthSession(
        id=uuid.uuid4(),
        user_id=op_user.id,
        token_version=1,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=15),
    )
    session.add(op_sess)

    boot_id = uuid.UUID("4e5b9a32-c830-49c0-b86a-3b561a596389")
    conn_id = uuid.UUID("bdb0d8c6-70c9-414a-897e-45c451ee4ab8")

    device = Device(
        device_id="cabinet-mqtt-001",
        name="Tủ Mạng MQTT 01",
        device_type="CONTROLLER",
        data_source="REAL",
        lifecycle_state="ACTIVE",
        connection_state="ONLINE",
        health_state="OK",
        hardware_version="esp32-wroom32-dht22-relay-controller-v1",
        firmware_version="1.2.0",
        capabilities={"temperature": True, "humidity": True, "fan_relay": True},
        current_boot_id=boot_id,
        current_connection_id=conn_id,
        latest_seq=5,
        latest_status_seq=1,
        last_command_seq=0,
        last_seen_at=now,
        last_telemetry_at=now,
        desired_config_version=1,
        applied_config_version=1,
        broker_desired_enabled=True,
        broker_actual_enabled=True,
        broker_sync_state="SYNCED",
        state_version=1,
    )
    session.add(device)

    other_device = Device(
        device_id="sim-mqtt-002",
        name="Tủ Mô Phỏng 02",
        device_type="CONTROLLER",
        data_source="SIMULATED",
        lifecycle_state="ACTIVE",
        connection_state="ONLINE",
        health_state="OK",
        capabilities={"temperature": True, "humidity": True, "fan_relay": True},
        current_boot_id=uuid.uuid4(),
        current_connection_id=uuid.uuid4(),
        latest_seq=1,
        last_command_seq=0,
        last_seen_at=now,
        last_telemetry_at=now,
        desired_config_version=1,
        applied_config_version=1,
        broker_desired_enabled=True,
        broker_sync_state="SYNCED",
        state_version=1,
    )
    session.add(other_device)
    session.flush()

    snapshot_v1, canonical_v1, hash_v1 = build_controller_config_snapshot(
        config_version=1,
        fan_on_c=30.0,
        fan_off_c=28.0,
        temp_alert_c=35.0,
        humidity_alert_pct=80.0,
        temp_clear_margin_c=1.0,
        humidity_clear_margin_pct=3.0,
        telemetry_interval_ms=5000,
        manual_timeout_s=60,
        extra={},
    )
    cfg1 = DeviceConfig(
        id=uuid.uuid4(),
        device_id=device.device_id,
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
        canonical_json=canonical_v1,
        content_hash=hash_v1,
        created_by=op_user.id,
        created_at=now,
    )
    session.add(cfg1)

    shadow = DeviceShadow(
        device_id=device.device_id,
        desired=snapshot_v1,
        reported={
            "temperature_c": 29.0,
            "humidity_pct": 55.0,
            "sensor_ok": True,
            "fan_output": False,
            "mode": "AUTO",
            "clock_synced": True,
            "config_version": 1,
            "config_hash": hash_v1,
        },
        desired_version=1,
        reported_version=1,
        desired_at=now,
        reported_at=now,
        reported_boot_id=boot_id,
        reported_seq=5,
        drift_status="IN_SYNC",
        updated_at=now,
    )
    session.add(shadow)

    access = DeviceAccess(
        id=uuid.uuid4(),
        device_id=device.device_id,
        user_id=op_user.id,
        access_level="OPERATE",
        granted_by=op_user.id,
        granted_at=now,
    )
    session.add(access)
    session.flush()

    return {
        "now": now,
        "op_user": op_user,
        "op_sess": op_sess,
        "device": device,
        "other_device": other_device,
        "boot_id": boot_id,
        "conn_id": conn_id,
        "hash_v1": hash_v1,
        "access": access,
    }


def test_telemetry_ingest_data01_and_data02(app, transaction_session):
    """Kiểm tra DATA-01 (NaN/Infinity, sai dải, DHT fault null) và DATA-02 (dedup, out-of-order seq)."""
    env = _seed_mqtt_test_env(transaction_session)
    device = env["device"]
    topic = build_telemetry_topic(device.device_id)
    initial_last_seen = device.last_seen_at

    # 1. DATA-01: Từ chối NaN trong JSON và không cập nhật last_seen_at
    nan_raw = (
        '{"schema_version":3,"device_id":"cabinet-mqtt-001","profile":"CONTROLLER",'
        '"boot_id":"4e5b9a32-c830-49c0-b86a-3b561a596389",'
        '"connection_id":"bdb0d8c6-70c9-414a-897e-45c451ee4ab8",'
        '"seq":10,"measured_at":"2026-10-22T15:00:00Z","clock_synced":true,"uptime_ms":50000,'
        '"temperature_c":NaN,"humidity_pct":60.0,"sensor_ok":true,"fan_output":false,"mode":"AUTO"}'
    )
    res_nan = MqttIngestService.ingest_message(
        topic,
        nan_raw,
        received_at=initial_last_seen + timedelta(seconds=5),
        db_session=transaction_session,
    )
    assert not res_nan.accepted
    assert res_nan.reason_code == "NON_FINITE_NUMBER"
    assert device.last_seen_at == initial_last_seen

    # 2. DATA-01: Từ chối lệch device_id giữa topic và payload
    wrong_id_payload = {
        "schema_version": 3,
        "device_id": "sim-mqtt-002",
        "profile": "CONTROLLER",
        "fan_binding_id": None,
        "boot_id": str(env["boot_id"]),
        "connection_id": str(env["conn_id"]),
        "seq": 10,
        "measured_at": "2026-10-22T15:00:00Z",
        "clock_synced": True,
        "uptime_ms": 50000,
        "temperature_c": 31.0,
        "humidity_pct": 60.0,
        "sensor_ok": True,
        "fan_output": True,
        "mode": "AUTO",
        "config_version": 1,
        "config_hash": env["hash_v1"],
        "fault_code": None,
        "extra": {},
    }
    res_wrong_id = MqttIngestService.ingest_message(
        topic,
        json.dumps(wrong_id_payload),
        db_session=transaction_session,
    )
    assert not res_wrong_id.accepted
    assert res_wrong_id.reason_code == "DEVICE_ID_MISMATCH"

    # 3. Ingest hợp lệ (seq=12) -> Cập nhật latest_seq=12, shadow.reported
    valid_t12_time = initial_last_seen + timedelta(seconds=10)
    valid_payload_12 = {
        "schema_version": 3,
        "device_id": device.device_id,
        "profile": "CONTROLLER",
        "fan_binding_id": None,
        "boot_id": str(env["boot_id"]),
        "connection_id": str(env["conn_id"]),
        "seq": 12,
        "measured_at": "2026-10-22T15:00:10Z",
        "clock_synced": True,
        "uptime_ms": 60000,
        "temperature_c": 31.2,
        "humidity_pct": 62.0,
        "sensor_ok": True,
        "fan_output": True,
        "mode": "AUTO",
        "config_version": 1,
        "config_hash": env["hash_v1"],
        "fault_code": None,
        "extra": {},
    }
    res_12 = MqttIngestService.ingest_message(
        topic,
        json.dumps(valid_payload_12),
        received_at=valid_t12_time,
        db_session=transaction_session,
    )
    assert res_12.accepted
    assert res_12.outcome == "INGESTED"
    assert int(device.latest_seq) == 12
    assert device.last_seen_at == valid_t12_time

    # 4. DATA-02: Gửi lại đúng gói seq=12 -> DUPLICATE_IGNORED (idempotent)
    res_dup = MqttIngestService.ingest_message(
        topic,
        json.dumps(valid_payload_12),
        received_at=valid_t12_time + timedelta(seconds=2),
        db_session=transaction_session,
    )
    assert res_dup.accepted
    assert res_dup.outcome == "DUPLICATE_IGNORED"
    assert device.last_seen_at == valid_t12_time

    # 5. DATA-02: Cùng (device_id, boot_id, seq=12) nhưng sửa nhiệt độ -> từ chối DUPLICATE_SEQ_HASH_MISMATCH
    tampered_12 = dict(valid_payload_12, temperature_c=35.0)
    res_tampered = MqttIngestService.ingest_message(
        topic,
        json.dumps(tampered_12),
        received_at=valid_t12_time + timedelta(seconds=3),
        db_session=transaction_session,
    )
    assert not res_tampered.accepted
    assert res_tampered.reason_code == "DUPLICATE_SEQ_HASH_MISMATCH"

    # 6. DATA-02: Gói tin đến muộn out-of-order (seq=11 < latest_seq=12) -> Lưu lịch sử nhưng không lùi latest_seq
    late_payload_11 = dict(valid_payload_12, seq=11, temperature_c=28.5)
    res_11 = MqttIngestService.ingest_message(
        topic,
        json.dumps(late_payload_11),
        received_at=valid_t12_time + timedelta(seconds=4),
        db_session=transaction_session,
    )
    assert res_11.accepted
    assert res_11.outcome == "INGESTED_HISTORICAL"
    assert int(device.latest_seq) == 12
    shadow = transaction_session.get(DeviceShadow, device.device_id)
    assert shadow.reported["temperature_c"] == 31.2

    # 7. DATA-01: Lỗi cảm biến DHT22 (sensor_ok=False) -> temperature_c & humidity_pct giữ nguyên None, không biến thành 0
    fault_payload_13 = dict(
        valid_payload_12,
        seq=13,
        sensor_ok=False,
        temperature_c=None,
        humidity_pct=None,
        fault_code="DHT_READ_TIMEOUT",
    )
    res_13 = MqttIngestService.ingest_message(
        topic,
        json.dumps(fault_payload_13),
        received_at=valid_t12_time + timedelta(seconds=5),
        db_session=transaction_session,
    )
    assert res_13.accepted
    assert device.health_state == "FAULT"
    row_13 = transaction_session.get(Telemetry, res_13.telemetry_id)
    assert row_13.temperature_c is None
    assert row_13.humidity_pct is None
    assert row_13.sensor_ok is False


def test_status_lwt_retained_and_offline_sweeper(app, transaction_session):
    """Kiểm tra DATA-02 & Mục 8.3:
    - LWT của connection cũ không làm offline phiên mới.
    - Retained ONLINE không gia hạn last_seen_at.
    - Offline sweeper đánh dấu OFFLINE sau 45s không có gói tin.
    """
    env = _seed_mqtt_test_env(transaction_session)
    device = env["device"]
    status_topic = build_status_topic(device.device_id)
    t0 = env["now"]

    # 1. LWT của connection cũ (khác current_connection_id) -> bị bỏ qua
    old_conn_lwt = {
        "schema_version": 3,
        "device_id": device.device_id,
        "connection": "OFFLINE",
        "boot_id": str(env["boot_id"]),
        "connection_id": str(uuid.uuid4()),
        "reason": "LWT",
    }
    res_old_lwt = MqttIngestService.ingest_message(
        status_topic,
        json.dumps(old_conn_lwt),
        is_retained=True,
        received_at=t0 + timedelta(seconds=2),
        db_session=transaction_session,
    )
    assert res_old_lwt.accepted
    assert res_old_lwt.outcome == "STALE_LWT_IGNORED"
    assert device.connection_state == "ONLINE"

    # 2. LWT đúng current_connection_id -> chuyển thiết bị sang OFFLINE
    current_lwt = {
        "schema_version": 3,
        "device_id": device.device_id,
        "connection": "OFFLINE",
        "boot_id": str(env["boot_id"]),
        "connection_id": str(env["conn_id"]),
        "reason": "LWT",
    }
    res_cur_lwt = MqttIngestService.ingest_message(
        status_topic,
        json.dumps(current_lwt),
        is_retained=True,
        received_at=t0 + timedelta(seconds=5),
        db_session=transaction_session,
    )
    assert res_cur_lwt.accepted
    assert res_cur_lwt.outcome == "OFFLINE_RECORDED"
    assert device.connection_state == "OFFLINE"

    # 3. Nhận bản tin ONLINE nhưng có cờ retained=True -> không tự bật lại ONLINE hay gia hạn last_seen_at
    new_conn_id = uuid.uuid4()
    online_status_payload = {
        "schema_version": 3,
        "device_id": device.device_id,
        "profile": "CONTROLLER",
        "connection": "ONLINE",
        "boot_id": str(env["boot_id"]),
        "connection_id": str(new_conn_id),
        "status_seq": 2,
        "config_version": 1,
        "config_hash": env["hash_v1"],
        "firmware_version": "1.2.1",
        "hardware_version": "esp32-wroom32-dht22-relay-controller-v1",
        "uptime_ms": 75000,
        "clock_synced": True,
        "health": "OK",
        "fault_code": None,
    }
    res_retained = MqttIngestService.ingest_message(
        status_topic,
        json.dumps(online_status_payload),
        is_retained=True,
        received_at=t0 + timedelta(seconds=10),
        db_session=transaction_session,
    )
    assert res_retained.accepted
    assert res_retained.outcome == "RETAINED_ONLINE_IGNORED"
    assert device.connection_state == "OFFLINE"
    assert device.last_seen_at == t0

    # 4. Bản tin ONLINE live (is_retained=False) -> phục hồi ONLINE và cập nhật last_seen_at
    t_live = t0 + timedelta(seconds=15)
    res_live = MqttIngestService.ingest_message(
        status_topic,
        json.dumps(online_status_payload),
        is_retained=False,
        received_at=t_live,
        db_session=transaction_session,
    )
    assert res_live.accepted
    assert res_live.outcome == "STATUS_UPDATED"
    assert device.connection_state == "ONLINE"
    assert device.current_connection_id == new_conn_id
    assert device.last_seen_at == t_live

    # 5. Sau 46 giây không có gói tin mới -> Offline Sweeper chuyển sang OFFLINE
    swept_count = MqttDispatcherService.sweep_offline_devices(
        now_utc=t_live + timedelta(seconds=46),
        offline_timeout_s=45,
        db_session=transaction_session,
    )
    assert swept_count >= 1
    assert device.connection_state == "OFFLINE"


def test_outbox_dispatcher_retries_timeout_and_ack_matching(app, transaction_session):
    """Kiểm tra CMD-01, CMD-02:
    - Dispatch lệnh từ Outbox -> SENT (attempt 1).
    - Retry tại +3s (attempt 2) và +6s (attempt 3), không reset sent_at/expires_at.
    - Từ chối ACK sai thiết bị / sai seq / sai boot_id.
    - Timeout tại +10s -> TIMEOUT.
    - ACK đến muộn sau TIMEOUT -> APPLIED_LATE, duplicate ACK sau đó được bỏ qua.
    """
    env = _seed_mqtt_test_env(transaction_session)
    device = env["device"]
    t0 = env["now"]

    cmd = _enqueue_test_command(
        transaction_session,
        device=device,
        user=env["op_user"],
        auth_session=env["op_sess"],
        cmd_type="SET_MODE",
        params={"mode": "MANUAL", "duration_s": 60},
        idempotency_key="idem-mqtt-cmd-001",
        now_utc=t0,
    )
    assert cmd.status == "PENDING"
    original_expires_at = cmd.expires_at

    published_messages = []

    def mock_publish(topic: str, payload_json: str, qos: int, retained: bool) -> bool:
        published_messages.append((topic, json.loads(payload_json), qos, retained))
        return True

    # 1. Dispatch lần đầu tại t0 -> attempt 1
    t_send = cmd.requested_at
    stats_1 = MqttDispatcherService.dispatch_pending_outbox(
        mock_publish,
        worker_id="test-worker",
        now_utc=t_send,
        db_session=transaction_session,
    )
    assert stats_1.published_commands == 1
    assert cmd.status == "SENT"
    assert cmd.attempts == 1
    assert cmd.sent_at == t_send
    assert len(published_messages) == 1

    # 2. Tại t_send + 2s (chưa tới mốc 3s) -> chưa retry
    stats_2s = MqttDispatcherService.dispatch_pending_outbox(
        mock_publish,
        worker_id="test-worker",
        now_utc=t_send + timedelta(seconds=2),
        db_session=transaction_session,
    )
    assert stats_2s.published_commands == 0
    assert cmd.attempts == 1

    # 3. Tại t_send + 3s -> Retry 1 (attempt 2), giữ nguyên sent_at và expires_at
    stats_3s = MqttDispatcherService.dispatch_pending_outbox(
        mock_publish,
        worker_id="test-worker",
        now_utc=t_send + timedelta(seconds=3),
        db_session=transaction_session,
    )
    assert stats_3s.scheduled_retries == 1
    assert stats_3s.published_commands == 1
    assert cmd.attempts == 2
    assert cmd.sent_at == t_send
    assert cmd.expires_at == original_expires_at

    # 4. Tại t_send + 6s -> Retry 2 (attempt 3)
    stats_6s = MqttDispatcherService.dispatch_pending_outbox(
        mock_publish,
        worker_id="test-worker",
        now_utc=t_send + timedelta(seconds=6),
        db_session=transaction_session,
    )
    assert stats_6s.scheduled_retries == 1
    assert stats_6s.published_commands == 1
    assert cmd.attempts == 3

    # 5. CMD-02: Từ chối ACK từ thiết bị khác (cross-device ACK) hoặc sai seq/boot_id
    ack_base = {
        "schema_version": 3,
        "device_id": device.device_id,
        "command_id": str(cmd.command_id),
        "status": "APPLIED",
        "type": "SET_MODE",
        "boot_id": str(env["boot_id"]),
        "connection_id": str(env["conn_id"]),
        "device_command_seq": int(cmd.device_command_seq),
        "executed_at": "2026-10-22T15:00:07Z",
        "result": {"mode": "MANUAL", "fan_output": False},
    }
    res_cross = MqttIngestService.ingest_message(
        build_acks_topic(env["other_device"].device_id),
        json.dumps(dict(ack_base, device_id=env["other_device"].device_id)),
        received_at=t_send + timedelta(seconds=7),
        db_session=transaction_session,
    )
    assert not res_cross.accepted
    assert res_cross.reason_code == "CROSS_DEVICE_ACK"
    assert cmd.status == "SENT"

    res_wrong_boot = MqttIngestService.ingest_message(
        build_acks_topic(device.device_id),
        json.dumps(dict(ack_base, boot_id=str(uuid.uuid4()))),
        received_at=t_send + timedelta(seconds=7),
        db_session=transaction_session,
    )
    assert not res_wrong_boot.accepted
    assert res_wrong_boot.reason_code == "ACK_BOOT_MISMATCH"
    assert cmd.status == "SENT"

    # 6. Tại t_send + 10s (chưa nhận ACK hợp lệ) -> chuyển sang TIMEOUT
    stats_10s = MqttDispatcherService.dispatch_pending_outbox(
        mock_publish,
        worker_id="test-worker",
        now_utc=t_send + timedelta(seconds=10),
        db_session=transaction_session,
    )
    assert stats_10s.timed_out_commands == 1
    assert cmd.status == "TIMEOUT"
    assert cmd.error_code == "ACK_TIMEOUT"

    # 7. ACK hợp lệ đến muộn tại t_send + 11s -> lưu APPLIED_LATE
    res_late_ack = MqttIngestService.ingest_message(
        build_acks_topic(device.device_id),
        json.dumps(ack_base),
        received_at=t_send + timedelta(seconds=11),
        db_session=transaction_session,
    )
    assert res_late_ack.accepted
    assert res_late_ack.outcome == "APPLIED_LATE"
    assert cmd.status == "APPLIED_LATE"

    # 8. Gửi lại ACK trùng lặp -> DUPLICATE_ACK_IGNORED
    res_dup_ack = MqttIngestService.ingest_message(
        build_acks_topic(device.device_id),
        json.dumps(ack_base),
        received_at=t_send + timedelta(seconds=12),
        db_session=transaction_session,
    )
    assert res_dup_ack.accepted
    assert res_dup_ack.outcome == "DUPLICATE_ACK_IGNORED"


def test_cmd03_lease_reclaim_and_cmd04_permission_revoked(app, transaction_session):
    """Kiểm tra CMD-03 (thu hồi lease hết hạn khi worker crash) và CMD-04 (hủy lệnh khi user mất quyền trước khi gửi)."""
    env = _seed_mqtt_test_env(transaction_session)
    device = env["device"]

    # --- Phần 1: CMD-04 (Thu hồi quyền OPERATE trước khi worker gửi lệnh) ---
    cmd_revoke = _enqueue_test_command(
        transaction_session,
        device=device,
        user=env["op_user"],
        auth_session=env["op_sess"],
        cmd_type="SET_MODE",
        params={"mode": "MANUAL", "duration_s": 60},
        idempotency_key="idem-cmd04-revoke",
        now_utc=env["now"],
    )
    assert cmd_revoke.status == "PENDING"

    # Hạ quyền từ OPERATE xuống VIEW trong lúc lệnh còn PENDING
    env["access"].access_level = "VIEW"
    transaction_session.flush()

    published = []
    stats_revoked = MqttDispatcherService.dispatch_pending_outbox(
        lambda t, p, q, r: (published.append(t) or True),
        worker_id="worker-cmd04",
        now_utc=cmd_revoke.requested_at + timedelta(seconds=1),
        db_session=transaction_session,
    )
    assert stats_revoked.cancelled_commands == 1
    assert stats_revoked.published_commands == 0
    assert len(published) == 0
    assert cmd_revoke.status == "CANCELLED"
    assert cmd_revoke.error_code == "ACTOR_PERMISSION_REVOKED"

    # --- Phần 2: CMD-03 (Khôi phục quyền OPERATE, mô phỏng crash khi đang LEASED) ---
    env["access"].access_level = "OPERATE"
    transaction_session.flush()

    cmd_crash = _enqueue_test_command(
        transaction_session,
        device=device,
        user=env["op_user"],
        auth_session=env["op_sess"],
        cmd_type="SET_MODE",
        params={"mode": "MANUAL", "duration_s": 60},
        idempotency_key="idem-cmd03-crash",
        now_utc=env["now"],
    )
    ob_crash = (
        transaction_session.execute(
            select(OutboxMessage).where(OutboxMessage.command_id == cmd_crash.command_id)
        )
        .scalars()
        .first()
    )
    t_crash = cmd_crash.requested_at
    ob_crash.status = "LEASED"
    ob_crash.locked_by = "crashed-worker"
    ob_crash.lease_until = t_crash + timedelta(seconds=5)
    dangling_attempt = CommandAttempt(
        id=uuid.uuid4(),
        command_id=cmd_crash.command_id,
        attempt_no=1,
        dispatch_started_at=t_crash,
        worker_id="crashed-worker",
        outcome=None,
    )
    transaction_session.add(dangling_attempt)
    transaction_session.flush()

    # Sau khi lease hết hạn (t_crash + 6s), worker mới reclaim và gửi thành công ở attempt_no=2
    stats_reclaim = MqttDispatcherService.dispatch_pending_outbox(
        lambda t, p, q, r: (published.append(t) or True),
        worker_id="healthy-worker",
        now_utc=t_crash + timedelta(seconds=6),
        db_session=transaction_session,
    )
    assert stats_reclaim.reclaimed_leases == 1
    assert stats_reclaim.published_commands == 1
    assert dangling_attempt.outcome == "LEASE_EXPIRED"
    assert cmd_crash.status == "SENT"
    assert cmd_crash.attempts == 2
