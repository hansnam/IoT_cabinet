"""
User Management Service Module
Quản lý các nghiệp vụ cập nhật hồ sơ người dùng và đổi mật khẩu:
- update_profile: Khóa dòng User, cập nhật full_name, ghi AuditLog nguyên tử.
- change_password: Giới hạn tốc độ riêng biệt (purpose='CHANGE_PASSWORD'), tái kiểm tra phiên
  trong lock (chưa hết hạn, chưa revoked, user ACTIVE, single-role), xác minh old_password,
  ghi AuditLog khi thất bại (không log secret), thu hồi mọi phiên cũ, logout và yêu cầu đăng nhập lại.
"""
import logging
import uuid

from django.contrib.auth import logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest
from django.middleware.csrf import rotate_token
from django.utils import timezone

from apps.accounts.models import AuthSession, User
from apps.accounts.policies.roles import get_user_role
from apps.accounts.security.rate_limit import (
    acquire_rate_limit_advisory_locks,
    check_rate_limit,
    get_client_ip,
    hash_identifier,
    record_login_attempt,
)
from apps.accounts.services.sessions import (
    revoke_all_user_sessions,
    verify_auth_session,
)
from apps.audit.models import AuditLog
from apps.audit.services import AuditService

logger = logging.getLogger(__name__)


def update_profile(
    *,
    user: User,
    full_name: str,
    request: HttpRequest,
) -> User:
    """
    Cập nhật họ và tên của chính người dùng hiện tại (actor).
    """
    trimmed_name = full_name.strip()
    request_id = getattr(request, "request_id", None) or uuid.uuid4()
    ip = get_client_ip(request)

    session_id = request.session.get("auth_session_id")
    if not session_id and getattr(request, "session", None) and request.session.session_key:
        auth_sess = AuthSession.objects.filter(
            session_key=request.session.session_key,
            revoked_at__isnull=True
        ).first()
        if auth_sess:
            session_id = str(auth_sess.id)

    with transaction.atomic():
        locked_user = User.objects.select_for_update().filter(id=user.id).first()
        if not locked_user:
            raise PermissionDenied("Tài khoản người dùng không tồn tại.")

        if locked_user.status != User.Status.ACTIVE or not locked_user.is_active:
            raise PermissionDenied("Tài khoản người dùng đã bị khóa hoặc ngừng hoạt động.")

        role = get_user_role(locked_user)
        if not role:
            raise PermissionDenied("Tài khoản người dùng chưa được phân quyền vai trò hợp lệ.")

        locked_user.full_name = trimmed_name
        locked_user.save(update_fields=["full_name", "updated_at"])

        AuditService.record_event(
            actor_user=locked_user,
            actor_session_id=session_id,
            actor_type=AuditLog.ActorType.USER,
            actor_name_snapshot=locked_user.username,
            function_key="user.update",
            action="USER_UPDATE",
            outcome="SUCCESS",
            target_type="USER",
            target_id=str(locked_user.id),
            target_name_snapshot=locked_user.username,
            summary="Cập nhật thông tin hồ sơ cá nhân thành công",
            request_id=request_id,
            ip_address=ip,
            metadata={
                "fields": ["full_name"],
                "target_username": locked_user.username,
            }
        )

    return locked_user


def change_password(
    *,
    user: User,
    old_password: str,
    new_password: str,
    request: HttpRequest,
) -> tuple[User | None, str | None, int | None]:
    """
    Thay đổi mật khẩu tài khoản người dùng:
    1. Giữ PostgreSQL Advisory Lock cho mục đích CHANGE_PASSWORD.
    2. Kiểm tra Rate Limiting (5 lần sai / user, 20 lần sai / IP). Nếu vượt ngưỡng, ghi audit DENIED và trả RATE_LIMITED.
    3. Khóa dòng User (select_for_update).
    4. Kiểm tra lại phiên trong registry ngay sau khi khóa: còn hạn, chưa revoke, đúng user, role hợp lệ.
    5. Kiểm tra old_password bằng Argon2id. Nếu sai, ghi audit FAILED (không log secret) và trả INVALID_CREDENTIALS.
    6. Kiểm tra mật khẩu mới khác cũ và thẩm định qua Django validators.
    7. Cập nhật password, đặt must_change_password=False, ghi nhận password_changed_at.
    8. Thu hồi toàn bộ phiên làm việc của user trong registry (revoke_all_user_sessions).
    9. Ghi nhận AuditLog với action=PASSWORD_CHANGE, outcome=SUCCESS.
    10. Đăng xuất phiên hiện tại của request (logout + rotate_token) để yêu cầu client đăng nhập lại đúng kế hoạch.

    Trả về:
        tuple[User | None, str | None, int | None]:
            (updated_user, error_code, retry_after)
    """
    ip = get_client_ip(request)
    request_id = getattr(request, "request_id", None) or uuid.uuid4()
    purpose = "CHANGE_PASSWORD"

    user_key = hash_identifier(user.username, purpose)
    ip_key = hash_identifier(ip, purpose)

    with transaction.atomic():
        # Khóa advisory lock chống race condition brute-force đổi mật khẩu
        acquire_rate_limit_advisory_locks(user_key, ip_key)

        is_limited, retry_after, _ = check_rate_limit(user.username, ip, purpose=purpose)
        if is_limited:
            record_login_attempt(
                username=user.username,
                ip=ip,
                success=False,
                failure_code="RATE_LIMITED",
                request_id=request_id,
                purpose=purpose
            )
            AuditService.record_event(
                actor_user=user,
                actor_type=AuditLog.ActorType.USER,
                function_key="auth.change_password",
                action="PASSWORD_CHANGE",
                outcome="DENIED",
                target_type="USER",
                target_id=str(user.id),
                target_name_snapshot=user.username,
                summary="Yêu cầu thay đổi mật khẩu bị từ chối do vượt quá tần suất cho phép",
                error_code="RATE_LIMITED",
                request_id=request_id,
                ip_address=ip,
                metadata={"target_username": user.username}
            )
            return None, "RATE_LIMITED", retry_after

        # Khóa dòng User để đọc dữ liệu mới nhất
        locked_user = User.objects.select_for_update().filter(id=user.id).first()
        if not locked_user or locked_user.status != User.Status.ACTIVE or not locked_user.is_active:
            raise PermissionDenied("Tài khoản người dùng không hợp lệ hoặc đã bị vô hiệu hóa.")

        # Tái kiểm tra phiên trong registry ngay sau khi khóa dòng: còn hạn, chưa revoke, đúng user, role hợp lệ
        session_key = getattr(request.session, "session_key", None)
        if not session_key:
            raise PermissionDenied("Phiên làm việc không hợp lệ.")

        is_valid, auth_sess, reason = verify_auth_session(session_key, user=locked_user)
        if not is_valid or not auth_sess:
            raise PermissionDenied(f"Phiên làm việc không còn hiệu lực: {reason or 'INVALID'}")

        # Xác minh mật khẩu hiện tại
        if not locked_user.check_password(old_password):
            record_login_attempt(
                username=locked_user.username,
                ip=ip,
                success=False,
                failure_code="INVALID_CREDENTIALS",
                request_id=request_id,
                purpose=purpose
            )
            AuditService.record_event(
                actor_user=locked_user,
                actor_session_id=auth_sess.id,
                actor_type=AuditLog.ActorType.USER,
                actor_name_snapshot=locked_user.username,
                function_key="auth.change_password",
                action="PASSWORD_CHANGE",
                outcome="FAILED",
                target_type="USER",
                target_id=str(locked_user.id),
                target_name_snapshot=locked_user.username,
                summary="Thay đổi mật khẩu thất bại do mật khẩu hiện tại không chính xác",
                error_code="INVALID_CREDENTIALS",
                request_id=request_id,
                ip_address=ip,
                metadata={"target_username": locked_user.username}
            )
            return None, "INVALID_CREDENTIALS", None

        # Kiểm tra mật khẩu mới khác mật khẩu cũ
        if old_password == new_password:
            raise ValueError("Mật khẩu mới không được trùng với mật khẩu hiện tại.")

        # Thẩm định độ mạnh mật khẩu qua Django validators (Defense-in-depth)
        validate_password(new_password, user=locked_user)

        # Cập nhật mật khẩu mới và gỡ cờ bắt buộc đổi mật khẩu
        locked_user.set_password(new_password)
        locked_user.must_change_password = False
        locked_user.password_changed_at = timezone.now()
        locked_user.save(update_fields=["password", "must_change_password", "password_changed_at", "updated_at"])

        # Thu hồi toàn bộ phiên làm việc của người dùng trong registry (chống cookie replay)
        revoke_all_user_sessions(locked_user, reason="PASSWORD_CHANGED")

        # Ghi nhận AuditLog thành công
        AuditService.record_event(
            actor_user=locked_user,
            actor_session_id=auth_sess.id,
            actor_type=AuditLog.ActorType.USER,
            actor_name_snapshot=locked_user.username,
            function_key="auth.change_password",
            action="PASSWORD_CHANGE",
            outcome="SUCCESS",
            target_type="USER",
            target_id=str(locked_user.id),
            target_name_snapshot=locked_user.username,
            summary="Thay đổi mật khẩu tài khoản thành công",
            request_id=request_id,
            ip_address=ip,
            metadata={
                "fields": ["password"],
                "target_username": locked_user.username,
            }
        )

    # Đăng xuất phiên hiện tại, xoay vòng CSRF token, yêu cầu client đăng nhập lại
    logout(request)
    rotate_token(request)

    return locked_user, None, None