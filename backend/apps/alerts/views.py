import uuid
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.alerts.models import Alert
from apps.alerts.serializers import AlertSerializer
from apps.audit.models import AuditLog
from apps.audit.services import AuditService
from apps.devices.policies import visible_devices


class AlertViewSet(viewsets.ModelViewSet):
    """
    API ViewSet quản lý Cảnh báo sự cố (Alerts & Incidents):
    - GET /api/alerts/ : Danh sách cảnh báo
      Tham số:
        - device_id: Lọc theo mã tủ
        - status: OPEN / ACKNOWLEDGED / RESOLVED
        - severity: INFO / WARNING / CRITICAL
    - POST /api/alerts/{id}/acknowledge/ : Ghi nhận cảnh báo
    - POST /api/alerts/{id}/resolve/ : Đánh dấu đã giải quyết cảnh báo
    """
    serializer_class = AlertSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Alert.objects.select_related('device', 'acknowledged_by').all()

        if user and user.is_authenticated:
            allowed_devices = visible_devices(user)
            qs = qs.filter(device__in=allowed_devices)

        device_id = self.request.query_params.get('device_id')
        if device_id:
            qs = qs.filter(device_id=device_id)

        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param.upper())

        severity = self.request.query_params.get('severity')
        if severity:
            qs = qs.filter(severity=severity.upper())

        return qs

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(qs[:100], many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        """Ghi nhận cảnh báo đang xảy ra"""
        alert = self.get_object()
        if alert.status != 'OPEN':
            return Response(
                {"message": f"Cảnh báo đang ở trạng thái '{alert.status}', không cần ghi nhận lại."},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = request.user if (request.user and request.user.is_authenticated) else None
        alert.status = 'ACKNOWLEDGED'
        alert.acknowledged_at = timezone.now()
        alert.acknowledged_by = user
        alert.save(update_fields=['status', 'acknowledged_at', 'acknowledged_by'])

        request_id = getattr(request, 'request_id', None) or str(uuid.uuid4())
        AuditService.record_event(
            function_key="ALERT_MANAGEMENT",
            action="ALERT_ACKNOWLEDGED",
            outcome="SUCCESS",
            summary=f"Ghi nhận cảnh báo #{alert.id} trên tủ {alert.device_id}",
            request_id=request_id,
            actor_user=user,
            actor_type=AuditLog.ActorType.USER if user else AuditLog.ActorType.SYSTEM,
            target_type="ALERT",
            target_id=str(alert.id),
            device_id=alert.device_id,
            metadata={'device_id': alert.device_id}
        )

        return Response(AlertSerializer(alert).data)

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """Đánh dấu cảnh báo đã được khắc phục"""
        alert = self.get_object()
        if alert.status == 'RESOLVED':
            return Response(
                {"message": "Cảnh báo này đã được khắc phục trước đó."},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = request.user if (request.user and request.user.is_authenticated) else None
        alert.status = 'RESOLVED'
        alert.resolved_at = timezone.now()
        alert.save(update_fields=['status', 'resolved_at'])

        request_id = getattr(request, 'request_id', None) or str(uuid.uuid4())
        AuditService.record_event(
            function_key="ALERT_MANAGEMENT",
            action="ALERT_RESOLVED",
            outcome="SUCCESS",
            summary=f"Đánh dấu khắc phục xong cảnh báo #{alert.id} trên tủ {alert.device_id}",
            request_id=request_id,
            actor_user=user,
            actor_type=AuditLog.ActorType.USER if user else AuditLog.ActorType.SYSTEM,
            target_type="ALERT",
            target_id=str(alert.id),
            device_id=alert.device_id,
            metadata={'device_id': alert.device_id}
        )

        return Response(AlertSerializer(alert).data)
