import uuid
from sqlalchemy import (
    Column, String, Text, Boolean, Integer,
    ForeignKey, CheckConstraint, UniqueConstraint, Index, text, func
)
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB, BIGINT
from backend.app.extensions import db

class Command(db.Model):
    """Bảng commands: Lịch sử và trạng thái lệnh điều khiển thiết bị (Mục 6.4)"""
    __tablename__ = "commands"

    command_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(64), ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False)
    requested_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    source = Column(String(20), nullable=False)
    requesting_session_id = Column(UUID(as_uuid=True), ForeignKey("auth_sessions.id", ondelete="RESTRICT"), nullable=True)
    type = Column(String(32), nullable=False)
    params = Column(JSONB, nullable=False, default=dict)
    config_version = Column(Integer, nullable=True)
    expected_boot_id = Column(UUID(as_uuid=True), nullable=True)
    expected_connection_id = Column(UUID(as_uuid=True), nullable=True)
    device_command_seq = Column(BIGINT, nullable=False)
    idempotency_key = Column(String(128), nullable=True)
    request_hash = Column(String(64), nullable=True)
    status = Column(String(20), nullable=False, default="PENDING")
    requested_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    sent_at = Column(TIMESTAMP(timezone=True), nullable=True)
    ack_at = Column(TIMESTAMP(timezone=True), nullable=True)
    executed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    result = Column(JSONB, nullable=True)
    error_code = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("source IN ('USER', 'SYSTEM', 'AUTOMATION', 'AI')", name="ck_commands_source"),
        CheckConstraint("type IN ('SET_MODE', 'SET_FAN', 'APPLY_CONFIG')", name="ck_commands_type"),
        CheckConstraint(
            "status IN ('PENDING', 'SENT', 'APPLIED', 'APPLIED_LATE', 'REJECTED', 'TIMEOUT', 'FAILED', 'CANCELLED')",
            name="ck_commands_status"
        ),
        CheckConstraint("expires_at > requested_at", name="ck_commands_expires_after_requested"),
        UniqueConstraint("device_id", "device_command_seq", name="uq_commands_device_command_seq"),
        Index("ix_commands_device_requested", "device_id", text("requested_at DESC")),
        Index(
            "uq_commands_idempotency", 
            "requested_by", "device_id", "idempotency_key", 
            unique=True, 
            postgresql_where=idempotency_key.isnot(None)
        ),
        Index(
            "uq_commands_single_sent_per_device", 
            "device_id", 
            unique=True, 
            postgresql_where=text("status = 'SENT'")
        ),
    )


class CommandAttempt(db.Model):
    """Bảng command_attempts: Nhật ký từng lần thử dispatch/ACK của lệnh (Mục 6.4)"""
    __tablename__ = "command_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    command_id = Column(UUID(as_uuid=True), ForeignKey("commands.command_id", ondelete="CASCADE"), nullable=False)
    attempt_no = Column(Integer, nullable=False)
    dispatch_started_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    broker_confirmed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    ack_received_at = Column(TIMESTAMP(timezone=True), nullable=True)
    outcome = Column(String(32), nullable=True)
    error_code = Column(Text, nullable=True)
    worker_id = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("command_id", "attempt_no", name="uq_command_attempts_command_attempt_no"),
        Index("ix_command_attempts_command_started", "command_id", "dispatch_started_at"),
    )


class OutboxMessage(db.Model):
    """Bảng outbox_messages: Transactional Outbox phục vụ gửi lệnh bền vững sang MQTT (Mục 6.4)"""
    __tablename__ = "outbox_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    command_id = Column(UUID(as_uuid=True), ForeignKey("commands.command_id", ondelete="CASCADE"), unique=True, nullable=False)
    topic = Column(Text, nullable=False)
    payload = Column(JSONB, nullable=False)
    qos = Column(Integer, nullable=False, default=1)
    retained = Column(Boolean, nullable=False, default=False)
    status = Column(String(20), nullable=False, default="PENDING")
    available_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    lease_until = Column(TIMESTAMP(timezone=True), nullable=True)
    locked_by = Column(Text, nullable=True)
    publish_count = Column(Integer, nullable=False, default=0)
    published_at = Column(TIMESTAMP(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'LEASED', 'PUBLISHED', 'FAILED', 'CANCELLED')", name="ck_outbox_messages_status"),
        Index("ix_outbox_messages_status_available", "status", "available_at"),
    )
