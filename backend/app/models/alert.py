import uuid
from sqlalchemy import (
    Column, String, Text, Boolean, Integer,
    ForeignKey, CheckConstraint, UniqueConstraint, Index, text, func
)
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB, REAL
from backend.app.extensions import db

class AlertRule(db.Model):
    """Bảng alert_rules: Định nghĩa quy tắc kích hoạt cảnh báo (Mục 6.5)"""
    __tablename__ = "alert_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(64), ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False)
    code = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    severity = Column(String(20), nullable=False, default="WARNING")
    condition_source = Column(String(32), nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("severity IN ('INFO', 'WARNING', 'CRITICAL')", name="ck_alert_rules_severity"),
        CheckConstraint(
            "condition_source IN ('APPLIED_CONFIG', 'CONNECTION', 'HEALTH', 'FAN_MONITOR')",
            name="ck_alert_rules_condition_source"
        ),
        UniqueConstraint("device_id", "code", name="uq_alert_rules_device_code"),
        UniqueConstraint("device_id", "id", name="uq_alert_rules_device_id_composite"),
    )


class Alert(db.Model):
    """Bảng alerts: Các sự cố cảnh báo đang mở hoặc đã xử lý (Mục 6.5)"""
    __tablename__ = "alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(64), ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False)
    rule_id = Column(UUID(as_uuid=True), ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(64), nullable=False)
    severity = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="OPEN")
    opened_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    last_seen_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    cleared_at = Column(TIMESTAMP(timezone=True), nullable=True)
    acknowledged_at = Column(TIMESTAMP(timezone=True), nullable=True)
    acknowledged_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    resolved_at = Column(TIMESTAMP(timezone=True), nullable=True)
    resolved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    observed_value = Column(REAL, nullable=True)
    threshold = Column(REAL, nullable=True)
    message = Column(Text, nullable=False)
    config_version = Column(Integer, nullable=True)
    fan_id = Column(UUID(as_uuid=True), nullable=True)            # P1 FK fans
    fan_binding_id = Column(UUID(as_uuid=True), nullable=True)    # P1 FK fan_node_bindings
    evaluation_quality = Column(String(20), nullable=False, default="VALID")
    evidence = Column(JSONB, nullable=False, default=dict)
    condition_active = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        CheckConstraint("severity IN ('INFO', 'WARNING', 'CRITICAL')", name="ck_alerts_severity"),
        CheckConstraint("status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')", name="ck_alerts_status"),
        CheckConstraint("evaluation_quality IN ('VALID', 'UNKNOWN', 'STALE')", name="ck_alerts_evaluation_quality"),
        Index(
            "uq_live_alert",
            "device_id", "rule_id",
            unique=True,
            postgresql_where=text("status <> 'RESOLVED'")
        ),
        Index("ix_alerts_device_opened", "device_id", text("opened_at DESC")),
        Index("ix_alerts_status_opened", "status", "opened_at"),
    )


class AlertEvent(db.Model):
    """Bảng alert_events: Lịch sử các bước xử lý sự cố cảnh báo (Mục 6.5)"""
    __tablename__ = "alert_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_id = Column(UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(32), nullable=False)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    actor_type = Column(String(20), nullable=False)
    occurred_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    note = Column(Text, nullable=True)
    evidence = Column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('OPENED', 'UPDATED', 'ACKNOWLEDGED', 'CLEARED', 'REACTIVATED', 'RESOLVED')",
            name="ck_alert_events_type"
        ),
        CheckConstraint("actor_type IN ('USER', 'SYSTEM')", name="ck_alert_events_actor_type"),
        Index("ix_alert_events_alert_occurred", "alert_id", "occurred_at"),
    )
