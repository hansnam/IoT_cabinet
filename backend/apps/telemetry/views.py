import csv
from django.db.models import Avg, Max, Min
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.devices.policies import visible_devices
from apps.telemetry.models import Telemetry
from apps.telemetry.serializers import TelemetrySerializer


def sanitize_csv_cell(value):
    """
    Phòng thủ tấn công CSV Formula Injection (CWE-1236).
    Nếu ký tự đầu là '=', '+', '-', '@', '\t', '\r', thêm tiền tố dấu nháy đơn (').
    """
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in ('=', '+', '-', '@', '\t', '\r'):
        return f"'{s}"
    return s


def build_telemetry_csv_response(device, query_params):
    """
    Tạo phản hồi tải file CSV dữ liệu viễn trắc với mã UTF-8 BOM và chống CSV Injection.
    """
    qs = Telemetry.objects.filter(device=device)

    from_time = query_params.get('from_time')
    if from_time:
        dt = parse_datetime(from_time)
        if dt:
            qs = qs.filter(measured_at__gte=dt)

    to_time = query_params.get('to_time')
    if to_time:
        dt = parse_datetime(to_time)
        if dt:
            qs = qs.filter(measured_at__lte=dt)

    limit_param = query_params.get('limit', '5000')
    try:
        limit = min(int(limit_param), 10000)
    except ValueError:
        limit = 5000

    records = qs[:limit]

    timestamp_str = timezone.now().strftime('%Y%m%d_%H%M%S')
    filename = f"telemetry_{device.id}_{timestamp_str}.csv"

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    # Ghi UTF-8 BOM để Excel trên Windows hiển thị đúng tiếng Việt
    response.write('\ufeff')

    writer = csv.writer(response)
    writer.writerow([
        'Thời gian đo (ISO8601)',
        'Mã tủ',
        'Nhiệt độ (°C)',
        'Độ ẩm (%)',
        'Trạng thái quạt',
        'Cửa tủ',
        'Chế độ',
        'Uptime (s)',
        'Seq',
    ])

    for r in records:
        writer.writerow([
            sanitize_csv_cell(r.measured_at.isoformat() if r.measured_at else ''),
            sanitize_csv_cell(device.id),
            sanitize_csv_cell(r.temperature),
            sanitize_csv_cell(r.humidity),
            sanitize_csv_cell(r.fan_status),
            sanitize_csv_cell(r.door_status),
            sanitize_csv_cell(r.mode),
            sanitize_csv_cell(r.uptime),
            sanitize_csv_cell(r.seq),
        ])

    return response


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
    - GET /api/telemetry/export/ : Xuất file CSV lịch sử viễn trắc
    """
    serializer_class = TelemetrySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return Telemetry.objects.none()

        allowed_devices = visible_devices(user)
        qs = Telemetry.objects.select_related('device').filter(device__in=allowed_devices)

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

    @action(detail=False, methods=['get'])
    def export(self, request):
        """API xuất dữ liệu CSV viễn trắc qua query param device_id"""
        device_id = request.query_params.get('device_id') or request.query_params.get('cabinet_id')
        if not device_id:
            return Response(
                {"error": {"code": "MISSING_DEVICE_ID", "message": "Vui lòng chỉ định tham số device_id."}},
                status=status.HTTP_400_BAD_REQUEST
            )
        from apps.devices.models import Device
        from apps.devices.policies import require_device_permission
        device = Device.objects.filter(id=device_id).first()
        if not device:
            return Response(
                {"error": {"code": "DEVICE_NOT_FOUND", "message": f"Không tìm thấy tủ mạng '{device_id}'."}},
                status=status.HTTP_404_NOT_FOUND
            )
        require_device_permission(request.user, device, action='VIEW')
        return build_telemetry_csv_response(device, request.query_params)
