from django.urls import path
from apps.devices.access_views import DeviceAccessDetailView, DeviceAccessListView

urlpatterns = [
    path("", DeviceAccessListView.as_view(), name="device_access_list"),
    path("<int:pk>/", DeviceAccessDetailView.as_view(), name="device_access_detail"),
]
