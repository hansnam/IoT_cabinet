"""
Authentication Backend Module for Django REST Framework
Cung cấp RegistrySessionAuthentication kế thừa từ SessionAuthentication của DRF:
- Xác thực cookie session của Django.
- Cung cấp authenticate_header() để trả về mã HTTP 401 thay vì 403 khi chưa xác thực.
- Đối chiếu trạng thái phiên với AuthSession Registry (kiểm tra hạn tuyệt đối 15 phút, cờ revoked, trạng thái user, vai trò).
- Tự động thực thi kiểm tra CSRF token trên các phương thức HTTP gây thay đổi dữ liệu (POST, PUT, PATCH, DELETE).
"""
from typing import Any

from django.conf import settings
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import AuthenticationFailed

from apps.accounts.services.sessions import verify_auth_session


class RegistrySessionAuthentication(SessionAuthentication):
    """
    Xác thực phiên làm việc kết hợp kiểm tra Registry và CSRF trong DRF:
    Bảo đảm người dùng có cookie Django session hợp lệ VÀ bản ghi AuthSession trong DB
    vẫn còn hiệu lực, chưa bị thu hồi và chưa quá hạn 15 phút.
    """
    def authenticate_header(self, request: Any) -> str:
        """
        Cung cấp challenge header để DRF trả về HTTP 401 (thay vì HTTP 403) khi chưa xác thực.
        """
        return "Session"

    def authenticate(self, request: Any) -> tuple[Any, Any] | None:
        cookie_name = getattr(settings, "SESSION_COOKIE_NAME", "sessionid")
        session_cookie_sent = cookie_name in request.COOKIES or bool(getattr(request.session, "session_key", None))

        # 1. Xác thực session tiêu chuẩn của Django qua SessionAuthentication
        user_auth_tuple = super().authenticate(request)
        if user_auth_tuple is None:
            if session_cookie_sent:
                # Client gửi session cookie nhưng không xác thực được (đã đăng xuất hoặc không tồn tại)
                raise AuthenticationFailed("Phiên làm việc đã hết hạn hoặc bị thu hồi.", code="NOT_AUTHENTICATED")
            return None

        user, auth = user_auth_tuple
        session_key = getattr(request.session, "session_key", None)
        if not session_key:
            return None

        # 2. Đối chiếu phiên với AuthSession Registry
        is_valid, session, reason = verify_auth_session(session_key, user=user)

        if not is_valid:
            if reason == "SESSION_REVOKED":
                raise AuthenticationFailed("Phiên làm việc đã bị thu hồi.", code="SESSION_REVOKED")
            elif reason == "SESSION_EXPIRED":
                raise AuthenticationFailed("Phiên làm việc đã hết hạn (15 phút).", code="SESSION_EXPIRED")
            elif reason == "USER_INACTIVE":
                raise AuthenticationFailed("Tài khoản người dùng đã bị khóa hoặc ngừng hoạt động.", code="USER_INACTIVE")
            elif reason == "USER_NO_ROLE":
                raise AuthenticationFailed("Tài khoản người dùng chưa được phân quyền vai trò hợp lệ.", code="USER_NO_ROLE")
            else:
                raise AuthenticationFailed("Phiên làm việc không hợp lệ.", code="SESSION_INVALID")

        return user, auth