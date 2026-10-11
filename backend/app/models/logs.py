import uuid
from sqlalchemy import (
    Column, String, Text, Boolean, Integer,
    ForeignKey, CheckConstraint, UniqueConstraint, Index, text, func
)
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB, INET
from backend.app.extensions import db

class AuditLog(db.Model):
    """Bảng audit_logs: Kiểm toán các thao tác bảo mật và quản trị nhạy cảm (Mục 6.5)"""
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    actor_session_id = Column(UUID(as_uuid=True), ForeignKey("auth_sessions.id", ondelete="RESTRICT"), nullable=True)
    actor_type = Column(String(20), nullable=False)
    actor_name_snapshot = Column(Text, nullable=True)
    device_id = Column(String(64), ForeignKey("devices.device_id", ondelete="SET NULL"), nullable=True)
    action = Column(Text, nullable=False)
    target_type = Column(Text, nullable=False)
    target_id = Column(Text, nullable=False)
    success = Column(Boolean, nullable=False)
    reason_code = Column(Text, nullable=True)
    request_id = Column(UUID(as_uuid=True), nullable=False)
    ip_address = Column(INET, nullable=True)
    detail = Column(JSONB, nullable=False, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("actor_type IN ('USER', 'SYSTEM', 'SERVICE')", name="ck_audit_logs_actor_type"),
        Index("ix_audit_logs_actor_created", "actor_user_id", text("created_at DESC")),
        Index("ix_audit_logs_device_created", "device_id", text("created_at DESC")),
        Index("ix_audit_logs_action_created", "action", text("created_at DESC")),
        Index("ix_audit_logs_request_id", "request_id"),
    )


class UserActivityLog(db.Model):
    """Bảng user_activity_logs: Nhật ký làm việc — Ai dùng chức năng gì, kết quả ra sao (Mục 6.5 & 6.10)"""
    __tablename__ = "user_activity_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    actor_session_id = Column(UUID(as_uuid=True), ForeignKey("auth_sessions.id", ondelete="RESTRICT"), nullable=True)
    actor_type = Column(String(20), nullable=False)
    actor_name_snapshot = Column(String(128), nullable=True)
    function_key = Column(String(80), nullable=False)
    action = Column(Text, nullable=False)
    target_type = Column(Text, nullable=True)
    target_id = Column(Text, nullable=True)
    target_name_snapshot = Column(Text, nullable=True)
    device_id = Column(String(64), ForeignKey("devices.device_id", ondelete="SET NULL"), nullable=True)
    outcome = Column(String(20), nullable=False)
    summary = Column(Text, nullable=False)
    request_id = Column(UUID(as_uuid=True), nullable=False)
    correlation_id = Column(UUID(as_uuid=True), nullable=True)
    related_audit_log_id = Column(UUID(as_uuid=True), ForeignKey("audit_logs.id", ondelete="SET NULL"), nullable=True)
    occurred_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    duration_ms = Column(Integer, nullable=True)
    error_code = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint("actor_type IN ('USER', 'SYSTEM')", name="ck_user_activity_logs_actor_type"),
        CheckConstraint(
            "action IN ('VIEW', 'CREATE', 'UPDATE', 'DELETE', 'LOGIN', 'LOGOUT', 'EXPORT', 'CONTROL', 'ACKNOWLEDGE', 'RESOLVE', 'PROVISION', 'REVOKE')",
            name="ck_user_activity_logs_action"
        ),
        CheckConstraint("outcome IN ('SUCCESS', 'ACCEPTED', 'DENIED', 'FAILED')", name="ck_user_activity_logs_outcome"),
        UniqueConstraint("request_id", "function_key", "action", name="uq_user_activity_logs_req_func_act"),
        Index("ix_user_activity_logs_actor_occurred", "actor_user_id", text("occurred_at DESC")),
        Index("ix_user_activity_logs_function_occurred", "function_key", text("occurred_at DESC")),
        Index("ix_user_activity_logs_device_occurred", "device_id", text("occurred_at DESC")),
        Index("ix_user_activity_logs_outcome_occurred", "outcome", text("occurred_at DESC")),
    )
