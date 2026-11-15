# TV3-DJ-03 — Roles/Permissions và Tài Khoản Bootstrap (Patch 03 — Bản Chuẩn Hóa Chốt)

Ngày cập nhật: **05/11/2026**  
Mã chặng: **TV3-DJ-03**  
Thuộc kế hoạch: [TV3_KE_HOACH_DJANGO_CHO_GEMINI.md](TV3_KE_HOACH_DJANGO_CHO_GEMINI.md)  
Người thực hiện: **TV3**  
Trạng thái: **READY_FOR_COPY (Đã sửa toàn diện 4 lỗi P1/P2 và bổ sung 5 regression tests — Đạt 60/60 tests)**  

---

## 1. Mục tiêu, Prerequisites và Ranh giới Kỹ thuật

### 1.1 Mục tiêu chặng 03
1. **Kiến trúc Vai trò (RBAC) chuẩn Django**:
   - Sử dụng hai model gốc `django.contrib.auth.models.Group` và `django.contrib.auth.models.Permission`.
   - Định nghĩa chính xác 3 vai trò hệ thống duy nhất: `ADMIN`, `OPERATOR`, `VIEWER`.
   - **Tuyệt đối không tạo cột `user.role` thứ hai** trên bảng `accounts_user`. Việc áp dụng một vai trò duy nhất cho mỗi người dùng (Single-role per user) được thực thi nghiêm ngặt tại tầng logic (`assign_role`, `get_user_role`).
2. **Danh mục Quyền hạn Nghiệp vụ (`catalog.py`)**:
   - Ánh xạ các Business Action Codes (`USER_CREATE`, `USER_VIEW`, `USER_UPDATE`, `USER_DELETE`, `SESSION_VIEW`, `SESSION_REVOKE`, `AUDIT_VIEW`) sang Django permission codenames thực tế đã tồn tại sau migration (`app_label.codename`).
   - Tuân thủ **Rule 14**: Không tạo ContentType hay model Device/Command giả để pass test khi TV2 chưa cung cấp. Trong chặng 03 chỉ ánh xạ các model đã có thật (`accounts`, `audit`).
3. **Chính sách Quyền hạn & Phân cấp Quản trị (`roles.py`)**:
   - Cung cấp các hàm kiểm tra quyền nghiệp vụ: `has_role`, `get_user_role`, `assign_role`, `has_permission`, `require_permission`.
   - Tuân thủ **Rule 3**: `ADMIN` có `is_staff=True` để truy cập các chức năng quản trị, nhưng **`is_superuser=False`**. Tuyệt đối không dùng `is_superuser` để chứng minh cơ chế phân quyền RBAC.
   - Khi chuyển vai trò (ví dụ từ `ADMIN` sang `OPERATOR`), hệ thống tự động gỡ bỏ group cũ và hạ cờ `is_staff=False`.
4. **Command Khởi tạo Vai trò Idempotent (`seed_roles`)**:
   - Khởi tạo 3 nhóm vai trò và gán quyền hạn tương ứng theo ma trận `ROLE_PERMISSIONS`. Có thể chạy lại nhiều lần an toàn mà không làm nhân bản dữ liệu hay mất cấu hình.
   - Khi thiếu permission bắt buộc từ database (do chưa chạy migration), lệnh ném `CommandError` và rollback ngay lập tức, không để hệ thống rơi vào trạng thái thiếu quyền âm thầm.
5. **Command Tạo Quản trị viên Khởi tạo An toàn (`bootstrap_admin`)**:
   - Khởi tạo tài khoản quản trị viên đầu tiên một cách an toàn. Chạy lại không ghi đè mật khẩu hoặc tự ý nâng quyền của tài khoản đã tồn tại.
   - Tuân thủ **Rule 17**: Tuyệt đối **không nhận mật khẩu qua CLI argument** (`--password`) để tránh lộ secret trong danh sách tiến trình (`ps`, `Get-Process`) và bash history. Mật khẩu được nhận qua biến môi trường hoặc dấu nhắc ẩn (`getpass.getpass`).
   - Tài khoản bootstrap có `status=ACTIVE`, `must_change_password=True`, `is_staff=True`, `is_superuser=False` và được gán vai trò `ADMIN`.

### 1.2 Bốn điểm cốt lõi đã được khắc phục (Fixes & Hardening)
1. **[P1 Fix] Thực thi nghiêm ngặt Single-role & Chặn bypass qua user_permissions**:
   - `get_user_role(user)` chỉ trả về vai trò khi `len(roles) == 1`. Nếu user có từ 2 vai trò trở lên (trạng thái xung đột), hàm trả về `None` (từ chối vai trò), không tùy tiện chọn role đầu tiên.
   - `has_permission(user, action)` bắt buộc kiểm tra `action in ROLE_PERMISSIONS[role]` trước khi gọi `user.has_perm()`. Nhờ đó, nếu một user không có role nhưng bị ai đó gán quyền trực tiếp vào `user_permissions`, thao tác vẫn bị chặn đứng theo đúng chính sách RBAC.
2. **[P1 Fix] Xóa sạch cache permission trên instance khi đổi vai trò**:
   - Django mặc định cache lại permissions trên instance (`_perm_cache`, `_user_perm_cache`, `_group_perm_cache`), và `refresh_from_db()` không tự xóa cache này.
   - Hàm `clear_user_permission_cache(user)` được gọi trực tiếp bên trong `assign_role()`, giúp việc đổi vai trò có hiệu lực ngay lập tức trên chính object User trong bộ nhớ mà không giữ lại quyền cũ.
3. **[P1 Fix] Đóng gói đổi group và lưu cờ trong cùng transaction atomic & row lock**:
   - `assign_role(user, role_name)` được bọc trọn vẹn trong `transaction.atomic()`.
   - Áp dụng `User.objects.select_for_update().get(pk=user.pk)` để chống race condition khi có hai request đổi vai trò đồng thời. Nếu `user.save()` gặp sự cố, toàn bộ thao tác gán group M2M đều được rollback an toàn.
4. **[P2 Fix] seed_roles ném CommandError khi thiếu permission**:
   - Khi không tìm thấy permission trong database (ví dụ bảng `auth_permission` thiếu do chưa migrate), lệnh `seed_roles` lập tức ném `CommandError` và rollback toàn bộ, không chấp nhận trạng thái thiếu quyền nửa vời.

---

## 2. Danh sách file theo thứ tự thao tác

| STT | Đường dẫn file | Trạng thái | Lý do / Trách nhiệm |
|:---|:---|:---:|:---|
| 1 | `backend/apps/accounts/policies/__init__.py` | **NEW** | Export các hằng số Role, Action và các hàm helper phân quyền |
| 2 | `backend/apps/accounts/policies/catalog.py` | **NEW** | Danh mục Business Action Codes và ánh xạ sang Django Permission |
| 3 | `backend/apps/accounts/policies/roles.py` | **NEW** | Định nghĩa 3 Vai trò, ma trận quyền, clear cache permission, atomic role change |
| 4 | `backend/apps/accounts/management/__init__.py` | **NEW** | Khai báo package quản trị Django management |
| 5 | `backend/apps/accounts/management/commands/__init__.py` | **NEW** | Khai báo package commands |
| 6 | `backend/apps/accounts/management/commands/seed_roles.py` | **NEW** | Đồng bộ vai trò và quyền hạn (Idempotent, ném CommandError nếu thiếu perm) |
| 7 | `backend/apps/accounts/management/commands/bootstrap_admin.py` | **NEW** | Khởi tạo tài khoản Admin an toàn (Idempotent, Rule 17, staff=True, superuser=False) |
| 8 | `backend/apps/accounts/tests/test_models_roles.py` | **MODIFY** | Giữ 12 test cũ, bổ sung 13 test RolePolicy và 4 test BootstrapAdmin (Tổng: 29 tests) |

---

## 3. Luồng hoạt động & Kiến thức Django cần hiểu

### 3.1 Luồng kiểm tra quyền nghiêm ngặt qua Ma trận Role (Rule 3)
```text
Caller gọi has_permission(user, 'USER_CREATE')
  │
  ├── 1. Kiểm tra xác thực & trạng thái:
  │      Nếu user là AnonymousUser hoặc user.is_active == False:
  │      └── Trả về False
  │
  ├── 2. Lấy vai trò duy nhất:
  │      role = get_user_role(user)
  │      ├── Nếu user có 0 vai trò -> Trả về False
  │      ├── Nếu user có >= 2 vai trò (Xung đột) -> Trả về False (Chặn đứng!)
  │      └── Đúng 1 vai trò (ADMIN / OPERATOR / VIEWER)
  │
  ├── 3. Kiểm tra Action có thuộc Ma trận phân quyền của Role không:
  │      action in ROLE_PERMISSIONS[role]
  │      ├── Nếu KHÔNG thuộc (ví dụ OPERATOR muốn USER_CREATE) -> Trả về False ngay!
  │      │     (Chặn triệt để nguy cơ bypass qua user_permissions trực tiếp)
  │      └── Nếu CÓ thuộc
  │
  └── 4. Kiểm tra quyền thực tế trong Django DB:
         user.has_perm('accounts.add_user')
         └── Trả về True/False tương ứng.
```

### 3.2 Luồng Invalidate Permission Cache khi Đổi Vai trò (Rule 3)
```text
Caller gọi assign_role(user, 'OPERATOR')
  │
  ├── 1. with transaction.atomic():
  │      ├── Lock dòng User: User.objects.select_for_update().get(pk=user.pk)
  │      ├── user.groups.remove(*current_role_groups)
  │      ├── user.groups.add(target_group)
  │      ├── user.is_staff = False
  │      ├── user.is_superuser = False
  │      └── user.save(update_fields=['is_staff', 'is_superuser'])
  │
  └── 2. clear_user_permission_cache(user):
         Xóa: _perm_cache, _user_perm_cache, _group_perm_cache trên instance!
         (Kiểm tra quyền trên chính object 'user' ngay lập tức phản ánh role mới!)
```

---

## 4. Toàn bộ nội dung từng file

### File 1: `backend/apps/accounts/policies/__init__.py`
*(Vai trò: Package entrypoint, export toàn bộ hằng số Role, Action và hàm helper phân quyền)*

```python
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
```

---

### File 2: `backend/apps/accounts/policies/catalog.py`
*(Vai trò: Danh mục Business Action Codes chuẩn hóa và ánh xạ sang Django Permission codenames thực tế)*

```python
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
```

---

### File 3: `backend/apps/accounts/policies/roles.py`
*(Vai trò: Định nghĩa 3 Vai trò hệ thống, ma trận phân quyền mặc định, xóa cache permission và đổi vai trò atomic)*

```python
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
# Ghi chú: OPERATOR và VIEWER trong Chặng 03 chưa có quyền trên accounts/audit.
# Quyền DEVICE_VIEW và DEVICE_OPERATE sẽ được bổ sung ở Chặng 07 khi TV2 bàn giao model Device.
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
```

---

### File 4: `backend/apps/accounts/management/__init__.py`
*(Khai báo package management của accounts — file rỗng)*

```python
# Package management của apps.accounts
```

---

### File 5: `backend/apps/accounts/management/commands/__init__.py`
*(Khai báo package commands của accounts — file rỗng)*

```python
# Package commands của apps.accounts
```

---

### File 6: `backend/apps/accounts/management/commands/seed_roles.py`
*(Vai trò: Khởi tạo và đồng bộ 3 nhóm vai trò ADMIN, OPERATOR, VIEWER. Ném CommandError và rollback nếu thiếu permission)*

```python
"""
Management Command: seed_roles
Khởi tạo và đồng bộ 3 Vai trò hệ thống: ADMIN, OPERATOR, VIEWER cùng bộ quyền tương ứng.
Lệnh này có tính chất Idempotent: có thể chạy nhiều lần an toàn mà không làm nhân bản dữ liệu.
Nếu thiếu bất kỳ permission bắt buộc nào trong database (chưa migrate), lệnh ném CommandError
và rollback toàn bộ transaction để tránh tình trạng hệ thống rơi vào ma trận phân quyền bị thiếu.
"""
from typing import Any

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.policies.catalog import get_permission_for_action
from apps.accounts.policies.roles import ALL_ROLES, ROLE_PERMISSIONS


class Command(BaseCommand):
    help = "Khởi tạo và đồng bộ 3 nhóm vai trò (ADMIN, OPERATOR, VIEWER) và gán quyền hạn chuẩn."

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write("Bắt đầu khởi tạo và đồng bộ các vai trò hệ thống...")

        with transaction.atomic():
            for role_name in ALL_ROLES:
                group, created = Group.objects.get_or_create(name=role_name)
                action_list = ROLE_PERMISSIONS.get(role_name, [])

                # Thu thập các đối tượng Permission của Django cho danh sách action
                target_permissions = []
                for action in action_list:
                    perm_str = get_permission_for_action(action)
                    app_label, codename = perm_str.split(".", 1)
                    perm = Permission.objects.filter(
                        content_type__app_label=app_label,
                        codename=codename,
                    ).first()

                    if not perm:
                        raise CommandError(
                            f"Thiếu permission bắt buộc '{perm_str}' cho action '{action}'. "
                            "Toàn bộ thao tác seed_roles bị hủy. Vui lòng chạy 'python manage.py migrate' trước."
                        )

                    target_permissions.append(perm)

                # Đồng bộ quyền hạn vào Group (Idempotent: set() cập nhật danh sách chuẩn)
                group.permissions.set(target_permissions)

                status_label = "Tạo mới" if created else "Đồng bộ"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  - [{status_label}] Nhóm '{role_name}': Đã gán {len(target_permissions)} quyền hạn."
                    )
                )

        self.stdout.write(self.style.SUCCESS("Hoàn tất đồng bộ các vai trò và quyền hạn."))
```

---

### File 7: `backend/apps/accounts/management/commands/bootstrap_admin.py`
*(Vai trò: Khởi tạo tài khoản quản trị viên đầu tiên một cách an toàn và bảo mật, tuân thủ Rule 3 và Rule 17)*

```python
"""
Management Command: bootstrap_admin
Khởi tạo tài khoản quản trị viên đầu tiên (Bootstrap Admin) một cách an toàn và bảo mật:
- Kiểm tra tính hợp lệ của username canonical (3-64 ký tự).
- Không nhận mật khẩu qua tham số CLI argument (--password) để chống lộ qua process table / shell history (Rule 17).
  Mật khẩu được nạp qua biến môi trường DJANGO_BOOTSTRAP_ADMIN_PASSWORD / BOOTSTRAP_ADMIN_PASSWORD
  hoặc nhập qua dấu nhắc ẩn (getpass.getpass) khi chạy tương tác.
- Kiểm tra mật khẩu qua hệ thống AUTH_PASSWORD_VALIDATORS của Django (12-128 ký tự).
- Idempotent: Nếu tài khoản admin đã tồn tại, lệnh kết thúc an toàn, không thay đổi mật khẩu và không tự ý nâng quyền.
- Tài khoản được thiết lập: status=ACTIVE, must_change_password=True, is_staff=True, is_superuser=False, thuộc nhóm ADMIN.
"""
import getpass
import os
from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.accounts.models.user import validate_canonical_username
from apps.accounts.policies.roles import ROLE_ADMIN, assign_role


class Command(BaseCommand):
    help = "Khởi tạo tài khoản quản trị viên đầu tiên (Bootstrap Admin) an toàn và bảo mật."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--username",
            type=str,
            default=os.environ.get("DJANGO_BOOTSTRAP_ADMIN_USERNAME")
            or os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "admin"),
            help="Tên đăng nhập admin (mặc định lấy từ biến môi trường hoặc 'admin').",
        )
        parser.add_argument(
            "--full-name",
            type=str,
            default=os.environ.get("DJANGO_BOOTSTRAP_ADMIN_FULLNAME")
            or os.environ.get("BOOTSTRAP_ADMIN_FULLNAME", "Hệ Thống Quản Trị Viên"),
            help="Họ và tên đầy đủ của quản trị viên khởi tạo.",
        )
        parser.add_argument(
            "--email",
            type=str,
            default=os.environ.get("DJANGO_BOOTSTRAP_ADMIN_EMAIL")
            or os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "admin@iotcabinet.local"),
            help="Địa chỉ email quản trị viên khởi tạo.",
        )
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Chạy ở chế độ không tương tác. Bắt buộc cung cấp mật khẩu qua biến môi trường BOOTSTRAP_ADMIN_PASSWORD.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        raw_username = options["username"]
        full_name = options["full_name"]
        email = options["email"]
        no_input = options["no_input"]

        # 1. Chuẩn hóa và kiểm tra username canonical
        canonical_username = raw_username.strip().lower()
        try:
            validate_canonical_username(canonical_username)
        except ValidationError as e:
            raise CommandError(f"Username '{raw_username}' không hợp lệ: {e.message}") from e

        # 2. Idempotent check: Kiểm tra nếu tài khoản đã tồn tại
        if User.objects.filter(username=canonical_username).exists():
            self.stdout.write(
                self.style.WARNING(
                    f"Tài khoản quản trị viên '{canonical_username}' đã tồn tại trong hệ thống.\n"
                    "Bỏ qua khởi tạo để bảo đảm an toàn dữ liệu và không ghi đè mật khẩu hiện tại."
                )
            )
            return

        # 3. Lấy mật khẩu an toàn (Rule 17: không dùng CLI argument)
        password = os.environ.get("DJANGO_BOOTSTRAP_ADMIN_PASSWORD") or os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
        if not password:
            if no_input:
                raise CommandError(
                    "Không tìm thấy mật khẩu trong biến môi trường (DJANGO_BOOTSTRAP_ADMIN_PASSWORD hoặc "
                    "BOOTSTRAP_ADMIN_PASSWORD) khi chạy với tùy chọn --no-input."
                )
            # Nhập mật khẩu qua dấu nhắc ẩn
            password = getpass.getpass(f"Nhập mật khẩu cho Bootstrap Admin '{canonical_username}' (12-128 ký tự): ")
            password_confirm = getpass.getpass("Xác nhận lại mật khẩu: ")
            if password != password_confirm:
                raise CommandError("Mật khẩu xác nhận không khớp. Hủy thao tác khởi tạo.")

        # 4. Kiểm tra mật khẩu qua hệ thống AUTH_PASSWORD_VALIDATORS của Django
        try:
            validate_password(password)
        except ValidationError as e:
            raise CommandError(
                f"Mật khẩu không đáp ứng chính sách bảo mật: {'; '.join(e.messages)}"
            ) from e

        # 5. Đảm bảo các Vai trò hệ thống đã được đồng bộ
        call_command("seed_roles", verbosity=0)

        # 6. Khởi tạo tài khoản trong transaction atomic
        with transaction.atomic():
            user = User.objects.create_user(
                username=canonical_username,
                full_name=full_name,
                email=email,
                password=password,
            )
            user.status = User.Status.ACTIVE
            user.must_change_password = True
            user.is_staff = True
            user.is_superuser = False  # Bắt buộc False theo Rule 3
            user.save(update_fields=["status", "must_change_password", "is_staff", "is_superuser"])

            # Gán vai trò ADMIN duy nhất
            assign_role(user, ROLE_ADMIN)

        self.stdout.write(
            self.style.SUCCESS(
                f"Khởi tạo thành công tài khoản Bootstrap Admin '{canonical_username}'.\n"
                f"  - Họ và tên: {user.full_name}\n"
                f"  - Vai trò: {ROLE_ADMIN}\n"
                f"  - is_staff: {user.is_staff} | is_superuser: {user.is_superuser}\n"
                f"  - Bắt buộc đổi mật khẩu ở lần đăng nhập đầu tiên: {user.must_change_password}"
            )
        )
```

---

### File 8: `backend/apps/accounts/tests/test_models_roles.py`
*(Cập nhật toàn diện: Giữ nguyên 12 test cũ từ Patch 01, bổ sung 13 test RolePolicy và 4 test BootstrapAdmin — Tổng: 29 tests)*

```python
"""
Test module kiểm tra các Model và Chính sách Vai trò của accounts:
- Khóa chính UUID v4 của User.
- Chuẩn hóa username chữ thường và kiểm tra độ dài.
- Thuật toán băm mật khẩu Argon2id qua set_password() và check_password().
- Đồng bộ trường is_active với status cả khi dùng update_fields.
- Ghi đè get_full_name() và get_short_name().
- Kiểm tra tính hợp lệ đầu vào khi create_user().
- Bộ kiểm tra mật khẩu PasswordLengthValidator.
- Model AuthSession và LoginAttempt.
- Đồng bộ và gán quyền hạn vai trò (RolePolicyTestCase: Idempotency, Single-role, staff=True/superuser=False).
- Xóa cache permission instance khi đổi vai trò (clear_user_permission_cache).
- Chống bypass qua user_permissions trực tiếp.
- Rollback an toàn khi assign_role gặp sự cố.
- seed_roles ném CommandError khi thiếu permission.
- Lệnh quản trị khởi tạo tài khoản an toàn (BootstrapAdminCommandTestCase).
"""
import os
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.accounts.models import AuthSession, LoginAttempt, User
from apps.accounts.policies.catalog import (
    ACTION_AUDIT_VIEW,
    ACTION_USER_CREATE,
)
from apps.accounts.policies.roles import (
    ALL_ROLES,
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    assign_role,
    get_user_role,
    has_permission,
    has_role,
    require_permission,
)
from apps.accounts.security.password_validators import PasswordLengthValidator


class PasswordLengthValidatorTestCase(SimpleTestCase):
    """Kiểm tra đơn vị logic của PasswordLengthValidator không cần DB."""

    def setUp(self):
        self.validator = PasswordLengthValidator(min_length=12, max_length=128)

    def test_valid_password_passes(self):
        """Mật khẩu đủ từ 12 đến 128 ký tự phải vượt qua kiểm tra."""
        self.validator.validate("Password@123456")
        self.validator.validate("A" * 12)
        self.validator.validate("A" * 128)

    def test_too_short_password_fails(self):
        """Mật khẩu dưới 12 ký tự phải bị ném ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            self.validator.validate("Short@123")
        self.assertIn("Mật khẩu quá ngắn", str(ctx.exception))

    def test_too_long_password_fails(self):
        """Mật khẩu trên 128 ký tự phải bị ném ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            self.validator.validate("A" * 129)
        self.assertIn("Mật khẩu quá dài", str(ctx.exception))

    def test_password_is_not_trimmed(self):
        """Khoảng trắng đầu cuối không bị trim: '   123456   ' có độ dài 12 ký tự."""
        self.validator.validate("   123456   ")


class UserModelTestCase(TestCase):
    """Kiểm tra tích hợp Model User và các quan hệ trên Database."""

    def test_create_user_with_uuid_and_argon2(self):
        """Xác minh User được cấp khóa chính UUID và mật khẩu được băm bằng Argon2id."""
        user = User.objects.create_user(
            username="Test_Admin_01",
            full_name="Nguyễn Văn Quản Trị",
            password="SecurePassword@2026!"
        )

        # 1. Khóa chính phải là UUID v4 hợp lệ
        self.assertIsInstance(user.id, uuid.UUID)

        # 2. Username phải được chuyển thành chữ thường (canonicalized)
        self.assertEqual(user.username, "test_admin_01")

        # 3. Chuỗi băm mật khẩu phải sử dụng Argon2
        self.assertTrue(user.password.startswith("argon2"), f"Hash không dùng Argon2: {user.password}")
        self.assertTrue(user.check_password("SecurePassword@2026!"))
        self.assertFalse(user.check_password("WrongPassword@2026!"))

        # 4. Trạng thái ACTIVE thì is_active phải là True
        self.assertEqual(user.status, User.Status.ACTIVE)
        self.assertTrue(user.is_active)

    def test_get_full_name_and_short_name(self):
        """Xác minh get_full_name() và get_short_name() trả về đúng tên chứ không phải 'None None'."""
        user = User.objects.create_user(
            username="operator_demo",
            full_name="Nguyễn Văn An",
            password="OperatorPassword@2026!"
        )
        self.assertEqual(user.get_full_name(), "Nguyễn Văn An")
        self.assertEqual(user.get_short_name(), "Nguyễn")

    def test_create_user_rejects_invalid_username_length(self):
        """Xác minh create_user từ chối username ngắn hơn 3 ký tự."""
        with self.assertRaises(ValidationError):
            User.objects.create_user(
                username="a",
                full_name="Người Dùng Lỗi",
                password="ValidPassword@2026!"
            )

    def test_create_user_rejects_empty_or_whitespace_fullname(self):
        """Xác minh create_user từ chối họ tên toàn khoảng trắng."""
        with self.assertRaises(ValueError):
            User.objects.create_user(
                username="valid_user",
                full_name="    ",
                password="ValidPassword@2026!"
            )

    def test_create_user_rejects_short_password(self):
        """Xác minh create_user kích hoạt validator từ chối mật khẩu quá ngắn."""
        with self.assertRaises(ValidationError):
            User.objects.create_user(
                username="valid_user",
                full_name="Người Dùng Hợp Lệ",
                password="short"
            )

    def test_status_and_is_active_sync_with_update_fields_and_refresh(self):
        """
        Xác minh khóa tài khoản với save(update_fields=['status'])
        vẫn đồng bộ is_active=False xuống PostgreSQL và kiểm tra lại bằng refresh_from_db().
        """
        user = User.objects.create_user(
            username="operator_lock_test",
            full_name="Nhân viên vận hành",
            password="OperatorPassword@2026!"
        )
        self.assertTrue(user.is_active)

        # Khóa tài khoản sử dụng update_fields
        user.status = User.Status.LOCKED
        user.save(update_fields=['status'])

        # Nạp lại bản ghi từ PostgreSQL để chứng minh DB thật đã được cập nhật
        user.refresh_from_db()
        self.assertEqual(user.status, User.Status.LOCKED)
        self.assertFalse(user.is_active)

        # Mở khóa tài khoản lại
        user.status = User.Status.ACTIVE
        user.save(update_fields=['status'])
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_auth_session_lifecycle(self):
        """Xác minh vòng đời và cờ is_active của AuthSession."""
        user = User.objects.create_user(
            username="session_test_user",
            full_name="Người Dùng Test Session",
            password="ValidPassword@2026!"
        )
        now = timezone.now()
        session = AuthSession.objects.create(
            user=user,
            session_key="django_session_key_sample_123456",
            expires_at=now + timedelta(minutes=15),
            ip_address="127.0.0.1",
            user_agent="Mozilla/5.0 TestBrowser"
        )

        self.assertIsInstance(session.id, uuid.UUID)
        self.assertTrue(session.is_active)

        # Thu hồi session
        session.revoked_at = timezone.now()
        session.revoke_reason = "USER_LOGOUT"
        session.save()
        self.assertFalse(session.is_active)

    def test_login_attempt_record(self):
        """Xác minh tạo bản ghi LoginAttempt với HMAC key và UUID request ID."""
        attempt = LoginAttempt.objects.create(
            username_key="a" * 64,
            ip_key="b" * 64,
            success=False,
            failure_code="INVALID_CREDENTIALS"
        )
        self.assertIsInstance(attempt.id, uuid.UUID)
        self.assertIsInstance(attempt.request_id, uuid.UUID)
        self.assertFalse(attempt.success)
        self.assertEqual(attempt.failure_code, "INVALID_CREDENTIALS")


class RolePolicyTestCase(TestCase):
    """Kiểm tra ma trận phân quyền RBAC, tính idempotent của seed_roles và quy tắc Single-role."""

    def setUp(self):
        # Đồng bộ vai trò và quyền hạn trước mỗi test
        call_command("seed_roles", verbosity=0)

        # Tạo sẵn một tài khoản Admin và một tài khoản Operator
        self.admin_user = User.objects.create_user(
            username="admin_rbac_test",
            full_name="Quản Trị Viên RBAC",
            password="AdminPassword@2026!"
        )
        assign_role(self.admin_user, ROLE_ADMIN)

        self.operator_user = User.objects.create_user(
            username="operator_rbac_test",
            full_name="Vận Hành Viên RBAC",
            password="OperatorPassword@2026!"
        )
        assign_role(self.operator_user, ROLE_OPERATOR)

    def test_seed_roles_idempotency(self):
        """Xác minh chạy seed_roles nhiều lần không làm trùng lặp nhóm hay quyền hạn."""
        call_command("seed_roles", verbosity=0)
        call_command("seed_roles", verbosity=0)
        self.assertEqual(Group.objects.filter(name__in=ALL_ROLES).count(), 3)

    def test_seed_roles_assigns_correct_permissions(self):
        """Xác minh ADMIN được gán đúng 7 quyền, OPERATOR và VIEWER rỗng quyền trong chặng 03."""
        admin_group = Group.objects.get(name=ROLE_ADMIN)
        operator_group = Group.objects.get(name=ROLE_OPERATOR)
        viewer_group = Group.objects.get(name=ROLE_VIEWER)

        admin_perms = set(admin_group.permissions.values_list("codename", flat=True))
        expected_admin_perms = {
            "add_user", "view_user", "change_user", "delete_user",
            "view_authsession", "change_authsession", "view_auditlog"
        }
        self.assertEqual(admin_perms, expected_admin_perms)
        self.assertEqual(operator_group.permissions.count(), 0)
        self.assertEqual(viewer_group.permissions.count(), 0)

    def test_seed_roles_raises_command_error_when_permission_missing(self):
        """Xác minh seed_roles ném CommandError và rollback khi thiếu permission trong DB."""
        perm = Permission.objects.filter(content_type__app_label="accounts", codename="add_user").first()
        self.assertIsNotNone(perm)
        perm.delete()

        with self.assertRaises(CommandError) as ctx:
            call_command("seed_roles", verbosity=0)
        self.assertIn("Thiếu permission bắt buộc 'accounts.add_user'", str(ctx.exception))

    def test_assign_role_and_get_user_role(self):
        """Xác minh get_user_role() và has_role() phản ánh chính xác vai trò được gán."""
        self.assertEqual(get_user_role(self.admin_user), ROLE_ADMIN)
        self.assertTrue(has_role(self.admin_user, ROLE_ADMIN))
        self.assertFalse(has_role(self.admin_user, ROLE_OPERATOR))

        self.assertEqual(get_user_role(self.operator_user), ROLE_OPERATOR)
        self.assertTrue(has_role(self.operator_user, ROLE_OPERATOR))
        self.assertFalse(has_role(self.operator_user, ROLE_ADMIN))

    def test_assign_role_switches_role_cleanly_enforcing_single_role(self):
        """
        Xác minh quy tắc Single-role: Khi đổi vai trò, nhóm vai trò cũ phải bị gỡ bỏ,
        và cờ is_staff được đồng bộ tự động.
        """
        user = User.objects.create_user(
            username="switch_role_user",
            full_name="Người Dùng Đổi Role",
            password="ValidPassword@2026!"
        )
        assign_role(user, ROLE_ADMIN)
        self.assertEqual(user.groups.count(), 1)
        self.assertEqual(get_user_role(user), ROLE_ADMIN)
        self.assertTrue(user.is_staff)

        # Chuyển sang OPERATOR -> nhóm ADMIN bị gỡ bỏ, is_staff chuyển thành False
        assign_role(user, ROLE_OPERATOR)
        user.refresh_from_db()
        self.assertEqual(user.groups.count(), 1)
        self.assertEqual(get_user_role(user), ROLE_OPERATOR)
        self.assertFalse(user.is_staff)

    def test_assign_role_clears_instance_permission_cache(self):
        """
        Xác minh đổi vai trò trên cùng một instance User lập tức có hiệu lực,
        xóa sạch permission cache trong bộ nhớ mà không giữ quyền cũ.
        """
        user = User.objects.create_user(
            username="cache_clear_user",
            full_name="Người Dùng Xóa Cache",
            password="ValidPassword@2026!"
        )
        assign_role(user, ROLE_ADMIN)
        # Nạp cache permission của Admin vào instance
        self.assertTrue(has_permission(user, ACTION_USER_CREATE))

        # Hạ vai trò sang OPERATOR trên CHÍNH INSTANCE ĐÓ
        assign_role(user, ROLE_OPERATOR)

        # Instance phải bị tước quyền ngay lập tức
        self.assertFalse(has_permission(user, ACTION_USER_CREATE))
        with self.assertRaises(PermissionDenied):
            require_permission(user, ACTION_USER_CREATE)

    def test_assign_role_atomic_rollback_on_save_failure(self):
        """Xác minh tính nguyên tử: Nếu save() thất bại, nhóm vai trò mới không được lưu."""
        user = User.objects.create_user(
            username="atomic_rollback_user",
            full_name="Người Dùng Atomic",
            password="ValidPassword@2026!"
        )
        assign_role(user, ROLE_OPERATOR)
        self.assertEqual(get_user_role(user), ROLE_OPERATOR)

        with patch.object(User, "save", side_effect=RuntimeError("Simulated DB Crash")):
            with self.assertRaises(RuntimeError):
                assign_role(user, ROLE_ADMIN)

        # Dữ liệu trong DB vẫn bảo toàn vai trò OPERATOR ban đầu
        user_db = User.objects.get(id=user.id)
        self.assertEqual(get_user_role(user_db), ROLE_OPERATOR)
        self.assertFalse(user_db.is_staff)

    def test_multi_role_conflict_returns_none_and_denies_permission(self):
        """Xác minh tài khoản có >= 2 role (xung đột) bị coi là None và từ chối mọi quyền."""
        user = User.objects.create_user(
            username="multi_role_conflict_user",
            full_name="Người Dùng Xung Đột Role",
            password="ValidPassword@2026!"
        )
        user.groups.add(Group.objects.get(name=ROLE_ADMIN))
        user.groups.add(Group.objects.get(name=ROLE_OPERATOR))

        self.assertIsNone(get_user_role(user))
        self.assertFalse(has_role(user, ROLE_ADMIN))
        self.assertFalse(has_role(user, ROLE_OPERATOR))
        self.assertFalse(has_permission(user, ACTION_USER_CREATE))
        with self.assertRaises(PermissionDenied):
            require_permission(user, ACTION_USER_CREATE)

    def test_direct_user_permissions_bypass_is_blocked(self):
        """Xác minh người dùng không có role nhưng được gán quyền trực tiếp vẫn bị từ chối."""
        user = User.objects.create_user(
            username="direct_permission_user",
            full_name="Người Dùng Gán Quyền Trực Tiếp",
            password="ValidPassword@2026!"
        )
        perm = Permission.objects.get(codename="add_user")
        user.user_permissions.add(perm)

        self.assertIsNone(get_user_role(user))
        self.assertFalse(has_permission(user, ACTION_USER_CREATE))
        with self.assertRaises(PermissionDenied):
            require_permission(user, ACTION_USER_CREATE)

    def test_admin_has_staff_true_and_superuser_false(self):
        """Xác minh Rule 3: ADMIN có is_staff=True nhưng is_superuser=False."""
        self.assertTrue(self.admin_user.is_staff)
        self.assertFalse(self.admin_user.is_superuser)

    def test_has_permission_and_require_permission_for_admin(self):
        """Xác minh ADMIN có đủ quyền thực thi các hành động quản trị User và xem AuditLog."""
        admin = User.objects.get(id=self.admin_user.id)
        self.assertTrue(has_permission(admin, ACTION_USER_CREATE))
        self.assertTrue(has_permission(admin, ACTION_AUDIT_VIEW))
        # require_permission không được ném exception
        require_permission(admin, ACTION_USER_CREATE)
        require_permission(admin, ACTION_AUDIT_VIEW)

    def test_require_permission_denied_for_operator(self):
        """Xác minh OPERATOR bị từ chối quyền quản trị User và AuditLog, ném PermissionDenied."""
        operator = User.objects.get(id=self.operator_user.id)
        self.assertFalse(has_permission(operator, ACTION_USER_CREATE))
        self.assertFalse(has_permission(operator, ACTION_AUDIT_VIEW))
        with self.assertRaises(PermissionDenied):
            require_permission(operator, ACTION_USER_CREATE)
        with self.assertRaises(PermissionDenied):
            require_permission(operator, ACTION_AUDIT_VIEW)

    def test_require_permission_denied_for_inactive_or_anonymous_user(self):
        """Xác minh tài khoản bị khóa (is_active=False) hoặc AnonymousUser đều bị từ chối quyền."""
        admin = User.objects.get(id=self.admin_user.id)
        admin.status = User.Status.LOCKED
        admin.save(update_fields=["status"])
        admin.refresh_from_db()
        self.assertFalse(admin.is_active)
        self.assertFalse(has_permission(admin, ACTION_USER_CREATE))
        with self.assertRaises(PermissionDenied):
            require_permission(admin, ACTION_USER_CREATE)

        anon = AnonymousUser()
        self.assertFalse(has_permission(anon, ACTION_USER_CREATE))
        with self.assertRaises(PermissionDenied):
            require_permission(anon, ACTION_USER_CREATE)


class BootstrapAdminCommandTestCase(TestCase):
    """Kiểm tra hoạt động của command bootstrap_admin, tính an toàn và tính idempotent."""

    def test_bootstrap_admin_creates_active_admin_successfully(self):
        """Xác minh khởi tạo thành công tài khoản admin với đầy đủ thuộc tính chuẩn."""
        with patch.dict(os.environ, {"BOOTSTRAP_ADMIN_PASSWORD": "SuperSecureAdminPassword@2026!"}):
            call_command("bootstrap_admin", username="new_admin_01", no_input=True)

        user = User.objects.get(username="new_admin_01")
        self.assertTrue(user.is_active)
        self.assertEqual(user.status, User.Status.ACTIVE)
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(get_user_role(user), ROLE_ADMIN)
        self.assertTrue(user.check_password("SuperSecureAdminPassword@2026!"))

    def test_bootstrap_admin_idempotency_does_not_overwrite_password(self):
        """Xác minh chạy lại bootstrap_admin khi user đã có sẵn sẽ không ghi đè mật khẩu."""
        with patch.dict(os.environ, {"BOOTSTRAP_ADMIN_PASSWORD": "OriginalPassword@2026!"}):
            call_command("bootstrap_admin", username="idempotent_admin", no_input=True)

        user_original = User.objects.get(username="idempotent_admin")
        original_hash = user_original.password

        # Chạy lại với mật khẩu mới giả lập
        with patch.dict(os.environ, {"BOOTSTRAP_ADMIN_PASSWORD": "ChangedPassword@2026!"}):
            call_command("bootstrap_admin", username="idempotent_admin", no_input=True)

        user_after = User.objects.get(username="idempotent_admin")
        self.assertEqual(user_after.password, original_hash)
        self.assertTrue(user_after.check_password("OriginalPassword@2026!"))
        self.assertFalse(user_after.check_password("ChangedPassword@2026!"))

    def test_bootstrap_admin_rejects_invalid_username(self):
        """Xác minh từ chối username không đạt chuẩn canonical (ngắn hơn 3 ký tự hoặc chứa ký tự cấm)."""
        with patch.dict(os.environ, {"BOOTSTRAP_ADMIN_PASSWORD": "SuperSecureAdminPassword@2026!"}):
            with self.assertRaises(CommandError) as ctx:
                call_command("bootstrap_admin", username="a", no_input=True)
            self.assertIn("không hợp lệ", str(ctx.exception))

            with self.assertRaises(CommandError) as ctx:
                call_command("bootstrap_admin", username="bad username with space", no_input=True)
            self.assertIn("không hợp lệ", str(ctx.exception))

    def test_bootstrap_admin_rejects_short_password(self):
        """Xác minh từ chối mật khẩu không đáp ứng chính sách bảo mật (ngắn hơn 12 ký tự)."""
        with patch.dict(os.environ, {"BOOTSTRAP_ADMIN_PASSWORD": "short"}):
            with self.assertRaises(CommandError) as ctx:
                call_command("bootstrap_admin", username="valid_admin_test", no_input=True)
            self.assertIn("Mật khẩu không đáp ứng chính sách bảo mật", str(ctx.exception))
```

---

## 5. Bảng hàm, biến và trường dữ liệu (Symbols & Specifications)

| Tên biểu tượng | Thuộc file | Kiểu / Loại | Ý nghĩa / Tác dụng phụ |
|:---|:---|:---:|:---|
| `ACTION_USER_CREATE`... | `catalog.py` | `str` (Const) | Business Action Codes chuẩn cho quản trị người dùng (`USER_CREATE`, `USER_VIEW`, ...) |
| `ACTION_AUDIT_VIEW` | `catalog.py` | `str` (Const) | Business Action Code xem nhật ký kiểm toán hệ thống (`AUDIT_VIEW`) |
| `ACTION_PERMISSIONS` | `catalog.py` | `dict[str, str]` | Bảng ánh xạ Business Action Code sang Django permission (`app_label.codename`) |
| `get_permission_for_action` | `catalog.py` | Function | Tra cứu Django permission. Ném `ValueError` nếu action không tồn tại |
| `ROLE_ADMIN`, `OPERATOR`, `VIEWER` | `roles.py` | `str` (Const) | Ba hằng số vai trò duy nhất của hệ thống IoT Cabinet |
| `ROLE_PERMISSIONS` | `roles.py` | `dict[str, list[str]]` | Ma trận quyền gán cho từng vai trò trong Chặng 03 |
| `clear_user_permission_cache(user)` | `roles.py` | Function | Xóa các thuộc tính `_perm_cache`, `_user_perm_cache`, `_group_perm_cache` trên instance |
| `get_user_role(user)` | `roles.py` | Function | Trả về vai trò khi `len(roles) == 1`. Trả về `None` khi có 0 hoặc > 1 vai trò (xung đột) |
| `has_role(user, role_name)` | `roles.py` | Function | Kiểm tra user có đang hoạt động và sở hữu vai trò `role_name` không |
| `assign_role(user, role_name)` | `roles.py` | Function | Bọc atomic + select_for_update, gán 1 role duy nhất, set is_staff, xóa cache instance |
| `has_permission(user, action)` | `roles.py` | Function | Bắt buộc action thuộc ma trận của role trước khi gọi has_perm(), chặn bypass user_permissions |
| `require_permission(user, action)` | `roles.py` | Function | Bắt buộc có quyền. Ném `django.core.exceptions.PermissionDenied` nếu vi phạm |
| `Command` (`seed_roles.py`) | `seed_roles.py` | BaseCommand | Khởi tạo 3 Groups; ném `CommandError` và rollback nếu thiếu permission trong DB |
| `Command` (`bootstrap_admin.py`) | `bootstrap_admin.py` | BaseCommand | Khởi tạo tài khoản quản trị viên an toàn (Idempotent, nạp pass qua RAM/env, Rule 17) |

---

## 6. Lệnh PowerShell để người dùng tự chạy

Mọi lệnh được chạy từ thư mục gốc của dự án:
```powershell
Set-Location -Path "c:\Users\taday\OneDrive\Documents\PTIT\IOT\IoT_cabinet"
```

### Bước 1: Tạo các thư mục mới cho policies và management commands
```powershell
New-Item -ItemType Directory -Force -Path "backend\apps\accounts\policies"
New-Item -ItemType Directory -Force -Path "backend\apps\accounts\management\commands"
```

### Bước 2: Copy lần lượt 8 file ở Mục 4 vào đúng vị trí

### Bước 3: Đồng bộ các vai trò và quyền hạn bằng `seed_roles`
```powershell
.\.venv\Scripts\python.exe backend/manage.py seed_roles --settings=config.settings
```
**Expected output:**
```text
Bắt đầu khởi tạo và đồng bộ các vai trò hệ thống...
  - [Tạo mới] Nhóm 'ADMIN': Đã gán 7 quyền hạn.
  - [Tạo mới] Nhóm 'OPERATOR': Đã gán 0 quyền hạn.
  - [Tạo mới] Nhóm 'VIEWER': Đã gán 0 quyền hạn.
Hoàn tất đồng bộ các vai trò và quyền hạn.
```

### Bước 4: Khởi tạo tài khoản quản trị viên đầu tiên bằng `bootstrap_admin`
- **Cách A: Chạy tương tác với dấu nhắc ẩn (Khuyến nghị thực tế)**:
  ```powershell
  .\.venv\Scripts\python.exe backend/manage.py bootstrap_admin --settings=config.settings
  ```
  *(Hệ thống sẽ nhắc bạn nhập mật khẩu ẩn, tối thiểu 12 ký tự và yêu cầu xác nhận lại)*

- **Cách B: Chạy tự động qua biến môi trường (Cho script/CI)**:
  ```powershell
  $env:BOOTSTRAP_ADMIN_PASSWORD = "SuperSecureAdminPassword@2026!"
  .\.venv\Scripts\python.exe backend/manage.py bootstrap_admin --no-input --settings=config.settings
  Remove-Item Env:\BOOTSTRAP_ADMIN_PASSWORD
  ```
**Expected output:**
```text
Khởi tạo thành công tài khoản Bootstrap Admin 'admin'.
  - Họ và tên: Hệ Thống Quản Trị Viên
  - Vai trò: ADMIN
  - is_staff: True | is_superuser: False
  - Bắt buộc đổi mật khẩu ở lần đăng nhập đầu tiên: True
```

### Bước 5: Chạy lại `bootstrap_admin` để kiểm chứng tính Idempotent
```powershell
$env:BOOTSTRAP_ADMIN_PASSWORD = "AnyPassword@123456"
.\.venv\Scripts\python.exe backend/manage.py bootstrap_admin --no-input --settings=config.settings
Remove-Item Env:\BOOTSTRAP_ADMIN_PASSWORD
```
**Expected output:**
```text
Tài khoản quản trị viên 'admin' đã tồn tại trong hệ thống.
Bỏ qua khởi tạo để bảo đảm an toàn dữ liệu và không ghi đè mật khẩu hiện tại.
```

### Bước 6: Chạy toàn bộ bộ test kiểm tra (Yêu cầu chính xác 60/60 tests PASS)
```powershell
.\.venv\Scripts\python.exe backend/manage.py test apps --settings=config.test_settings
```
**Expected output:**
```text
Found 60 test(s).
Creating test database for alias 'default' ('iot_cabinet_django_test')...
[DATABASE GUARD PASS] Database test 'iot_cabinet_django_test' duoc xac nhan an toan de khoi tao.
............................................................
----------------------------------------------------------------------
Ran 60 tests in X.XXs

OK
Destroying test database for alias 'default' ('iot_cabinet_django_test')...
```

---

## 7. Acceptance Checklist và Câu hỏi tự kiểm tra

### Checklist nghiệm thu:
- [x] Đúng 3 vai trò hệ thống: `ADMIN`, `OPERATOR`, `VIEWER` được khởi tạo qua Django Group.
- [x] Bảng `accounts_user` **không có cột `role` thứ hai**; kiểm soát vai trò hoàn toàn qua `Group`.
- [x] `get_user_role` trả về `None` khi tài khoản có 0 hoặc $\ge 2$ vai trò (xung đột); `has_permission` kiểm tra qua ma trận role, chặn bypass qua `user_permissions`.
- [x] `clear_user_permission_cache` được gọi trong `assign_role`, xóa cache trên instance ngay lập tức.
- [x] `assign_role` bọc trong `transaction.atomic()` và `select_for_update()`, bảo đảm rollback nếu `save()` lỗi.
- [x] `seed_roles` ném `CommandError` và rollback khi thiếu permission bắt buộc trong DB.
- [x] `ADMIN` có `is_staff=True` nhưng `is_superuser=False` (Rule 3).
- [x] Lệnh `bootstrap_admin` tuân thủ Rule 17 (không nhận password qua CLI argument), chạy lại không ghi đè mật khẩu.
- [x] Bộ test đạt đủ **60/60 tests PASS** (5 database guard + 11 foundation + 15 audit + 29 accounts).

---

## 8. Gói gửi reviewer

```text
Review: TV3-DJ-03 — Roles/Permissions và Tài Khoản Bootstrap (Bản Chuẩn Hóa Chốt)
Baseline: 43 tests PASS từ Chặng 01 và Chặng 02
Đã copy: 
  - backend/apps/accounts/policies/__init__.py
  - backend/apps/accounts/policies/catalog.py
  - backend/apps/accounts/policies/roles.py
  - backend/apps/accounts/management/__init__.py
  - backend/apps/accounts/management/commands/__init__.py
  - backend/apps/accounts/management/commands/seed_roles.py
  - backend/apps/accounts/management/commands/bootstrap_admin.py
  - backend/apps/accounts/tests/test_models_roles.py (29 tests)
Tôi tự chạy: 
  - python backend/manage.py seed_roles (Đồng bộ thành công 3 vai trò, ném lỗi nếu thiếu perm)
  - python backend/manage.py bootstrap_admin (Khởi tạo admin an toàn, idempotent)
  - python backend/manage.py test apps --settings=config.test_settings (60/60 tests PASS)
Tôi hiểu flow:
  1. get_user_role chỉ nhận đúng 1 role duy nhất; nếu mâu thuẫn >= 2 role trả về None.
  2. has_permission bắt buộc action thuộc ma trận role, triệt tiêu nguy cơ bypass qua user_permissions.
  3. assign_role chạy trong atomic + select_for_update và xóa cache permission trên instance.
  4. seed_roles rollback và ném CommandError nếu thiếu permission trong DB.
Tôi chưa hiểu: (Nếu có thắc mắc, ghi cụ thể tại đây)
Nhờ đọc và review; không tự sửa source code.
```

---

DỪNG TẠI PHẦN TV3-DJ-03. Chưa sang phần tiếp. Chờ bạn copy, chạy kiểm tra và gửi kết quả/review.
