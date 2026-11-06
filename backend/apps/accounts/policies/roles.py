"""
Roles & Policies Module
Quản lý các Vai trò người dùng (Roles) và Kiểm soát truy cập dựa trên vai trò (RBAC):
- Định nghĩa 3 vai trò hệ thống duy nhất: ADMIN, OPERATOR, VIEWER.
- Role được hiện thực hóa qua model Group và Permission chuẩn của Django.
- Mỗi người dùng thuộc duy nhất một Vai trò (Single-role per user).
  Trường hợp có 0 hoặc nhiều hơn 1 vai trò được coi là không hợp lệ (get_user_role trả về None).
- has_permission() kiểm tra qua ma trận vai trò trước khi gọi has_perm(), chặn triệt để bypass qua user_permissions.
- Đồng bộ is_staff=True cho ADMIN, is_staff=False cho OPERATOR/VIEWER.
- ADMIN thông thường có is_superuser=False; không sử dụng is_superuser để chứng minh RBAC (Rule 3).
- assign_role() thực thi trong transaction.atomic(), khóa dòng bằng select_for_update() và xóa cache permission.
"""
import logging
from typing import Any, Final

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.accounts.policies.catalog import (
    ACTION_AUDIT_VIEW,
    ACTION_SESSION_REVOKE,
    ACTION_SESSION_VIEW,
    ACTION_USER_CREATE,
    ACTION_USER_DELETE,
    ACTION_USER_UPDATE,
    ACTION_USER_VIEW,
    get_permission_for_action,
)

logger = logging.getLogger(__name__)

# 1. Ba Vai trò duy nhất của hệ thống IoT Cabinet
ROLE_ADMIN: Final[str] = "ADMIN"
ROLE_OPERATOR: Final[str] = "OPERATOR"
ROLE_VIEWER: Final[str] = "VIEWER"

ALL_ROLES: Final[tuple[str, ...]] = (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_VIEWER,
)

# 2. Ma trận phân quyền mặc định cho từng Vai trò (Dựa trên Business Action Codes)
ROLE_PERMISSIONS: Final[dict[str, list[str]]] = {
    ROLE_ADMIN: [
        ACTION_USER_CREATE,
        ACTION_USER_VIEW,
        ACTION_USER_UPDATE,
        ACTION_USER_DELETE,
        ACTION_SESSION_VIEW,
        ACTION_SESSION_REVOKE,
        ACTION_AUDIT_VIEW,
    ],
    ROLE_OPERATOR: [],
    ROLE_VIEWER: [],
}

def clear_user_permission_cache(user: Any) -> None:
    """
    Xóa bỏ các trường cache permission nội bộ mà Django lưu trên instance User:
    _perm_cache, _user_perm_cache, _group_perm_cache.
    Theo tài liệu Django, refresh_from_db() không tự xóa cache này.
    """
    for cache_attr in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
        if hasattr(user, cache_attr):
            delattr(user, cache_attr)

def get_user_role(user: Any) -> str | None:
    """
    Lấy tên vai trò (Role) hiện tại của người dùng.
    Bảo đảm quy tắc Single-role per user:
    - Nếu người dùng thuộc chính xác 1 vai trò trong ALL_ROLES -> trả về tên vai trò.
    - Nếu người dùng thuộc 0 vai trò hoặc nhiều hơn 1 vai trò (mâu thuẫn) -> trả về None.

    Tham số:
        user: Thực thể User (hoặc AnonymousUser).

    Trả về:
        str | None: Tên role ('ADMIN', 'OPERATOR', 'VIEWER') hoặc None nếu không hợp lệ / không có.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return None

    roles = list(user.groups.filter(name__in=ALL_ROLES).values_list("name", flat=True))
    if len(roles) != 1:
        if len(roles) > 1:
            logger.warning(
                "Người dùng '%s' (ID: %s) có nhiều hơn một vai trò: %s. Trạng thái bị coi là không hợp lệ.",
                getattr(user, "username", "unknown"),
                getattr(user, "id", "unknown"),
                roles,
            )
        return None

    return roles[0]

def has_role(user: Any, role_name: str) -> bool:
    """
    Kiểm tra người dùng có đang mang vai trò cụ thể hay không.

    Tham số:
        user: Thực thể User.
        role_name (str): Tên vai trò cần kiểm tra ('ADMIN', 'OPERATOR', 'VIEWER').

    Trả về:
        bool: True nếu người dùng hợp lệ, đang hoạt động (is_active) và có đúng vai trò này.
    """
    if not user or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    return get_user_role(user) == role_name

def assign_role(user: Any, role_name: str) -> None:
    """
    Gán vai trò duy nhất cho người dùng và đồng bộ cờ is_staff / is_superuser:
    - Bọc toàn bộ thao tác trong transaction.atomic() và khóa dòng User (select_for_update) chống race condition.
    - Loại bỏ người dùng khỏi toàn bộ các nhóm role khác trong ALL_ROLES.
    - Thêm người dùng vào nhóm role mới tương ứng.
    - Đồng bộ: is_staff = True đối với ADMIN, is_staff = False đối với OPERATOR và VIEWER.
    - Đảm bảo is_superuser = False (Rule 3).
    - Xóa sạch cache permission trên instance để phản ánh quyền mới ngay lập tức.

    Tham số:
        user: Thực thể User.
        role_name (str): Tên vai trò cần gán ('ADMIN', 'OPERATOR', 'VIEWER').

    Ngoại lệ:
        ValueError: Nếu role_name không nằm trong ALL_ROLES.
    """
    if role_name not in ALL_ROLES:
        raise ValueError(f"Vai trò '{role_name}' không hợp lệ. Chỉ chấp nhận một trong: {ALL_ROLES}")

    target_group, _ = Group.objects.get_or_create(name=role_name)
    user_model = get_user_model()

    with transaction.atomic():
        if hasattr(user, "pk") and user.pk:
            # Khóa dòng User để ngăn chặn race condition khi hai tiến trình cùng cập nhật vai trò
            user_model.objects.select_for_update().get(pk=user.pk)

        # 1. Rút người dùng khỏi toàn bộ các role group hiện tại để bảo đảm single-role
        current_role_groups = Group.objects.filter(name__in=ALL_ROLES)
        user.groups.remove(*current_role_groups)

        # 2. Thêm vào role group đích
        user.groups.add(target_group)

        # 3. Đồng bộ cờ is_staff và is_superuser
        user.is_staff = (role_name == ROLE_ADMIN)
        user.is_superuser = False

        # 4. Lưu lại các trường đồng bộ nếu user đã tồn tại trong DB
        if hasattr(user, "pk") and user.pk:
            user.save(update_fields=["is_staff", "is_superuser"])


    # 5. Xóa triệt để cache permission trên instance User trong bộ nhớ
    clear_user_permission_cache(user)

def has_permission(user: Any, action: str) -> bool:
    """
    Kiểm tra người dùng có quyền thực hiện hành động nghiệp vụ (action) hay không:
    - Người dùng phải xác thực (is_authenticated) và đang hoạt động (is_active).
    - Người dùng phải sở hữu chính xác một vai trò hợp lệ (Single-role).
    - Hành động nghiệp vụ bắt buộc phải nằm trong ma trận ROLE_PERMISSIONS của vai trò đó.
      (Điều này ngăn chặn triệt để lỗ hổng bypass qua direct user_permissions).
    - Sau khi thỏa mãn ma trận vai trò, kiểm tra tiếp qua Django user.has_perm().

    Tham số:
        user: Thực thể User.
        action (str): Mã hành động nghiệp vụ (ví dụ: 'USER_CREATE', 'AUDIT_VIEW').

    Trả về:
        bool: True nếu có quyền, False nếu không.
    """
    if not user or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False

    # 1. Xác định vai trò duy nhất hợp lệ
    role = get_user_role(user)
    if not role:
        return False

    # 2. Kiểm tra Action có được cấp phép cho vai trò này trong ma trận không
    allowed_actions = ROLE_PERMISSIONS.get(role, [])
    if action not in allowed_actions:
        return False

    # 3. Tra cứu permission Django tương ứng
    try:
        perm_string = get_permission_for_action(action)
    except ValueError:
        return False

    return bool(user.has_perm(perm_string))

def require_permission(user: Any, action: str) -> None:
    """
    Bắt buộc người dùng phải có quyền hạn cho hành động nghiệp vụ:
    Nếu không có quyền, ném ngoại lệ django.core.exceptions.PermissionDenied.

    Tham số:
        user: Thực thể User.
        action (str): Mã hành động nghiệp vụ.

    Ngoại lệ:
        PermissionDenied: Khi người dùng không đủ thẩm quyền hoặc không hoạt động.
    """
    if not has_permission(user, action):
        raise PermissionDenied(f"Thao tác bị từ chối: Người dùng không có quyền '{action}'.")