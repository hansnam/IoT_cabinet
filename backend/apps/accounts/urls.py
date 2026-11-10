"""
Accounts URL Configuration Module
Định tuyến các endpoint xác thực và quản lý tài khoản:
- /api/v1/auth/csrf/
- /api/v1/auth/login/
- /api/v1/auth/logout/
"""
from django.urls import path

from apps.accounts.views.auth import CsrfTokenView, LoginView, LogoutView
from apps.accounts.views.profile import ChangePasswordView, ProfileMeView

urlpatterns = [
    # Auth core
    path("auth/csrf/", CsrfTokenView.as_view(), name="auth_csrf"),
    path("auth/login/", LoginView.as_view(), name="auth_login"),
    path("auth/logout/", LogoutView.as_view(), name="auth_logout"),

    # Profile me
    path("profile/me/", ProfileMeView.as_view(), name="profile_me"),
    path("auth/me/", ProfileMeView.as_view(), name="auth_me"),

    # Change password
    path("profile/change-password/", ChangePasswordView.as_view(), name="profile_change_password"),
    path("auth/change-password/", ChangePasswordView.as_view(), name="auth_change_password"),
]