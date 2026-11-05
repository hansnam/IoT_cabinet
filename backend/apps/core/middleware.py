"""
Core Middleware Module
Quản lý vòng đời Request ID và đính kèm các Header bảo mật bắt buộc.
"""
import uuid

from django.http import HttpRequest, HttpResponse
from django.utils.deprecation import MiddlewareMixin


class RequestIDAndSecurityHeadersMiddleware(MiddlewareMixin):
    """
    Middleware xử lý:
    1. Trích xuất hoặc sinh mới X-Request-ID dạng UUID v4 cho mọi HTTP request.
    2. Đính kèm header X-Request-ID vào HTTP response để đối soát vết lỗi.
    3. Thiết lập các header chống cache và bảo vệ trình duyệt (no-store, nosniff, DENY).
    """
    def process_request(self, request: HttpRequest) -> None:
        """Kiểm tra header X-Request-ID từ client gửi lên; nếu không hợp lệ thì sinh mới."""
        client_request_id = request.headers.get('X-Request-ID')
        valid_uuid = None

        if client_request_id:
            try:
                parsed = uuid.UUID(str(client_request_id).strip())
                if parsed.version == 4:
                    valid_uuid = str(parsed)
            except (ValueError, TypeError, AttributeError):
                valid_uuid = None

        if not valid_uuid:
            valid_uuid = str(uuid.uuid4())

        request.request_id = valid_uuid # pyright: ignore[reportAttributeAccessIssue]

    def process_response(self, request: HttpRequest, response: HttpResponse) -> HttpResponse:
        """Đính kèm các header bảo vệ vào phản hồi HTTP."""
        request_id = getattr(request, 'request_id', None)
        if request_id:
            response['X-Request-ID'] = request_id

        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'DENY'

        return response