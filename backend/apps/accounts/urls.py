"""
Accounts URL Configuration Module
Định tuyến các endpoint xác thực, quản lý tài khoản và IAM:
- /api/v1/auth/csrf/
- /api/v1/auth/login/
- /api/v1/auth/logout/
- /api/v1/profile/me/
- /api/v1/profile/change-password/
- /api/v1/management/users/
- /api/v1/management/users/<uuid>/
"""
from django.urls import path

from apps.accounts.views.auth import CsrfTokenView, LoginView, LogoutView
from apps.accounts.views.management import UserManagementDetailView, UserManagementListView
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

    # Quản trị người dùng (Chỉ ADMIN)
    path("management/users/", UserManagementListView.as_view(), name="management_users_list"),
    path("management/users/<uuid:pk>/", UserManagementDetailView.as_view(), name="management_users_detail"),
]
