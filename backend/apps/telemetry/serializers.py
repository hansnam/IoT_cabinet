from rest_framework import serializers
from apps.telemetry.models import Telemetry


class TelemetrySerializer(serializers.ModelSerializer):
    device_id = serializers.CharField(source='device.id', read_only=True)

    class Meta:
        model = Telemetry
        fields = [
            'id', 'device_id', 'seq', 'temperature', 'humidity',
            'fan_status', 'door_status', 'mode', 'uptime',
            'measured_at', 'received_at', 'raw_data'
        ]
        read_only_fields = ['received_at']
