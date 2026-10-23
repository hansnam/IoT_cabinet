"""
MQTT Ingest Pipeline Service (Thành viên 2 phụ trách - Mục 8, DATA-01, DATA-02, CMD-01, CMD-02)
Xử lý luồng bản tin từ thiết bị gửi lên Broker:
1. /telemetry -> Kiểm tra hợp lệ, chống trùng (dedup), lưu telemetry, cập nhật Device & DeviceShadow.
2. /status    -> Xử lý heartbeat ONLINE (bỏ qua retained ONLINE cũ) và LWT OFFLINE (chỉ khớp current connection).
3. /acks      -> Đối chiếu command_id/device_id/type/seq/boot_id/connection_id, cập nhật APPLIED / APPLIED_LATE / REJECTED.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from flask import current_app, has_app_context
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.extensions import db
from backend.app.models.command import Command, CommandAttempt, OutboxMessage
from backend.app.models.device import Device, DeviceConfig
from backend.app.models.telemetry import DeviceEvent, DeviceShadow, Telemetry
from backend.app.mqtt.topics import parse_incoming_topic
from backend.app.mqtt.validators import (
    MqttValidationError,
    parse_strict_json,
    validate_ack_payload,
    validate_status_payload,
    validate_telemetry_payload,
)
from backend.app.outbox.service import OutboxService
from backend.app.shadows.service import format_utc_iso


@dataclass
class IngestResult:
    """Kết quả xử lý một bản tin MQTT đầu vào."""

    accepted: bool
    outcome: str
    reason_code: str | None = None
    message: str | None = None
    device_id: str | None = None
    channel: str | None = None
    telemetry_id: uuid.UUID | None = None
    command_id: uuid.UUID | None = None


class MqttIngestService:
    """Dịch vụ xử lý bản tin MQTT đầu vào (/telemetry, /status, /acks)."""

    # Hook mở rộng tùy chọn cho TV4 (Alert Evaluator) đăng ký callback sau khi ingest
    _telemetry_hooks: list[Callable[[Device, Telemetry, Session], None]] = []

    @classmethod
    def register_telemetry_hook(
        cls, hook: Callable[[Device, Telemetry, Session], None]
    ) -> None:
        if hook not in cls._telemetry_hooks:
            cls._telemetry_hooks.append(hook)

    @staticmethod
    def _is_fan_monitor_enabled() -> bool:
        if has_app_context():
            return bool(current_app.config.get("FAN_MONITOR_ENABLED", False))
        return False

    @staticmethod
    def _load_and_verify_device(
        device_id: str, db_session: Session
    ) -> tuple[Device | None, IngestResult | None]:
        device = (
            db_session.execute(
                select(Device)
                .where(Device.device_id == str(device_id))
                .with_for_update()
            )
            .scalars()
            .first()
        )
        if device is None:
            return None, IngestResult(
                accepted=False,
                outcome="REJECTED",
                reason_code="UNKNOWN_DEVICE",
                message=f"Thiết bị '{device_id}' không tồn tại trong hệ thống.",
                device_id=device_id,
            )

        if device.lifecycle_state not in ("ACTIVE", "MAINTENANCE"):
            return None, IngestResult(
                accepted=False,
                outcome="REJECTED",
                reason_code="DEVICE_LIFECYCLE_DENIED",
                message=(
                    f"Thiết bị '{device_id}' đang ở trạng thái '{device.lifecycle_state}', "
                    "từ chối ingest dữ liệu."
                ),
                device_id=device_id,
            )

        return device, None

    @staticmethod
    def _verify_device_config_pair(
        device_id: str,
        config_version: int | None,
        config_hash: str | None,
        db_session: Session,
    ) -> DeviceConfig | None:
        """Kiểm tra cặp (config_version, config_hash) khớp với bản ghi trong bảng device_configs (Mục 8.2)."""
        if config_version is None and config_hash is None:
            return None

        cfg = (
            db_session.execute(
                select(DeviceConfig).where(
                    DeviceConfig.device_id == str(device_id),
                    DeviceConfig.version == int(config_version),  # type: ignore[arg-type]
                )
            )
            .scalars()
            .first()
        )
        if cfg is None or (cfg.content_hash or "").lower() != (config_hash or "").lower():
            raise MqttValidationError(
                "CONFIG_MISMATCH",
                f"Cấu hình báo cáo (version={config_version}, hash={config_hash}) không khớp với DB.",
            )
        return cfg

    @staticmethod
    def _is_boot_already_superseded(
        device: Device, candidate_boot_id: uuid.UUID, db_session: Session
    ) -> bool:
        """Kiểm tra xem boot_id khác current_boot_id có phải là gói tin trễ từ lần boot cũ hay không."""
        if device.current_boot_id is None or candidate_boot_id == device.current_boot_id:
            return False

        seen_in_telemetry = (
            db_session.execute(
                select(Telemetry.id)
                .where(
                    Telemetry.device_id == device.device_id,
                    Telemetry.boot_id == candidate_boot_id,
                )
                .limit(1)
            )
            .scalars()
            .first()
        )
        if seen_in_telemetry is not None:
            return True

        seen_in_events = (
            db_session.execute(
                select(DeviceEvent.id)
                .where(
                    DeviceEvent.device_id == device.device_id,
                    DeviceEvent.boot_id == candidate_boot_id,
                )
                .limit(1)
            )
            .scalars()
            .first()
        )
        return seen_in_events is not None

    @staticmethod
    def _insert_device_event_if_new(
        *,
        device_id: str,
        event_type: str,
        source: str,
        boot_id: uuid.UUID | None,
        connection_id: uuid.UUID | None,
        seq: int | None,
        occurred_at: datetime | None,
        received_at: datetime,
        details: dict[str, Any],
        dedup_key: str | None,
        db_session: Session,
    ) -> None:
        if dedup_key is not None:
            existing = (
                db_session.execute(
                    select(DeviceEvent.id).where(
                        DeviceEvent.device_id == device_id,
                        DeviceEvent.dedup_key == dedup_key,
                    )
                )
                .scalars()
                .first()
            )
            if existing is not None:
                return

        ev = DeviceEvent(
            id=uuid.uuid4(),
            device_id=device_id,
            event_type=event_type,
            boot_id=boot_id,
            connection_id=connection_id,
            seq=seq,
            occurred_at=occurred_at,
            received_at=received_at,
            source=source,
            details=details,
            dedup_key=dedup_key,
        )
        db_session.add(ev)

    @staticmethod
    def _recompute_shadow_drift(device: Device, db_session: Session) -> str:
        if device.desired_config_version is None:
            return "UNKNOWN"
        if device.applied_config_version != device.desired_config_version:
            return "PENDING"
        active_cmd = (
            db_session.execute(
                select(Command.command_id)
                .where(
                    Command.device_id == device.device_id,
                    Command.status.in_(("PENDING", "SENT")),
                )
                .limit(1)
            )
            .scalars()
            .first()
        )
        if active_cmd is not None:
            return "PENDING"
        return "IN_SYNC"

    @staticmethod
    def _maybe_enqueue_config_resync(
        device: Device,
        *,
        now_utc: datetime,
        db_session: Session,
    ) -> Command | None:
        """Tự động tạo lệnh SYSTEM APPLY_CONFIG khi thiết bị ACTIVE kết nối lại mà
        applied_config_version lệch với desired_config_version (Mục 7.2 & 8.4),
        trừ khi cấu hình đó đang bị chặn bởi BLOCKED_AUTHORIZATION.
        """
        if device.lifecycle_state != "ACTIVE":
            return None
        if device.desired_config_version is None:
            return None
        if device.applied_config_version == device.desired_config_version:
            return None

        # Kiểm tra đã có lệnh APPLY_CONFIG đang chờ gửi/ACK chưa
        active_cfg_cmd = (
            db_session.execute(
                select(Command)
                .where(
                    Command.device_id == device.device_id,
                    Command.type == "APPLY_CONFIG",
                    Command.status.in_(("PENDING", "SENT")),
                )
                .limit(1)
            )
            .scalars()
            .first()
        )
        if active_cfg_cmd is not None:
            return None

        # Kiểm tra xem version hiện tại có bị hủy do BLOCKED_AUTHORIZATION không (Mục 7.2)
        last_ver_cmd = (
            db_session.execute(
                select(Command)
                .where(
                    Command.device_id == device.device_id,
                    Command.type == "APPLY_CONFIG",
                    Command.config_version == device.desired_config_version,
                )
                .order_by(Command.requested_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if last_ver_cmd is not None and last_ver_cmd.error_code == "BLOCKED_AUTHORIZATION":
            return None

        desired_cfg = (
            db_session.execute(
                select(DeviceConfig).where(
                    DeviceConfig.device_id == device.device_id,
                    DeviceConfig.version == device.desired_config_version,
                )
            )
            .scalars()
            .first()
        )
        if desired_cfg is None:
            return None

        next_cmd_seq = int(device.last_command_seq or 0) + 1
        device.last_command_seq = next_cmd_seq

        resync_cmd = Command(
            command_id=uuid.uuid4(),
            device_id=device.device_id,
            requested_by=None,
            source="SYSTEM",
            requesting_session_id=None,
            type="APPLY_CONFIG",
            params={
                "config_version": desired_cfg.version,
                "config_json": desired_cfg.canonical_json,
                "content_hash": desired_cfg.content_hash,
            },
            config_version=desired_cfg.version,
            expected_boot_id=device.current_boot_id,
            expected_connection_id=device.current_connection_id,
            device_command_seq=next_cmd_seq,
            idempotency_key=None,
            request_hash=desired_cfg.content_hash,
            status="PENDING",
            requested_at=now_utc,
            expires_at=now_utc + timedelta(seconds=15),
            attempts=0,
        )
        db_session.add(resync_cmd)
        db_session.flush()

        OutboxService.enqueue_command(resync_cmd, db_session=db_session, available_at=now_utc)
        return resync_cmd

    @classmethod
    def ingest_message(
        cls,
        topic: str,
        raw_payload: bytes | str,
        *,
        is_retained: bool = False,
        received_at: datetime | None = None,
        db_session: Session | None = None,
        commit: bool = True,
    ) -> IngestResult:
        """Điểm vào thống nhất cho mọi thông điệp MQTT từ Mosquitto."""
        sess = db_session if db_session is not None else db.session
        now_utc = received_at if received_at is not None else datetime.now(timezone.utc)

        parsed_topic = parse_incoming_topic(topic)
        if parsed_topic is None:
            return IngestResult(
                accepted=False,
                outcome="REJECTED",
                reason_code="INVALID_TOPIC",
                message=f"MQTT topic không hợp lệ: {topic}",
            )

        try:
            payload = parse_strict_json(raw_payload)
        except MqttValidationError as exc:
            return IngestResult(
                accepted=False,
                outcome="REJECTED",
                reason_code=exc.code,
                message=exc.message,
                device_id=parsed_topic.device_id,
                channel=parsed_topic.channel,
            )

        try:
            if parsed_topic.channel == "telemetry":
                result = cls.handle_telemetry(
                    parsed_topic.device_id,
                    payload,
                    received_at=now_utc,
                    db_session=sess,
                )
            elif parsed_topic.channel == "status":
                result = cls.handle_status(
                    parsed_topic.device_id,
                    payload,
                    is_retained=is_retained,
                    received_at=now_utc,
                    db_session=sess,
                )
            elif parsed_topic.channel == "acks":
                result = cls.handle_ack(
                    parsed_topic.device_id,
                    payload,
                    received_at=now_utc,
                    db_session=sess,
                )
            else:
                return IngestResult(
                    accepted=False,
                    outcome="REJECTED",
                    reason_code="UNSUPPORTED_CHANNEL",
                    message=f"Channel không được hỗ trợ: {parsed_topic.channel}",
                    device_id=parsed_topic.device_id,
                    channel=parsed_topic.channel,
                )

            if commit and result.accepted:
                sess.commit()
            return result
        except MqttValidationError as exc:
            return IngestResult(
                accepted=False,
                outcome="REJECTED",
                reason_code=exc.code,
                message=exc.message,
                device_id=parsed_topic.device_id,
                channel=parsed_topic.channel,
            )
        except IntegrityError as exc:
            sess.rollback()
            return IngestResult(
                accepted=False,
                outcome="REJECTED",
                reason_code="DB_INTEGRITY_ERROR",
                message=f"Lỗi toàn vẹn dữ liệu khi ingest: {exc}",
                device_id=parsed_topic.device_id,
                channel=parsed_topic.channel,
            )

    @classmethod
    def handle_telemetry(
        cls,
        device_id: str,
        payload: dict[str, Any],
        *,
        received_at: datetime,
        db_session: Session,
    ) -> IngestResult:
        """Xử lý bản tin /telemetry (Mục 8.1, 8.2, DATA-01, DATA-02)."""
        data = validate_telemetry_payload(
            device_id,
            payload,
            fan_monitor_enabled=cls._is_fan_monitor_enabled(),
        )

        device, err = cls._load_and_verify_device(device_id, db_session)
        if err is not None or device is None:
            err.channel = "telemetry"  # type: ignore[union-attr]
            return err  # type: ignore[return-value]

        if device.device_type != data["profile"]:
            raise MqttValidationError(
                "PROFILE_MISMATCH",
                f"Profile '{data['profile']}' không khớp với device_type '{device.device_type}' trong DB.",
            )

        cls._verify_device_config_pair(
            device_id,
            data["config_version"],
            data["config_hash"],
            db_session,
        )

        # Kiểm tra trùng lặp (device_id, boot_id, seq) (DATA-02)
        existing_row = (
            db_session.execute(
                select(Telemetry).where(
                    Telemetry.device_id == device_id,
                    Telemetry.boot_id == data["boot_id"],
                    Telemetry.seq == data["seq"],
                )
            )
            .scalars()
            .first()
        )
        if existing_row is not None:
            if existing_row.raw_payload_hash == data["raw_payload_hash"]:
                return IngestResult(
                    accepted=True,
                    outcome="DUPLICATE_IGNORED",
                    reason_code="DUPLICATE_TELEMETRY",
                    message="Bản tin telemetry trùng lặp idempotent, đã bỏ qua.",
                    device_id=device_id,
                    channel="telemetry",
                    telemetry_id=existing_row.id,
                )
            return IngestResult(
                accepted=False,
                outcome="REJECTED",
                reason_code="DUPLICATE_SEQ_HASH_MISMATCH",
                message="Trùng (device_id, boot_id, seq) nhưng khác raw_payload_hash, từ chối ghi đè lịch sử.",
                device_id=device_id,
                channel="telemetry",
            )

        # Xác định bản tin có phải là live current mới nhất để cập nhật latest/shadow hay không
        is_backfill = bool(data["is_backfill"])
        is_new_boot = False
        is_live_latest = False

        if not is_backfill:
            if device.current_boot_id is None:
                is_new_boot = True
                is_live_latest = True
            elif data["boot_id"] == device.current_boot_id:
                if device.latest_seq is None or data["seq"] > int(device.latest_seq):
                    is_live_latest = True
                else:
                    # Gói tin đến muộn (out-of-order seq < latest_seq) trong cùng boot_id:
                    # Lưu lịch sử nhưng không lùi latest/shadow (DATA-02)
                    is_live_latest = False
            else:
                # Khác current_boot_id: kiểm tra xem boot_id này đã từng xuất hiện trước đây chưa
                if cls._is_boot_already_superseded(device, data["boot_id"], db_session):
                    is_live_latest = False
                else:
                    is_new_boot = True
                    is_live_latest = True

        telemetry_row = Telemetry(
            id=uuid.uuid4(),
            device_id=device_id,
            profile=data["profile"],
            fan_binding_id=data["fan_binding_id"],
            boot_id=data["boot_id"],
            connection_id=data["connection_id"],
            seq=data["seq"],
            schema_version=3,
            measured_at=data["measured_at"],
            received_at=received_at,
            uptime_ms=data["uptime_ms"],
            clock_synced=data["clock_synced"],
            temperature_c=data["temperature_c"],
            humidity_pct=data["humidity_pct"],
            sensor_ok=data["sensor_ok"],
            fan_output=data["fan_output"],
            mode=data["mode"],
            rpm=data["rpm"],
            rpm_min=data["rpm_min"],
            rpm_max=data["rpm_max"],
            pulse_count=data["pulse_count"],
            rpm_window_ms=data["rpm_window_ms"],
            pulses_per_revolution=data["pulses_per_revolution"],
            demand_on=data["demand_on"],
            demand_valid=data["demand_valid"],
            controller_signal_age_ms=data["controller_signal_age_ms"],
            rpm_sensor_status=data["rpm_sensor_status"],
            fan_state=data["fan_state"],
            config_version=data["config_version"],
            config_hash=data["config_hash"],
            fault_code=data["fault_code"],
            extra=data["extra"],
            is_backfill=is_backfill,
            raw_payload_hash=data["raw_payload_hash"],
        )
        db_session.add(telemetry_row)

        if is_live_latest:
            if is_new_boot:
                cls._insert_device_event_if_new(
                    device_id=device_id,
                    event_type="BOOT",
                    source="DEVICE",
                    boot_id=data["boot_id"],
                    connection_id=data["connection_id"],
                    seq=data["seq"],
                    occurred_at=data["measured_at"] or received_at,
                    received_at=received_at,
                    details={"uptime_ms": data["uptime_ms"]},
                    dedup_key=f"boot:{data['boot_id']}",
                    db_session=db_session,
                )
                device.current_boot_id = data["boot_id"]
                device.latest_status_seq = None

            device.current_connection_id = data["connection_id"]
            device.latest_seq = data["seq"]
            device.last_seen_at = received_at
            device.last_telemetry_at = received_at

            if device.connection_state != "ONLINE":
                device.connection_state = "ONLINE"
                cls._insert_device_event_if_new(
                    device_id=device_id,
                    event_type="ONLINE",
                    source="DEVICE",
                    boot_id=data["boot_id"],
                    connection_id=data["connection_id"],
                    seq=data["seq"],
                    occurred_at=data["measured_at"] or received_at,
                    received_at=received_at,
                    details={"via": "telemetry"},
                    dedup_key=f"online:{data['connection_id']}",
                    db_session=db_session,
                )

            if data["profile"] == "CONTROLLER":
                new_health = (
                    "FAULT"
                    if (data["sensor_ok"] is False or data["fault_code"] is not None)
                    else "OK"
                )
            else:
                new_health = (
                    "FAULT"
                    if (data["rpm_sensor_status"] == "FAULT" or data["fault_code"] is not None)
                    else "OK"
                )

            if device.health_state != new_health:
                device.health_state = new_health
                if new_health == "FAULT":
                    cls._insert_device_event_if_new(
                        device_id=device_id,
                        event_type="FAULT",
                        source="DEVICE",
                        boot_id=data["boot_id"],
                        connection_id=data["connection_id"],
                        seq=data["seq"],
                        occurred_at=data["measured_at"] or received_at,
                        received_at=received_at,
                        details={
                            "fault_code": data["fault_code"],
                            "sensor_ok": data["sensor_ok"],
                        },
                        dedup_key=f"fault:{data['boot_id']}:{data['seq']}",
                        db_session=db_session,
                    )

            if data["config_version"] is not None:
                device.applied_config_version = data["config_version"]

            device.updated_at = received_at

            # Cập nhật DeviceShadow.reported
            shadow = (
                db_session.execute(
                    select(DeviceShadow)
                    .where(DeviceShadow.device_id == device_id)
                    .with_for_update()
                )
                .scalars()
                .first()
            )
            if shadow is None:
                shadow = DeviceShadow(
                    device_id=device_id,
                    desired={},
                    reported={},
                    desired_version=device.desired_config_version or 0,
                    reported_version=0,
                    drift_status="UNKNOWN",
                    updated_at=received_at,
                )
                db_session.add(shadow)

            reported_dict = dict(shadow.reported) if isinstance(shadow.reported, dict) else {}
            if data["profile"] == "CONTROLLER":
                reported_dict.update({
                    "temperature_c": data["temperature_c"],
                    "humidity_pct": data["humidity_pct"],
                    "sensor_ok": data["sensor_ok"],
                    "fan_output": data["fan_output"],
                    "mode": data["mode"],
                    "fault_code": data["fault_code"],
                    "config_version": data["config_version"],
                    "config_hash": data["config_hash"],
                    "clock_synced": data["clock_synced"],
                    "uptime_ms": data["uptime_ms"],
                    "measured_at": format_utc_iso(data["measured_at"]),
                })
            else:
                reported_dict.update({
                    "rpm": data["rpm"],
                    "rpm_min": data["rpm_min"],
                    "rpm_max": data["rpm_max"],
                    "pulse_count": data["pulse_count"],
                    "rpm_window_ms": data["rpm_window_ms"],
                    "pulses_per_revolution": data["pulses_per_revolution"],
                    "demand_on": data["demand_on"],
                    "demand_valid": data["demand_valid"],
                    "controller_signal_age_ms": data["controller_signal_age_ms"],
                    "rpm_sensor_status": data["rpm_sensor_status"],
                    "fan_state": data["fan_state"],
                    "mode": data["mode"],
                    "fault_code": data["fault_code"],
                    "config_version": data["config_version"],
                    "config_hash": data["config_hash"],
                    "clock_synced": data["clock_synced"],
                    "uptime_ms": data["uptime_ms"],
                    "measured_at": format_utc_iso(data["measured_at"]),
                })

            shadow.reported = reported_dict
            shadow.reported_version = int(shadow.reported_version or 0) + 1
            shadow.reported_at = received_at
            shadow.reported_boot_id = data["boot_id"]
            shadow.reported_seq = data["seq"]
            shadow.drift_status = cls._recompute_shadow_drift(device, db_session)
            shadow.updated_at = received_at

        db_session.flush()

        # Gọi hook cảnh báo (nếu TV4 đã đăng ký) khi là mẫu live mới nhất
        if is_live_latest:
            for hook in cls._telemetry_hooks:
                hook(device, telemetry_row, db_session)

        return IngestResult(
            accepted=True,
            outcome="INGESTED" if is_live_latest else "INGESTED_HISTORICAL",
            device_id=device_id,
            channel="telemetry",
            telemetry_id=telemetry_row.id,
        )

    @classmethod
    def handle_status(
        cls,
        device_id: str,
        payload: dict[str, Any],
        *,
        is_retained: bool,
        received_at: datetime,
        db_session: Session,
    ) -> IngestResult:
        """Xử lý bản tin /status (ONLINE heartbeat và OFFLINE LWT - Mục 8.3 & DATA-02)."""
        data = validate_status_payload(
            device_id,
            payload,
            fan_monitor_enabled=cls._is_fan_monitor_enabled(),
        )

        device, err = cls._load_and_verify_device(device_id, db_session)
        if err is not None or device is None:
            err.channel = "status"  # type: ignore[union-attr]
            return err  # type: ignore[return-value]

        if data["connection"] == "OFFLINE":
            # Mục 8.3 & DATA-02: Chỉ cập nhật OFFLINE khi khớp đúng current_connection_id
            if (
                device.current_connection_id is None
                or data["connection_id"] != device.current_connection_id
            ):
                return IngestResult(
                    accepted=True,
                    outcome="STALE_LWT_IGNORED",
                    reason_code="OLD_CONNECTION_LWT",
                    message="Bỏ qua LWT của phiên kết nối cũ, không làm offline phiên mới.",
                    device_id=device_id,
                    channel="status",
                )

            if device.connection_state != "OFFLINE":
                device.connection_state = "OFFLINE"
                device.updated_at = received_at
                cls._insert_device_event_if_new(
                    device_id=device_id,
                    event_type="OFFLINE",
                    source="DEVICE",
                    boot_id=data["boot_id"],
                    connection_id=data["connection_id"],
                    seq=data.get("status_seq"),
                    occurred_at=received_at,
                    received_at=received_at,
                    details={"reason": data.get("reason", "LWT")},
                    dedup_key=f"offline:{data['connection_id']}",
                    db_session=db_session,
                )
            db_session.flush()
            return IngestResult(
                accepted=True,
                outcome="OFFLINE_RECORDED",
                device_id=device_id,
                channel="status",
            )

        # connection == "ONLINE"
        if device.device_type != data["profile"]:
            raise MqttValidationError(
                "PROFILE_MISMATCH",
                f"Profile '{data['profile']}' không khớp với device_type '{device.device_type}'.",
            )

        cls._verify_device_config_pair(
            device_id,
            data["config_version"],
            data["config_hash"],
            db_session,
        )

        # Mục 8.2, 8.3 & DATA-02: Retained ONLINE không gia hạn last_seen / không là heartbeat mới
        if is_retained:
            return IngestResult(
                accepted=True,
                outcome="RETAINED_ONLINE_IGNORED",
                reason_code="RETAINED_STATUS_NO_HEARTBEAT",
                message="Bản tin ONLINE retained chỉ dùng tham khảo, không gia hạn last_seen_at.",
                device_id=device_id,
                channel="status",
            )

        if (
            device.current_boot_id == data["boot_id"]
            and device.current_connection_id == data["connection_id"]
            and device.latest_status_seq is not None
            and data["status_seq"] <= int(device.latest_status_seq)
        ):
            return IngestResult(
                accepted=True,
                outcome="DUPLICATE_STATUS_IGNORED",
                reason_code="STALE_STATUS_SEQ",
                message="status_seq không mới hơn latest_status_seq, đã bỏ qua.",
                device_id=device_id,
                channel="status",
            )

        if (
            device.current_boot_id is not None
            and data["boot_id"] != device.current_boot_id
            and cls._is_boot_already_superseded(device, data["boot_id"], db_session)
        ):
            return IngestResult(
                accepted=True,
                outcome="STALE_STATUS_IGNORED",
                reason_code="OLD_BOOT_STATUS",
                message="Bỏ qua bản tin status từ phiên boot cũ.",
                device_id=device_id,
                channel="status",
            )

        is_new_boot = device.current_boot_id != data["boot_id"]
        if is_new_boot:
            cls._insert_device_event_if_new(
                device_id=device_id,
                event_type="BOOT",
                source="DEVICE",
                boot_id=data["boot_id"],
                connection_id=data["connection_id"],
                seq=data["status_seq"],
                occurred_at=received_at,
                received_at=received_at,
                details={"uptime_ms": data["uptime_ms"]},
                dedup_key=f"boot:{data['boot_id']}",
                db_session=db_session,
            )

        was_offline_or_reconnected = (
            device.connection_state != "ONLINE"
            or device.current_connection_id != data["connection_id"]
        )

        device.current_boot_id = data["boot_id"]
        device.current_connection_id = data["connection_id"]
        device.latest_status_seq = data["status_seq"]
        device.last_seen_at = received_at
        device.health_state = data["health"]

        if data["firmware_version"]:
            device.firmware_version = data["firmware_version"]
        if data["hardware_version"]:
            device.hardware_version = data["hardware_version"]
        if data["config_version"] is not None:
            device.applied_config_version = data["config_version"]

        if was_offline_or_reconnected:
            device.connection_state = "ONLINE"
            cls._insert_device_event_if_new(
                device_id=device_id,
                event_type="ONLINE",
                source="DEVICE",
                boot_id=data["boot_id"],
                connection_id=data["connection_id"],
                seq=data["status_seq"],
                occurred_at=received_at,
                received_at=received_at,
                details={"via": "status"},
                dedup_key=f"online:{data['connection_id']}",
                db_session=db_session,
            )

        device.updated_at = received_at

        # Nếu thiết bị vừa kết nối/khởi động lại mà cấu hình applied khác desired -> tự động re-sync
        cls._maybe_enqueue_config_resync(device, now_utc=received_at, db_session=db_session)

        shadow = (
            db_session.execute(
                select(DeviceShadow)
                .where(DeviceShadow.device_id == device_id)
                .with_for_update()
            )
            .scalars()
            .first()
        )
        if shadow is not None:
            shadow.drift_status = cls._recompute_shadow_drift(device, db_session)
            shadow.updated_at = received_at

        db_session.flush()
        return IngestResult(
            accepted=True,
            outcome="STATUS_UPDATED",
            device_id=device_id,
            channel="status",
        )

    @classmethod
    def handle_ack(
        cls,
        device_id: str,
        payload: dict[str, Any],
        *,
        received_at: datetime,
        db_session: Session,
    ) -> IngestResult:
        """Xử lý bản tin /acks xác nhận lệnh từ thiết bị (Mục 8.4, CMD-01, CMD-02)."""
        data = validate_ack_payload(device_id, payload)

        device, err = cls._load_and_verify_device(device_id, db_session)
        if err is not None or device is None:
            err.channel = "acks"  # type: ignore[union-attr]
            return err  # type: ignore[return-value]

        command = (
            db_session.execute(
                select(Command)
                .where(Command.command_id == data["command_id"])
                .with_for_update()
            )
            .scalars()
            .first()
        )
        if command is None:
            return IngestResult(
                accepted=False,
                outcome="REJECTED",
                reason_code="UNKNOWN_COMMAND",
                message=f"Không tìm thấy command_id '{data['command_id']}'.",
                device_id=device_id,
                channel="acks",
                command_id=data["command_id"],
            )

        # Chống Cross-device ACK (CMD-02)
        if command.device_id != device_id:
            return IngestResult(
                accepted=False,
                outcome="REJECTED",
                reason_code="CROSS_DEVICE_ACK",
                message=(
                    f"Command '{command.command_id}' thuộc thiết bị '{command.device_id}', "
                    f"không thuộc '{device_id}'."
                ),
                device_id=device_id,
                channel="acks",
                command_id=command.command_id,
            )

        if command.type != data["type"]:
            return IngestResult(
                accepted=False,
                outcome="REJECTED",
                reason_code="ACK_TYPE_MISMATCH",
                message=f"Loại lệnh ACK ('{data['type']}') không khớp với lệnh gốc ('{command.type}').",
                device_id=device_id,
                channel="acks",
                command_id=command.command_id,
            )

        if int(command.device_command_seq) != int(data["device_command_seq"]):
            return IngestResult(
                accepted=False,
                outcome="REJECTED",
                reason_code="ACK_SEQ_MISMATCH",
                message=(
                    f"device_command_seq trong ACK ({data['device_command_seq']}) "
                    f"không khớp với lệnh gốc ({command.device_command_seq})."
                ),
                device_id=device_id,
                channel="acks",
                command_id=command.command_id,
            )

        # Kiểm tra expected_boot_id & expected_connection_id (CMD-02)
        if command.expected_boot_id is not None and data["boot_id"] != command.expected_boot_id:
            return IngestResult(
                accepted=False,
                outcome="REJECTED",
                reason_code="ACK_BOOT_MISMATCH",
                message="boot_id trong ACK không khớp với expected_boot_id của lệnh.",
                device_id=device_id,
                channel="acks",
                command_id=command.command_id,
            )

        if (
            command.expected_connection_id is not None
            and data["connection_id"] != command.expected_connection_id
        ):
            return IngestResult(
                accepted=False,
                outcome="REJECTED",
                reason_code="ACK_CONNECTION_MISMATCH",
                message="connection_id trong ACK không khớp với expected_connection_id của lệnh.",
                device_id=device_id,
                channel="acks",
                command_id=command.command_id,
            )

        # Duplicate ACK idempotency (CMD-01)
        if command.status in ("APPLIED", "APPLIED_LATE", "REJECTED"):
            return IngestResult(
                accepted=True,
                outcome="DUPLICATE_ACK_IGNORED",
                reason_code="DUPLICATE_ACK",
                message="ACK trùng lặp cho lệnh đã hoàn tất, bỏ qua idempotent.",
                device_id=device_id,
                channel="acks",
                command_id=command.command_id,
            )

        if command.status in ("CANCELLED", "FAILED"):
            return IngestResult(
                accepted=False,
                outcome="REJECTED",
                reason_code="COMMAND_ALREADY_TERMINAL",
                message=f"Lệnh đang ở trạng thái kết thúc '{command.status}', không nhận ACK.",
                device_id=device_id,
                channel="acks",
                command_id=command.command_id,
            )

        res_dict = data["result"] if isinstance(data["result"], dict) else {}

        # Nếu result trong ACK có khai báo config_version/config_hash thì xác thực khớp DB
        if res_dict.get("config_version") is not None or res_dict.get("config_hash") is not None:
            ack_cfg_ver, ack_cfg_hash = (
                res_dict.get("config_version"),
                res_dict.get("config_hash"),
            )
            if not isinstance(ack_cfg_ver, int) or not isinstance(ack_cfg_hash, str):
                raise MqttValidationError(
                    "INVALID_ACK_RESULT_CONFIG",
                    "config_version và config_hash trong result của ACK không hợp lệ.",
                )
            cls._verify_device_config_pair(device_id, ack_cfg_ver, ack_cfg_hash, db_session)

        if data["status"] == "REJECTED":
            final_status = "REJECTED"
            command.error_code = data["error_code"] or "DEVICE_REJECTED"
        else:
            # data["status"] == "APPLIED"
            if command.status == "TIMEOUT" or received_at > command.expires_at:
                final_status = "APPLIED_LATE"
            else:
                final_status = "APPLIED"
            command.error_code = None

        command.status = final_status
        command.ack_at = received_at
        command.executed_at = data["executed_at"] or received_at
        command.result = res_dict

        # Cập nhật CommandAttempt gần nhất
        latest_attempt = (
            db_session.execute(
                select(CommandAttempt)
                .where(CommandAttempt.command_id == command.command_id)
                .order_by(CommandAttempt.attempt_no.desc())
                .limit(1)
                .with_for_update()
            )
            .scalars()
            .first()
        )
        if latest_attempt is not None:
            latest_attempt.ack_received_at = received_at
            latest_attempt.outcome = final_status
            latest_attempt.error_code = command.error_code
        else:
            attempt_no = max(1, int(command.attempts or 1))
            command.attempts = attempt_no
            db_session.add(
                CommandAttempt(
                    id=uuid.uuid4(),
                    command_id=command.command_id,
                    attempt_no=attempt_no,
                    dispatch_started_at=command.sent_at or received_at,
                    broker_confirmed_at=command.sent_at or received_at,
                    ack_received_at=received_at,
                    outcome=final_status,
                    error_code=command.error_code,
                    worker_id="ack-ingest",
                )
            )

        # Đảm bảo OutboxMessage không còn ở trạng thái chờ gửi/retry
        outbox = (
            db_session.execute(
                select(OutboxMessage)
                .where(OutboxMessage.command_id == command.command_id)
                .with_for_update()
            )
            .scalars()
            .first()
        )
        if outbox is not None and outbox.status in ("PENDING", "LEASED"):
            outbox.status = "PUBLISHED"
            outbox.lease_until = None
            outbox.locked_by = None

        device.last_seen_at = received_at
        if final_status in ("APPLIED", "APPLIED_LATE"):
            if command.type == "APPLY_CONFIG" and command.config_version is not None:
                device.applied_config_version = int(command.config_version)
            elif res_dict.get("config_version") is not None:
                device.applied_config_version = int(res_dict["config_version"])

            shadow = (
                db_session.execute(
                    select(DeviceShadow)
                    .where(DeviceShadow.device_id == device_id)
                    .with_for_update()
                )
                .scalars()
                .first()
            )
            if shadow is None:
                shadow = DeviceShadow(
                    device_id=device_id,
                    desired={},
                    reported={},
                    desired_version=device.desired_config_version or 0,
                    reported_version=0,
                    drift_status="UNKNOWN",
                    updated_at=received_at,
                )
                db_session.add(shadow)

            reported_dict = dict(shadow.reported) if isinstance(shadow.reported, dict) else {}
            if "mode" in res_dict and res_dict["mode"] is not None:
                reported_dict["mode"] = res_dict["mode"]
            elif command.type == "SET_MODE" and isinstance(command.params, dict):
                reported_dict["mode"] = command.params.get("mode")

            if "fan_output" in res_dict and res_dict["fan_output"] is not None:
                reported_dict["fan_output"] = bool(res_dict["fan_output"])
            elif command.type == "SET_FAN" and isinstance(command.params, dict):
                reported_dict["fan_output"] = bool(command.params.get("on"))

            if "config_version" in res_dict and res_dict["config_version"] is not None:
                reported_dict["config_version"] = res_dict["config_version"]
            elif command.type == "APPLY_CONFIG" and command.config_version is not None:
                reported_dict["config_version"] = command.config_version

            if "config_hash" in res_dict and res_dict["config_hash"] is not None:
                reported_dict["config_hash"] = res_dict["config_hash"]

            shadow.reported = reported_dict
            shadow.reported_version = int(shadow.reported_version or 0) + 1
            shadow.reported_at = received_at
            shadow.drift_status = cls._recompute_shadow_drift(device, db_session)
            shadow.updated_at = received_at

        device.updated_at = received_at
        db_session.flush()

        return IngestResult(
            accepted=True,
            outcome=final_status,
            device_id=device_id,
            channel="acks",
            command_id=command.command_id,
        )

