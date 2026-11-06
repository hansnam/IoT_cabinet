"""
AppConfig cho audit application.
Quản lý cơ sở dữ liệu kiểm toán và nhật ký hệ thống.
"""
from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.audit'
    label = 'audit'
    verbose_name = 'Kiểm toán & Nhật ký hệ thống'