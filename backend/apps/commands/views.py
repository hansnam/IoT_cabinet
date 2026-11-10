from rest_framework import permissions, viewsets
from rest_framework.response import Response
from apps.commands.models import Command
from apps.commands.serializers import CommandSerializer
from apps.devices.policies import visible_devices


class CommandViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API ViewSet theo dõi lịch sử và trạng thái các lệnh điều khiển:
    - GET /api/commands/ : Danh sách các lệnh đã phát hành
      Tham số:
        - device_id: Lọc theo mã tủ
        - status: PENDING / SENT / ACKED / TIMEOUT / FAILED
    - GET /api/commands/{id}/ : Chi tiết một lệnh cụ thể
    """
    serializer_class = CommandSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return Command.objects.none()

        allowed_devices = visible_devices(user)
        qs = Command.objects.select_related('device', 'actor').filter(device__in=allowed_devices)

        device_id = self.request.query_params.get('device_id')
        if device_id:
            qs = qs.filter(device_id=device_id)

        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param.upper())

        return qs

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(qs[:100], many=True)
        return Response(serializer.data)
