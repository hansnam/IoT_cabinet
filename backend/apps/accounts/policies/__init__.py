"""
Package accounts.policies
Cung cấp hạ tầng phân quyền RBAC dựa trên Django Group & Permission:
- Danh mục Business Action Codes chuẩn hóa (catalog.py).
- Quản lý các Vai trò ADMIN, OPERATOR, VIEWER và ma trận quyền hạn (roles.py).
"""
from apps.accounts.policies.catalog import (
    ACTION_AUDIT_VIEW,
    ACTION_PERMISSIONS,
    ACTION_SESSION_REVOKE,
    ACTION_SESSION_VIEW,
    ACTION_USER_CREATE,
    ACTION_USER_DELETE,
    ACTION_USER_UPDATE,
    ACTION_USER_VIEW,
    ALL_ACTIONS,
    get_permission_for_action,
)
from apps.accounts.policies.roles import (
    ALL_ROLES,
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_PERMISSIONS,
    ROLE_VIEWER,
    assign_role,
    clear_user_permission_cache,
    get_user_role,
    has_permission,
    has_role,
    require_permission,
)

__all__ = [
    "ACTION_AUDIT_VIEW",
    "ACTION_PERMISSIONS",
    "ACTION_SESSION_REVOKE",
    "ACTION_SESSION_VIEW",
    "ACTION_USER_CREATE",
    "ACTION_USER_DELETE",
    "ACTION_USER_UPDATE",
    "ACTION_USER_VIEW",
    "ALL_ACTIONS",
    "ALL_ROLES",
    "ROLE_ADMIN",
    "ROLE_OPERATOR",
    "ROLE_PERMISSIONS",
    "ROLE_VIEWER",
    "assign_role",
    "clear_user_permission_cache",
    "get_permission_for_action",
    "get_user_role",
    "has_permission",
    "has_role",
    "require_permission",
]