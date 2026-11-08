"""
Authentication Service Module
Quản lý quy trình xác thực thông tin đăng nhập, thiết lập phiên và đăng xuất:
- Tích hợp kiểm tra Rate Limiter trước khi đánh giá credentials, bảo vệ bằng advisory lock xuyên suốt.
- Sử dụng hàm authenticate() của Django với Argon2id để kiểm tra mật khẩu.
- Chống tấn công dò quét tài khoản (Username Enumeration) bằng thông báo lỗi chung.
- Luồng establish_session:
  1. Bọc toàn bộ quy trình trong try...with transaction.atomic():
  2. Khóa dòng User (select_for_update), đọc lại và kiểm tra trạng thái/role tươi mới nhất từ DB.
  3. Gọi login() và cycle session_key chống cố định phiên.
  4. Đặt thời hạn tuyệt đối 15 phút cho session cookie.
  5. Lưu django_session, tạo AuthSession registry và ghi AuditLog.
  6. Nếu bất kỳ bước nào lỗi (session save, registry, audit, commit), rollback toàn bộ và dọn sạch session.
  7. Xoay vòng CSRF token mới (rotate_token) ngay sau khi transaction commit thành công (Rule 7).
- Luồng logout_session:
  1. Thu hồi phiên trong AuthSession registry.
  2. Ghi nhật ký AuditLog (outcome='SUCCESS', action='USER_LOGOUT') theo đúng hợp đồng.
  3. Gọi logout(request) của Django và xoay vòng CSRF token.
"""
import logging
import uuid
from typing import Any

from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest
from django.middleware.csrf import rotate_token

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
    create_auth_session,
    revoke_auth_session,
)
from apps.audit.models import AuditLog
from apps.audit.services import SENSITIVE_STRING_MARKERS, AuditService

logger = logging.getLogger(__name__)

def authenticate_credentials(
    request: HttpRequest,
    username: str,
    password: str
) -> tuple[User | None, str | None, int | None]:
    """
    Kiểm tra thông tin đăng nhập của người dùng kèm chốt chặn Rate Limit:
    1. Giữ khóa PostgreSQL Transaction Advisory Lock xuyên suốt: kiểm tra ngưỡng -> xác thực credentials -> ghi attempt.
    2. Chuẩn hóa canonical username và gọi authenticate() của Django.
    3. Phân loại mã lỗi nội bộ (INVALID_CREDENTIALS, ACCOUNT_LOCKED, ACCOUNT_ARCHIVED)
       để ghi nhận LoginAttempt chính xác, nhưng trả thông báo chung cho client.

    Trả về:
        tuple[User | None, str | None, int | None]:
            - user: Thực thể User nếu xác thực thành công, ngược lại None.
            - error_code: Mã lỗi ('RATE_LIMITED', 'INVALID_CREDENTIALS'...).
            - retry_after: Số giây phải chờ nếu bị RATE_LIMITED.
    """
    ip = get_client_ip(request)
    request_id = getattr(request, "request_id", None) or uuid.uuid4()
    canonical_username = username.strip().lower()

    user_key = hash_identifier(canonical_username, "LOGIN")
    ip_key = hash_identifier(ip, "LOGIN")

    target_user: User | None = None
    failure_code: str | None = None

    # Khóa Advisory Lock bao trọn toàn bộ quá trình: Kiểm tra ngưỡng -> Xác thực credentials -> Ghi nhận Attempt
    with transaction.atomic():
        acquire_rate_limit_advisory_locks(user_key, ip_key)

        is_limited, retry_after, reason = check_rate_limit(canonical_username, ip, purpose="LOGIN")
        if is_limited:
            record_login_attempt(
                username=canonical_username,
                ip=ip,
                success=False,
                failure_code="RATE_LIMITED",
                request_id=request_id
            )
            return None, "RATE_LIMITED", retry_after

        target_user = User.objects.filter(username=canonical_username).first()

        # Xác thực bằng Django authenticate (tự động kiểm tra Argon2id và cờ is_active)
        authenticated_user = authenticate(
            request,
            username=canonical_username,
            password=password
        )

        if isinstance(authenticated_user, User) and not None:
            # Đăng nhập thành công: Ghi nhận LoginAttempt ngay dưới lock
            record_login_attempt(
                username=canonical_username,
                ip=ip,
                success=True,
                user=authenticated_user,
                request_id=request_id
            )
            return authenticated_user, None, None

        # Đăng nhập thất bại: Phân loại nguyên nhân phục vụ audit nội bộ
        if target_user:
            if target_user.status == User.Status.LOCKED:
                failure_code = "ACCOUNT_LOCKED"
            elif target_user.status == User.Status.ARCHIVED:
                failure_code = "ACCOUNT_ARCHIVED"
            else:
                failure_code = "INVALID_CREDENTIALS"
        else:
            failure_code = "INVALID_CREDENTIALS"

        record_login_attempt(
            username=canonical_username,
            ip=ip,
            success=False,
            user=target_user,
            failure_code=failure_code,
            request_id=request_id
        )

    # Ghi log kiểm toán cho lần đăng nhập thất bại theo đúng hợp đồng AuditService (ngoài lock sau khi commit)
    if target_user:
        AuditService.record_event(
            actor_user=target_user,
            actor_type=AuditLog.ActorType.USER,
            function_key="auth.login",
            action="USER_LOGIN",
            outcome="FAILED",
            error_code=failure_code,
            target_type="USER",
            target_id=str(target_user.id),
            target_name_snapshot=target_user.username,
            summary="Đăng nhập thất bại do thông tin không chính xác",
            request_id=request_id,
            ip_address=ip,
            metadata={"ip_address": ip, "reason": failure_code}
        )
    else:
        meta: dict[str, Any] = {"ip_address": ip, "reason": "INVALID_CREDENTIALS"}
        if not any(marker in canonical_username for marker in SENSITIVE_STRING_MARKERS):
            meta["attempted_username"] = canonical_username[:64]

        target_name = canonical_username[:255] if not any(marker in canonical_username for marker in SENSITIVE_STRING_MARKERS) else None

        AuditService.record_event(
            actor_user=None,
            actor_type=AuditLog.ActorType.SYSTEM,
            function_key="auth.login",
            action="USER_LOGIN",
            outcome="FAILED",
            error_code="INVALID_CREDENTIALS",
            target_type="USER",
            target_id=None,
            target_name_snapshot=target_name,
            summary="Đăng nhập thất bại cho tài khoản không tồn tại",
            request_id=request_id,
            ip_address=ip,
            metadata=meta
        )

    return None, failure_code, None


def establish_session(
    request: HttpRequest,
    user: User
) -> AuthSession:
    """
    Thiết lập phiên làm việc chính thức sau khi xác thực thành công (Rule 6, 7, 10):
    - Toàn bộ các thao tác (login, khóa dòng User, kiểm tra trạng thái tươi mới,
      lưu django_session, AuthSession registry, AuditLog) được gom trong một transaction.atomic() duy nhất.
    - Khóa dòng User (select_for_update) và đọc lại từ DB để loại trừ khoảng trống (race condition)
      khi tài khoản bị khóa/hủy hoặc mật khẩu bị đổi ngay trước khi phiên được khởi tạo.
    - So sánh hash locked_user.password với user.password lúc xác thực; nếu khác thì từ chối ngay.
    - Nếu bất kỳ bước nào thất bại, toàn bộ thay đổi database bị rollback, session in-memory bị flush/xóa sạch.
    - Xoay vòng CSRF token (rotate_token) chỉ thực hiện SAU KHI transaction commit thành công.

    Tham số:
        request (HttpRequest): Request HTTP hiện tại.
        user (User): Người dùng đăng nhập thành công.

    Trả về:
        AuthSession: Bản ghi Registry của phiên.
    """
    request_id = getattr(request, "request_id", None) or uuid.uuid4()
    ip = get_client_ip(request)

    try:
        with transaction.atomic():
            # 1. Khóa dòng User (select_for_update) và đọc lại trạng thái mới nhất từ DB
            locked_user = User.objects.select_for_update().filter(id=user.id).first()
            if not locked_user:
                raise PermissionDenied("Tài khoản người dùng không tồn tại.")

            # Kiểm tra trạng thái hoạt động: nếu bị LOCKED hoặc ARCHIVED trong khoảng trống, từ chối ngay
            if locked_user.status != User.Status.ACTIVE or not locked_user.is_active:
                raise PermissionDenied("Tài khoản người dùng đã bị khóa hoặc ngừng hoạt động.")

            # Kiểm tra mật khẩu có bị thay đổi giữa bước xác thực và tạo phiên hay không (P1)
            if locked_user.password != user.password:
                raise PermissionDenied("Thông tin xác thực của tài khoản đã thay đổi.")

            # Kiểm tra vai trò hợp lệ duy nhất (single-role)
            user_role = get_user_role(locked_user)
            if not user_role:
                raise PermissionDenied("Tài khoản người dùng chưa được phân quyền vai trò hợp lệ.")

            # 2. Đăng nhập qua framework Django (tự động cycle session_key chống cố định phiên)
            login(request, locked_user)

            # 3. Thiết lập thời gian sống tuyệt đối 15 phút (900 giây)
            request.session.set_expiry(900)

            # 4. Lưu django_session vào DB trong transaction
            request.session.save()

            # 5. Tạo bản ghi Registry trong cùng transaction
            auth_session = create_auth_session(locked_user, request, request.session.session_key)
            request.session["auth_session_id"] = str(auth_session.id)
            request.session.save()

            # 6. Ghi nhận AuditLog với outcome='SUCCESS' và câu mẫu cố định trong cùng transaction
            AuditService.record_event(
                actor_user=locked_user,
                actor_type=AuditLog.ActorType.USER,
                function_key="auth.login",
                action="USER_LOGIN",
                outcome="SUCCESS",
                target_type="USER",
                target_id=str(locked_user.id),
                target_name_snapshot=locked_user.username,
                summary="Đăng nhập thành công vào hệ thống",
                request_id=request_id,
                ip_address=ip,
                metadata={"session_id": str(auth_session.id), "ip_address": ip}
            )

        # 7. Xoay vòng CSRF token mới sau khi transaction đã commit thành công (Rule 7)
        rotate_token(request)
        return auth_session

    except Exception as exc:
        # Dọn sạch session in-memory khi transaction lỗi
        try:
            request.session.flush()
        except Exception as flush_exc:
            # Ghi log cảnh báo, nhưng không chặn lỗi gốc
            logger.warning("Không thể flush session khi rollback: %s", flush_exc)
        raise exc


def logout_session(request: HttpRequest) -> None:
    """
    Đăng xuất an toàn và giải phóng phiên làm việc:
    - Thu hồi phiên trong AuthSession Registry.
    - Ghi nhận AuditLog sự kiện USER_LOGOUT theo đúng hợp đồng AuditService.
    - Hủy session Django và xoay vòng token CSRF.
    """
    user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    session_key = getattr(request.session, "session_key", None)
    request_id = getattr(request, "request_id", None) or uuid.uuid4()
    ip = get_client_ip(request)

    # 1. Thu hồi phiên trong Registry
    if session_key:
        revoke_auth_session(session_key, reason="USER_LOGOUT")

    # 2. Ghi nhận AuditLog
    if user:
        AuditService.record_event(
            actor_user=user,
            actor_type=AuditLog.ActorType.USER,
            function_key="auth.logout",
            action="USER_LOGOUT",
            outcome="SUCCESS",
            target_type="USER",
            target_id=str(user.pk),
            target_name_snapshot=user.username,
            summary="Đăng xuất khỏi hệ thống thành công",
            request_id=request_id,
            ip_address=ip,
            metadata={"ip_address": ip} if ip else None
        )

    # 3. Đăng xuất khỏi Django session
    logout(request)

    # 4. Xoay vòng CSRF token
    rotate_token(request)