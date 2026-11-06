from django.contrib import admin
from apps.telemetry.models import Telemetry


@admin.register(Telemetry)
class TelemetryAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'device', 'temperature', 'humidity',
        'fan_status', 'door_status', 'mode', 'measured_at'
    )
    list_filter = ('device', 'fan_status', 'door_status', 'mode', 'measured_at')
    search_fields = ('device__id', 'device__name')
    date_hierarchy = 'measured_at'
    readonly_fields = (
        'device', 'seq', 'temperature', 'humidity',
        'fan_status', 'door_status', 'mode', 'uptime',
        'measured_at', 'received_at', 'raw_data'
    )
