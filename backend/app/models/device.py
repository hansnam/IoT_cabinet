import uuid
from sqlalchemy import (
    Column, String, Text, Boolean, Integer,
    ForeignKey, CheckConstraint, UniqueConstraint, Index, text, func
)
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB, BIGINT, REAL
from backend.app.extensions import db

class Device(db.Model):
    """Bảng devices: Định danh và trạng thái thiết bị IoT (Mục 6.3)"""
    __tablename__ = "devices"

    device_id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    location_id = Column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=True)
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    device_type = Column(String(32), nullable=False)
    data_source = Column(String(20), nullable=False, default="REAL")
    lifecycle_state = Column(String(32), nullable=False, default="REGISTERED")
    connection_state = Column(String(20), nullable=False, default="UNKNOWN")
    health_state = Column(String(20), nullable=False, default="UNKNOWN")
    hardware_version = Column(Text, nullable=True)
    firmware_version = Column(Text, nullable=True)
    capabilities = Column(JSONB, nullable=False, default=dict)
    current_boot_id = Column(UUID(as_uuid=True), nullable=True)
    current_connection_id = Column(UUID(as_uuid=True), nullable=True)
    latest_seq = Column(BIGINT, nullable=True)
    latest_status_seq = Column(BIGINT, nullable=True)
    last_command_seq = Column(BIGINT, nullable=False, default=0)
    last_seen_at = Column(TIMESTAMP(timezone=True), nullable=True)
    last_telemetry_at = Column(TIMESTAMP(timezone=True), nullable=True)
    desired_config_version = Column(Integer, nullable=True)
    applied_config_version = Column(Integer, nullable=True)
    broker_desired_enabled = Column(Boolean, nullable=False, default=False)
    broker_actual_enabled = Column(Boolean, nullable=True)
    broker_sync_state = Column(String(20), nullable=False, default="PENDING")
    state_version = Column(Integer, nullable=False, default=1)
    maintenance_note = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("device_type IN ('CONTROLLER', 'FAN_MONITOR')", name="ck_devices_device_type"),
        CheckConstraint("data_source IN ('REAL', 'SIMULATED')", name="ck_devices_data_source"),
        CheckConstraint(
            "lifecycle_state IN ('REGISTERED', 'PROVISIONED', 'ACTIVE', 'MAINTENANCE', 'LOCKED', 'DECOMMISSIONED')",
            name="ck_devices_lifecycle_state"
        ),
        CheckConstraint("connection_state IN ('UNKNOWN', 'ONLINE', 'OFFLINE')", name="ck_devices_connection_state"),
        CheckConstraint("health_state IN ('UNKNOWN', 'OK', 'FAULT')", name="ck_devices_health_state"),
        CheckConstraint("broker_sync_state IN ('PENDING', 'SYNCED', 'FAILED')", name="ck_devices_broker_sync_state"),
        CheckConstraint("state_version >= 1", name="ck_devices_state_version"),
        Index("ix_devices_location_id", "location_id"),
        Index("ix_devices_lifecycle_last_seen", "lifecycle_state", "last_seen_at"),
    )


class DeviceAccess(db.Model):
    """Bảng device_access: Phân quyền truy cập theo từng thiết bị (Mục 6.3)"""
    __tablename__ = "device_access"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(64), ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    access_level = Column(String(20), nullable=False)
    granted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    granted_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("access_level IN ('VIEW', 'OPERATE')", name="ck_device_access_level"),
        CheckConstraint("expires_at IS NULL OR expires_at > granted_at", name="ck_device_access_expires_after_granted"),
        UniqueConstraint("device_id", "user_id", name="uq_device_access_device_user"),
        Index("ix_device_access_user_device", "user_id", "device_id"),
    )


class DeviceCredential(db.Model):
    """Bảng device_credentials: Quản lý thông tin xác thực MQTT của thiết bị (Mục 6.3)"""
    __tablename__ = "device_credentials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(64), ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False)
    credential_version = Column(Integer, nullable=False, default=1)
    mqtt_username = Column(String(80), unique=True, nullable=False)
    client_id = Column(String(64), nullable=False)
    broker_client_ref = Column(Text, nullable=True)
    status = Column(String(20), nullable=False)
    issued_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    activated_at = Column(TIMESTAMP(timezone=True), nullable=True)
    revoked_at = Column(TIMESTAMP(timezone=True), nullable=True)
    expires_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    revoked_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    rotation_reason = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('REGISTERED', 'ACTIVE', 'REVOKED')", name="ck_device_credentials_status"),
        UniqueConstraint("device_id", "credential_version", name="uq_device_credentials_device_version"),
        Index(
            "uq_active_device_credential", 
            "device_id", 
            unique=True, 
            postgresql_where=text("status = 'ACTIVE'")
        ),
    )


class DeviceConfig(db.Model):
    """Bảng device_configs: Cấu hình phiên bản của thiết bị (Mục 6.3)"""
    __tablename__ = "device_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(64), ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    profile = Column(String(32), nullable=False)
    fan_binding_id = Column(UUID(as_uuid=True), nullable=True)  # P1 FK fan_node_bindings
    fan_on_c = Column(REAL, nullable=True)
    fan_off_c = Column(REAL, nullable=True)
    temp_alert_c = Column(REAL, nullable=True)
    humidity_alert_pct = Column(REAL, nullable=True)
    temp_clear_margin_c = Column(REAL, nullable=True)
    humidity_clear_margin_pct = Column(REAL, nullable=True)
    manual_timeout_s = Column(Integer, nullable=True)
    rpm_window_ms = Column(Integer, nullable=True)
    pulses_per_revolution = Column(Integer, nullable=True)
    startup_grace_ms = Column(Integer, nullable=True)
    no_rotation_persistence_ms = Column(Integer, nullable=True)
    stop_grace_ms = Column(Integer, nullable=True)
    controller_heartbeat_timeout_ms = Column(Integer, nullable=True)
    telemetry_interval_ms = Column(Integer, nullable=False, default=5000)
    schema_version = Column(Integer, nullable=False, default=3)
    extra = Column(JSONB, nullable=False, default=dict)
    canonical_json = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("profile IN ('CONTROLLER', 'FAN_MONITOR')", name="ck_device_configs_profile"),
        UniqueConstraint("device_id", "version", name="uq_device_configs_device_version"),
    )


class DeviceOperation(db.Model):
    """Bảng device_operations: Tác vụ quản trị bất đồng bộ với Broker (Mục 6.3)"""
    __tablename__ = "device_operations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(64), ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False)
    operation_type = Column(String(32), nullable=False)
    requested_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    target_state = Column(String(32), nullable=True)
    credential_id = Column(UUID(as_uuid=True), ForeignKey("device_credentials.id", ondelete="RESTRICT"), nullable=True)
    status = Column(String(20), nullable=False, default="PENDING")
    phase = Column(String(32), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    requested_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(TIMESTAMP(timezone=True), nullable=True)
    finished_at = Column(TIMESTAMP(timezone=True), nullable=True)
    next_retry_at = Column(TIMESTAMP(timezone=True), nullable=True)
    error_code = Column(Text, nullable=True)
    error_detail = Column(Text, nullable=True)
    request_id = Column(UUID(as_uuid=True), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    request_hash = Column(String(64), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "operation_type IN ('PROVISION', 'ACTIVATE', 'LOCK', 'UNLOCK', 'DECOMMISSION', 'ROTATE_CREDENTIAL')",
            name="ck_device_operations_type"
        ),
        CheckConstraint("status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')", name="ck_device_operations_status"),
        UniqueConstraint("requested_by", "device_id", "operation_type", "idempotency_key", name="uq_device_operations_idempotency"),
        Index(
            "uq_device_operations_pending_running",
            "device_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'RUNNING')")
        ),
    )
