"""
Permissions Package
PermissionService & RBAC decorators: kiểm tra user ACTIVE, session valid, role permissions, device scope SQL.
"""
from backend.app.permissions.decorators import require_auth, require_permission
from backend.app.permissions.messages import DispatchReason
from backend.app.permissions.service import (
    DEVICE_OPERATE_PERMISSIONS,
    DEVICE_PERMISSIONS,
    DEVICE_VIEW_PERMISSIONS,
    EXT_RESERVED_PERMISSIONS,
    P0_SUPPORTED_PERMISSIONS,
    AuthRequired,
    AuthorizationError,
    DeviceScopeNotFound,
    DispatchAuthorizationResult,
    PermissionDenied,
    PermissionService,
)

__all__ = [
    "PermissionService",
    "AuthorizationError",
    "PermissionDenied",
    "DeviceScopeNotFound",
    "AuthRequired",
    "DispatchAuthorizationResult",
    "DispatchReason",
    "P0_SUPPORTED_PERMISSIONS",
    "EXT_RESERVED_PERMISSIONS",
    "DEVICE_OPERATE_PERMISSIONS",
    "DEVICE_VIEW_PERMISSIONS",
    "DEVICE_PERMISSIONS",
    "require_auth",
    "require_permission",
]
