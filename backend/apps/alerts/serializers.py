from rest_framework import serializers
from apps.alerts.models import Alert


class AlertSerializer(serializers.ModelSerializer):
    device_id = serializers.CharField(source='device.id', read_only=True)
    acknowledged_by_username = serializers.CharField(source='acknowledged_by.username', read_only=True)

    class Meta:
        model = Alert
        fields = [
            'id', 'device_id', 'alert_type', 'severity', 'status',
            'message', 'threshold', 'current_value', 'created_at',
            'acknowledged_at', 'acknowledged_by_username', 'resolved_at'
        ]
        read_only_fields = ['id', 'created_at']
