from django.urls import include, path
from rest_framework.routers import DefaultRouter
from apps.devices.views import DeviceViewSet

router = DefaultRouter()
router.register(r'', DeviceViewSet, basename='device')

urlpatterns = [
    path('<str:id>/telemetry/export/', DeviceViewSet.as_view({'get': 'export'}), name='device-telemetry-export'),
    path('', include(router.urls)),
]
