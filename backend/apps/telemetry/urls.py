from django.urls import include, path
from rest_framework.routers import DefaultRouter
from apps.telemetry.views import TelemetryViewSet

router = DefaultRouter()
router.register(r'', TelemetryViewSet, basename='telemetry')

urlpatterns = [
    path('', include(router.urls)),
]
