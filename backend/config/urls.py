"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.urls import include, path
from apps.accounts.admin_site import cabinet_admin_site
from apps.core.views import health_live, health_ready

urlpatterns = [
    # Health checks
    path('health/live', health_live, name='health_live'),
    path('health/ready', health_ready, name='health_ready'),

    # API v1 Endpoints (Accounts / IAM / Auth)
    path("api/v1/", include("apps.accounts.urls")),

    # IoT REST API v1
    path('api/v1/devices/', include('apps.devices.urls')),
    path('api/v1/telemetry/', include('apps.telemetry.urls')),
    path('api/v1/commands/', include('apps.commands.urls')),
    path('api/v1/alerts/', include('apps.alerts.urls')),

    # Tương thích trực tiếp với React Frontend (/api/devices, /api/telemetry, /api/alerts, /api/commands)
    path('api/devices/', include('apps.devices.urls')),
    path('api/telemetry/', include('apps.telemetry.urls')),
    path('api/commands/', include('apps.commands.urls')),
    path('api/alerts/', include('apps.alerts.urls')),

    # Django Admin site
    path("admin/", cabinet_admin_site.urls),
]

# Khai báo các handler lỗi toàn cục trả về JSON envelope
handler400 = 'apps.core.errors.handler400'
handler403 = 'apps.core.errors.handler403'
handler404 = 'apps.core.errors.handler404'
handler500 = 'apps.core.errors.handler500'
