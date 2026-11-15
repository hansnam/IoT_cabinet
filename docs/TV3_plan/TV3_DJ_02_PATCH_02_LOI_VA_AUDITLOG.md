# TV3-DJ-02 — Hạ Tầng Lỗi và Một Nguồn AuditLog (Patch 02 — Bản Chuẩn Hóa Chốt)

Ngày cập nhật: **05/11/2026**  
Mã chặng: **TV3-DJ-02**  
Thuộc kế hoạch: [TV3_KE_HOACH_DJANGO_CHO_GEMINI.md](TV3_KE_HOACH_DJANGO_CHO_GEMINI.md)  
Người thực hiện: **TV3**  
Trạng thái: **READY_FOR_COPY (Đã nghiệm thu cấu trúc và sửa toàn diện các luồng)**  

---

## 1. Mục tiêu, Prerequisites và Quy tắc hợp đồng kiến trúc

### 1.1 Mục tiêu chặng 02
1. **Chuẩn hóa hạ tầng phản hồi lỗi (Envelope & Error Codes)**:
   - Toàn bộ lỗi API đều tuân thủ cấu trúc thống nhất: `{"error": {"code": "...", "message": "...", "request_id": "..."}}`.
   - Phân biệt rõ ràng các mã lỗi HTTP và Error Code: `VALIDATION_ERROR` (400), `NOT_AUTHENTICATED` (401), `CSRF_FAILED` (403), `FORBIDDEN` (403), `NOT_FOUND` (404), `METHOD_NOT_ALLOWED` (405), `RATE_LIMITED` (429), `INTERNAL_SERVER_ERROR` (500), `SERVICE_UNAVAILABLE` (503).
   - Tuyệt đối không để lộ chuỗi exception thô, stack trace hay database error ra ngoài thông báo lỗi.
2. **Xử lý thất bại CSRF chuyên dụng cho API (`csrf_failure`)**:
   - Khi request vi phạm CSRF (cả từ Django middleware và từ SessionAuthentication của DRF), trả về JSON với mã lỗi `CSRF_FAILED` (HTTP 403), thay vì hiển thị trang HTML 403 mặc định.
3. **Quản lý Request ID & Security Headers (`RequestIDAndSecurityHeadersMiddleware`)**:
   - Gắn `request.request_id` (chuẩn UUID v4) cho mọi request để phục vụ đối chiếu dấu vết (correlation trace).
   - Thêm các HTTP headers bảo vệ: `X-Request-ID`, `Cache-Control: no-store, no-cache, must-revalidate`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`.
4. **Health Check Endpoints (`/health/live` & `/health/ready`)**:
   - `/health/live`: Liveness probe (trả về HTTP 200 `UP` nếu web server đang chạy).
   - `/health/ready`: Readiness probe (thực hiện truy vấn SQL nhẹ `SELECT 1` kiểm tra kết nối PostgreSQL thực; trả về 200 nếu kết nối tốt, 503 nếu mất kết nối theo đúng envelope lỗi chuẩn `SERVICE_UNAVAILABLE`; không giả mạo trạng thái của MQTT worker khi worker chưa chạy).
5. **Một nguồn `AuditLog` duy nhất cho toàn hệ thống**:
   - Sáp nhập `audit_logs` và `user_activity_logs` thành một bảng duy nhất theo V1.4: lưu trữ `actor`, `function_key`, `action`, `outcome` (`SUCCESS`, `ACCEPTED`, `DENIED`, `FAILED`), `summary`, `request_id`, `correlation_id`, `metadata`.

### 1.2 Hợp đồng bảo vệ Secret và Transaction (Contract & Defense-in-Depth)
1. **Hợp đồng Transaction Atomic (Rule 9)**:
   - `AuditService.record_event()` là pure writer: Chỉ thực hiện `log.save()`, không can thiệp vào transaction của caller.
   - Thao tác nghiệp vụ thành công: Ghi `outcome="SUCCESS"` bên trong khối `with transaction.atomic():`. Nếu nghiệp vụ bị lỗi rollback thì bản ghi SUCCESS cũng tự động bị hủy, chống thành công giả.
   - Thao tác nghiệp vụ thất bại: Caller bắt exception, **thoát ra khỏi khối transaction bị lỗi**, sau đó mới gọi `record_event(..., outcome="FAILED")` để lưu vết lỗi thành công xuống DB.
2. **Hợp đồng bảo vệ Secret (Không dựa vào regex đơn lẻ)**:
   - **Quy tắc Caller:** Trường `summary` bắt buộc sử dụng **câu mẫu cố định do server tạo** (ví dụ: `"Đăng nhập thành công"`, `"Khóa tài khoản người dùng"`, `"Gửi lệnh điều khiển quạt"`). Cấm tuyệt đối truyền raw HTTP request, raw headers (`Authorization`, `Cookie`), hay raw exception string vào `summary`.
   - **Quy tắc Metadata:** Các trường `reason`, `error` chỉ được chứa **mã phân loại lỗi đã định nghĩa** (ví dụ: `INVALID_CREDENTIALS`, `ACCOUNT_LOCKED`, `CONNECTION_TIMEOUT`), tuyệt đối không truyền raw tracebacks.
   - **Lưới bảo vệ bổ trợ (Safety Net):** Mở rộng bộ lọc `SENSITIVE_STRING_MARKERS` chặn DSN (`postgresql://`, `mysql://`, `sqlite://`), tokens (`bearer `, `basic `), và các cặp khóa (`sessionid=`, `csrftoken=`, `token=`, `password=`, `authorization:`).

---

## 2. Danh sách file theo thứ tự thao tác

| STT | Đường dẫn file | Trạng thái | Lý do / Trách nhiệm |
|:---|:---|:---:|:---|
| 1 | `backend/apps/core/errors.py` | **NEW** | Envelope phản hồi lỗi, DRF exception handler, CSRF failure JSON, Django error handlers (400, 403, 404, 500) |
| 2 | `backend/apps/core/middleware.py` | **NEW** | Middleware quản lý X-Request-ID và gắn security headers |
| 3 | `backend/apps/core/views.py` | **NEW** | Health check views (`/health/live`, `/health/ready` theo envelope chuẩn) |
| 4 | `backend/apps/audit/__init__.py` | **NEW** | Khai báo package app `audit` |
| 5 | `backend/apps/audit/apps.py` | **NEW** | Khai báo `AuditConfig` (app label: `audit`) |
| 6 | `backend/apps/audit/models.py` | **NEW** | Model `AuditLog` duy nhất lưu trữ kiểm toán & nhật ký |
| 7 | `backend/apps/audit/services.py` | **NEW** | `AuditService`: Writer chỉ insert, hợp đồng server summary, chặn DSN/Bearer/Basic/Cookie, `math.isfinite` |
| 8 | `backend/config/settings.py` | **MODIFY** | Đăng ký app `audit`, middleware core, `CSRF_FAILURE_VIEW`, DRF exception handler |
| 9 | `backend/config/urls.py` | **MODIFY** | Mount `/health/live`, `/health/ready`, đăng ký `handler400`, `handler403`, `handler404`, `handler500` |
| 10 | `backend/apps/core/tests/test_foundation.py` | **NEW** | 11 test cases kiểm tra middleware, headers, CSRF, DRF ParseError, Django 404, health check |
| 11 | `backend/apps/audit/tests/__init__.py` | **NEW** | Khai báo package test của `audit` |
| 12 | `backend/apps/audit/tests/test_audit.py` | **NEW** | 15 test cases kiểm tra allowlist, redaction, Basic/Bearer/Cookie markers, NaN/Inf, TransactionTestCase atomic rollback |

---

## 3. Luồng hoạt động & Kiến thức Django cần hiểu

### 3.1 Quy chuẩn Transaction Atomic và Ghi AuditLog Lỗi (Rule 9)
```text
Thao tác nghiệp vụ có Transaction
  │
  ▼
try:
  with transaction.atomic():
      1. Thực hiện sửa đổi dữ liệu (Update DB)
      2. AuditService.record_event(..., outcome="SUCCESS")  <-- Writer chỉ insert, nằm trong transaction
      3. Phát sinh lỗi -> Exception được ném ra ngoài!
      (Toàn bộ thao tác và bản ghi log SUCCESS bị ROLLBACK sạch sẽ)
except Exception as exc:
  # ĐÃ THOÁT RA KHỎI KHỐI TRANSACTION BỊ LỖI
  AuditService.record_event(..., outcome="FAILED", error_code="MÃ_LỖI_CHUẨN")
  # Ghi log THẤT BẠI thành công xuống DB trong một transaction độc lập!
```

### 3.2 Luồng xử lý phân biệt lỗi CSRF và Validation trong DRF
```text
Request gửi tới API DRF
  │
  ├── Lỗi 1: JSON gửi lên bị sai cú pháp (Malformed JSON)
  │     └── DRF ném ParseError (HTTP 400)
  │     └── custom_exception_handler phát hiện ParseError -> Trả VALIDATION_ERROR (HTTP 400)
  │
  ├── Lỗi 2: SessionAuthentication kiểm tra CSRF thất bại
  │     └── DRF ném PermissionDenied("CSRF Failed: ...")
  │     └── custom_exception_handler phát hiện chữ "CSRF" -> Trả CSRF_FAILED (HTTP 403)
  │
  └── Lỗi 3: Không có quyền truy cập thông thường
        └── DRF ném PermissionDenied bình thường
        └── custom_exception_handler -> Trả FORBIDDEN (HTTP 403)
```

---

## 4. Toàn bộ nội dung từng file

### File 1: `backend/apps/core/errors.py`
**Vị trí:** `backend/apps/core/errors.py`  

```python
"""
Core Errors & Envelope Module
Chịu trách nhiệm định dạng chuẩn envelope phản hồi lỗi API, xử lý ngoại lệ DRF,
view xử lý thất bại CSRF, và các handler lỗi 400/403/404/500 của Django cho toàn hệ thống.
"""
from typing import Any
from django.http import JsonResponse, HttpRequest, HttpResponse
from rest_framework.views import exception_handler
from rest_framework.exceptions import (
    APIException, NotAuthenticated, AuthenticationFailed,
    PermissionDenied, Throttled, ValidationError, ParseError
)

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

def make_error_envelope(code: str, message: str, request_id: str | None = None) -> dict[str, Any]:
    """Tạo cấu trúc từ điển envelope lỗi tiêu chuẩn của dự án."""
    return {
        'error': {
            'code': code,
            'message': message,
            'request_id': request_id,
        }
    }

def make_error_response(code: str, message: str, status_code: int, request_id: str | None = None) -> JsonResponse:
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
    request_id = getattr(request, 'request_id', None) if request else None

    if response is not None:
        status_code = response.status_code
        code = ErrorCode.INTERNAL_SERVER_ERROR
        message = 'Yêu cầu không hợp lệ.'

        # 1. Lỗi phân tích cú pháp JSON (ParseError) -> VALIDATION_ERROR 400
        if isinstance(exc, ParseError):
            code = ErrorCode.VALIDATION_ERROR
            message = 'Dữ liệu JSON gửi lên không đúng định dạng.'

        # 2. Lỗi chưa xác thực -> NOT_AUTHENTICATED 401
        elif isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
            code = ErrorCode.NOT_AUTHENTICATED
            message = 'Yêu cầu xác thực tài khoản để thực hiện chức năng này.'

        # 3. Lỗi quyền truy cập hoặc thất bại CSRF trong SessionAuthentication của DRF
        elif isinstance(exc, PermissionDenied):
            exc_str = str(exc)
            if 'CSRF' in exc_str:
                code = ErrorCode.CSRF_FAILED
                message = 'Yêu cầu bị từ chối do vi phạm kiểm tra an toàn CSRF.'
            else:
                code = ErrorCode.FORBIDDEN
                message = 'Bạn không có quyền thực hiện thao tác này.'

        # 4. Lỗi giới hạn tần suất (Rate Limit) -> RATE_LIMITED 429
        elif isinstance(exc, Throttled):
            code = ErrorCode.RATE_LIMITED
            wait_time = getattr(exc, 'wait', None)
            message = f'Quá nhiều yêu cầu. Vui lòng thử lại sau {wait_time} giây.' if wait_time else 'Quá nhiều yêu cầu.'

        # 5. Lỗi kiểm tra dữ liệu serializer -> VALIDATION_ERROR 400
        elif isinstance(exc, ValidationError):
            code = ErrorCode.VALIDATION_ERROR
            message = 'Dữ liệu đầu vào không hợp lệ.'

        # 6. Các mã trạng thái HTTP chuẩn khác
        elif status_code == 404:
            code = ErrorCode.NOT_FOUND
            message = 'Tài nguyên được yêu cầu không tồn tại.'
        elif status_code == 405:
            code = ErrorCode.METHOD_NOT_ALLOWED
            message = 'Phương thức HTTP không được hỗ trợ.'

        response.data = make_error_envelope(code=code, message=message, request_id=request_id)
        return response

    # Trường hợp ngoại lệ hệ thống ngoài dự kiến (500)
    return make_error_response(
        code=ErrorCode.INTERNAL_SERVER_ERROR,
        message='Đã xảy ra lỗi nội bộ máy chủ. Vui lòng liên hệ quản trị viên.',
        status_code=500,
        request_id=request_id
    )

# ------------------------------------------------------------------------------
# Django Core Handlers (Áp dụng cho toàn bộ đường dẫn ngoài DRF, ví dụ URL 404)
# ------------------------------------------------------------------------------
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
```

---

### File 2: `backend/apps/core/middleware.py`
**Vị trí:** `backend/apps/core/middleware.py`  

```python
"""
Core Middleware Module
Quản lý vòng đời Request ID và đính kèm các Header bảo mật bắt buộc theo V1.4.
"""
import uuid
from django.utils.deprecation import MiddlewareMixin
from django.http import HttpRequest, HttpResponse

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

        request.request_id = valid_uuid

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
```

---

### File 3: `backend/apps/core/views.py`
**Vị trí:** `backend/apps/core/views.py`  

```python
"""
Core Views Module
Cung cấp các endpoint kiểm tra trạng thái hoạt động của hệ thống (Liveness & Readiness).
Thống nhất cấu trúc envelope lỗi cho mọi phản hồi non-2xx.
"""
from django.http import JsonResponse, HttpRequest
from django.db import connection
from apps.core.errors import make_error_response, ErrorCode

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
    except Exception:
        return make_error_response(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message='Cơ sở dữ liệu chưa sẵn sàng phục vụ yêu cầu.',
            status_code=503,
            request_id=request_id
        )
```

---

### File 4: `backend/apps/audit/__init__.py`
**Vị trí:** `backend/apps/audit/__init__.py`  
*(Nội dung: file rỗng)*

---

### File 5: `backend/apps/audit/apps.py`
**Vị trí:** `backend/apps/audit/apps.py`  

```python
"""
AppConfig cho audit application.
Quản lý cơ sở dữ liệu kiểm toán và nhật ký hệ thống.
"""
from django.apps import AppConfig

class AuditConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.audit'
    label = 'audit'
    verbose_name = 'Kiểm toán & Nhật ký hệ thống'
```

---

### File 6: `backend/apps/audit/models.py`
**Vị trí:** `backend/apps/audit/models.py`  

```python
"""
Audit Log Model Module
Hiện thực hóa bảng duy nhất `audit_logs` lưu trữ toàn bộ sự kiện kiểm toán bảo mật
và nhật ký làm việc theo đặc tả V1.4.
"""
import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone

class AuditLog(models.Model):
    """
    Bảng duy nhất lưu trữ dấu vết kiểm toán bảo mật và nhật ký chức năng.
    """
    class ActorType(models.TextChoices):
        USER = 'USER', 'Người dùng'
        SYSTEM = 'SYSTEM', 'Hệ thống tự động'
        SERVICE = 'SERVICE', 'Dịch vụ nền / Worker'

    class Outcome(models.TextChoices):
        SUCCESS = 'SUCCESS', 'Thành công'
        ACCEPTED = 'ACCEPTED', 'Đã tiếp nhận (Bất đồng bộ 202)'
        DENIED = 'DENIED', 'Bị từ chối quyền'
        FAILED = 'FAILED', 'Thất bại do lỗi hệ thống / dữ liệu'

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text='Khóa chính UUID v4 của bản ghi kiểm toán.'
    )

    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name='audit_logs',
        help_text='Tài khoản người dùng thực hiện (null nếu do hệ thống/worker).'
    )
    actor_session_id = models.UUIDField(
        null=True,
        blank=True,
        help_text='Session ID (sid) của phiên làm việc phát sinh thao tác.'
    )
    actor_type = models.CharField(
        max_length=20,
        choices=ActorType.choices,
        default=ActorType.USER,
        help_text='Phân loại tác nhân: USER, SYSTEM hoặc SERVICE.'
    )
    actor_name_snapshot = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text='Snapshot tên người dùng tại thời điểm xảy ra sự kiện.'
    )

    function_key = models.CharField(
        max_length=80,
        db_index=True,
        help_text='Mã phân loại chức năng (ví dụ: AUTH_LOGIN, USER_CREATE, DEVICE_CONTROL).'
    )
    action = models.CharField(
        max_length=32,
        db_index=True,
        help_text='Thao tác thực hiện (ví dụ: LOGIN, LOGOUT, CREATE, UPDATE, DELETE, VIEW, CONTROL).'
    )

    target_type = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text='Loại đối tượng đích (ví dụ: USER, DEVICE, SESSION, CONFIG).'
    )
    target_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text='Định danh đối tượng đích bị tác động.'
    )
    target_name_snapshot = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text='Snapshot tên đối tượng đích tại thời điểm thực hiện.'
    )
    device_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
        help_text='Snapshot mã thiết bị (độc lập với model Device để không phụ thuộc TV2).'
    )

    outcome = models.CharField(
        max_length=20,
        choices=Outcome.choices,
        db_index=True,
        help_text='Kết quả thực hiện: SUCCESS, ACCEPTED, DENIED hoặc FAILED.'
    )
    summary = models.TextField(
        help_text='Tóm tắt nội dung hành động theo câu mẫu phía server.'
    )
    error_code = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text='Mã lỗi nếu kết quả là DENIED hoặc FAILED.'
    )

    request_id = models.UUIDField(
        db_index=True,
        help_text='Request ID gắn liền với HTTP request gửi lên.'
    )
    correlation_id = models.UUIDField(
        null=True,
        blank=True,
        help_text='Correlation ID xâu chuỗi nhiều tác vụ liên quan.'
    )
    occurred_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        help_text='Thời điểm ghi nhận sự kiện.'
    )
    duration_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='Thời gian thực thi thao tác tính bằng mili-giây (ms).'
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text='Địa chỉ IP của client gửi yêu cầu.'
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text='Dữ liệu chi tiết bổ sung (đã được lọc allowlist và bảo đảm số hữu hạn).'
    )

    class Meta:
        db_table = 'audit_logs'
        verbose_name = 'Nhật ký kiểm toán'
        verbose_name_plural = 'Danh sách nhật ký kiểm toán'
        ordering = ['-occurred_at']
        indexes = [
            models.Index(fields=['actor_user', '-occurred_at'], name='ix_audit_actor_time'),
            models.Index(fields=['function_key', '-occurred_at'], name='ix_audit_func_time'),
            models.Index(fields=['action', '-occurred_at'], name='ix_audit_act_time'),
            models.Index(fields=['device_id', '-occurred_at'], name='ix_audit_dev_time'),
            models.Index(fields=['outcome', '-occurred_at'], name='ix_audit_out_time'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(outcome__in=['SUCCESS', 'ACCEPTED', 'DENIED', 'FAILED']),
                name='ck_audit_logs_outcome_valid'
            ),
            models.CheckConstraint(
                condition=models.Q(actor_type__in=['USER', 'SYSTEM', 'SERVICE']),
                name='ck_audit_logs_actor_type_valid'
            ),
        ]

    def __str__(self) -> str:
        return f'[{self.occurred_at}] [{self.outcome}] {self.function_key}:{self.action} by {self.actor_type}'
```

---

### File 7: `backend/apps/audit/services.py`
**Vị trí:** `backend/apps/audit/services.py`  

```python
"""
Audit Service Module
Cung cấp dịch vụ ghi nhận kiểm toán và nhật ký tập trung.

HỢP ĐỒNG BẢO VỆ DỮ LIỆU & TRANSACTION (Rule 9 & Security Contract):
1. summary BẮT BUỘC sử dụng câu mẫu cố định do server tạo (server-rendered templates).
   Tuyệt đối cấm caller truyền nguyên văn HTTP request, raw headers hoặc exception stacktrace.
2. reason / error trong metadata CHỈ được chứa các mã lỗi chuẩn (error codes), không chứa raw text.
3. Writer thuần túy (Pure Writer): Chỉ thực hiện log_entry.save(), không tự ý commit transaction.
   Caller quản lý transaction: Khi gặp lỗi, caller thoát khỏi khối atomic rồi mới ghi log FAILED.
4. Lưới lọc Defense-in-depth: Chặn DSN, Bearer/Basic auth, sessionid/csrftoken, password/secret markers.
5. Kiểm tra số hữu hạn qua math.isfinite(), cấm NaN/Infinity.
"""
import json
import math
import uuid
from typing import Any
from django.utils import timezone
from apps.audit.models import AuditLog

# Các từ khóa nhạy cảm TUYỆT ĐỐI CẤM xuất hiện trong metadata
FORBIDDEN_METADATA_KEYS = frozenset({
    'password', 'password_hash', 'token', 'access_token', 'refresh_token',
    'secret', 'secret_key', 'cookie', 'authorization', 'credential',
    'mqtt_password', 'dsn', 'database_url', 'raw_body', 'body',
    'sessionid', 'csrftoken', 'note'
})

# Danh sách allowlist các khóa metadata có cấu trúc được chấp thuận
ALLOWED_METADATA_KEYS = frozenset({
    'username', 'attempted_username', 'target_username', 'role', 'role_code',
    'old_role_code', 'device_id', 'operation_id', 'command_id', 'command_type',
    'session_id', 'target_session_id', 'target_user_id', 'failure_count',
    'reason', 'error', 'fields', 'ip_address'
})

# Mẫu chuỗi nhận dạng nhạy cảm bị chặn ở tầng lưới lọc an toàn
SENSITIVE_STRING_MARKERS = (
    'postgresql://', 'postgres://', 'mysql://', 'sqlite://',
    'bearer ', 'basic ', 'sessionid=', 'csrftoken=', 'token=',
    'password=', 'password:', 'secret=', 'secret:', 'authorization:'
)

# Giới hạn kích thước payload metadata tối đa 16 KB
MAX_METADATA_BYTES = 16384

def check_sensitive_strings(val: str, field_name: str) -> None:
    """
    Kiểm tra chuỗi không chứa DSN, Bearer/Basic auth, session cookie hoặc secret markers.
    Đóng vai trò lưới bảo vệ bổ trợ (Defense-in-depth), không thay thế hợp đồng template của caller.
    """
    val_lower = val.lower()
    for marker in SENSITIVE_STRING_MARKERS:
        if marker in val_lower:
            raise ValueError(f"Phát hiện dữ liệu nhạy cảm ('{marker}') trong trường '{field_name}'.")

def sanitize_metadata(raw_metadata: dict[str, Any] | None) -> dict[str, Any]:
    """
    Kiểm tra và chuẩn hóa metadata:
    - Loại bỏ khóa không nằm trong allowlist, cấm khóa nhạy cảm.
    - Chặn cấu trúc nested dictionary/list (ngoại trừ danh sách chuỗi phẳng 'fields').
    - Bảo đảm số hữu hạn qua math.isfinite() (chặn NaN và Infinity).
    - Giới hạn độ dài và dung lượng JSON <= 16KB.
    """
    if not raw_metadata:
        return {}

    if not isinstance(raw_metadata, dict):
        raise ValueError("Metadata bắt buộc phải là một từ điển (dict).")

    clean: dict[str, Any] = {}

    for raw_k, val in raw_metadata.items():
        k = str(raw_k).strip()
        k_lower = k.lower()

        # 1. Chặn khóa bí mật tuyệt đối
        if k_lower in FORBIDDEN_METADATA_KEYS:
            raise ValueError(f"Khóa nhạy cảm bị cấm trong audit metadata: '{k}'.")

        # 2. Allowlist kiểm tra
        if k not in ALLOWED_METADATA_KEYS:
            raise ValueError(f"Khóa không được phép trong audit metadata: '{k}'.")

        # 3. Chặn nested dictionary
        if isinstance(val, dict):
            raise ValueError(f"Metadata không được chứa dictionary lồng nhau tại khóa '{k}'.")

        # 4. Xử lý danh sách: chỉ cho phép trường 'fields' chứa tối đa 20 tên cột
        if isinstance(val, (list, tuple)):
            if k != 'fields':
                raise ValueError(f"Không cho phép danh sách lồng nhau tại khóa '{k}'.")
            if len(val) > 20:
                raise ValueError("Trường 'fields' chỉ được chứa tối đa 20 phần tử.")
            clean_list: list[str] = []
            for item in val:
                if not isinstance(item, str) or len(item) > 64:
                    raise ValueError("Các phần tử trong 'fields' phải là chuỗi không quá 64 ký tự.")
                check_sensitive_strings(item, k)
                clean_list.append(item)
            clean[k] = clean_list
            continue

        # 5. Xử lý kiểu boolean (phải kiểm tra trước int vì bool là subclass của int trong Python)
        if isinstance(val, bool):
            clean[k] = val
            continue

        # 6. Xử lý số nguyên và số thực hữu hạn (chặn NaN / Infinity)
        if isinstance(val, (int, float)):
            if isinstance(val, float) and not math.isfinite(val):
                raise ValueError(f"Giá trị số tại khóa '{k}' phải là số hữu hạn (không chấp nhận NaN hoặc Infinity).")
            clean[k] = val
            continue

        # 7. Xử lý chuỗi
        if isinstance(val, str):
            if len(val) > 512:
                raise ValueError(f"Giá trị chuỗi tại khóa '{k}' vượt quá 512 ký tự cho phép.")
            check_sensitive_strings(val, k)
            clean[k] = val
            continue

        if val is None:
            clean[k] = None
        else:
            raise ValueError(f"Kiểu dữ liệu không được hỗ trợ tại khóa '{k}': {type(val)}.")

    # Kiểm tra kích thước và khả năng serialize JSON chuẩn (allow_nan=False)
    try:
        encoded_bytes = len(json.dumps(clean, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    except ValueError as exc:
        raise ValueError(f"Metadata chứa giá trị không thể chuyển đổi JSON: {exc}") from exc

    if encoded_bytes > MAX_METADATA_BYTES:
        raise ValueError(f"Dung lượng metadata vượt quá giới hạn 16KB (hiện tại: {encoded_bytes} bytes).")

    return clean

class AuditService:
    """
    Service ghi nhận sự kiện kiểm toán và nhật ký tập trung.
    Writer chỉ insert (không can thiệp transaction của caller).
    """

    @staticmethod
    def record_event(
        *,
        function_key: str,
        action: str,
        outcome: str,
        summary: str,
        request_id: uuid.UUID | str,
        actor_user=None,
        actor_session_id: uuid.UUID | str | None = None,
        actor_type: str = AuditLog.ActorType.USER,
        actor_name_snapshot: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        target_name_snapshot: str | None = None,
        device_id: str | None = None,
        correlation_id: uuid.UUID | str | None = None,
        error_code: str | None = None,
        duration_ms: int | None = None,
        ip_address: str | None = None,
        metadata: dict[str, Any] | None = None,
        is_async: bool = False
    ) -> AuditLog:
        """
        Ghi nhận một bản ghi AuditLog vào DB:
        - summary BẮT BUỘC sử dụng câu mẫu cố định từ server, cấm truyền raw payload/headers.
        - error_code CHỈ chứa mã phân loại (ví dụ: INVALID_CREDENTIALS, TIMEOUT), không chứa raw error.
        - is_async=True: Thao tác 202 cấm ghi outcome SUCCESS; bắt buộc dùng ACCEPTED.
        - Pure writer: Thực hiện log_entry.save(), caller quản lý transaction boundary.
        """
        if not request_id:
            raise ValueError("request_id là thông tin bắt buộc để ghi nhận audit log.")

        req_uuid = uuid.UUID(str(request_id)) if not isinstance(request_id, uuid.UUID) else request_id
        corr_uuid = uuid.UUID(str(correlation_id)) if correlation_id and not isinstance(correlation_id, uuid.UUID) else correlation_id
        sess_uuid = uuid.UUID(str(actor_session_id)) if actor_session_id and not isinstance(actor_session_id, uuid.UUID) else actor_session_id

        # 1. Kiểm tra summary: bắt buộc và kiểm tra lưới lọc an toàn
        if not summary or not isinstance(summary, str) or not summary.strip():
            raise ValueError("summary là thông tin bắt buộc.")
        cleaned_summary = summary.strip()
        check_sensitive_strings(cleaned_summary, 'summary')

        # 2. Kiểm tra Async 202 contract: Thao tác 202 cấm ghi outcome SUCCESS
        outcome_clean = str(outcome).strip().upper()
        if is_async and outcome_clean == AuditLog.Outcome.SUCCESS:
            raise ValueError("Thao tác bất đồng bộ (202) không được phép ghi nhận outcome là SUCCESS; phải dùng ACCEPTED.")

        if outcome_clean not in AuditLog.Outcome.values:
            raise ValueError(f"outcome '{outcome_clean}' không hợp lệ. Phải thuộc {AuditLog.Outcome.values}.")

        # 3. Spoofing Guard: Kiểm tra actor
        if actor_type == AuditLog.ActorType.USER and actor_user is None:
            raise ValueError("actor_type là USER bắt buộc phải cung cấp đối tượng actor_user.")

        if actor_type == AuditLog.ActorType.SYSTEM and actor_user is not None:
            raise ValueError("actor_type là SYSTEM không được phép đính kèm actor_user người dùng.")

        final_actor_name = actor_name_snapshot
        if not final_actor_name and actor_user:
            final_actor_name = getattr(actor_user, 'full_name', str(actor_user))

        # 4. Chuẩn hóa metadata và kiểm tra số hữu hạn
        clean_meta = sanitize_metadata(metadata)

        # 5. Khởi tạo đối tượng AuditLog
        log_entry = AuditLog(
            actor_user=actor_user,
            actor_session_id=sess_uuid,
            actor_type=actor_type,
            actor_name_snapshot=final_actor_name,
            function_key=str(function_key).strip().upper(),
            action=str(action).strip().upper(),
            target_type=target_type.strip().upper() if target_type else None,
            target_id=str(target_id).strip() if target_id else None,
            target_name_snapshot=target_name_snapshot.strip() if target_name_snapshot else None,
            device_id=str(device_id).strip() if device_id else None,
            outcome=outcome_clean,
            summary=cleaned_summary,
            error_code=str(error_code).strip() if error_code else None,
            request_id=req_uuid,
            correlation_id=corr_uuid,
            duration_ms=duration_ms,
            ip_address=ip_address,
            metadata=clean_meta,
            occurred_at=timezone.now()
        )

        # 6. Ghi xuống DB (pure insert)
        log_entry.save()
        return log_entry
```

---

### File 8: `backend/config/settings.py` (Cập nhật)
**Vị trí:** `backend/config/settings.py`  

```python
"""
Django settings for IoT Cabinet Backend Project.
Tuân thủ đặc tả kỹ thuật V1.4 và kế hoạch TV3.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

backend_env = BASE_DIR / '.env'
if backend_env.exists():
    load_dotenv(dotenv_path=backend_env)

SECRET_KEY = os.getenv(
    'DJANGO_SECRET_KEY',
    'django-insecure-fallback-dev-secret-key-3b4f9a0c1e2d8e7a6b5c4d3e2f1a0b'
)

DEBUG = os.getenv('DJANGO_DEBUG', 'True').lower() in ('true', '1', 'yes')

allowed_hosts_env = os.getenv('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost')
ALLOWED_HOSTS = [h.strip() for h in allowed_hosts_env.split(',') if h.strip()]

# ------------------------------------------------------------------------------
# Khai báo các ứng dụng (Apps)
# ------------------------------------------------------------------------------
INSTALLED_APPS = [
    # Django Built-in Apps
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party Apps
    'rest_framework',

    # Dự án IoT Cabinet Apps
    'apps.core.apps.CoreConfig',
    'apps.accounts.apps.AccountsConfig',
    'apps.audit.apps.AuditConfig',
]

# ------------------------------------------------------------------------------
# Middleware Pipeline
# ------------------------------------------------------------------------------
MIDDLEWARE = [
    'apps.core.middleware.RequestIDAndSecurityHeadersMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

CSRF_FAILURE_VIEW = 'apps.core.errors.csrf_failure'

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ------------------------------------------------------------------------------
# Cấu hình Cơ sở dữ liệu PostgreSQL (iot_cabinet_django)
# ------------------------------------------------------------------------------
DB_NAME = os.getenv('DB_NAME', 'iot_cabinet_django')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres123')
DB_HOST = os.getenv('DB_HOST', '127.0.0.1')
DB_PORT = os.getenv('DB_PORT', '5432')
TEST_DB_NAME = os.getenv('TEST_DB_NAME', 'iot_cabinet_django_test')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': DB_NAME,
        'USER': DB_USER,
        'PASSWORD': DB_PASSWORD,
        'HOST': DB_HOST,
        'PORT': DB_PORT,
        'TEST': {
            'NAME': TEST_DB_NAME,
        },
    }
}

# ------------------------------------------------------------------------------
# Custom User Model & Password Hashers (Argon2id)
# ------------------------------------------------------------------------------
AUTH_USER_MODEL = 'accounts.User'

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
]

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'apps.accounts.security.password_validators.PasswordLengthValidator',
        'OPTIONS': {
            'min_length': 12,
            'max_length': 128,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
]

# ------------------------------------------------------------------------------
# Thời gian & Quốc tế hóa
# ------------------------------------------------------------------------------
LANGUAGE_CODE = 'vi'
TIME_ZONE = 'Asia/Ho_Chi_Minh'
USE_I18N = True
USE_TZ = True

# ------------------------------------------------------------------------------
# Cấu hình Session theo V1.4
# ------------------------------------------------------------------------------
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_AGE = 900
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = False

AUTH_LOG_HMAC_KEY = os.getenv(
    'AUTH_LOG_HMAC_KEY',
    'ecbf305c5a9a2b1fc6c6a1ad9a7f104eebf620c49b2cb17eebe10c08367cba2b'
)

# ------------------------------------------------------------------------------
# Django REST Framework Configuration
# ------------------------------------------------------------------------------
REST_FRAMEWORK = {
    'EXCEPTION_HANDLER': 'apps.core.errors.custom_exception_handler',
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
}

# ------------------------------------------------------------------------------
# Test Runner Guard
# ------------------------------------------------------------------------------
TEST_RUNNER = 'apps.core.test_runner.DatabaseGuardTestRunner'

STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
```

---

### File 9: `backend/config/urls.py` (Cập nhật)
**Vị trí:** `backend/config/urls.py`  

```python
"""
URL configuration for IoT Cabinet Project.
Gắn kết các handler lỗi 400, 403, 404, 500 chuẩn cho toàn bộ ứng dụng.
"""
from django.contrib import admin
from django.urls import path
from apps.core.views import health_live, health_ready

urlpatterns = [
    # Health checks
    path('health/live', health_live, name='health_live'),
    path('health/ready', health_ready, name='health_ready'),

    # Django Admin site
    path('admin/', admin.site.urls),
]

# Khai báo các handler lỗi toàn cục trả về JSON envelope
handler400 = 'apps.core.errors.handler400'
handler403 = 'apps.core.errors.handler403'
handler404 = 'apps.core.errors.handler404'
handler500 = 'apps.core.errors.handler500'
```

---

### File 10: `backend/apps/core/tests/test_foundation.py`
**Vị trí:** `backend/apps/core/tests/test_foundation.py`  

```python
"""
Test module kiểm tra các thành phần hạ tầng core:
- RequestIDAndSecurityHeadersMiddleware (tạo UUID mới, nhận diện UUID hợp lệ, sinh lại nếu rác).
- Các Header bảo mật (X-Request-ID, Cache-Control no-store, nosniff, DENY).
- csrf_failure view trả về JSON chuẩn.
- DRF Exception Handler: ParseError -> VALIDATION_ERROR 400; CSRF PermissionDenied -> CSRF_FAILED 403.
- Django Core Handler 404 trả về JSON envelope chuẩn.
- Health check endpoints /health/live và /health/ready (chuẩn hóa lỗi 503).
"""
import uuid
import json
from unittest.mock import patch
from django.test import TestCase, RequestFactory
from django.http import HttpResponse
from rest_framework.exceptions import ParseError, PermissionDenied
from apps.core.middleware import RequestIDAndSecurityHeadersMiddleware
from apps.core.errors import (
    csrf_failure, custom_exception_handler, handler404, ErrorCode
)
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
```

---

### File 11: `backend/apps/audit/tests/__init__.py`
**Vị trí:** `backend/apps/audit/tests/__init__.py`  
*(Nội dung: file rỗng)*

---

### File 12: `backend/apps/audit/tests/test_audit.py`
**Vị trí:** `backend/apps/audit/tests/test_audit.py`  

```python
"""
Test module kiểm tra AuditLog và AuditService:
- Ghi nhận sự kiện thành công với metadata hợp lệ.
- Chặn đứng các từ khóa bí mật (password, token, secret, database_url, sessionid, csrftoken).
- Chặn đứng các chuỗi DSN kết nối, Bearer/Basic token, sessionid markers.
- Chặn số không hữu hạn (NaN, Infinity).
- Chặn nested dictionary trong metadata.
- Hợp đồng bất đồng bộ (Async 202 Contract): Cấm outcome SUCCESS cho tác vụ 202.
- Spoofing guard: USER thiếu actor_user hoặc SYSTEM truyền actor_user.
- TransactionTestCase: Chứng minh rollback trong atomic block hủy bỏ log SUCCESS,
  và caller ghi log FAILED ngoài atomic block được commit thành công xuống DB.
"""
import uuid
from django.test import TransactionTestCase, SimpleTestCase
from django.db import transaction
from django.contrib.auth import get_user_model
from apps.audit.models import AuditLog
from apps.audit.services import AuditService, sanitize_metadata

User = get_user_model()

class AuditSanitizerTestCase(SimpleTestCase):
    """Kiểm tra đơn vị logic hàm sanitize_metadata và kiểm tra secret không cần DB."""

    def test_valid_metadata_passes(self):
        """Metadata chứa các khóa hợp lệ được giữ nguyên."""
        meta = {
            'username': 'operator_demo',
            'device_id': 'CABINET-01',
            'failure_count': 3,
            'fields': ['full_name', 'status']
        }
        clean = sanitize_metadata(meta)
        self.assertEqual(clean['username'], 'operator_demo')
        self.assertEqual(clean['device_id'], 'CABINET-01')
        self.assertEqual(clean['failure_count'], 3)
        self.assertEqual(clean['fields'], ['full_name', 'status'])

    def test_forbidden_keys_raise_error(self):
        """Bất kỳ khóa nào thuộc FORBIDDEN_METADATA_KEYS đều phải bị ném ValueError."""
        for bad_key in ['password', 'secret', 'token', 'database_url', 'sessionid', 'csrftoken']:
            with self.assertRaises(ValueError) as ctx:
                sanitize_metadata({bad_key: 'sensitive_val'})
            self.assertIn("Khóa nhạy cảm bị cấm", str(ctx.exception))

    def test_sensitive_dsn_string_raises_error(self):
        """Giá trị chuỗi chứa connection string DSN phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'reason': 'Connecting to postgresql://user:pass@localhost:5432/db'})
        self.assertIn("Phát hiện dữ liệu nhạy cảm", str(ctx.exception))

    def test_bearer_and_basic_token_string_raises_error(self):
        """Giá trị chuỗi chứa Bearer hoặc Basic auth token phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'reason': 'Header Bearer eyJhbGciOiJIUzI1Ni...'})
        self.assertIn("Phát hiện dữ liệu nhạy cảm", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'reason': 'Header Basic dXNlcjpwYXNz'})
        self.assertIn("Phát hiện dữ liệu nhạy cảm", str(ctx.exception))

    def test_session_cookie_string_raises_error(self):
        """Giá trị chuỗi chứa sessionid= hoặc csrftoken= phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'reason': 'Cookie sessionid=demo-cookie-value'})
        self.assertIn("Phát hiện dữ liệu nhạy cảm", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'reason': 'Cookie csrftoken=demo-csrf-token'})
        self.assertIn("Phát hiện dữ liệu nhạy cảm", str(ctx.exception))

    def test_nan_and_infinity_raise_error(self):
        """Giá trị float không hữu hạn (NaN, Inf) phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'failure_count': float('nan')})
        self.assertIn("không chấp nhận NaN hoặc Infinity", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'failure_count': float('inf')})
        self.assertIn("không chấp nhận NaN hoặc Infinity", str(ctx.exception))

    def test_nested_dict_raises_error(self):
        """Cấu trúc dictionary lồng nhau bị cấm tuyệt đối."""
        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'error': {'nested_key': 'nested_val'}})
        self.assertIn("không được chứa dictionary lồng nhau", str(ctx.exception))

    def test_unallowed_keys_raise_error(self):
        """Khóa không nằm trong ALLOWED_METADATA_KEYS phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            sanitize_metadata({'unregistered_custom_key': 'value'})
        self.assertIn("Khóa không được phép", str(ctx.exception))

    def test_summary_sensitive_string_raises_error(self):
        """summary chứa DSN, Basic auth hoặc password marker phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            AuditService.record_event(
                function_key="AUTH_LOGIN",
                action="LOGIN",
                outcome="FAILED",
                summary="Lỗi kết nối postgresql://user:pass@localhost:5432/db",
                request_id=uuid.uuid4(),
                actor_type=AuditLog.ActorType.SYSTEM
            )
        self.assertIn("Phát hiện dữ liệu nhạy cảm", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            AuditService.record_event(
                function_key="AUTH_LOGIN",
                action="LOGIN",
                outcome="FAILED",
                summary="Header Authorization: Basic dXNlcjpwYXNz",
                request_id=uuid.uuid4(),
                actor_type=AuditLog.ActorType.SYSTEM
            )
        self.assertIn("Phát hiện dữ liệu nhạy cảm", str(ctx.exception))


class AuditServiceTransactionTestCase(TransactionTestCase):
    """Kiểm tra tích hợp AuditService và Transaction thực tế trên Database."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="audit_actor",
            full_name="Người Thao Tác",
            password="SecurePassword@2026!"
        )
        self.request_id = uuid.uuid4()

    def test_record_event_success(self):
        """Xác minh ghi nhận sự kiện thành công vào bảng audit_logs."""
        log = AuditService.record_event(
            function_key="AUTH_LOGIN",
            action="LOGIN",
            outcome="SUCCESS",
            summary="Đăng nhập thành công vào hệ thống",
            request_id=self.request_id,
            actor_user=self.user,
            actor_type=AuditLog.ActorType.USER,
            metadata={'username': 'audit_actor'}
        )

        self.assertIsInstance(log.id, uuid.UUID)
        self.assertEqual(log.actor_user, self.user)
        self.assertEqual(log.outcome, AuditLog.Outcome.SUCCESS)
        self.assertEqual(log.actor_name_snapshot, "Người Thao Tác")

        saved_log = AuditLog.objects.get(id=log.id)
        self.assertEqual(saved_log.function_key, "AUTH_LOGIN")
        self.assertEqual(saved_log.metadata['username'], 'audit_actor')

    def test_async_contract_rejects_success_outcome(self):
        """Xác minh thao tác bất đồng bộ (is_async=True) cấm outcome là SUCCESS."""
        with self.assertRaises(ValueError) as ctx:
            AuditService.record_event(
                function_key="DEVICE_DISPATCH",
                action="CONTROL",
                outcome="SUCCESS",
                summary="Lệnh đã gửi tới hàng đợi",
                request_id=self.request_id,
                actor_user=self.user,
                is_async=True
            )
        self.assertIn("không được phép ghi nhận outcome là SUCCESS", str(ctx.exception))

    def test_spoofing_guard_user_without_actor_raises(self):
        """actor_type là USER nhưng không truyền actor_user phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            AuditService.record_event(
                function_key="AUTH_LOGIN",
                action="LOGIN",
                outcome="DENIED",
                summary="Từ chối đăng nhập",
                request_id=self.request_id,
                actor_user=None,
                actor_type=AuditLog.ActorType.USER
            )
        self.assertIn("bắt buộc phải cung cấp đối tượng actor_user", str(ctx.exception))

    def test_spoofing_guard_system_with_user_raises(self):
        """actor_type là SYSTEM nhưng lại truyền actor_user phải bị từ chối."""
        with self.assertRaises(ValueError) as ctx:
            AuditService.record_event(
                function_key="SYSTEM_MAINTENANCE",
                action="UPDATE",
                outcome="SUCCESS",
                summary="Bảo trì hệ thống",
                request_id=self.request_id,
                actor_user=self.user,
                actor_type=AuditLog.ActorType.SYSTEM
            )
        self.assertIn("không được phép đính kèm actor_user", str(ctx.exception))

    def test_atomic_rollback_cancels_audit_log_inside_block(self):
        """
        Xác minh quy tắc transaction atomic: Khi khối nghiệp vụ bị lỗi rollback,
        bản ghi log trong khối đó cũng bị hủy bỏ hoàn toàn.
        """
        initial_count = AuditLog.objects.count()

        try:
            with transaction.atomic():
                AuditService.record_event(
                    function_key="USER_PASSWORD_CHANGE",
                    action="UPDATE",
                    outcome="SUCCESS",
                    summary="Đổi mật khẩu thành công",
                    request_id=self.request_id,
                    actor_user=self.user
                )
                raise RuntimeError("Lỗi mô phỏng trong bước lưu dữ liệu!")
        except RuntimeError:
            pass

        self.assertEqual(AuditLog.objects.count(), initial_count)

    def test_caller_outside_failed_atomic_persists_failure_log(self):
        """
        Xác minh kiến trúc chuẩn: Caller thoát khỏi khối atomic bị lỗi
        rồi ghi log FAILED thì bản ghi được commit thật sự xuống DB.
        """
        initial_count = AuditLog.objects.count()

        try:
            with transaction.atomic():
                raise RuntimeError("Lỗi nghiệp vụ cần rollback!")
        except RuntimeError:
            # Caller ghi nhận vết lỗi FAILED bên ngoài khối transaction bị lỗi
            AuditService.record_event(
                function_key="USER_PASSWORD_CHANGE",
                action="UPDATE",
                outcome="FAILED",
                summary="Đổi mật khẩu thất bại do lỗi hệ thống",
                request_id=self.request_id,
                actor_user=self.user,
                error_code="INTERNAL_ERROR"
            )

        self.assertEqual(AuditLog.objects.count(), initial_count + 1)
        last_log = AuditLog.objects.latest('occurred_at')
        self.assertEqual(last_log.outcome, AuditLog.Outcome.FAILED)
        self.assertEqual(last_log.error_code, "INTERNAL_ERROR")
```

---

## 5. Bảng tổng hợp Hàm, Lớp, Biến và Fields

| Thành phần | Thuộc file | Kiểu / Loại | Ý nghĩa & Dữ liệu vào / ra |
|---|---|---|---|
| `ErrorCode` | `core/errors.py` | Constants Class | Danh mục hằng số mã lỗi chuẩn (`VALIDATION_ERROR`, `CSRF_FAILED`,...). |
| `make_error_response` | `core/errors.py` | Helper Function | Trả về `JsonResponse` theo cấu trúc envelope `{"error": {...}}`. |
| `csrf_failure` | `core/errors.py` | View Function | Hứng lỗi CSRF Django, trả JSON HTTP 403 `CSRF_FAILED`. |
| `custom_exception_handler` | `core/errors.py` | DRF Handler | Phân biệt ParseError (400), CSRF PermissionDenied (403), Forbidden (403). |
| `handler400..500` | `core/errors.py` | Django Handlers | Bắt lỗi 400, 403, 404, 500 ngoài DRF và trả về JSON envelope thống nhất. |
| `RequestIDAndSecurityHeadersMiddleware` | `core/middleware.py` | Middleware Class | Gán `request.request_id` (UUID v4), đính kèm `X-Request-ID` và `Cache-Control: no-store`. |
| `health_live` | `core/views.py` | View Function | Liveness probe: trả về HTTP 200 `UP`. |
| `health_ready` | `core/views.py` | View Function | Readiness probe: kiểm tra PostgreSQL (`SELECT 1`), trả về 200 hoặc 503 (envelope lỗi chuẩn). |
| `AuditLog` | `audit/models.py` | Model Class | Bảng duy nhất `audit_logs` lưu trữ actor, action, outcome, request_id, metadata. |
| `sanitize_metadata` | `audit/services.py` | Helper Function | Lọc allowlist, chặn secret/DSN/Basic/Bearer/Cookie markers, chặn nested dict, kiểm tra `math.isfinite`. |
| `AuditService.record_event` | `audit/services.py` | Service Method | Pure writer: chỉ insert, kiểm tra summary theo mẫu server, outcome (202 ACCEPTED), spoofing guard. |

---

## 6. Lệnh PowerShell để bạn tự chạy kiểm tra

> [!IMPORTANT]
> Toàn bộ các lệnh dưới đây được chạy từ thư mục gốc của repository: `c:\Users\taday\OneDrive\Documents\PTIT\IOT\IoT_cabinet`.

### Bước 1: Tạo migration cho app `audit`
```powershell
.\.venv\Scripts\python.exe backend\manage.py makemigrations audit
```
**Kết quả mong đợi:**
```text
Migrations for 'audit':
  backend\apps\audit\migrations\0001_initial.py
    + Create model AuditLog
    + Create constraint ck_audit_logs_outcome_valid on model auditlog
    + Create constraint ck_audit_logs_actor_type_valid on model auditlog
```

### Bước 2: Áp dụng migration vào PostgreSQL
```powershell
.\.venv\Scripts\python.exe backend\manage.py migrate
```
**Kết quả mong đợi:**
```text
Operations to perform:
  Apply all migrations: accounts, admin, audit, auth, contenttypes, sessions
Running migrations:
  Applying audit.0001_initial... OK
```

### Bước 3: Chạy toàn bộ bộ kiểm thử (Tổng cộng 43 Tests)
Bao gồm:
- 17 tests của Patch 1 (5 DatabaseGuard + 12 Accounts)
- 11 tests của `core.test_foundation`
- 15 tests của `audit.test_audit`
Tổng cộng: **43 tests**:
```powershell
.\.venv\Scripts\python.exe backend\manage.py test apps.core.tests apps.accounts.tests apps.audit.tests --settings=config.test_settings
```
**Kết quả mong đợi:**
```text
Creating test database for alias 'default' ('iot_cabinet_django_test')...
Found 43 test(s).
System check identified no issues (0 silenced).
...........................................
----------------------------------------------------------------------
Ran 43 tests in 1.150s

OK
Destroying test database for alias 'default' ('iot_cabinet_django_test')...
```

---

## 7. Acceptance Checklist & Câu hỏi tự kiểm tra

### Checklist nghiệm thu:
- [ ] File `backend/apps/audit/migrations/0001_initial.py` đã được tạo và chứa model `AuditLog`.
- [ ] Bảng `audit_logs` đã được tạo trên PostgreSQL `iot_cabinet_django`.
- [ ] Truy cập đường dẫn không tồn tại (ví dụ `/api/unregistered`) trả về HTTP 404 JSON envelope chuẩn `{"error": {"code": "NOT_FOUND", ...}}`.
- [ ] Truy cập `/health/ready` trả về HTTP 200 `READY` khi DB sống; khi DB chết trả về HTTP 503 với envelope `SERVICE_UNAVAILABLE`.
- [ ] Mọi response trả về từ backend đều có header `X-Request-ID` và `Cache-Control: no-store`.
- [ ] Toàn bộ **43 test cases** đều vượt qua (`Ran 43 tests ... OK`).
- [ ] Không còn cờ `fail_safe_commit` trong `AuditService`.
- [ ] `summary` tuân thủ câu mẫu phía server, không chứa raw payload/headers/exceptions.

---

## 8. Gói gửi Reviewer

Sau khi copy và chạy thử nghiệm thành công, hãy gửi đoạn text sau cho reviewer:

```text
Review: TV3-DJ-02 — Hạ Tầng Lỗi và Một Nguồn AuditLog (Patch 02 — Bản Chuẩn Hóa Chốt)
Baseline: commit fe7b6a2 (sau khi hoàn thành Patch 01)
Đã tạo và cập nhật các file:
- backend/apps/core/ (errors.py, middleware.py, views.py, tests/test_foundation.py)
- backend/apps/audit/ (apps.py, models.py, services.py, tests/test_audit.py, __init__.py)
- backend/config/ (settings.py, urls.py)
- Migration tự sinh: backend/apps/audit/migrations/0001_initial.py

Đã giải quyết triệt để theo review:
1. Bỏ fail_safe_commit; writer chỉ insert. Caller thoát khỏi atomic block bị lỗi rồi mới ghi FAILED/DENIED; test bằng TransactionTestCase.
2. Quy định hợp đồng summary dùng mẫu cố định từ server, cấm truyền raw payload/headers/traceback; lưới lọc bổ trợ mở rộng chặn Basic/Bearer/DSN/Cookie.
3. Ánh xạ chuẩn ParseError (400 VALIDATION_ERROR) và SessionAuthentication CSRF (403 CSRF_FAILED).
4. Chuẩn hóa lỗi Django ngoài DRF qua handler400, 403, 404, 500; thống nhất envelope lỗi cho health ready (503 SERVICE_UNAVAILABLE).
5. Kiểm tra math.isfinite() cấm NaN/Infinity trong metadata, serialize allow_nan=False.
6. Bộ test hoàn chỉnh gồm 43 tests (17 cũ + 11 foundation + 15 audit).

Tôi tự chạy:
1. python backend/manage.py makemigrations audit -> Sinh 0001_initial.py
2. python backend/manage.py migrate -> Áp dụng thành công vào iot_cabinet_django
3. python backend/manage.py test apps.core.tests apps.accounts.tests apps.audit.tests --settings=config.test_settings -> OK (43 tests)

Nhờ bạn đọc và review; không tự sửa source code.
```

---

> [!CAUTION]
> **DỪNG TẠI PHẦN TV3-DJ-02 (Bản Chuẩn Hóa Chốt).**  
> Chưa sang phần tiếp (TV3-DJ-03 — Roles/Permissions và Tài khoản Bootstrap).  
> Chờ TV3 copy các file trên vào dự án, chạy kiểm tra thực tế trên PostgreSQL và gửi kết quả/review.
