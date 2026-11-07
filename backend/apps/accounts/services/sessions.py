"""
Session Registry Service Module
Quản lý vòng đời của phiên làm việc trong bảng accounts_auth_session (Registry):
- Thiết lập thời gian sống tuyệt đối 15 phút (900 giây) sử dụng datetime expires_at.
- Tuyệt đối không dùng cơ chế trượt (no sliding window): Request/polling không được phép gia hạn expires_at.
- Cung cấp các hàm kiểm tra hiệu lực phiên (verify_auth_session) và thu hồi phiên (revoke_auth_session).
- Đảm bảo khi người dùng bị khóa/hủy tài khoản, phiên làm việc lập tức bị vô hiệu hóa và thu hồi (chống sống lại khi unlock).
- Kiểm tra chặt chẽ vai trò người dùng (single-role per user) tại mỗi lần verify phiên.
"""
from datetime import timedelta
from typing import Any, Final

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.http import HttpRequest
from django.utils import timezone

from apps.accounts.models import AuthSession, User
from apps.accounts.policies.roles import get_user_role
from apps.accounts.security.rate_limit import get_client_ip

SESSION_EXPIRY_SECONDS: Final[int] = 900  # 15 phút thời hạn sống tuyệt đối

def create_auth_session(
    user: User,
    request: HttpRequest,
    session_key: str
) -> AuthSession:
    """
    Khởi tạo một bản ghi AuthSession mới trong Registry cho người dùng đăng nhập thành công:
    - Gắn thời gian hết hạn tuyệt đối = now + 15 phút.
    - Lưu địa chỉ IP và chuỗi User-Agent của client phục vụ giám sát.

    Tham số:
        user (User): Người dùng vừa đăng nhập.
        request (HttpRequest): Yêu cầu HTTP hiện tại.
        session_key (str): Khóa phiên của Django (django_session).

    Trả về:
        AuthSession: Thực thể phiên làm việc mới được tạo.
    """
    now = timezone.now()
    expires_at = now + timedelta(seconds=SESSION_EXPIRY_SECONDS)
    ip = get_client_ip(request)
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:512]

    return AuthSession.objects.create(
        user=user,
        session_key=session_key,
        expires_at=expires_at,
        ip_address=ip,
        user_agent=user_agent
    )

def verify_auth_session(
    session_key: str,
    user: Any = None
) -> tuple[bool, AuthSession | None, str | None]:
    """
    Xác minh tính hợp lệ của phiên làm việc trong Registry:
    1. Bản ghi AuthSession phải tồn tại theo session_key.
    2. Phiên chưa bị thu hồi (revoked_at is None).
    3. Phiên chưa quá hạn tuyệt đối 15 phút (expires_at > now).
    4. Người dùng sở hữu phiên phải khớp với user yêu cầu (nếu truyền vào).
    5. Tài khoản người dùng phải ở trạng thái ACTIVE và is_active=True. Nếu không, lập tức thu hồi phiên.
    6. Người dùng phải có vai trò hợp lệ duy nhất (get_user_role(user) is not None).

    Tham số:
        session_key (str): Khóa session của Django.
        user (User | None): Người dùng đối chiếu (tùy chọn).

    Trả về:
        tuple[bool, AuthSession | None, str | None]:
            - is_valid (bool): True nếu phiên hoàn toàn hợp lệ.
            - session (AuthSession | None): Thực thể phiên nếu tìm thấy.
            - reason (str | None): Lý do từ chối nếu không hợp lệ.
    """
    if not session_key:
        return False, None, "SESSION_KEY_EMPTY"

    session = AuthSession.objects.filter(session_key=session_key).select_related("user").first()
    if not session:
        return False, None, "SESSION_NOT_FOUND"

    # 1. Kiểm tra xem phiên đã bị thu hồi chưa
    if session.revoked_at is not None:
        return False, session, "SESSION_REVOKED"

    # 2. Kiểm tra thời hạn sống tuyệt đối 15 phút
    if session.expires_at <= timezone.now():
        return False, session, "SESSION_EXPIRED"

    # 3. Đối chiếu người dùng sở hữu phiên
    if user and getattr(user, "is_authenticated", False) and session.user_id != user.id:
            return False, session, "USER_MISMATCH"

    # 4. Kiểm tra trạng thái hoạt động của tài khoản trong database
    if session.user.status != User.Status.ACTIVE or not session.user.is_active:
        revoke_all_user_sessions(session.user, reason=f"USER_{session.user.status}")
        return False, session, "USER_INACTIVE"

    # 5. Kiểm tra tính hợp lệ của vai trò (single-role)
    user_role = get_user_role(session.user)
    if not user_role:
        revoke_all_user_sessions(session.user, reason="USER_NO_ROLE")
        return False, session, "USER_NO_ROLE"

    # Phiên hợp lệ: Tuyệt đối không gia hạn expires_at (no sliding)
    return True, session, None

def revoke_auth_session(
    session_key: str,
    reason: str = "USER_LOGOUT"
) -> bool:
    """
    Thu hồi một phiên làm việc cụ thể theo session_key:
    Cập nhật revoked_at và revoke_reason, không xóa vật lý bản ghi để giữ audit trace.
    """
    session = AuthSession.objects.filter(
        session_key=session_key,
        revoked_at__isnull=True
    ).first()

    if session:
        session.revoked_at = timezone.now()
        session.revoke_reason = reason
        session.save(update_fields=["revoked_at", "revoke_reason"])
        return True
    return False

def revoke_all_user_sessions(
    user: User,
    reason: str = "REVOKE_ALL"
) -> int:
    """
    Thu hồi toàn bộ các phiên làm việc đang hoạt động của một người dùng:
    Thường dùng khi đổi mật khẩu, admin khóa tài khoản hoặc reset mật khẩu.
    """
    return AuthSession.objects.filter(
        user=user,
        revoked_at__isnull=True
    ).update(
        revoked_at=timezone.now(),
        revoke_reason=reason
    )

def lock_user(user: User, reason: str = "USER_LOCKED") -> None:
    """
    Khóa tài khoản người dùng và thu hồi ngay lập tức toàn bộ phiên đang hoạt động.
    Sử dụng select_for_update() để đồng bộ hóa tuyệt đối với establish_session().
    """
    with transaction.atomic():
        u = User.objects.select_for_update().filter(id=user.id).first()
        if u:
            u.status = User.Status.LOCKED
            u.is_active = False
            u.save(update_fields=["status", "is_active"])
            revoke_all_user_sessions(u, reason=reason)

def archive_user(user: User, reason: str = "USER_ARCHIVED") -> None:
    """
    Hủy / Lưu trữ tài khoản người dùng và thu hồi ngay lập tức toàn bộ phiên đang hoạt động.
    Sử dụng select_for_update() để đồng bộ hóa tuyệt đối với establish_session().
    """
    with transaction.atomic():
        u = User.objects.select_for_update().filter(id=user.id).first()
        if u:
            u.status = User.Status.ARCHIVED
            u.is_active = False
            u.save(update_fields=["status", "is_active"])
            revoke_all_user_sessions(u, reason=reason)

@receiver(post_save, sender=User)
def revoke_sessions_on_user_status_change(sender, instance: User, **kwargs):
    """
    Signal tự động thu hồi toàn bộ phiên làm việc của người dùng khi tài khoản
    chuyển trạng thái sang LOCKED hoặc ARCHIVED, hoặc is_active=False.
    Bảo đảm cookie cũ không thể sống lại sau khi tài khoản được mở khóa.
    """
    if instance.status != User.Status.ACTIVE or not instance.is_active:
        revoke_all_user_sessions(instance, reason=f"USER_{instance.status}")