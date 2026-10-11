import uuid
from sqlalchemy import (
    Column, String, Text, Boolean, Integer,
    ForeignKey, CheckConstraint, UniqueConstraint, Index, text, func
)
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB, BIGINT, REAL
from backend.app.extensions import db

class Telemetry(db.Model):
    """Bảng telemetry: Lưu trữ dữ liệu đo lường cảm biến theo chu kỳ (Mục 6.4)"""
    __tablename__ = "telemetry"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(64), ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False)
    profile = Column(String(32), nullable=False)
    fan_binding_id = Column(UUID(as_uuid=True), nullable=True)  # P1 FK fan_node_bindings
    boot_id = Column(UUID(as_uuid=True), nullable=False)
    connection_id = Column(UUID(as_uuid=True), nullable=False)
    seq = Column(BIGINT, nullable=False)
    schema_version = Column(Integer, nullable=False, default=3)
    measured_at = Column(TIMESTAMP(timezone=True), nullable=True)
    received_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    uptime_ms = Column(BIGINT, nullable=False)
    clock_synced = Column(Boolean, nullable=False, default=False)
    temperature_c = Column(REAL, nullable=True)
    humidity_pct = Column(REAL, nullable=True)
    sensor_ok = Column(Boolean, nullable=True)
    fan_output = Column(Boolean, nullable=True)
    mode = Column(String(20), nullable=True)
    rpm = Column(REAL, nullable=True)
    rpm_min = Column(REAL, nullable=True)
    rpm_max = Column(REAL, nullable=True)
    pulse_count = Column(BIGINT, nullable=True)
    rpm_window_ms = Column(Integer, nullable=True)
    pulses_per_revolution = Column(Integer, nullable=True)
    demand_on = Column(Boolean, nullable=True)
    demand_valid = Column(Boolean, nullable=True)
    controller_signal_age_ms = Column(BIGINT, nullable=True)
    rpm_sensor_status = Column(String(32), nullable=True)
    fan_state = Column(String(32), nullable=True)
    config_version = Column(Integer, nullable=True)
    config_hash = Column(String(64), nullable=True)
    fault_code = Column(Text, nullable=True)
    extra = Column(JSONB, nullable=False, default=dict)
    is_backfill = Column(Boolean, nullable=False, default=False)
    raw_payload_hash = Column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint("profile IN ('CONTROLLER', 'FAN_MONITOR')", name="ck_telemetry_profile"),
        CheckConstraint("mode IS NULL OR mode IN ('AUTO', 'MANUAL', 'MONITOR')", name="ck_telemetry_mode"),
        UniqueConstraint("device_id", "boot_id", "seq", name="uq_telemetry_device_boot_seq"),
        Index("ix_telemetry_device_received", "device_id", text("received_at DESC")),
        Index("ix_telemetry_device_measured", "device_id", "measured_at", postgresql_where=measured_at.isnot(None)),
    )


class DeviceEvent(db.Model):
    """Bảng device_events: Sự kiện vòng đời & trạng thái quan trọng của thiết bị (Mục 6.4)"""
    __tablename__ = "device_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(64), ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(64), nullable=False)
    boot_id = Column(UUID(as_uuid=True), nullable=True)
    connection_id = Column(UUID(as_uuid=True), nullable=True)
    seq = Column(BIGINT, nullable=True)
    occurred_at = Column(TIMESTAMP(timezone=True), nullable=True)
    received_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    source = Column(String(20), nullable=False)
    details = Column(JSONB, nullable=False, default=dict)
    dedup_key = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("source IN ('DEVICE', 'SYSTEM', 'USER')", name="ck_device_events_source"),
        Index("ix_device_events_device_received", "device_id", text("received_at DESC")),
        Index(
            "uq_device_events_dedup", 
            "device_id", 
            "dedup_key", 
            unique=True, 
            postgresql_where=dedup_key.isnot(None)
        ),
    )


class DeviceShadow(db.Model):
    """Bảng device_shadows: Trạng thái mong muốn (desired) và báo cáo (reported) (Mục 6.4)"""
    __tablename__ = "device_shadows"

    device_id = Column(String(64), ForeignKey("devices.device_id", ondelete="CASCADE"), primary_key=True)
    desired = Column(JSONB, nullable=False, default=dict)
    reported = Column(JSONB, nullable=False, default=dict)
    desired_version = Column(Integer, nullable=False, default=0)
    reported_version = Column(Integer, nullable=False, default=0)
    desired_at = Column(TIMESTAMP(timezone=True), nullable=True)
    reported_at = Column(TIMESTAMP(timezone=True), nullable=True)
    reported_boot_id = Column(UUID(as_uuid=True), nullable=True)
    reported_seq = Column(BIGINT, nullable=True)
    drift_status = Column(String(20), nullable=False, default="UNKNOWN")
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("drift_status IN ('IN_SYNC', 'PENDING', 'STALE', 'UNKNOWN')", name="ck_device_shadows_drift"),
    )
