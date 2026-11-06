"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from apps.core.views import health_live, health_ready

urlpatterns = [
    # Health checks
    path('health/live', health_live, name='health_live'),
    path('health/ready', health_ready, name='health_ready'),

    # Django Admin site
    path('admin/', admin.site.urls),

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
]

# Khai báo các handler lỗi toàn cục trả về JSON envelope
handler400 = 'apps.core.errors.handler400'
handler403 = 'apps.core.errors.handler403'
handler404 = 'apps.core.errors.handler404'
handler500 = 'apps.core.errors.handler500'