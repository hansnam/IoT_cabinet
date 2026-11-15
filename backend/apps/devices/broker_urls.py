from django.urls import path
from apps.devices.broker_views import BrokerSettingsView, BrokerTestConnectionView

urlpatterns = [
    path('settings/', BrokerSettingsView.as_view(), name='mqtt_broker_settings'),
    path('settings/test/', BrokerTestConnectionView.as_view(), name='mqtt_broker_settings_test'),
]
