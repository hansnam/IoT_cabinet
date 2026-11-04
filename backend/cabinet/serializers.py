from rest_framework import serializers
from .models import Device, Telemetry, Alert, CommandLog


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = [
            "device_id",
            "name",
            "device_type",
            "location",
            "connection_state",
            "health_state",
            "mode",
            "fan_output",
            "temp_threshold_on",
            "temp_threshold_off",
            "temp_alert_thresh",
            "humidity_alert_thresh",
            "last_seen_at",
            "created_at",
            "updated_at",
        ]


class TelemetrySerializer(serializers.ModelSerializer):
    device_id = serializers.CharField(source="device.device_id", read_only=True)

    class Meta:
        model = Telemetry
        fields = [
            "id",
            "device_id",
            "temperature_c",
            "humidity_pct",
            "fan_output",
            "mode",
            "received_at",
        ]


class AlertSerializer(serializers.ModelSerializer):
    device_id = serializers.CharField(source="device.device_id", read_only=True)

    class Meta:
        model = Alert
        fields = [
            "id",
            "device_id",
            "alert_type",
            "severity",
            "message",
            "status",
            "created_at",
            "resolved_at",
        ]


class CommandLogSerializer(serializers.ModelSerializer):
    device_id = serializers.CharField(source="device.device_id", read_only=True)

    class Meta:
        model = CommandLog
        fields = [
            "id",
            "device_id",
            "command_type",
            "params",
            "status",
            "created_at",
            "executed_at",
        ]
