"""
AppConfig cho accounts application.
Chịu trách nhiệm quản lý User, AuthSession, LoginAttempt và chính sách phân quyền.
"""
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.accounts'
    label = 'accounts'
    verbose_name = "Accounts Application"