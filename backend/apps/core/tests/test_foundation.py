"""
Test module kiểm tra các thành phần hạ tầng core:
- RequestIDAndSecurityHeadersMiddleware (tạo UUID mới, nhận diện UUID hợp lệ, sinh lại nếu rác).
- Các Header bảo mật (X-Request-ID, Cache-Control no-store, nosniff, DENY).
- csrf_failure view trả về JSON chuẩn.
- DRF Exception Handler: ParseError -> VALIDATION_ERROR 400; CSRF PermissionDenied -> CSRF_FAILED 403.
- Django Core Handler 404 trả về JSON envelope chuẩn.
- Health check endpoints /health/live và /health/ready (chuẩn hóa lỗi 503).
"""
import json
import uuid
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from rest_framework.exceptions import ParseError, PermissionDenied

from apps.core.errors import (
    ErrorCode,
    csrf_failure,
    custom_exception_handler,
    handler404,
)
from apps.core.middleware import RequestIDAndSecurityHeadersMiddleware
from apps.core.views import health_live, health_ready


class MiddlewareAndSecurityHeadersTestCase(TestCase):
    """Kiểm tra RequestIDAndSecurityHeadersMiddleware."""

    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = RequestIDAndSecurityHeadersMiddleware(get_response=lambda r: HttpResponse("OK"))

    def test_generates_new_uuid_when_no_header(self):
        """Tự động sinh UUID v4 mới khi client không gửi X-Request-ID."""
        request = self.factory.get('/health/live')
        self.middleware.process_request(request)

        self.assertTrue(hasattr(request, 'request_id'))
        parsed = uuid.UUID(request.request_id)
        self.assertEqual(parsed.version, 4)

    def test_reuses_valid_client_uuid(self):
        """Sử dụng lại UUID v4 hợp lệ do client gửi lên."""
        client_uuid = str(uuid.uuid4())
        request = self.factory.get('/health/live', HTTP_X_REQUEST_ID=client_uuid)
        self.middleware.process_request(request)

        self.assertEqual(request.request_id, client_uuid)

    def test_replaces_invalid_client_uuid(self):
        """Thay thế chuỗi rác bằng UUID v4 hợp lệ nếu client gửi rác."""
        request = self.factory.get('/health/live', HTTP_X_REQUEST_ID='invalid-not-a-uuid')
        self.middleware.process_request(request)

        self.assertNotEqual(request.request_id, 'invalid-not-a-uuid')
        parsed = uuid.UUID(request.request_id)
        self.assertEqual(parsed.version, 4)

    def test_response_headers_are_attached(self):
        """Đảm bảo các header bảo mật được đính kèm vào response."""
        request = self.factory.get('/health/live')
        self.middleware.process_request(request)
        response = HttpResponse("OK")
        final_response = self.middleware.process_response(request, response)

        self.assertEqual(final_response['X-Request-ID'], request.request_id)
        self.assertIn('no-store', final_response['Cache-Control'])
        self.assertEqual(final_response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(final_response['X-Frame-Options'], 'DENY')


class FoundationViewsAndExceptionTestCase(TestCase):
    """Kiểm tra CSRF failure, DRF exception handler, Handler 404, và Health views."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_csrf_failure_returns_json_envelope(self):
        """Xác minh csrf_failure trả JSON HTTP 403 với envelope chuẩn."""
        request = self.factory.post('/api/test')
        request.request_id = str(uuid.uuid4())

        response = csrf_failure(request, reason="CSRF token missing")
        self.assertEqual(response.status_code, 403)

        data = json.loads(response.content.decode('utf-8'))
        self.assertIn('error', data)
        self.assertEqual(data['error']['code'], ErrorCode.CSRF_FAILED)
        self.assertEqual(data['error']['request_id'], request.request_id)

    def test_drf_parse_error_mapped_to_validation_error(self):
        """Xác minh lỗi cú pháp JSON ParseError được ánh xạ sang VALIDATION_ERROR 400."""
        request = self.factory.post('/api/test')
        request.request_id = str(uuid.uuid4())
        exc = ParseError("JSON parse error - Expecting value")

        response = custom_exception_handler(exc, {'request': request})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['error']['code'], ErrorCode.VALIDATION_ERROR)
        self.assertIn("JSON", response.data['error']['message'])

    def test_drf_csrf_permission_denied_mapped_to_csrf_failed(self):
        """Xác minh SessionAuthentication từ chối do CSRF được ánh xạ sang CSRF_FAILED 403."""
        request = self.factory.post('/api/test')
        request.request_id = str(uuid.uuid4())
        exc = PermissionDenied("CSRF Failed: CSRF token missing or incorrect.")

        response = custom_exception_handler(exc, {'request': request})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['error']['code'], ErrorCode.CSRF_FAILED)

    def test_custom_handler404_returns_json_envelope(self):
        """Xác minh handler404 toàn cục trả JSON envelope chuẩn NOT_FOUND."""
        request = self.factory.get('/api/nonexistent-url')
        request.request_id = str(uuid.uuid4())

        response = handler404(request)
        self.assertEqual(response.status_code, 404)
        data = json.loads(response.content.decode('utf-8'))
        self.assertEqual(data['error']['code'], ErrorCode.NOT_FOUND)

    def test_health_live_returns_up(self):
        """Xác minh /health/live trả HTTP 200 UP."""
        request = self.factory.get('/health/live')
        response = health_live(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content.decode('utf-8'))
        self.assertEqual(data['status'], 'UP')

    def test_health_ready_db_connected(self):
        """Xác minh /health/ready trả HTTP 200 READY khi DB kết nối thành công."""
        request = self.factory.get('/health/ready')
        response = health_ready(request)

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content.decode('utf-8'))
        self.assertEqual(data['status'], 'READY')
        self.assertEqual(data['database'], 'CONNECTED')

    def test_health_ready_db_disconnected_returns_standard_error_envelope(self):
        """Xác minh /health/ready trả HTTP 503 với envelope lỗi SERVICE_UNAVAILABLE chuẩn khi DB lỗi."""
        request = self.factory.get('/health/ready')
        request.request_id = str(uuid.uuid4())

        with patch('django.db.connection.cursor', side_effect=Exception("DB Connection Timeout")):
            response = health_ready(request)

        self.assertEqual(response.status_code, 503)
        data = json.loads(response.content.decode('utf-8'))
        self.assertIn('error', data)
        self.assertEqual(data['error']['code'], ErrorCode.SERVICE_UNAVAILABLE)
