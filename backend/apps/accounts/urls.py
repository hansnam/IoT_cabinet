"""
Accounts URL Configuration Module
Định tuyến các endpoint xác thực và quản lý tài khoản:
- /api/v1/auth/csrf/
- /api/v1/auth/login/
- /api/v1/auth/logout/
"""
from django.urls import path

from apps.accounts.views.auth import CsrfTokenView, LoginView, LogoutView

urlpatterns = [
    path("auth/csrf/", CsrfTokenView.as_view(), name="auth_csrf"),
    path("auth/login/", LoginView.as_view(), name="auth_login"),
    path("auth/logout/", LogoutView.as_view(), name="auth_logout"),
]