"""
Core Errors & Envelope Module
Chịu trách nhiệm định dạng chuẩn envelope phản hồi lỗi API, xử lý ngoại lệ DRF,
view xử lý thất bại CSRF, và các handler lỗi 400/403/404/500 của Django cho toàn hệ thống.
"""
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    ParseError,
    PermissionDenied,
    Throttled,
    ValidationError,
)
from rest_framework.views import exception_handler


class ErrorCode:
    VALIDATION_ERROR = 'VALIDATION_ERROR'
    NOT_AUTHENTICATED = 'NOT_AUTHENTICATED'
    CSRF_FAILED = 'CSRF_FAILED'
    FORBIDDEN = 'FORBIDDEN'
    NOT_FOUND = 'NOT_FOUND'
    METHOD_NOT_ALLOWED = 'METHOD_NOT_ALLOWED'
    RATE_LIMITED = 'RATE_LIMITED'
    INTERNAL_SERVER_ERROR = 'INTERNAL_SERVER_ERROR'
    SERVICE_UNAVAILABLE = 'SERVICE_UNAVAILABLE'

def make_error_envelope(code: str, message: str, request_id:str | None = None) -> dict[str, Any]:
    """Tạo cấu trúc từ điển envelope lỗi tiêu chuẩn của dự án."""
    return {
        'error': {
            'code': code,
            'message': message,
            'request_id': request_id,
        }
    }

def make_error_response(code: str, message: str, status_code: int, request_id:str | None = None) -> JsonResponse:
    """Tạo đối tượng JsonResponse chứa envelope lỗi và mã trạng thái HTTP tương ứng."""
    return JsonResponse(
        make_error_envelope(code=code, message=message, request_id=request_id),
        status=status_code
    )

def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    """
    View xử lý khi kiểm tra CSRF của Django thất bại (CSRF_FAILURE_VIEW).
    Trả về JSON với mã lỗi CSRF_FAILED (HTTP 403) thay vì trang HTML mặc định.
    """
    request_id = getattr(request, 'request_id', None)
    return make_error_response(
        code=ErrorCode.CSRF_FAILED,
        message='Yêu cầu bị từ chối do vi phạm kiểm tra an toàn CSRF. Vui lòng làm mới trang.',
        status_code=403,
        request_id=request_id
    )

def custom_exception_handler(exc: Exception, context: dict[str, Any]) -> HttpResponse | None:
    """
    Exception handler tùy biến cho Django REST Framework.
    Bắt mọi ngoại lệ của DRF và chuẩn hóa về envelope lỗi thống nhất.
    Phân biệt rõ ràng giữa ParseError (400), CSRF failure (403 CSRF_FAILED) và Forbidden thông thường.
    """
    response = exception_handler(exc, context)

    request = context.get('request')
    request_id = getattr(request, 'request_id', None)

    if response is not None:
        status_code = response.status_code
        code = ErrorCode.INTERNAL_SERVER_ERROR
        message = 'Yêu cầu không hợp lệ.'

        # 1. Lỗi phân tích cú pháp JSON (ParseError) -> VALIDATION_ERROR 400
        if (isinstance(exc, ParseError)):
            code = ErrorCode.VALIDATION_ERROR
            message = 'Dữ liệu gửi lên không hợp lệ. Vui lòng kiểm tra định dạng JSON và các trường dữ liệu.'

        # 2. Lỗi chưa xác thực -> NOT_AUTHENTICATED 401
        elif isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
            code = ErrorCode.NOT_AUTHENTICATED
            message = 'Yêu cầu chưa xác thực. Vui lòng đăng nhập để tiếp tục.'

        # 3. Lỗi quyền truy cập hoặc thất bại CSRF trong SessionAuthentication của DRF
        elif isinstance(exc, (PermissionDenied,)):
            exc_str = str(exc)
            if 'CSRF' in exc_str:
                code = ErrorCode.CSRF_FAILED
                message = 'Yêu cầu bị từ chối do vi phạm kiểm tra an toàn CSRF. Vui lòng làm mới trang.'
            elif exc.get_codes() == 'must_change_password':
                code = ErrorCode.FORBIDDEN
                message = 'Tài khoản bắt buộc phải đổi mật khẩu trước khi truy cập các chức năng khác.'
            else:
                code = ErrorCode.FORBIDDEN
                message = 'Bạn không có quyền truy cập tài nguyên này.'

        # 4. Lỗi giới hạn tần suất (Rate Limit) -> RATE_LIMITED 429
        elif isinstance(exc, Throttled):
            code = ErrorCode.RATE_LIMITED
            wait_time = getattr(exc, 'wait', None)
            message = f'Quá nhiều yêu cầu. Vui lòng thử lại sau {wait_time} giây.' if wait_time else 'Quá nhiều yêu cầu.'

        # 5. Lỗi kiểm tra dữ liệu serializer -> VALIDATION_ERROR 400
        elif isinstance(exc, ValidationError):
            code = ErrorCode.VALIDATION_ERROR
            message = 'Dữ liệu gửi lên không hợp lệ. Vui lòng kiểm tra các trường dữ liệu.'

        # 6. Các mã trạng thái HTTP chuẩn khác
        elif status_code == 404:
            code = ErrorCode.NOT_FOUND
            message = 'Tài nguyên yêu cầu không tồn tại.'
        elif status_code == 405:
            code = ErrorCode.METHOD_NOT_ALLOWED
            message = 'Phương thức HTTP không được phép cho tài nguyên này.'

        response.data = make_error_envelope(code=code, message=message, request_id=request_id)
        return response

    return make_error_response(
        code=ErrorCode.INTERNAL_SERVER_ERROR,
        message='Đã xảy ra lỗi không mong muốn trên máy chủ. Vui lòng thử lại sau.',
        status_code=500,
        request_id=request_id
    )

# Django Core Handlers (Áp dụng cho toàn bộ đường dẫn ngoài DRF, ví dụ URL 404)
def handler400(request: HttpRequest, exception: Any = None) -> JsonResponse:
    request_id = getattr(request, 'request_id', None)
    return make_error_response(ErrorCode.VALIDATION_ERROR, 'Yêu cầu không hợp lệ.', 400, request_id)

def handler403(request: HttpRequest, exception: Any = None) -> JsonResponse:
    request_id = getattr(request, 'request_id', None)
    return make_error_response(ErrorCode.FORBIDDEN, 'Bạn không có quyền truy cập tài nguyên này.', 403, request_id)

def handler404(request: HttpRequest, exception: Any = None) -> JsonResponse:
    request_id = getattr(request, 'request_id', None)
    return make_error_response(ErrorCode.NOT_FOUND, 'Đường dẫn được yêu cầu không tồn tại.', 404, request_id)

def handler500(request: HttpRequest) -> JsonResponse:
    request_id = getattr(request, 'request_id', None)
    return make_error_response(ErrorCode.INTERNAL_SERVER_ERROR, 'Đã xảy ra lỗi nội bộ máy chủ.', 500, request_id)
