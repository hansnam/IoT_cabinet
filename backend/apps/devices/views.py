import hashlib
import json
import uuid
from django.db import transaction
from django.utils import timezone
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.audit.models import AuditLog
from apps.audit.services import AuditService
from apps.commands.models import Command, OutboxMessage
from apps.devices.models import Device, DeviceAccess, DeviceConfig
from apps.devices.mqtt_client import publish_command_to_broker
from apps.devices.policies import require_device_permission, visible_devices
from apps.devices.serializers import (
    DeviceConfigSerializer,
    DeviceControlSerializer,
    DeviceSerializer,
)


def build_esp32_config_json_and_hash(desired_config: dict) -> tuple[str, str]:
    """
    Chuẩn hóa cấu hình mong muốn thành chuỗi JSON và mã băm SHA-256 khớp 100%
    với hàm onMqttMessage(APPLY_CONFIG) trong firmware/scr/main.cpp của TV1.
    """
    max_temp = float(desired_config.get('temp_alert_c', desired_config.get('max_temp', 35.0)))
    max_hum = float(desired_config.get('humidity_alert_pct', desired_config.get('max_hum', 80.0)))
    fan_on = float(desired_config.get('fan_on_c', min(max_temp - 2.0, 30.0)))
    fan_off = float(desired_config.get('fan_off_c', fan_on - 2.0))
    if fan_off >= fan_on:
        fan_off = fan_on - 2.0

    cfg_obj = {
        'profile': desired_config.get('profile', 'CONTROLLER'),
        'fan_binding_id': desired_config.get('fan_binding_id', None),
        'fan_on_c': fan_on,
        'fan_off_c': fan_off,
        'temp_alert_c': max_temp,
        'humidity_alert_pct': max_hum,
        'temp_clear_margin_c': float(desired_config.get('temp_clear_margin_c', 1.0)),
        'humidity_clear_margin_pct': float(desired_config.get('humidity_clear_margin_pct', 3.0)),
        'telemetry_interval_ms': int(desired_config.get('telemetry_interval_ms', 5000)),
        'manual_timeout_s': int(desired_config.get('manual_timeout_s', 60)),
    }
    config_json = json.dumps(cfg_obj, separators=(',', ':'), sort_keys=True)
    content_hash = hashlib.sha256(config_json.encode('utf-8')).hexdigest()
    return config_json, content_hash


class DeviceViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    API ViewSet quản lý Thiết bị tủ mạng (Devices):
    - GET /api/devices/ : Danh sách thiết bị trong phạm vi phân quyền (Scope filtering)
    - GET /api/devices/{id}/ : Chi tiết thiết bị
    - GET /api/devices/{id}/latest/ : Số đo và trạng thái mới nhất
    - POST /api/devices/{id}/control/ : Gửi lệnh điều khiển quạt / chế độ
    - GET/POST /api/devices/{id}/config/ : Đọc / Cập nhật cấu hình mong muốn
    """
    serializer_class = DeviceSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return Device.objects.none()
        return visible_devices(user)

    def retrieve(self, request, *args, **kwargs):
        device = self.get_object()
        require_device_permission(request.user, device, action='VIEW')
        serializer = self.get_serializer(device)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def latest(self, request, id=None):
        """Trả về dữ liệu viễn trắc và trạng thái mới nhất của tủ mạng"""
        device = self.get_object()
        require_device_permission(request.user, device, action='VIEW')

        data = {
            'device_id': device.id,
            'device_name': device.name,
            'status': device.status,
            'is_online': device.is_online(),
            'last_seen_at': device.last_seen_at,
            'temperature': device.latest_temperature,
            'humidity': device.latest_humidity,
            'fan_status': device.latest_fan_status,
            'door_status': device.latest_door_status,
            'mode': device.latest_mode,
            'desired_config': device.desired_config,
            'applied_config': device.applied_config,
        }
        return Response(data)

    @action(detail=True, methods=['post'])
    def control(self, request, id=None):
        """
        Nhận lệnh điều khiển từ người dùng, kiểm tra quyền OPERATE,
        lưu vào Command Log và Outbox, xuất bản tới MQTT Broker.
        """
        device = self.get_object()

        # Kiểm tra trạng thái vòng đời (chỉ thiết bị ACTIVE mới được nhận lệnh điều khiển)
        if device.status in ('LOCKED', 'DECOMMISSIONED', 'MAINTENANCE'):
            return Response(
                {"error": {"code": "DEVICE_LOCKED", "message": f"Tủ mạng đang ở trạng thái '{device.status}', không thể điều khiển."}},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Kiểm tra quyền OPERATE
        require_device_permission(request.user, device, action='OPERATE')
        user = request.user

        serializer = DeviceControlSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        command_type = validated_data.get('command', 'SET_FAN')
        correlation_id = str(uuid.uuid4())
        cmd_seq = Command.objects.filter(device=device).count() + 1

        params = {}
        payload = {
            'schema_version': 3,
            'command_id': correlation_id,
            'correlation_id': correlation_id,
            'type': command_type,
            'command': command_type,
            'device_id': device.id,
            'device_command_seq': cmd_seq,
            'timestamp': timezone.now().isoformat(),
        }
        if 'fan' in validated_data:
            fan_bool = (validated_data['fan'] == 'ON')
            payload['fan'] = validated_data['fan']
            payload['on'] = fan_bool
            params['on'] = fan_bool
            params['fan'] = validated_data['fan']
        if 'mode' in validated_data:
            payload['mode'] = validated_data['mode']
            params['mode'] = validated_data['mode']
            params['duration_s'] = int(request.data.get('duration_s', 60))
        payload['params'] = params

        request_id = getattr(request, 'request_id', str(uuid.uuid4()))

        # Lưu Command và Outbox trong Transaction Atomic
        with transaction.atomic():
            cmd = Command.objects.create(
                device=device,
                command_type=command_type,
                payload=payload,
                status='PENDING',
                correlation_id=correlation_id,
                actor=user,
            )

            outbox = OutboxMessage.objects.create(
                command=cmd,
                topic=f"cabinet/v3/devices/{device.id}/commands",
                payload=payload,
                qos=1,
                status='PENDING'
            )

            # Ghi nhận vào AuditLog
            AuditService.record_event(
                function_key="DEVICE_CONTROL",
                action="COMMAND_ISSUED",
                outcome="ACCEPTED",
                summary=f"Gửi lệnh {command_type} tới tủ {device.id}",
                request_id=request_id,
                actor_user=user,
                actor_type=AuditLog.ActorType.USER if user else AuditLog.ActorType.SYSTEM,
                target_type="DEVICE",
                target_id=device.id,
                device_id=device.id,
                correlation_id=correlation_id,
                is_async=True,
                metadata={'command_id': str(cmd.id), 'device_id': device.id, 'command_type': command_type}
            )

        # Xuất bản ngay tới MQTT Broker (nếu có kết nối)
        published = publish_command_to_broker(device.id, payload)
        if published:
            cmd.status = 'SENT'
            cmd.sent_at = timezone.now()
            cmd.save(update_fields=['status', 'sent_at'])
            outbox.status = 'PUBLISHED'
            outbox.published_at = timezone.now()
            outbox.save(update_fields=['status', 'published_at'])

        return Response(
            {
                'status': 'ACCEPTED',
                'command_id': str(cmd.id),
                'correlation_id': correlation_id,
                'published_to_broker': published,
                'message': f"Lệnh '{command_type}' đã được ghi nhận và đưa vào hàng đợi xử lý."
            },
            status=status.HTTP_202_ACCEPTED
        )

    @action(detail=True, methods=['get', 'post'])
    def config(self, request, id=None):
        """Đọc hoặc cập nhật cấu hình mong muốn (desired_config) của thiết bị"""
        device = self.get_object()

        if request.method == 'GET':
            require_device_permission(request.user, device, action='VIEW')
            return Response({
                'device_id': device.id,
                'desired_config': device.desired_config,
                'applied_config': device.applied_config,
            })

        if device.status in ('LOCKED', 'DECOMMISSIONED', 'MAINTENANCE'):
            return Response(
                {"error": {"code": "DEVICE_LOCKED", "message": f"Tủ mạng đang ở trạng thái '{device.status}', không thể cập nhật cấu hình."}},
                status=status.HTTP_400_BAD_REQUEST
            )

        require_device_permission(request.user, device, action='OPERATE')
        user = request.user

        serializer = DeviceConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_desired = serializer.validated_data['desired_config']

        with transaction.atomic():
            device.desired_config = new_desired
            device.save(update_fields=['desired_config', 'updated_at'])

            latest_version = device.config_versions.count() + 1
            DeviceConfig.objects.create(
                device=device,
                version=latest_version,
                config_data=new_desired,
                applied=False
            )

            correlation_id = str(uuid.uuid4())
            cmd_seq = Command.objects.filter(device=device).count() + 1
            config_json, content_hash = build_esp32_config_json_and_hash(new_desired)
            payload = {
                'schema_version': 3,
                'command_id': correlation_id,
                'correlation_id': correlation_id,
                'type': 'APPLY_CONFIG',
                'command': 'APPLY_CONFIG',
                'device_id': device.id,
                'device_command_seq': cmd_seq,
                'config_version': latest_version,
                'config': new_desired,
                'params': {
                    'config_version': latest_version,
                    'config_json': config_json,
                    'content_hash': content_hash,
                },
                'timestamp': timezone.now().isoformat(),
            }

            Command.objects.create(
                device=device,
                command_type='APPLY_CONFIG',
                payload=payload,
                status='PENDING',
                correlation_id=correlation_id,
                actor=user,
            )

            request_id = getattr(request, 'request_id', None) or str(uuid.uuid4())
            AuditService.record_event(
                function_key="DEVICE_CONFIG",
                action="CONFIG_UPDATED",
                outcome="SUCCESS",
                summary=f"Cập nhật desired_config v{latest_version} cho tủ {device.id}",
                request_id=request_id,
                actor_user=user,
                actor_type=AuditLog.ActorType.USER if user else AuditLog.ActorType.SYSTEM,
                target_type="DEVICE",
                target_id=device.id,
                device_id=device.id,
                metadata={'device_id': device.id}
            )

        publish_command_to_broker(device.id, payload)

        return Response({
            'status': 'SUCCESS',
            'device_id': device.id,
            'version': latest_version,
            'desired_config': new_desired,
            'message': f"Đã cập nhật desired_config v{latest_version} và phát lệnh đồng bộ xuống tủ mạng."
        })

    @action(detail=True, methods=['get'])
    def export(self, request, id=None):
        """Xuất lịch sử đo đạc viễn trắc ra file CSV (có UTF-8 BOM và chống CSV Injection)"""
        device = self.get_object()
        require_device_permission(request.user, device, action='VIEW')

        from apps.telemetry.views import build_telemetry_csv_response
        return build_telemetry_csv_response(device, request.query_params)
