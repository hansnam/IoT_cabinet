from django.db.models import Avg, Max, Min
from django.utils.dateparse import parse_datetime
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.devices.policies import visible_devices
from apps.telemetry.models import Telemetry
from apps.telemetry.serializers import TelemetrySerializer


class TelemetryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API ViewSet tra cứu dữ liệu viễn trắc (Telemetry History & Analytics):
    - GET /api/telemetry/ : Danh sách bản ghi đo đạc theo thời gian
      Tham số query:
        - device_id (str): Lọc theo mã tủ (ví dụ: ?device_id=CABINET-01)
        - from_time (ISO8601): Thời điểm bắt đầu
        - to_time (ISO8601): Thời điểm kết thúc
        - limit (int): Giới hạn số lượng (mặc định 50, tối đa 500)
    - GET /api/telemetry/stats/ : Thống kê min/max/avg nhiệt độ, độ ẩm
    """
    serializer_class = TelemetrySerializer

    def get_queryset(self):
        user = self.request.user
        qs = Telemetry.objects.select_related('device').all()

        if user and user.is_authenticated:
            allowed_devices = visible_devices(user)
            qs = qs.filter(device__in=allowed_devices)

        # Lọc theo thiết bị
        device_id = self.request.query_params.get('device_id') or self.request.query_params.get('cabinet_id')
        if device_id:
            qs = qs.filter(device_id=device_id)

        # Lọc theo khoảng thời gian
        from_time = self.request.query_params.get('from_time')
        if from_time:
            dt = parse_datetime(from_time)
            if dt:
                qs = qs.filter(measured_at__gte=dt)

        to_time = self.request.query_params.get('to_time')
        if to_time:
            dt = parse_datetime(to_time)
            if dt:
                qs = qs.filter(measured_at__lte=dt)

        return qs

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        limit_param = request.query_params.get('limit', '50')
        try:
            limit = min(int(limit_param), 500)
        except ValueError:
            limit = 50
        serializer = self.get_serializer(qs[:limit], many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Thống kê tổng hợp số liệu đo đạc (Min, Max, Avg)"""
        qs = self.get_queryset()
        device_id = request.query_params.get('device_id') or request.query_params.get('cabinet_id')
        if device_id:
            qs = qs.filter(device_id=device_id)

        count = qs.count()
        aggregates = qs.aggregate(
            min_temp=Min('temperature'),
            max_temp=Max('temperature'),
            avg_temp=Avg('temperature'),
            min_hum=Min('humidity'),
            max_hum=Max('humidity'),
            avg_hum=Avg('humidity'),
        )

        return Response({
            'device_id': device_id or 'ALL',
            'count': count,
            'temperature': {
                'min': aggregates['min_temp'],
                'max': aggregates['max_temp'],
                'avg': round(aggregates['avg_temp'], 2) if aggregates['avg_temp'] is not None else None,
            },
            'humidity': {
                'min': aggregates['min_hum'],
                'max': aggregates['max_hum'],
                'avg': round(aggregates['avg_hum'], 2) if aggregates['avg_hum'] is not None else None,
            }
        })
