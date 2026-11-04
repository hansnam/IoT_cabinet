"""
SQLAlchemy Models Package (Mục 6 - 27 Bảng P0 CORE)
===================================================

1. Nhóm IAM (8 bảng):
   - User (users)
   - Role (roles)
   - Permission (permissions)
   - RolePermission (role_permissions)
   - UserRole (user_roles)
   - AuthSession (auth_sessions)
   - RefreshToken (refresh_tokens)
   - LoginAttempt (login_attempts)

2. Nhóm Tổ chức & Thiết bị (8 bảng):
   - Location (locations)
   - DeviceGroup (device_groups)
   - DeviceGroupMember (device_group_members)
   - Device (devices)
   - DeviceAccess (device_access)
   - DeviceCredential (device_credentials)
   - DeviceConfig (device_configs)
   - DeviceOperation (device_operations)

3. Nhóm Dữ liệu, Trạng thái & Lệnh (6 bảng):
   - Telemetry (telemetry)
   - DeviceEvent (device_events)
   - DeviceShadow (device_shadows)
   - Command (commands)
   - CommandAttempt (command_attempts)
   - OutboxMessage (outbox_messages)

4. Nhóm Cảnh báo, Audit & Hoạt động (5 bảng):
   - AlertRule (alert_rules)
   - Alert (alerts)
   - AlertEvent (alert_events)
   - AuditLog (audit_logs)
   - UserActivityLog (user_activity_logs)
"""

from backend.app.models.iam import (
    User,
    Role,
    Permission,
    RolePermission,
    UserRole,
    AuthSession,
    RefreshToken,
    LoginAttempt,
)

from backend.app.models.organization import (
    Location,
    DeviceGroup,
    DeviceGroupMember,
)

from backend.app.models.device import (
    Device,
    DeviceAccess,
    DeviceCredential,
    DeviceConfig,
    DeviceOperation,
)

from backend.app.models.telemetry import (
    Telemetry,
    DeviceEvent,
    DeviceShadow,
)

from backend.app.models.command import (
    Command,
    CommandAttempt,
    OutboxMessage,
)

from backend.app.models.alert import (
    AlertRule,
    Alert,
    AlertEvent,
)

from backend.app.models.logs import (
    AuditLog,
    UserActivityLog,
)

__all__ = [
    # IAM (8)
    "User",
    "Role",
    "Permission",
    "RolePermission",
    "UserRole",
    "AuthSession",
    "RefreshToken",
    "LoginAttempt",
    # Organization & Devices (8)
    "Location",
    "DeviceGroup",
    "DeviceGroupMember",
    "Device",
    "DeviceAccess",
    "DeviceCredential",
    "DeviceConfig",
    "DeviceOperation",
    # Telemetry & Commands (6)
    "Telemetry",
    "DeviceEvent",
    "DeviceShadow",
    "Command",
    "CommandAttempt",
    "OutboxMessage",
    # Alerts & Logs (5)
    "AlertRule",
    "Alert",
    "AlertEvent",
    "AuditLog",
    "UserActivityLog",
]
