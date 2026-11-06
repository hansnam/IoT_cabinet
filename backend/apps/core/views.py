"""
Core Views Module
Cung cấp các endpoint kiểm tra trạng thái hoạt động của hệ thống (Liveness & Readiness).
Thống nhất cấu trúc envelope lỗi cho mọi phản hồi non-2xx.
"""
from django.db import connection
from django.http import HttpRequest, JsonResponse

from apps.core.errors import ErrorCode, make_error_response


def health_live(request: HttpRequest) -> JsonResponse:
    """
    Liveness probe: Kiểm tra tiến trình web server Django đang phản hồi.
    Endpoint: GET /health/live
    """
    return JsonResponse({'status': 'UP'}, status=200)

def health_ready(request: HttpRequest) -> JsonResponse:
    """
    Readiness probe: Kiểm tra kết nối PostgreSQL thật.
    Endpoint: GET /health/ready
    - Trả về HTTP 200 nếu kết nối DB sẵn sàng.
    - Trả về HTTP 503 với envelope lỗi chuẩn ErrorCode.SERVICE_UNAVAILABLE nếu DB mất kết nối.
    """
    request_id = getattr(request, 'request_id', None)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1;')
            cursor.fetchone()
        return JsonResponse(
            {
                'status': 'READY',
                'database': 'CONNECTED'
            },
            status=200
        )
    except Exception:  # noqa: BLE001
        return make_error_response(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message='Cơ sở dữ liệu chưa sẵn sàng phục vụ yêu cầu.',
            status_code=503,
            request_id=request_id
        )