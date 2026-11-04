from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from .models import Device, Telemetry, Alert, CommandLog
from .serializers import (
    DeviceSerializer,
    TelemetrySerializer,
    AlertSerializer,
    CommandLogSerializer,
)
from .mqtt_client import send_mqtt_command


class DeviceViewSet(viewsets.ModelViewSet):
    queryset = Device.objects.all()
    serializer_class = DeviceSerializer
    lookup_field = "device_id"

    @action(detail=True, methods=["post"])
    def control(self, request, device_id=None):
        """Gửi lệnh điều khiển quạt hoặc đổi chế độ AUTO/MANUAL:
        Body mẫu:
        { "action": "SET_FAN", "on": true } hoặc { "action": "SET_MODE", "mode": "MANUAL" }
        """
        device = self.get_object()
        action_type = request.data.get("action") or request.data.get("type")
        
        if not action_type:
            return Response({"error": "Thiếu tham số 'action' (SET_FAN hoặc SET_MODE)"}, status=status.HTTP_400_BAD_REQUEST)

        params = {}
        if action_type == "SET_FAN":
            fan_on = bool(request.data.get("on", False))
            params = {"on": fan_on}
            device.fan_output = fan_on
            device.save(update_fields=["fan_output", "updated_at"])
        elif action_type == "SET_MODE":
            mode = request.data.get("mode", "AUTO").upper()
            params = {"mode": mode}
            device.mode = mode
            device.save(update_fields=["mode", "updated_at"])
        elif action_type == "SET_CONFIG":
            params = {
                "fan_on_temp": request.data.get("fan_on_temp", device.temp_threshold_on),
                "fan_off_temp": request.data.get("fan_off_temp", device.temp_threshold_off),
            }
            device.temp_threshold_on = params["fan_on_temp"]
            device.temp_threshold_off = params["fan_off_temp"]
            device.save(update_fields=["temp_threshold_on", "temp_threshold_off", "updated_at"])

        # Tạo lịch sử lệnh
        cmd_log = CommandLog.objects.create(
            device=device,
            command_type=action_type,
            params=params,
            status="SENT",
        )

        # Bắn lệnh ra MQTT broker
        mqtt_ok = send_mqtt_command(device.device_id, action_type, params)
        if not mqtt_ok:
            cmd_log.status = "FAILED"
            cmd_log.save(update_fields=["status"])

        return Response({
            "message": f"Đã phát lệnh {action_type} thành công",
            "command_id": cmd_log.id,
            "mqtt_dispatched": mqtt_ok,
            "device": DeviceSerializer(device).data,
        }, status=status.HTTP_200_OK)


class TelemetryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Telemetry.objects.all()
    serializer_class = TelemetrySerializer

    def get_queryset(self):
        qs = super().get_queryset()
        device_id = self.request.query_params.get("device_id")
        if device_id:
            qs = qs.filter(device__device_id=device_id)
        return qs[:100]  # Giới hạn 100 bản ghi gần nhất


class AlertViewSet(viewsets.ModelViewSet):
    queryset = Alert.objects.all()
    serializer_class = AlertSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        """Đóng cảnh báo"""
        alert = self.get_object()
        alert.status = "resolved"
        alert.resolved_at = timezone.now()
        alert.save(update_fields=["status", "resolved_at"])
        return Response({"message": "Cảnh báo đã được giải quyết", "alert": AlertSerializer(alert).data})

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        """Xác nhận đã tiếp nhận cảnh báo"""
        alert = self.get_object()
        alert.status = "acknowledged"
        alert.save(update_fields=["status"])
        return Response({"message": "Đã tiếp nhận cảnh báo", "alert": AlertSerializer(alert).data})


class CommandLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = CommandLog.objects.all()
    serializer_class = CommandLogSerializer
