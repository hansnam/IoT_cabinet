-- ==============================================================================
-- CƠ SỞ DỮ LIỆU: iot_cabinet (PostgreSQL 16)
-- ĐẶC TẢ V1.3: 27 BẢNG GIAI ĐOẠN P0 (CORE)
-- ==============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ==============================================================================
-- 1. NHÓM IAM: NGƯỜI DÙNG, VAI TRÒ, QUYỀN HẠN, PHIÊN (8 BẢNG)
-- ==============================================================================

-- 1.1 Bảng users
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name VARCHAR(128) NOT NULL,
    email VARCHAR(254),
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    token_version INT NOT NULL DEFAULT 1,
    password_changed_at TIMESTAMPTZ,
    failed_login_count INT NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ,
    last_login_at TIMESTAMPTZ,
    created_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_users_status CHECK (status IN ('ACTIVE', 'LOCKED', 'ARCHIVED')),
    CONSTRAINT ck_users_token_version CHECK (token_version >= 1),
    CONSTRAINT ck_users_failed_login_count CHECK (failed_login_count >= 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_lower_email ON users(LOWER(email)) WHERE email IS NOT NULL;

-- 1.2 Bảng roles
CREATE TABLE IF NOT EXISTS roles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(80) NOT NULL,
    description TEXT,
    is_system BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 1.3 Bảng permissions
CREATE TABLE IF NOT EXISTS permissions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code VARCHAR(80) UNIQUE NOT NULL,
    scope_type TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_permissions_scope_type CHECK (scope_type IN ('GLOBAL', 'DEVICE', 'SELF'))
);

-- 1.4 Bảng role_permissions
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id UUID NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    permission_id UUID NOT NULL REFERENCES permissions(id) ON DELETE RESTRICT,
    granted_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (role_id, permission_id)
);
CREATE INDEX IF NOT EXISTS ix_role_permissions_permission_id ON role_permissions(permission_id);

-- 1.5 Bảng user_roles
CREATE TABLE IF NOT EXISTS user_roles (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    role_id UUID NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    assigned_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, role_id),
    CONSTRAINT uq_one_role_per_user UNIQUE (user_id)
);
CREATE INDEX IF NOT EXISTS ix_user_roles_role_id ON user_roles(role_id);

-- 1.6 Bảng auth_sessions
CREATE TABLE IF NOT EXISTS auth_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    token_version INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    revoke_reason TEXT,
    ip_address INET,
    user_agent VARCHAR(512),
    reauthenticated_at TIMESTAMPTZ,
    CONSTRAINT ck_auth_sessions_expires_after_created CHECK (expires_at > created_at)
);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_created ON auth_sessions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_expires_at ON auth_sessions(expires_at);

-- 1.7 Bảng refresh_tokens
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES auth_sessions(id) ON DELETE RESTRICT,
    token_hash CHAR(64) UNIQUE NOT NULL,
    parent_token_id UUID,
    replaced_by_token_id UUID,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    reuse_detected_at TIMESTAMPTZ,
    CONSTRAINT ck_refresh_tokens_expires_after_issued CHECK (expires_at > issued_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_refresh_token ON refresh_tokens(session_id) 
WHERE used_at IS NULL AND revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_refresh_tokens_session_issued ON refresh_tokens(session_id, issued_at);

-- 1.8 Bảng login_attempts
CREATE TABLE IF NOT EXISTS login_attempts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username_key CHAR(64) NOT NULL,
    ip_key CHAR(64) NOT NULL,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    success BOOLEAN NOT NULL,
    failure_code TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_id UUID NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_login_attempts_username_occurred ON login_attempts(username_key, occurred_at);
CREATE INDEX IF NOT EXISTS ix_login_attempts_ip_occurred ON login_attempts(ip_key, occurred_at);

-- ==============================================================================
-- 2. NHÓM TỔ CHỨC & THIẾT BỊ (8 BẢNG)
-- ==============================================================================

-- 2.1 Bảng locations
CREATE TABLE IF NOT EXISTS locations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(128) NOT NULL,
    parent_id UUID REFERENCES locations(id) ON DELETE RESTRICT,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2.2 Bảng device_groups
CREATE TABLE IF NOT EXISTS device_groups (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(128) NOT NULL,
    description TEXT,
    created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2.3 Bảng devices (tạo trước device_group_members)
CREATE TABLE IF NOT EXISTS devices (
    device_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    location_id UUID REFERENCES locations(id) ON DELETE RESTRICT,
    owner_user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    device_type TEXT NOT NULL,
    data_source TEXT NOT NULL DEFAULT 'REAL',
    lifecycle_state TEXT NOT NULL DEFAULT 'REGISTERED',
    connection_state TEXT NOT NULL DEFAULT 'UNKNOWN',
    health_state TEXT NOT NULL DEFAULT 'UNKNOWN',
    hardware_version TEXT,
    firmware_version TEXT,
    capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
    current_boot_id UUID,
    current_connection_id UUID,
    latest_seq BIGINT,
    latest_status_seq BIGINT,
    last_command_seq BIGINT NOT NULL DEFAULT 0,
    last_seen_at TIMESTAMPTZ,
    last_telemetry_at TIMESTAMPTZ,
    desired_config_version INT,
    applied_config_version INT,
    broker_desired_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    broker_actual_enabled BOOLEAN,
    broker_sync_state TEXT NOT NULL DEFAULT 'PENDING',
    state_version INT NOT NULL DEFAULT 1,
    maintenance_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_devices_device_type CHECK (device_type IN ('CONTROLLER', 'FAN_MONITOR')),
    CONSTRAINT ck_devices_data_source CHECK (data_source IN ('REAL', 'SIMULATED')),
    CONSTRAINT ck_devices_lifecycle_state CHECK (lifecycle_state IN ('REGISTERED', 'PROVISIONED', 'ACTIVE', 'MAINTENANCE', 'LOCKED', 'DECOMMISSIONED')),
    CONSTRAINT ck_devices_connection_state CHECK (connection_state IN ('UNKNOWN', 'ONLINE', 'OFFLINE')),
    CONSTRAINT ck_devices_health_state CHECK (health_state IN ('UNKNOWN', 'OK', 'FAULT')),
    CONSTRAINT ck_devices_broker_sync_state CHECK (broker_sync_state IN ('PENDING', 'SYNCED', 'FAILED')),
    CONSTRAINT ck_devices_state_version CHECK (state_version >= 1)
);
CREATE INDEX IF NOT EXISTS ix_devices_location_id ON devices(location_id);
CREATE INDEX IF NOT EXISTS ix_devices_lifecycle_last_seen ON devices(lifecycle_state, last_seen_at);

-- 2.4 Bảng device_group_members
CREATE TABLE IF NOT EXISTS device_group_members (
    group_id UUID NOT NULL REFERENCES device_groups(id) ON DELETE CASCADE,
    device_id VARCHAR(64) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    added_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (group_id, device_id)
);
CREATE INDEX IF NOT EXISTS ix_device_group_members_device_id ON device_group_members(device_id);

-- 2.5 Bảng device_access
CREATE TABLE IF NOT EXISTS device_access (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_id VARCHAR(64) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    access_level TEXT NOT NULL,
    granted_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    CONSTRAINT ck_device_access_level CHECK (access_level IN ('VIEW', 'OPERATE')),
    CONSTRAINT ck_device_access_expires_after_granted CHECK (expires_at IS NULL OR expires_at > granted_at),
    CONSTRAINT uq_device_access_device_user UNIQUE (device_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_device_access_user_device ON device_access(user_id, device_id);

-- 2.6 Bảng device_credentials
CREATE TABLE IF NOT EXISTS device_credentials (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_id VARCHAR(64) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    credential_version INT NOT NULL DEFAULT 1,
    mqtt_username VARCHAR(80) UNIQUE NOT NULL,
    client_id VARCHAR(64) NOT NULL,
    broker_client_ref TEXT,
    status TEXT NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    revoked_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    rotation_reason TEXT,
    CONSTRAINT ck_device_credentials_status CHECK (status IN ('REGISTERED', 'ACTIVE', 'REVOKED')),
    CONSTRAINT uq_device_credentials_device_version UNIQUE (device_id, credential_version)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_device_credential ON device_credentials(device_id) 
WHERE status = 'ACTIVE';

-- 2.7 Bảng device_configs
CREATE TABLE IF NOT EXISTS device_configs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_id VARCHAR(64) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    version INT NOT NULL,
    profile TEXT NOT NULL,
    fan_binding_id UUID,
    fan_on_c REAL,
    fan_off_c REAL,
    temp_alert_c REAL,
    humidity_alert_pct REAL,
    temp_clear_margin_c REAL,
    humidity_clear_margin_pct REAL,
    manual_timeout_s INT,
    rpm_window_ms INT,
    pulses_per_revolution INT,
    startup_grace_ms INT,
    no_rotation_persistence_ms INT,
    stop_grace_ms INT,
    controller_heartbeat_timeout_ms INT,
    telemetry_interval_ms INT NOT NULL DEFAULT 5000,
    schema_version INT NOT NULL DEFAULT 3,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    canonical_json TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    created_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_device_configs_profile CHECK (profile IN ('CONTROLLER', 'FAN_MONITOR')),
    CONSTRAINT uq_device_configs_device_version UNIQUE (device_id, version)
);

-- 2.8 Bảng device_operations
CREATE TABLE IF NOT EXISTS device_operations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_id VARCHAR(64) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    operation_type TEXT NOT NULL,
    requested_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    target_state TEXT,
    credential_id UUID REFERENCES device_credentials(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    phase TEXT,
    attempts INT NOT NULL DEFAULT 0,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    next_retry_at TIMESTAMPTZ,
    error_code TEXT,
    error_detail TEXT,
    request_id UUID NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    request_hash CHAR(64) NOT NULL,
    CONSTRAINT ck_device_operations_type CHECK (operation_type IN ('PROVISION', 'ACTIVATE', 'LOCK', 'UNLOCK', 'DECOMMISSION', 'ROTATE_CREDENTIAL')),
    CONSTRAINT ck_device_operations_status CHECK (status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')),
    CONSTRAINT uq_device_operations_idempotency UNIQUE (requested_by, device_id, operation_type, idempotency_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_device_operations_pending_running ON device_operations(device_id) 
WHERE status IN ('PENDING', 'RUNNING');

-- ==============================================================================
-- 3. NHÓM DỮ LIỆU, TRẠNG THÁI & LỆNH (6 BẢNG)
-- ==============================================================================

-- 3.1 Bảng telemetry
CREATE TABLE IF NOT EXISTS telemetry (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_id VARCHAR(64) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    profile TEXT NOT NULL,
    fan_binding_id UUID,
    boot_id UUID NOT NULL,
    connection_id UUID NOT NULL,
    seq BIGINT NOT NULL,
    schema_version INT NOT NULL DEFAULT 3,
    measured_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uptime_ms BIGINT NOT NULL,
    clock_synced BOOLEAN NOT NULL DEFAULT FALSE,
    temperature_c REAL,
    humidity_pct REAL,
    sensor_ok BOOLEAN,
    fan_output BOOLEAN,
    mode TEXT,
    rpm REAL,
    rpm_min REAL,
    rpm_max REAL,
    pulse_count BIGINT,
    rpm_window_ms INT,
    pulses_per_revolution INT,
    demand_on BOOLEAN,
    demand_valid BOOLEAN,
    controller_signal_age_ms BIGINT,
    rpm_sensor_status TEXT,
    fan_state TEXT,
    config_version INT,
    config_hash CHAR(64),
    fault_code TEXT,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_backfill BOOLEAN NOT NULL DEFAULT FALSE,
    raw_payload_hash CHAR(64),
    CONSTRAINT ck_telemetry_profile CHECK (profile IN ('CONTROLLER', 'FAN_MONITOR')),
    CONSTRAINT ck_telemetry_mode CHECK (mode IS NULL OR mode IN ('AUTO', 'MANUAL', 'MONITOR')),
    CONSTRAINT uq_telemetry_device_boot_seq UNIQUE (device_id, boot_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_telemetry_device_received ON telemetry(device_id, received_at DESC);
CREATE INDEX IF NOT EXISTS ix_telemetry_device_measured ON telemetry(device_id, measured_at) 
WHERE measured_at IS NOT NULL;

-- 3.2 Bảng device_events
CREATE TABLE IF NOT EXISTS device_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_id VARCHAR(64) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    event_type VARCHAR(64) NOT NULL,
    boot_id UUID,
    connection_id UUID,
    seq BIGINT,
    occurred_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    dedup_key TEXT,
    CONSTRAINT ck_device_events_source CHECK (source IN ('DEVICE', 'SYSTEM', 'USER'))
);
CREATE INDEX IF NOT EXISTS ix_device_events_device_received ON device_events(device_id, received_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_device_events_dedup ON device_events(device_id, dedup_key) 
WHERE dedup_key IS NOT NULL;

-- 3.3 Bảng device_shadows
CREATE TABLE IF NOT EXISTS device_shadows (
    device_id VARCHAR(64) PRIMARY KEY REFERENCES devices(device_id) ON DELETE CASCADE,
    desired JSONB NOT NULL DEFAULT '{}'::jsonb,
    reported JSONB NOT NULL DEFAULT '{}'::jsonb,
    desired_version INT NOT NULL DEFAULT 0,
    reported_version INT NOT NULL DEFAULT 0,
    desired_at TIMESTAMPTZ,
    reported_at TIMESTAMPTZ,
    reported_boot_id UUID,
    reported_seq BIGINT,
    drift_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_device_shadows_drift CHECK (drift_status IN ('IN_SYNC', 'PENDING', 'STALE', 'UNKNOWN'))
);

-- 3.4 Bảng commands
CREATE TABLE IF NOT EXISTS commands (
    command_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_id VARCHAR(64) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    requested_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    source TEXT NOT NULL,
    requesting_session_id UUID REFERENCES auth_sessions(id) ON DELETE RESTRICT,
    type TEXT NOT NULL,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    config_version INT,
    expected_boot_id UUID,
    expected_connection_id UUID,
    device_command_seq BIGINT NOT NULL,
    idempotency_key VARCHAR(128),
    request_hash CHAR(64),
    status TEXT NOT NULL DEFAULT 'PENDING',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    sent_at TIMESTAMPTZ,
    ack_at TIMESTAMPTZ,
    executed_at TIMESTAMPTZ,
    attempts INT NOT NULL DEFAULT 0,
    result JSONB,
    error_code TEXT,
    CONSTRAINT ck_commands_source CHECK (source IN ('USER', 'SYSTEM', 'AUTOMATION', 'AI')),
    CONSTRAINT ck_commands_type CHECK (type IN ('SET_MODE', 'SET_FAN', 'APPLY_CONFIG')),
    CONSTRAINT ck_commands_status CHECK (status IN ('PENDING', 'SENT', 'APPLIED', 'APPLIED_LATE', 'REJECTED', 'TIMEOUT', 'FAILED', 'CANCELLED')),
    CONSTRAINT ck_commands_expires_after_requested CHECK (expires_at > requested_at),
    CONSTRAINT uq_commands_device_command_seq UNIQUE (device_id, device_command_seq)
);
CREATE INDEX IF NOT EXISTS ix_commands_device_requested ON commands(device_id, requested_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_commands_idempotency ON commands(requested_by, device_id, idempotency_key) 
WHERE idempotency_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_commands_single_sent_per_device ON commands(device_id) 
WHERE status = 'SENT';

-- 3.5 Bảng command_attempts
CREATE TABLE IF NOT EXISTS command_attempts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    command_id UUID NOT NULL REFERENCES commands(command_id) ON DELETE CASCADE,
    attempt_no INT NOT NULL,
    dispatch_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    broker_confirmed_at TIMESTAMPTZ,
    ack_received_at TIMESTAMPTZ,
    outcome VARCHAR(32),
    error_code TEXT,
    worker_id TEXT,
    CONSTRAINT uq_command_attempts_command_attempt_no UNIQUE (command_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS ix_command_attempts_command_started ON command_attempts(command_id, dispatch_started_at);

-- 3.6 Bảng outbox_messages
CREATE TABLE IF NOT EXISTS outbox_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    command_id UUID UNIQUE NOT NULL REFERENCES commands(command_id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    payload JSONB NOT NULL,
    qos INT NOT NULL DEFAULT 1,
    retained BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'PENDING',
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_until TIMESTAMPTZ,
    locked_by TEXT,
    publish_count INT NOT NULL DEFAULT 0,
    published_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_outbox_messages_status CHECK (status IN ('PENDING', 'LEASED', 'PUBLISHED', 'FAILED', 'CANCELLED'))
);
CREATE INDEX IF NOT EXISTS ix_outbox_messages_status_available ON outbox_messages(status, available_at);

-- ==============================================================================
-- 4. NHÓM CẢNH BÁO, AUDIT & HOẠT ĐỘNG (5 BẢNG)
-- ==============================================================================

-- 4.1 Bảng alert_rules
CREATE TABLE IF NOT EXISTS alert_rules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_id VARCHAR(64) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    severity TEXT NOT NULL DEFAULT 'WARNING',
    condition_source TEXT NOT NULL,
    created_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_alert_rules_severity CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    CONSTRAINT ck_alert_rules_condition_source CHECK (condition_source IN ('APPLIED_CONFIG', 'CONNECTION', 'HEALTH', 'FAN_MONITOR')),
    CONSTRAINT uq_alert_rules_device_code UNIQUE (device_id, code),
    CONSTRAINT uq_alert_rules_device_id_composite UNIQUE (device_id, id)
);

-- 4.2 Bảng alerts
CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_id VARCHAR(64) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    rule_id UUID NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    type VARCHAR(64) NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cleared_at TIMESTAMPTZ,
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    resolved_at TIMESTAMPTZ,
    resolved_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    observed_value REAL,
    threshold REAL,
    message TEXT NOT NULL,
    config_version INT,
    fan_id UUID,
    fan_binding_id UUID,
    evaluation_quality TEXT NOT NULL DEFAULT 'VALID',
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    condition_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT ck_alerts_severity CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    CONSTRAINT ck_alerts_status CHECK (status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')),
    CONSTRAINT ck_alerts_evaluation_quality CHECK (evaluation_quality IN ('VALID', 'UNKNOWN', 'STALE'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_live_alert ON alerts(device_id, rule_id) 
WHERE status <> 'RESOLVED';
CREATE INDEX IF NOT EXISTS ix_alerts_device_opened ON alerts(device_id, opened_at DESC);
CREATE INDEX IF NOT EXISTS ix_alerts_status_opened ON alerts(status, opened_at);

-- 4.3 Bảng alert_events
CREATE TABLE IF NOT EXISTS alert_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    alert_id UUID NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    event_type VARCHAR(32) NOT NULL,
    actor_user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    actor_type TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note TEXT,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT ck_alert_events_type CHECK (event_type IN ('OPENED', 'UPDATED', 'ACKNOWLEDGED', 'CLEARED', 'REACTIVATED', 'RESOLVED')),
    CONSTRAINT ck_alert_events_actor_type CHECK (actor_type IN ('USER', 'SYSTEM'))
);
CREATE INDEX IF NOT EXISTS ix_alert_events_alert_occurred ON alert_events(alert_id, occurred_at);

-- 4.4 Bảng audit_logs
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    actor_user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    actor_session_id UUID REFERENCES auth_sessions(id) ON DELETE RESTRICT,
    actor_type TEXT NOT NULL,
    actor_name_snapshot TEXT,
    device_id VARCHAR(64) REFERENCES devices(device_id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    success BOOLEAN NOT NULL,
    reason_code TEXT,
    request_id UUID NOT NULL,
    ip_address INET,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_audit_logs_actor_type CHECK (actor_type IN ('USER', 'SYSTEM', 'SERVICE'))
);
CREATE INDEX IF NOT EXISTS ix_audit_logs_actor_created ON audit_logs(actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_logs_device_created ON audit_logs(device_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_logs_action_created ON audit_logs(action, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_logs_request_id ON audit_logs(request_id);

-- 4.5 Bảng user_activity_logs (Nhật ký làm việc)
CREATE TABLE IF NOT EXISTS user_activity_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    actor_user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    actor_session_id UUID REFERENCES auth_sessions(id) ON DELETE RESTRICT,
    actor_type TEXT NOT NULL,
    actor_name_snapshot VARCHAR(128),
    function_key VARCHAR(80) NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    target_name_snapshot TEXT,
    device_id VARCHAR(64) REFERENCES devices(device_id) ON DELETE SET NULL,
    outcome TEXT NOT NULL,
    summary TEXT NOT NULL,
    request_id UUID NOT NULL,
    correlation_id UUID,
    related_audit_log_id UUID REFERENCES audit_logs(id) ON DELETE SET NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    duration_ms INT,
    error_code TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT ck_user_activity_logs_actor_type CHECK (actor_type IN ('USER', 'SYSTEM')),
    CONSTRAINT ck_user_activity_logs_action CHECK (action IN ('VIEW', 'CREATE', 'UPDATE', 'DELETE', 'LOGIN', 'LOGOUT', 'EXPORT', 'CONTROL', 'ACKNOWLEDGE', 'RESOLVE', 'PROVISION', 'REVOKE')),
    CONSTRAINT ck_user_activity_logs_outcome CHECK (outcome IN ('SUCCESS', 'ACCEPTED', 'DENIED', 'FAILED')),
    CONSTRAINT uq_user_activity_logs_req_func_act UNIQUE (request_id, function_key, action)
);
CREATE INDEX IF NOT EXISTS ix_user_activity_logs_actor_occurred ON user_activity_logs(actor_user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_user_activity_logs_function_occurred ON user_activity_logs(function_key, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_user_activity_logs_device_occurred ON user_activity_logs(device_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_user_activity_logs_outcome_occurred ON user_activity_logs(outcome, occurred_at DESC);
