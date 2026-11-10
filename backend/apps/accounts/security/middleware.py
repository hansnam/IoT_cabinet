"""Kiểm tra registry và yêu cầu đổi mật khẩu cho các trang Django HTML."""
from collections.abc import Callable

from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from apps.accounts.security.paths import ALLOWED_MUST_CHANGE_PATHS
from apps.accounts.services.sessions import verify_auth_session


class SessionRegistryAndMustChangeMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # API được bảo vệ bởi RegistrySessionAuthentication của DRF.
        if request.path.startswith("/api/"):
            return self.get_response(request)

        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            session_key = getattr(request.session, "session_key", None)
            is_valid, _, _ = verify_auth_session(session_key, user=user)
            if not is_valid:
                logout(request)
            elif user.must_change_password and request.path not in ALLOWED_MUST_CHANGE_PATHS:
                return redirect("/admin/password_change/")

        return self.get_response(request)
