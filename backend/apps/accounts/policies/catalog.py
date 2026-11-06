"""
Permission Catalog Module
Định nghĩa danh mục Business Action Codes chuẩn hóa cho toàn hệ thống IoT Cabinet
và ánh xạ từng action code sang Django Permission tương ứng (app_label.codename).

Chỉ định nghĩa các quyền cho các Model đã có thật sau migration (accounts, audit).
Tuyệt đối không bịa ContentType hay tạo model giả cho các thực thể chưa có (Device, Command).
"""
from typing import Final

# 1. Các Business Action Codes cho quản trị tài khoản (accounts)
ACTION_USER_CREATE: Final[str] = "USER_CREATE"
ACTION_USER_VIEW: Final[str] = "USER_VIEW"
ACTION_USER_UPDATE: Final[str] = "USER_UPDATE"
ACTION_USER_DELETE: Final[str] = "USER_DELETE"
ACTION_SESSION_VIEW: Final[str] = "SESSION_VIEW"
ACTION_SESSION_REVOKE: Final[str] = "SESSION_REVOKE"

# 2. Các Business Action Codes cho kiểm toán nhật ký (audit)
ACTION_AUDIT_VIEW: Final[str] = "AUDIT_VIEW"

# Tập hợp toàn bộ các action đã được đăng ký trong hệ thống
ALL_ACTIONS: Final[tuple[str, ...]] = (
    ACTION_USER_CREATE,
    ACTION_USER_VIEW,
    ACTION_USER_UPDATE,
    ACTION_USER_DELETE,
    ACTION_SESSION_VIEW,
    ACTION_SESSION_REVOKE,
    ACTION_AUDIT_VIEW,
)

# 3. Ánh xạ từ Business Action Code sang Django Permission codename (app_label.codename)
# Toàn bộ codename này đều do Django tự động sinh ra cho các model User, AuthSession, AuditLog
ACTION_PERMISSIONS: Final[dict[str, str]] = {
    ACTION_USER_CREATE: "accounts.add_user",
    ACTION_USER_VIEW: "accounts.view_user",
    ACTION_USER_UPDATE: "accounts.change_user",
    ACTION_USER_DELETE: "accounts.delete_user",
    ACTION_SESSION_VIEW: "accounts.view_authsession",
    ACTION_SESSION_REVOKE: "accounts.change_authsession",
    ACTION_AUDIT_VIEW: "audit.view_auditlog",
}

def get_permission_for_action(action: str) -> str:
    """
    Tra cứu mã Django permission (dạng 'app_label.codename') cho một business action code.

    Tham số:
        action (str): Mã hành động nghiệp vụ (ví dụ: 'USER_CREATE', 'AUDIT_VIEW').

    Trả về:
        str: Chuỗi permission của Django (ví dụ: 'accounts.add_user').

    Ngoại lệ:
        ValueError: Nếu action code chưa được đăng ký trong danh mục.
    """
    perm = ACTION_PERMISSIONS.get(action)
    if not perm:
        raise ValueError(f"Hành động '{action}' không tồn tại trong danh mục quyền hạn (catalog).")
    return perm