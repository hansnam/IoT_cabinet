import uuid
from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
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


class DeviceViewSet(viewsets.ModelViewSet):
    """
    API ViewSet quản lý Thiết bị tủ mạng (Devices):
    - GET /api/devices/ : Danh sách thiết bị trong phạm vi phân quyền (Scope filtering)
    - GET /api/devices/{id}/ : Chi tiết thiết bị
    - GET /api/devices/{id}/latest/ : Số đo và trạng thái mới nhất
    - POST /api/devices/{id}/control/ : Gửi lệnh điều khiển quạt / chế độ
    - GET/POST /api/devices/{id}/config/ : Đọc / Cập nhật cấu hình mong muốn
    """
    serializer_class = DeviceSerializer
    lookup_field = 'id'

    def get_queryset(self):
        user = self.request.user
        if user and user.is_authenticated:
            return visible_devices(user)
        # Trong môi trường dev/local nếu chưa đăng nhập qua session, cho phép xem danh sách
        return Device.objects.all()

    def retrieve(self, request, *args, **kwargs):
        device = self.get_object()
        if request.user and request.user.is_authenticated:
            require_device_permission(request.user, device, action='VIEW')
        serializer = self.get_serializer(device)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def latest(self, request, id=None):
        """Trả về dữ liệu viễn trắc và trạng thái mới nhất của tủ mạng"""
        device = self.get_object()
        if request.user and request.user.is_authenticated:
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

        # Kiểm tra trạng thái vòng đời
        if device.status in ('LOCKED', 'DECOMMISSIONED'):
            return Response(
                {"error": {"code": "DEVICE_LOCKED", "message": f"Tủ mạng đang ở trạng thái '{device.status}', không thể điều khiển."}},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Kiểm tra quyền OPERATE
        user = request.user if (request.user and request.user.is_authenticated) else None
        if user:
            require_device_permission(user, device, action='OPERATE')

        serializer = DeviceControlSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        command_type = validated_data.get('command', 'SET_FAN')
        correlation_id = str(uuid.uuid4())

        payload = {
            'command_id': correlation_id,
            'correlation_id': correlation_id,
            'command': command_type,
            'device_id': device.id,
            'timestamp': timezone.now().isoformat(),
        }
        if 'fan' in validated_data:
            payload['fan'] = validated_data['fan']
        if 'mode' in validated_data:
            payload['mode'] = validated_data['mode']

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
                topic=f"cabinet/{device.id}/command",
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
            return Response({
                'device_id': device.id,
                'desired_config': device.desired_config,
                'applied_config': device.applied_config,
            })

        user = request.user if (request.user and request.user.is_authenticated) else None
        if user:
            require_device_permission(user, device, action='OPERATE')

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
            payload = {
                'command_id': correlation_id,
                'correlation_id': correlation_id,
                'command': 'APPLY_CONFIG',
                'device_id': device.id,
                'config_version': latest_version,
                'config': new_desired,
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
