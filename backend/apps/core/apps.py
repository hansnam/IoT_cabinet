"""
AppConfig cho core application.
Quản lý các tiện ích hạ tầng chung: middleware, lỗi, test runner.
"""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'
    label = 'core'
    verbose_name = "Core Application"