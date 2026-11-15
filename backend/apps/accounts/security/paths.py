"""Đường dẫn được phép khi người dùng phải đổi mật khẩu."""

ALLOWED_MUST_CHANGE_PATHS = frozenset({
    "/api/v1/auth/csrf/",
    "/api/v1/auth/logout/",
    "/api/v1/auth/change-password/",
    "/api/v1/profile/change-password/",
    "/api/v1/profile/me/",
    "/api/v1/auth/me/",
    "/admin/password_change/",
    "/admin/password_change/done/",
    "/admin/logout/",
})