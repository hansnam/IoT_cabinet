from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import DeviceViewSet, TelemetryViewSet, AlertViewSet, CommandLogViewSet

router = DefaultRouter()
router.register(r"devices", DeviceViewSet, basename="device")
router.register(r"telemetry", TelemetryViewSet, basename="telemetry")
router.register(r"alerts", AlertViewSet, basename="alert")
router.register(r"commands", CommandLogViewSet, basename="command")

urlpatterns = [
    path("", include(router.urls)),
]
