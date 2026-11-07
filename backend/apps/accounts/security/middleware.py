"""
Security Middleware Module
Cung cấp SessionRegistryAndMustChangeMiddleware:
1. Đối chiếu phiên với AuthSession Registry: Nếu phiên bị thu hồi hoặc quá hạn 15 phút,
   tự động đăng xuất request và trả về lỗi 401 NOT_AUTHENTICATED đối với các API.
   Bỏ qua kiểm tra session cho các endpoint công khai (PUBLIC_AUTH_PATHS) để người dùng có cookie cũ vẫn đăng nhập lại được.
2. Chốt chặn Must-Change-Password: Nếu tài khoản có cờ must_change_password=True,
   ngăn chặn truy cập vào toàn bộ các API nghiệp vụ khác (trả về HTTP 403 MUST_CHANGE_PASSWORD),
   chỉ cho phép truy cập các endpoint đổi mật khẩu, xem profile chính mình và đăng xuất.
"""
from collections.abc import Callable
from typing import Any

from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from apps.accounts.services.sessions import verify_auth_session
from apps.core.errors import ErrorCode, make_error_response

# Danh sách các URL được phép truy cập khi tài khoản bắt buộc phải đổi mật khẩu
ALLOWED_MUST_CHANGE_PATHS: tuple[str, ...] = (
    "/api/v1/auth/csrf/",
    "/api/v1/auth/logout/",
    "/api/v1/auth/change-password/",
    "/api/v1/profile/change-password/",
    "/api/v1/profile/me/",
    "/admin/password_change/",
    "/admin/password_change/done/",
    "/admin/logout/",
)

# Danh sách URL công khai không bắt buộc kiểm tra phiên registry (để lấy CSRF và đăng nhập lại)
PUBLIC_AUTH_PATHS: tuple[str, ...] = (
    "/api/v1/auth/csrf/",
    "/api/v1/auth/login/",
)

class SessionRegistryAndMustChangeMiddleware:
    """
    Middleware kiểm tra phiên Registry và áp dụng chính sách must_change_password.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Bỏ qua xác thực session registry trên các endpoint công khai để người dùng có cookie cũ vẫn đăng nhập lại được
        if any(request.path.startswith(p) for p in PUBLIC_AUTH_PATHS):
            return self.get_response(request)

        user = getattr(request, "user", None)
        request_id = getattr(request, "request_id", None)

        # Chỉ áp dụng kiểm tra khi người dùng đã được định danh phiên
        if user and user.is_authenticated:
            session_key = getattr(request.session, "session_key", None)

            # 1. Kiểm tra tính hợp lệ của phiên trong Registry
            if session_key:
                is_valid, session, reason = verify_auth_session(session_key, user=user)
                if not is_valid:
                    logout(request)
                    if request.path.startswith("/api/"):
                        error_msg = "Phiên làm việc đã hết hạn hoặc bị thu hồi."
                        if reason == "USER_INACTIVE":
                            error_msg = "Tài khoản người dùng đã bị khóa hoặc ngừng hoạt động."
                        elif reason == "USER_NO_ROLE":
                            error_msg = "Tài khoản người dùng chưa được phân quyền vai trò hợp lệ."
                        return make_error_response(
                            ErrorCode.NOT_AUTHENTICATED,
                            error_msg,
                            401,
                            request_id
                        )

            # 2. Chốt chặn must_change_password
            if getattr(user, "must_change_password", False):
                path = request.path
                is_allowed = any(path.startswith(allowed_path) for allowed_path in ALLOWED_MUST_CHANGE_PATHS)

                if not is_allowed:
                    if path.startswith("/api/"):
                        return make_error_response(
                            ErrorCode.FORBIDDEN,
                            "Tài khoản bắt buộc phải đổi mật khẩu trước khi truy cập các chức năng khác.",
                            403,
                            request_id
                        )
                    else:
                        # Đối với giao diện Django Admin HTML, chuyển hướng đến trang đổi mật khẩu
                        return redirect("/admin/password_change/")

        return self.get_response(request)