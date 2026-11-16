from django.urls import include, path
from rest_framework.routers import DefaultRouter
from apps.devices.device_mqtt_views import (
    DeviceMqttDisableView,
    DeviceMqttEnableView,
    DeviceMqttMetadataView,
    DeviceMqttProvisionView,
    DeviceMqttResetPasswordView,
)
from apps.devices.views import DeviceViewSet

router = DefaultRouter()
router.register(r'', DeviceViewSet, basename='device')

urlpatterns = [
    path('<str:id>/mqtt/', DeviceMqttMetadataView.as_view(), name='device-mqtt-metadata'),
    path('<str:id>/mqtt/provision/', DeviceMqttProvisionView.as_view(), name='device-mqtt-provision'),
    path('<str:id>/mqtt/enable/', DeviceMqttEnableView.as_view(), name='device-mqtt-enable'),
    path('<str:id>/mqtt/disable/', DeviceMqttDisableView.as_view(), name='device-mqtt-disable'),
    path('<str:id>/mqtt/reset-password/', DeviceMqttResetPasswordView.as_view(), name='device-mqtt-reset-password'),
    path('<str:id>/telemetry/export/', DeviceViewSet.as_view({'get': 'export'}), name='device-telemetry-export'),
    path('', include(router.urls)),
]
