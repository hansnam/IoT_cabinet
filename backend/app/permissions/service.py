"""
Permission Service & RBAC Device Scoping Engine (Thành viên 3 chủ trì - TV3-04A Revision 3)
Cung cấp dịch vụ kiểm tra quyền hạn, phạm vi truy cập thiết bị (SQL Device Scope)
và guard thẩm quyền dispatch lệnh cho worker:
1. Catalog 28 quyền P0 (Global, Device, Self); từ chối toàn bộ quyền lạ và 3 mã hoãn EXT.
2. Kiểm tra quyền hạn trực tiếp từ database (ACTIVE user, UserRole, RolePermission, DeviceAccess, AuthSession).
3. Đọc dữ liệu dạng scalar snapshot, không dùng cache ORM/identity map, không bypass quyền đối với ADMIN.
4. Ràng buộc chặt chẽ session-user: session bắt buộc thuộc về đúng user_id của actor.
5. Đảm bảo 404 Not Found ngoài VIEW scope trước khi kiểm tra Action Permission (403).
6. visible_devices_query tạo câu lệnh SQLAlchemy Select lọc phạm vi tại tầng SQL.
7. authorize_dispatch thẩm định lệnh điều khiển và cấu hình bền vững cho TV2 worker.
8. Mọi thao tác đều là Read-only (no_autoflush, không commit/rollback/touch).
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from backend.app.extensions import db
from backend.app.models.command import Command
from backend.app.models.device import Device, DeviceAccess, DeviceConfig
from backend.app.models.iam import AuthSession, Permission, Role, RolePermission, User, UserRole
from backend.app.permissions.messages import DispatchReason
from backend.app.security.context import AuthContext, get_current_auth
from backend.app.security.messages import ErrorCode, ErrorMessage


# ==============================================================================
# HỆ THỐNG NGOẠI LỆ ĐẶC THÙ CHO PERMISSIONS (TRÁNH XUNG ĐỘT BUILT-IN PYTHON)
# ==============================================================================

class AuthorizationError(Exception):
    """Ngoại lệ cơ sở cho các lỗi liên quan đến xác thực và phân quyền trong TV3."""
    def __init__(self, message: str = ErrorMessage.FORBIDDEN, code: str = ErrorCode.FORBIDDEN, status_code: int = 403):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code

class PermissionDenied(AuthorizationError):
    """Từ chối truy cập do thiếu quyền hạn (HTTP 403 Forbidden)."""
    def __init__(self, message: str = ErrorMessage.FORBIDDEN, code: str = ErrorCode.FORBIDDEN):
        super().__init__(message, code, 403)


class DeviceScopeNotFound(AuthorizationError):
    """Thiết bị không tồn tại hoặc nằm ngoài phạm vi VIEW scope (HTTP 404 Not Found)."""
    def __init__(self, message: str = ErrorMessage.NOT_FOUND, code: str = ErrorCode.NOT_FOUND):
        super().__init__(message, code, 404)


class AuthRequired(AuthorizationError):
    """Yêu cầu xác thực hợp lệ (HTTP 401 Unauthorized)."""
    def __init__(self, message: str = ErrorMessage.UNAUTHORIZED, code: str = ErrorCode.UNAUTHORIZED):
        super().__init__(message, code, 401)

# ==============================================================================
# CẤU TRÚC KẾT QUẢ THẨM QUYỀN DISPATCH LỆNH CHO WORKER
# ==============================================================================

@dataclass(frozen=True)
class DispatchAuthorizationResult:
    """Kết quả kiểm tra thẩm quyền dispatch lệnh."""
    allowed: bool
    reason: str
    command_id: UUID | None = None
    device_id: str | None = None


# ==============================================================================
# CATALOG 28 QUYỀN P0 VÀ 3 MÃ QUYỀN HOÃN EXT
# ==============================================================================

P0_SUPPORTED_PERMISSIONS = frozenset({
    # Device Scope Permissions (VIEW)
    "device.read",
    "telemetry.read",
    "telemetry.export",
    "command.read",
    "alert.read",
    "activity.read",
    # Device Scope Permissions (OPERATE)
    "device.control",
    "device.configure",
    "alert.acknowledge",
    "alert.resolve",
    # Global Administrative Permissions
    "user.read",
    "user.create",
    "user.update",
    "user.lock",
    "user.role.assign",
    "user.password.reset",
    "device.register",
    "device.provision",
    "device.activate",
    "device.access.manage",
    "device.lifecycle.manage",
    "device.credential.rotate",
    "location.manage",
    "device_group.manage",
    "audit.read",
    "user_activity.read",
    # Self Permissions
    "profile.update",
    "password.change",
})

# 3 mã quyền dự phòng bị hoãn (EXT), luôn từ chối ở P0 kể cả đối với ADMIN
EXT_RESERVED_PERMISSIONS = frozenset({
    "user_activity.self.read",
    "session.self.manage",
    "session.admin.revoke",
})

# Quyền thao tác đòi hỏi cấp độ OPERATE trên thiết bị
DEVICE_OPERATE_PERMISSIONS = frozenset({
    "device.control",
    "device.configure",
    "alert.acknowledge",
    "alert.resolve",
})

DEVICE_VIEW_PERMISSIONS = frozenset({
    "device.read",
    "telemetry.read",
    "telemetry.export",
    "command.read",
    "alert.read",
    "activity.read",
})

DEVICE_PERMISSIONS = DEVICE_VIEW_PERMISSIONS | DEVICE_OPERATE_PERMISSIONS


# ==============================================================================
# PERMISSION SERVICE
# ==============================================================================

class PermissionService:
    """Dịch vụ trung tâm kiểm soát thẩm quyền và phạm vi thiết bị."""

    @staticmethod
    def require(
            permission: str,
            device_id: str | None = None,
            *,
            actor: AuthContext | None = None,
            db_session: Session | None = None,
            now: datetime | None = None,
    ) -> bool:
        """Kiểm tra quyền hạn và phạm vi truy cập thiết bị của actor.

        Quy tắc thực thi:
        1. Xác định actor: lấy từ request context nếu HTTP, hoặc actor truyền vào nếu worker.
        2. Ràng buộc session-user: User phải ACTIVE, AuthSession phải thuộc về chính user đó,
           chưa bị thu hồi, chưa hết hạn và khớp token_version.
        3. Nếu kiểm tra có device_id:
           - Thiết bị không tồn tại -> trả về 404.
           - User không có grant VIEW còn hạn (ADMIN có global scope) -> trả về 404 (chống lộ device).
           - Có VIEW nhưng permission là unknown hoặc mã hoãn EXT -> trả về 403.
           - Có VIEW nhưng Role trong DB không có quyền này -> trả về 403.
           - Có VIEW nhưng action đòi hỏi OPERATE mà grant chỉ là VIEW -> trả về 403.
        4. Nếu kiểm tra không có device_id (Global / Self):
           - Permission unknown hoặc EXT -> trả về 403.
           - Role trong DB không có quyền này -> trả về 403.
        """
        session = db_session if db_session is not None else db.session
        now_utc = now if now is not None else datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        # 1. Xác thực danh tính actor
        if actor is None:
            actor = get_current_auth()
        if actor is None or not isinstance(actor, AuthContext) or actor.user_id is None:
            raise AuthRequired(ErrorMessage.UNAUTHORIZED, ErrorCode.UNAUTHORIZED)

        # Chỉ hỗ trợ actor USER trong P0 (SYSTEM không đi qua actor USER giả)
        if actor.actor_type != "USER" or actor.session_id is None:
            raise AuthRequired(ErrorMessage.UNAUTHORIZED, ErrorCode.UNAUTHORIZED)

        with session.no_autoflush:
            # 2. Đọc scalar snapshot của User và AuthSession trực tiếp từ DB (bỏ qua identity map cache)
            # Ràng buộc điều kiện join kép bằng cả session_id và user_id
            session_row = session.execute(
                select(
                    User.status,
                    User.token_version.label("user_ver"),
                    Role.code,
                    AuthSession.revoked_at,
                    AuthSession.expires_at,
                    AuthSession.token_version.label("session_ver"),
                    AuthSession.user_id.label("session_user_id"),
                )
                .select_from(User)
                .outerjoin(UserRole, UserRole.user_id == User.id)
                .outerjoin(Role, Role.id == UserRole.role_id)
                .outerjoin(
                    AuthSession,
                    (AuthSession.id == actor.session_id) & (AuthSession.user_id == User.id)
                )
                .where(User.id == actor.user_id)
            ).first()

            if session_row is None or session_row.status != "ACTIVE":
                raise AuthRequired(ErrorMessage.UNAUTHORIZED, ErrorCode.UNAUTHORIZED)

            # Người dùng hợp lệ bắt buộc phải có ít nhất 1 role được gán trong hệ thống
            if session_row.code is None:
                raise AuthRequired(ErrorMessage.UNAUTHORIZED, ErrorCode.UNAUTHORIZED)

            # Ràng buộc session-user: session phải tồn tại, thuộc về chính actor, chưa revoke, chưa hết hạn, đúng version
            if (
                    session_row.session_user_id is None
                    or session_row.session_user_id != actor.user_id
                    or session_row.revoked_at is not None
                    or session_row.expires_at <= now_utc
                    or session_row.session_ver != session_row.user_ver
                    or actor.token_version != session_row.user_ver
            ):
                raise AuthRequired(ErrorMessage.UNAUTHORIZED, ErrorCode.UNAUTHORIZED)

            role_code = session_row.code

            # 3. Kiểm tra với phạm vi thiết bị (device_id được truyền vào)
            if device_id is not None:
                # 3.1 Kiểm tra sự tồn tại của thiết bị trong DB
                dev_exists = session.execute(
                    select(Device.device_id).where(Device.device_id == str(device_id))
                ).scalar_one_or_none()
                if not dev_exists:
                    # Thiết bị không tồn tại -> 404 trước action check
                    raise DeviceScopeNotFound(ErrorMessage.NOT_FOUND, ErrorCode.NOT_FOUND)

                # 3.2 Kiểm tra quyền VIEW scope trên thiết bị
                is_admin = (role_code == "ADMIN")
                user_grant_level = None

                if is_admin:
                    user_grant_level = "OPERATE"
                else:
                    grant = session.execute(
                        select(DeviceAccess.access_level, DeviceAccess.expires_at)
                        .where(
                            DeviceAccess.device_id == str(device_id),
                            DeviceAccess.user_id == actor.user_id,
                        )
                    ).first()

                    if grant is None:
                        raise DeviceScopeNotFound(ErrorMessage.NOT_FOUND, ErrorCode.NOT_FOUND)

                    if grant.expires_at is not None and grant.expires_at <= now_utc:
                        raise DeviceScopeNotFound(ErrorMessage.NOT_FOUND, ErrorCode.NOT_FOUND)

                    user_grant_level = grant.access_level
                # 3.3 Đã thỏa mãn VIEW scope. Tiếp tục kiểm tra Action Permission:
                if permission in EXT_RESERVED_PERMISSIONS or permission not in P0_SUPPORTED_PERMISSIONS:
                    raise PermissionDenied(ErrorMessage.FORBIDDEN, ErrorCode.FORBIDDEN)

                has_perm = session.execute(
                    select(Permission.code)
                    .select_from(UserRole)
                    .join(RolePermission, RolePermission.role_id == UserRole.role_id)
                    .join(Permission, Permission.id == RolePermission.permission_id)
                    .where(
                        UserRole.user_id == actor.user_id,
                        Permission.code == permission,
                    )
                ).scalar_one_or_none()
                if not has_perm:
                    raise PermissionDenied(ErrorMessage.FORBIDDEN, ErrorCode.FORBIDDEN)

                # Nếu hành động đòi hỏi cấp độ OPERATE, kiểm tra cấp độ grant
                if permission in DEVICE_OPERATE_PERMISSIONS:
                    if user_grant_level != "OPERATE":
                        raise PermissionDenied(ErrorMessage.FORBIDDEN, ErrorCode.FORBIDDEN)

                return True

            # 4. Kiểm tra quyền không gắn với thiết bị (Global hoặc Self)
            if permission in EXT_RESERVED_PERMISSIONS or permission not in P0_SUPPORTED_PERMISSIONS:
                raise PermissionDenied(ErrorMessage.FORBIDDEN, ErrorCode.FORBIDDEN)

            has_perm = session.execute(
                select(Permission.code)
                .select_from(UserRole)
                .join(RolePermission, RolePermission.role_id == UserRole.role_id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(
                    UserRole.user_id == actor.user_id,
                    Permission.code == permission,
                )
            ).scalar_one_or_none()
            if not has_perm:
                raise PermissionDenied(ErrorMessage.FORBIDDEN, ErrorCode.FORBIDDEN)

            return True


    @staticmethod
    def visible_devices_query(
            user: Any,
            *,
            db_session: Session | None = None,
            now: datetime | None = None,
    ) -> Select:
        """Tạo câu lệnh SQLAlchemy Select(Device) có điều kiện lọc phạm vi trực tiếp tại SQL.

        - ADMIN: Trả về toàn bộ thiết bị (Global Scope).
        - Non-admin: Inner join với device_access lấy các thiết bị có grant còn hạn.
        - Không nạp danh sách vào Python, cho phép caller gắn thêm filter/count/order_by/limit.
        """
        session = db_session if db_session is not None else db.session
        now_utc = now if now is not None else datetime.now(timezone.utc)

        if isinstance(user, AuthContext):
            uid = user.user_id
        elif hasattr(user, "id"):
            uid = user.id
        elif isinstance(user, (UUID, str)):
            uid = UUID(str(user))
        else:
            raise ValueError("Invalid user parameter")

        with session.no_autoflush:
            role_code = session.execute(
                select(Role.code)
                .select_from(UserRole)
                .join(Role, Role.id == UserRole.role_id)
                .where(UserRole.user_id == uid)
            ).scalar_one_or_none()

            if role_code == "ADMIN":
                return select(Device)

            stmt = (
                select(Device)
                .join(DeviceAccess, DeviceAccess.device_id == Device.device_id)
                .where(
                    DeviceAccess.user_id == uid,
                    DeviceAccess.access_level.in_(("VIEW", "OPERATE")),
                    or_(
                        DeviceAccess.expires_at.is_(None),
                        DeviceAccess.expires_at > now_utc,
                    ),
                )
            )
            return stmt

    @staticmethod
    def get_user_role(user_id: UUID | str, *, db_session: Session | None = None) -> str | None:
        """Helper lấy mã vai trò hiện tại của người dùng từ DB (phục vụ /auth/me)."""
        session = db_session if db_session is not None else db.session
        uid = UUID(str(user_id)) if not isinstance(user_id, UUID) else user_id
        with session.no_autoflush:
            return session.execute(
                select(Role.code)
                .select_from(UserRole)
                .join(Role, Role.id == UserRole.role_id)
                .where(UserRole.user_id == uid)
            ).scalar_one_or_none()

    @staticmethod
    def get_user_permissions(user_id: UUID | str, *, db_session: Session | None = None) -> list[str]:
        """Helper lấy danh sách mã quyền P0 hiện tại của người dùng từ DB."""
        session = db_session if db_session is not None else db.session
        uid = UUID(str(user_id)) if not isinstance(user_id, UUID) else user_id
        with session.no_autoflush:
            perms = session.execute(
                select(Permission.code)
                .select_from(UserRole)
                .join(RolePermission, RolePermission.role_id == UserRole.role_id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(UserRole.user_id == uid)
            ).scalars().all()
            return sorted([
                p for p in perms
                if p in P0_SUPPORTED_PERMISSIONS and p not in EXT_RESERVED_PERMISSIONS
            ])

    @staticmethod
    def authorize_dispatch(
            command_or_id: Command | UUID | str,
            *,
            db_session: Session | None = None,
            now: datetime | None = None,
    ) -> DispatchAuthorizationResult:
        """Thẩm định thẩm quyền dispatch lệnh dành cho worker trước khi gửi qua MQTT.

        Quy tắc thẩm định:
        1. Trích xuất ID và đọc scalar snapshot trực tiếp từ DB; từ chối lệnh chưa persist.
        2. Đọc Device scalar snapshot từ DB; bỏ qua thuộc tính bẩn chỉ gán trong RAM.
        3. Với APPLY_CONFIG: Tra cứu DeviceConfig tồn tại TRƯỚC MỌI NHÁNH ALLOW.
           - Re-sync cấu hình đã áp dụng (applied_config_version): Cho phép dù tác giả đã logout.
           - First apply: Tác giả phải còn ACTIVE và có quyền configure + OPERATE scope.
        4. Với lệnh thủ công USER (SET_MODE, SET_FAN):
           - Người yêu cầu phải ACTIVE, phiên phải tồn tại, thuộc đúng user, chưa revoke, chưa hết hạn, đúng token version.
           - Phải có quyền role 'device.control' và grant 'OPERATE' còn hạn.
        5. Tuyệt đối không commit, rollback, dispatch hay thay đổi dữ liệu trong database.
        """
        session = db_session if db_session is not None else db.session
        now_utc = now if now is not None else datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        # 1. Xác định Command ID
        if isinstance(command_or_id, Command):
            cid = command_or_id.command_id
        elif isinstance(command_or_id, (UUID, str)):
            cid = UUID(str(command_or_id))
        else:
            return DispatchAuthorizationResult(
                allowed=False,
                reason=DispatchReason.INVALID_COMMAND_IDENTIFIER,
            )

        with session.no_autoflush:
            # 2. Đọc scalar snapshot của Command từ DB (bỏ qua dirty/cached instance)
            cmd_row = session.execute(
                select(
                    Command.command_id,
                    Command.device_id,
                    Command.source,
                    Command.type,
                    Command.config_version,
                    Command.requested_by,
                    Command.requesting_session_id,
                    Command.status,
                ).where(Command.command_id == cid)
            ).first()

            if cmd_row is None:
                return DispatchAuthorizationResult(
                    allowed=False,
                    reason=DispatchReason.COMMAND_NOT_PERSISTED,
                )

            # 3. Kiểm tra nguồn phát sinh lệnh (chỉ hỗ trợ USER và SYSTEM ở P0)
            if cmd_row.source not in ("USER", "SYSTEM"):
                return DispatchAuthorizationResult(
                    allowed=False,
                    reason=DispatchReason.UNSUPPORTED_SOURCE,
                    command_id=cmd_row.command_id,
                    device_id=cmd_row.device_id,
                )

            # 4. Đọc scalar snapshot của Device từ DB
            device_row = session.execute(
                select(
                    Device.device_id,
                    Device.applied_config_version,
                    Device.lifecycle_state,
                ).where(Device.device_id == cmd_row.device_id)
            ).first()

            if device_row is None:
                return DispatchAuthorizationResult(
                    allowed=False,
                    reason=DispatchReason.DEVICE_NOT_FOUND,
                    command_id=cmd_row.command_id,
                    device_id=cmd_row.device_id,
                )

            # 5. Thẩm định lệnh cấu hình APPLY_CONFIG
            if cmd_row.type == "APPLY_CONFIG":
                target_version = cmd_row.config_version
                if target_version is None:
                    return DispatchAuthorizationResult(
                        allowed=False,
                        reason=DispatchReason.CONFIG_VERSION_MISSING,
                        command_id=cmd_row.command_id,
                        device_id=cmd_row.device_id,
                    )

                # Bắt buộc tra cứu bản ghi DeviceConfig hợp lệ từ DB TRƯỚC MỌI NHÁNH ALLOW
                cfg_row = session.execute(
                    select(
                        DeviceConfig.version,
                        DeviceConfig.created_by,
                    ).where(
                        DeviceConfig.device_id == cmd_row.device_id,
                        DeviceConfig.version == target_version,
                    )
                ).first()

                if cfg_row is None:
                    return DispatchAuthorizationResult(
                        allowed=False,
                        reason=DispatchReason.CONFIG_NOT_FOUND,
                        command_id=cmd_row.command_id,
                        device_id=cmd_row.device_id,
                    )

                if cfg_row.created_by is None:
                    return DispatchAuthorizationResult(
                        allowed=False,
                        reason=DispatchReason.CONFIG_AUTHOR_MISSING,
                        command_id=cmd_row.command_id,
                        device_id=cmd_row.device_id,
                    )

                # 5.1 Nhánh Re-sync: Cấu hình đã áp dụng thành công (đọc từ DB, không tin RAM)
                if device_row.applied_config_version == target_version:
                    return DispatchAuthorizationResult(
                        allowed=True,
                        reason=DispatchReason.AUTHORIZED_APPLIED_CONFIG,
                        command_id=cmd_row.command_id,
                        device_id=cmd_row.device_id,
                    )

                # 5.2 Nhánh First Apply: Kiểm tra tác giả cấu hình (cfg_row.created_by)
                author_row = session.execute(
                    select(User.status, Role.code)
                    .select_from(User)
                    .outerjoin(UserRole, UserRole.user_id == User.id)
                    .outerjoin(Role, Role.id == UserRole.role_id)
                    .where(User.id == cfg_row.created_by)
                ).first()

                if author_row is None or author_row.status != "ACTIVE":
                    return DispatchAuthorizationResult(
                        allowed=False,
                        reason=DispatchReason.AUTHOR_INACTIVE,
                        command_id=cmd_row.command_id,
                        device_id=cmd_row.device_id,
                    )

                # Kiểm tra tác giả có quyền role 'device.configure'
                has_configure = session.execute(
                    select(Permission.code)
                    .select_from(UserRole)
                    .join(RolePermission, RolePermission.role_id == UserRole.role_id)
                    .join(Permission, Permission.id == RolePermission.permission_id)
                    .where(
                        UserRole.user_id == cfg_row.created_by,
                        Permission.code == "device.configure",
                    )
                ).scalar_one_or_none()

                if not has_configure:
                    return DispatchAuthorizationResult(
                        allowed=False,
                        reason=DispatchReason.AUTHOR_PERMISSION_DENIED,
                        command_id=cmd_row.command_id,
                        device_id=cmd_row.device_id,
                    )

                # Kiểm tra tác giả có phạm vi OPERATE trên thiết bị (trừ khi là ADMIN)
                if author_row.code != "ADMIN":
                    author_grant = session.execute(
                        select(DeviceAccess.access_level, DeviceAccess.expires_at)
                        .where(
                            DeviceAccess.device_id == cmd_row.device_id,
                            DeviceAccess.user_id == cfg_row.created_by,
                        )
                    ).first()

                    if author_grant is None or author_grant.access_level != "OPERATE":
                        return DispatchAuthorizationResult(
                            allowed=False,
                            reason=DispatchReason.AUTHOR_SCOPE_DENIED,
                            command_id=cmd_row.command_id,
                            device_id=cmd_row.device_id,
                        )

                    if author_grant.expires_at is not None and author_grant.expires_at <= now_utc:
                        return DispatchAuthorizationResult(
                            allowed=False,
                            reason=DispatchReason.AUTHOR_SCOPE_EXPIRED,
                            command_id=cmd_row.command_id,
                            device_id=cmd_row.device_id,
                        )

                # First apply hợp lệ (Session của tác giả hết hạn KHÔNG hủy desired config)
                return DispatchAuthorizationResult(
                    allowed=True,
                    reason=DispatchReason.AUTHORIZED_FIRST_APPLY,
                    command_id=cmd_row.command_id,
                    device_id=cmd_row.device_id,
                )

            # 6. Thẩm định lệnh điều khiển thiết bị (SET_MODE, SET_FAN)
            if cmd_row.type in ("SET_MODE", "SET_FAN"):
                if cmd_row.source == "USER":
                    if cmd_row.requested_by is None or cmd_row.requesting_session_id is None:
                        return DispatchAuthorizationResult(
                            allowed=False,
                            reason=DispatchReason.MISSING_USER_OR_SESSION,
                            command_id=cmd_row.command_id,
                            device_id=cmd_row.device_id,
                        )

                    # Đọc User và AuthSession scalar trực tiếp từ DB
                    # Ràng buộc điều kiện join kép bằng cả session_id và user_id
                    user_sess_row = session.execute(
                        select(
                            User.status,
                            User.token_version.label("user_ver"),
                            Role.code,
                            AuthSession.revoked_at,
                            AuthSession.expires_at,
                            AuthSession.token_version.label("session_ver"),
                            AuthSession.user_id.label("session_user_id"),
                        )
                        .select_from(User)
                        .outerjoin(UserRole, UserRole.user_id == User.id)
                        .outerjoin(Role, Role.id == UserRole.role_id)
                        .outerjoin(
                            AuthSession,
                            (AuthSession.id == cmd_row.requesting_session_id) & (AuthSession.user_id == User.id)
                        )
                        .where(User.id == cmd_row.requested_by)
                    ).first()

                    if user_sess_row is None or user_sess_row.status != "ACTIVE":
                        return DispatchAuthorizationResult(
                            allowed=False,
                            reason=DispatchReason.USER_INACTIVE,
                            command_id=cmd_row.command_id,
                            device_id=cmd_row.device_id,
                        )

                    if (
                            user_sess_row.session_user_id is None
                            or user_sess_row.session_user_id != cmd_row.requested_by
                    ):
                        return DispatchAuthorizationResult(
                            allowed=False,
                            reason=DispatchReason.SESSION_NOT_FOUND,
                            command_id=cmd_row.command_id,
                            device_id=cmd_row.device_id,
                        )

                    if user_sess_row.revoked_at is not None:
                        return DispatchAuthorizationResult(
                            allowed=False,
                            reason=DispatchReason.SESSION_REVOKED,
                            command_id=cmd_row.command_id,
                            device_id=cmd_row.device_id,
                        )

                    if user_sess_row.expires_at <= now_utc:
                        return DispatchAuthorizationResult(
                            allowed=False,
                            reason=DispatchReason.SESSION_EXPIRED,
                            command_id=cmd_row.command_id,
                            device_id=cmd_row.device_id,
                        )

                    if user_sess_row.session_ver != user_sess_row.user_ver:
                        return DispatchAuthorizationResult(
                            allowed=False,
                            reason=DispatchReason.TOKEN_VERSION_MISMATCH,
                            command_id=cmd_row.command_id,
                            device_id=cmd_row.device_id,
                        )

                    # Kiểm tra quyền role 'device.control'
                    has_control = session.execute(
                        select(Permission.code)
                        .select_from(UserRole)
                        .join(RolePermission, RolePermission.role_id == UserRole.role_id)
                        .join(Permission, Permission.id == RolePermission.permission_id)
                        .where(
                            UserRole.user_id == cmd_row.requested_by,
                            Permission.code == "device.control",
                        )
                    ).scalar_one_or_none()

                    if not has_control:
                        return DispatchAuthorizationResult(
                            allowed=False,
                            reason=DispatchReason.PERMISSION_DENIED,
                            command_id=cmd_row.command_id,
                            device_id=cmd_row.device_id,
                        )

                    # Kiểm tra phạm vi OPERATE trên thiết bị
                    if user_sess_row.code != "ADMIN":
                        grant = session.execute(
                            select(DeviceAccess.access_level, DeviceAccess.expires_at)
                            .where(
                                DeviceAccess.device_id == cmd_row.device_id,
                                DeviceAccess.user_id == cmd_row.requested_by,
                            )
                        ).first()

                        if grant is None or grant.access_level != "OPERATE":
                            return DispatchAuthorizationResult(
                                allowed=False,
                                reason=DispatchReason.SCOPE_DENIED,
                                command_id=cmd_row.command_id,
                                device_id=cmd_row.device_id,
                            )

                        if grant.expires_at is not None and grant.expires_at <= now_utc:
                            return DispatchAuthorizationResult(
                                allowed=False,
                                reason=DispatchReason.SCOPE_EXPIRED,
                                command_id=cmd_row.command_id,
                                device_id=cmd_row.device_id,
                            )

                    return DispatchAuthorizationResult(
                        allowed=True,
                        reason=DispatchReason.AUTHORIZED,
                        command_id=cmd_row.command_id,
                        device_id=cmd_row.device_id,
                    )

                elif cmd_row.source == "SYSTEM":
                    # Lệnh điều khiển vật lý (SET_FAN, SET_MODE) không phát sinh tự động từ SYSTEM ở P0
                    return DispatchAuthorizationResult(
                        allowed=False,
                        reason=DispatchReason.SYSTEM_CONTROL_NOT_PERMITTED,
                        command_id=cmd_row.command_id,
                        device_id=cmd_row.device_id,
                    )

            return DispatchAuthorizationResult(
                allowed=False,
                reason=DispatchReason.UNKNOWN_COMMAND_TYPE,
                command_id=cmd_row.command_id,
                device_id=cmd_row.device_id,
            )