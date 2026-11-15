# TV3-DJ-04 — Login/Logout, Session Registry, CSRF và Rate Limiter (Patch 04)

Ngày cập nhật: **06/11/2026** (Đã giải quyết triệt để 2/2 điểm sau vòng thẩm định 3: P1 Password concurrency gap & P2 Username format / Audit template contract)  
Mã chặng: **TV3-DJ-04**  
Thuộc kế hoạch: [TV3_KE_HOACH_DJANGO_CHO_GEMINI.md](TV3_KE_HOACH_DJANGO_CHO_GEMINI.md)  
Người thực hiện: **TV3**  
Trạng thái: **READY_FOR_COPY (31 test cases bảo đảm toàn diện mọi kịch bản concurrency, stale cookie, password race condition, malformed sensitive username và transaction rollback)**  

---

## 1. Mục tiêu, Prerequisites và Ranh giới Kỹ thuật

### 1.1 Mục tiêu chặng 04 & Các Điểm Cốt Lõi Đã Vá
1. **Advisory Lock Bao Trọn Quy Trình Rate Limit (`authenticate_credentials`)**:
   - Sử dụng một khối `with transaction.atomic():` duy nhất bao trọn từ lúc `acquire_rate_limit_advisory_locks(user_key, ip_key)` -> `check_rate_limit()` -> `authenticate()` credentials -> `record_login_attempt()`.
   - Vì PostgreSQL `pg_advisory_xact_lock` là khóa theo giao dịch, khóa được giữ liên tục và chỉ giải phóng khi commit hoặc rollback. Điều này loại trừ hoàn toàn khoảng trống giữa lúc kiểm tra và ghi attempt, ngăn chặn tuyệt đối tình trạng nhiều request đồng thời cùng lọt qua khi chỉ còn 1 slot.
2. **Cho Phép Cookie Cũ/Hết Hạn Lấy CSRF Và Đăng Nhập Lại (`CsrfTokenView`, `LoginView`)**:
   - Khai báo tường minh `authentication_classes = []` trên cả `CsrfTokenView` và `LoginView`.
   - Trong `SessionRegistryAndMustChangeMiddleware`, cấu hình danh sách `PUBLIC_AUTH_PATHS = ("/api/v1/auth/csrf/", "/api/v1/auth/login/")` để bỏ qua kiểm tra session registry bắt buộc khi người dùng truy cập lấy token hoặc nộp thông tin đăng nhập mới.
   - Vẫn bảo toàn 100% kiểm tra an toàn CSRF (`@csrf_protect`) trên `POST /api/v1/auth/login/`.
   - Endpoint bảo vệ như `LogoutView` và các API nghiệp vụ vẫn dùng `RegistrySessionAuthentication` và trả chuẩn HTTP 401 `NOT_AUTHENTICATED` khi cookie bị thu hồi hoặc hết hạn.
3. **Triệt Tiêu Khoảng Trống Giữa Xác Thực & Tạo Phiên (`establish_session`) [Vá P1]**:
   - Trong `establish_session()`, toàn bộ quy trình từ `login()`, khóa dòng `select_for_update()`, đối chiếu hash mật khẩu, lưu `django_session`, tạo `AuthSession` đến `AuditLog` đều nằm trong một khối `try: with transaction.atomic():`.
   - Khóa dòng User bằng `User.objects.select_for_update().filter(id=user.id).first()` và đọc lại trạng thái mới nhất từ database ngay trong transaction tạo phiên.
   - **Chống cấp phiên khi mật khẩu bị thay đổi giữa xác thực và tạo phiên (P1)**: Trước `login()`, so sánh hash `locked_user.password` với `user.password` lúc xác thực. Nếu khác nhau (do mật khẩu bị đổi đồng thời trong DB), ném `PermissionDenied("Thông tin xác thực của tài khoản đã thay đổi.")`. Tại `LoginView`, bắt `PermissionDenied` và trả HTTP 401 với thông báo chung.
   - Nếu tài khoản bị `LOCKED` hoặc `ARCHIVED` trong khoảng trống, hệ thống lập tức ném `PermissionDenied`, rollback DB, xóa session in-memory và từ chối cấp phiên.
   - Các hàm `lock_user()` và `archive_user()` cũng sử dụng `select_for_update()` trong transaction atomic để đồng bộ hóa tuyệt đối với `establish_session()`.
4. **Bảo Vệ Hợp Đồng AuditService & Chặn Username Chứa Chuỗi Nhạy Cảm [Vá P2]**:
   - **Tầng Serializer**: `LoginRequestSerializer` sử dụng `RegexField(regex=r'^[a-zA-Z0-9._-]{3,64}$')` để trả ngay HTTP 400 (`VALIDATION_ERROR`) khi client gửi username sai định dạng hoặc chứa ký tự đặc biệt như `password=sample`.
   - **Tầng Service (Rule 1 Pure Writer & Server Templates)**: Trong `authenticate_credentials`, `establish_session`, `logout_session`, `summary` BẮT BUỘC sử dụng câu mẫu cố định do server định nghĩa (`"Đăng nhập thành công vào hệ thống"`, `"Đăng nhập thất bại do thông tin không chính xác"`, `"Đăng nhập thất bại cho tài khoản không tồn tại"`, `"Đăng xuất khỏi hệ thống thành công"`). Tuyệt đối không chèn trực tiếp username thô của client vào summary.
   - **Tầng Defense-in-depth cho Metadata**: Trước khi đưa `canonical_username` vào `metadata["attempted_username"]` hoặc `target_name_snapshot`, lọc kiểm tra qua `SENSITIVE_STRING_MARKERS`. Nếu phát hiện chứa từ khóa nhạy cảm (`password=`, `bearer `, `token=`,...), trường đó được loại bỏ để bảo đảm `AuditService` không bao giờ ném `ValueError` dẫn tới HTTP 500.

---

## 2. Danh sách file theo thứ tự thao tác

| STT | Đường dẫn file | Trạng thái | Lý do / Trách nhiệm |
|:---|:---|:---:|:---|
| 1 | `backend/apps/accounts/security/rate_limit.py` | **NEW** | HMAC key, limiter 5 user/20 IP/900s, PG Advisory Lock, Trusted Proxy IP guard |
| 2 | `backend/apps/accounts/services/__init__.py` | **NEW** | Package services của accounts, export các hàm session và auth |
| 3 | `backend/apps/accounts/services/sessions.py` | **NEW** | Quản lý registry 15m, lock_user/archive_user với select_for_update, single-role |
| 4 | `backend/apps/accounts/services/auth.py` | **NEW** | Advisory lock bao trọn authenticate; establish_session khóa row User, kiểm tra password tươi và bọc trọn login() |
| 5 | `backend/apps/accounts/serializers/__init__.py` | **NEW** | Package serializers của accounts |
| 6 | `backend/apps/accounts/serializers/auth.py` | **NEW** | Serializer kiểm tra regex username 3-64 ký tự, mật khẩu giữ nguyên khoảng trắng |
| 7 | `backend/apps/accounts/security/authentication.py` | **NEW** | DRF `RegistrySessionAuthentication`: authenticate_header trả 401, kiểm tra Registry |
| 8 | `backend/apps/accounts/security/middleware.py` | **NEW** | Bỏ qua registry check cho PUBLIC_AUTH_PATHS, bảo vệ must_change_password |
| 9 | `backend/apps/accounts/views/__init__.py` | **NEW** | Package views của accounts |
| 10 | `backend/apps/accounts/views/auth.py` | **NEW** | `authentication_classes = []` cho CsrfTokenView & LoginView; @csrf_protect trên login |
| 11 | `backend/apps/accounts/urls.py` | **NEW** | Định tuyến URL cho các endpoint xác thực accounts |
| 12 | `backend/config/urls.py` | **MODIFY** | Đăng ký đường dẫn `/api/v1/` vào URL router chính |
| 13 | `backend/config/settings.py` | **MODIFY** | Cấu hình `RegistrySessionAuthentication`, `TRUSTED_PROXIES`, `ALLOWED_HOSTS` |
| 14 | `backend/apps/accounts/tests/test_auth.py` | **NEW** | 31 test cases toàn diện: Rate Limit, Concurrency, Password Race, Stale cookie, Rollback, Hợp đồng Audit |

---

## 3. Luồng hoạt động & Kiến thức Django cần hiểu

### 3.1 Luồng Đăng Nhập Hoàn Chỉnh (Advisory Lock & User Row Lock Đồng Bộ)
```text
Client gửi POST /api/v1/auth/login/ kèm username, password, X-CSRFToken
  │
  ├── 1. Kiểm tra CSRF (@csrf_protect):
  │      Nếu thiếu hoặc sai token -> Trả về HTTP 403 CSRF_FAILED (Envelope chuẩn)
  │      (authentication_classes=[] giúp cookie cũ không gây lỗi 401 trước khi kiểm tra)
  │
  ├── 2. Trích xuất Client IP an toàn:
  │      Mặc định dùng REMOTE_ADDR; chỉ đọc X-Forwarded-For nếu REMOTE_ADDR in TRUSTED_PROXIES
  │
  ├── 3. Xác thực Credentials (authenticate_credentials):
  │      Khối transaction.atomic() DUY NHẤT bao trọn:
  │      ├── acquire_rate_limit_advisory_locks(user_key, ip_key)
  │      ├── check_rate_limit(): Nếu vượt ngưỡng -> record_login_attempt(RATE_LIMITED) -> HTTP 429
  │      ├── authenticate(username, password)
  │      └── record_login_attempt(SUCCESS hoặc FAILED)
  │      (Khóa advisory lock được giữ liên tục qua transaction, loại trừ race condition)
  │
  ├── 4. Thiết lập Phiên làm việc Toàn vẹn (establish_session):
  │      Bọc toàn bộ trong try...with transaction.atomic():
  │      ├── User.objects.select_for_update(): Khóa dòng User, đọc lại DB.
  │      │     Nếu status != ACTIVE hoặc is_active=False -> ném PermissionDenied -> Rollback!
  │      ├── login(request, locked_user): Tráo đổi session_key
  │      ├── request.session.set_expiry(900): Đặt hạn tuyệt đối 15 phút
  │      ├── request.session.save(): Lưu django_session xuống DB
  │      ├── create_auth_session(): Tạo bản ghi AuthSession trong registry (sid UUID v4)
  │      ├── request.session["auth_session_id"] = str(auth_session.id); request.session.save()
  │      └── AuditService.record_event(): Ghi USER_LOGIN outcome='SUCCESS'
  │      Nếu bất kỳ bước nào lỗi -> Rollback toàn bộ DB, flush session in-memory, quăng lỗi.
  │      Transaction commit thành công -> rotate_token(request) xoay vòng token CSRF.
  │
  └── 5. Trả về HTTP 200: Gắn session cookie HttpOnly và JSON thông tin người dùng
```

---

## 4. Toàn bộ nội dung từng file

### File 1: `backend/apps/accounts/security/rate_limit.py`
*(Vai trò: Quản lý giới hạn tần suất đăng nhập bằng khóa băm HMAC SHA-256, chống IP spoofing qua Trusted Proxies, đồng bộ bằng PostgreSQL Advisory Locks)*

```python
"""
Rate Limiter Security Module
Quản lý giới hạn tần suất đăng nhập (Rate Limiting) phục vụ chống tấn công dò mật khẩu (brute-force):
- Sử dụng khóa băm HMAC SHA-256 đối với username và địa chỉ IP để chống lộ thông tin trong DB.
- Ngưỡng giới hạn: Tối đa 5 lần thất bại / username và 20 lần thất bại / IP trong vòng 900 giây (15 phút).
- Tính toán header Retry-After chính xác dựa trên thời điểm của lần thử thất bại cũ nhất trong cửa sổ.
- Chống kéo dài vô hạn cửa sổ khóa: Các yêu cầu bị từ chối khi đang bị khóa (RATE_LIMITED) được loại trừ
  khỏi phép đếm vi phạm, bảo đảm cửa sổ khóa 15 phút không bị kéo dài bất tận do spam liên tục.
- Bảo vệ chống IP spoofing: Mặc định dùng REMOTE_ADDR, chỉ tin HTTP_X_FORWARDED_FOR khi proxy được cấu hình rõ.
- Đồng bộ hóa qua PostgreSQL Advisory Lock (pg_advisory_xact_lock) chống race condition trong môi trường đồng thời.
"""
import hashlib
import hmac
import uuid
from datetime import timedelta
from typing import Any, Final

from django.conf import settings
from django.db import connection
from django.http import HttpRequest
from django.utils import timezone

from apps.accounts.models import LoginAttempt

# Cấu hình các hằng số ngưỡng Rate Limit
RATE_LIMIT_LOGIN_MAX_USERNAME: Final[int] = 5
RATE_LIMIT_LOGIN_MAX_IP: Final[int] = 20
RATE_LIMIT_WINDOW_SECONDS: Final[int] = 900  # 15 phút


def get_client_ip(request: HttpRequest) -> str:
    """
    Trích xuất địa chỉ IP của client từ request HTTP:
    Mặc định sử dụng REMOTE_ADDR.
    Chỉ tin cậy HTTP_X_FORWARDED_FOR khi REMOTE_ADDR nằm trong TRUSTED_PROXIES được cấu hình rõ trong settings.
    """
    remote_addr = request.META.get("REMOTE_ADDR", "127.0.0.1")
    trusted_proxies = getattr(settings, "TRUSTED_PROXIES", None) or []
    if trusted_proxies and remote_addr in trusted_proxies:
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0].strip()
            if ip:
                return ip
    return remote_addr


def hash_identifier(value: str, purpose: str = "LOGIN") -> str:
    """
    Băm định danh (username hoặc IP) thành chuỗi băm HMAC SHA-256 64 ký tự hex:
    Sử dụng AUTH_LOG_HMAC_KEY hoặc SECRET_KEY làm khóa băm.
    """
    key_str = getattr(settings, "AUTH_LOG_HMAC_KEY", None) or settings.SECRET_KEY
    key_bytes = key_str.encode("utf-8")
    raw_payload = f"{purpose}:{value.strip().lower()}".encode("utf-8")
    return hmac.new(key_bytes, raw_payload, hashlib.sha256).hexdigest()


def _key_to_lock_id(hex_key: str) -> int:
    """Chuyển đổi 16 ký tự đầu của khóa HMAC thành số nguyên có dấu 64-bit (bigint) của PostgreSQL."""
    return int.from_bytes(bytes.fromhex(hex_key[:16]), byteorder="big", signed=True)


def acquire_rate_limit_advisory_locks(username_key: str, ip_key: str) -> None:
    """
    Khóa PostgreSQL Transaction Advisory Locks (pg_advisory_xact_lock) theo thứ tự tăng dần cố định.
    - Tự động giải phóng khi transaction kết thúc (commit hoặc rollback).
    - Luôn sắp xếp khóa theo thứ tự tăng dần để loại trừ nguy cơ deadlock khi có nhiều request đồng thời.
    - Trong môi trường SQLite (test cục bộ), hàm là no-op an toàn.
    """
    if connection.vendor != "postgresql":
        return

    k_user = _key_to_lock_id(username_key)
    k_ip = _key_to_lock_id(ip_key)

    unique_locks = sorted(list({k_user, k_ip}))
    with connection.cursor() as cursor:
        for lock_id in unique_locks:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])


def check_rate_limit(
    username: str,
    ip: str,
    purpose: str = "LOGIN"
) -> tuple[bool, int | None, str | None]:
    """
    Kiểm tra xem username hoặc địa chỉ IP có vượt quá ngưỡng thử đăng nhập thất bại hay không:
    - Cửa sổ thời gian: 900 giây tính từ thời điểm hiện tại trở về trước.
    - Loại trừ các bản ghi có failure_code='RATE_LIMITED' để chống việc spam liên tục
      kéo dài vô tận cửa sổ khóa (Anti-Infinite Window Extension).

    Tham số:
        username (str): Tên đăng nhập.
        ip (str): Địa chỉ IP của client.
        purpose (str): Mục đích rate-limit (mặc định 'LOGIN').

    Trả về:
        tuple[bool, int | None, str | None]:
            - is_limited (bool): True nếu bị khóa, False nếu được phép.
            - retry_after (int | None): Số giây client phải chờ trước khi thử lại.
            - reason (str | None): Lý do bị khóa ('USERNAME_LOCKED' hoặc 'IP_LOCKED').
    """
    now = timezone.now()
    window_start = now - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)

    user_key = hash_identifier(username, purpose)
    ip_key = hash_identifier(ip, purpose)

    # Chỉ đếm các lần thử sai mật khẩu thực tế, không đếm các lần bị chặn do RATE_LIMITED
    base_qs = LoginAttempt.objects.filter(
        success=False,
        occurred_at__gte=window_start
    ).exclude(failure_code="RATE_LIMITED")

    # 1. Kiểm tra ngưỡng vi phạm theo Username
    user_attempts = base_qs.filter(username_key=user_key)
    user_count = user_attempts.count()

    if user_count >= RATE_LIMIT_LOGIN_MAX_USERNAME:
        oldest_attempt = user_attempts.order_by("occurred_at").first()
        if oldest_attempt:
            elapsed = (now - oldest_attempt.occurred_at).total_seconds()
            retry_after = max(1, int(RATE_LIMIT_WINDOW_SECONDS - elapsed))
        else:
            retry_after = RATE_LIMIT_WINDOW_SECONDS
        return True, retry_after, "USERNAME_LOCKED"

    # 2. Kiểm tra ngưỡng vi phạm theo IP
    ip_attempts = base_qs.filter(ip_key=ip_key)
    ip_count = ip_attempts.count()

    if ip_count >= RATE_LIMIT_LOGIN_MAX_IP:
        oldest_attempt = ip_attempts.order_by("occurred_at").first()
        if oldest_attempt:
            elapsed = (now - oldest_attempt.occurred_at).total_seconds()
            retry_after = max(1, int(RATE_LIMIT_WINDOW_SECONDS - elapsed))
        else:
            retry_after = RATE_LIMIT_WINDOW_SECONDS
        return True, retry_after, "IP_LOCKED"

    return False, None, None


def record_login_attempt(
    username: str,
    ip: str,
    success: bool,
    user: Any = None,
    failure_code: str | None = None,
    request_id: uuid.UUID | None = None,
    purpose: str = "LOGIN"
) -> LoginAttempt:
    """
    Ghi nhận lịch sử nỗ lực đăng nhập vào database:
    - Băm username và IP thành HMAC trước khi ghi (Rule: không lưu raw secret/identifier).
    - Lưu mã phân loại thất bại (INVALID_CREDENTIALS, ACCOUNT_LOCKED, RATE_LIMITED...).
    """
    user_key = hash_identifier(username, purpose)
    ip_key = hash_identifier(ip, purpose)

    return LoginAttempt.objects.create(
        username_key=user_key,
        ip_key=ip_key,
        user=user,
        success=success,
        failure_code=failure_code,
        request_id=request_id or uuid.uuid4()
    )
```

---

### File 2: `backend/apps/accounts/services/__init__.py`
*(Package services của app accounts — export đầy đủ các dịch vụ auth và sessions)*

```python
"""
Accounts Services Package
Export các hàm nghiệp vụ chính về quản lý phiên, xác thực và thu hồi phiên.
"""
from apps.accounts.services.auth import (
    authenticate_credentials,
    establish_session,
    logout_session,
)
from apps.accounts.services.sessions import (
    archive_user,
    create_auth_session,
    lock_user,
    revoke_all_user_sessions,
    revoke_auth_session,
    verify_auth_session,
)

__all__ = [
    "create_auth_session",
    "verify_auth_session",
    "revoke_auth_session",
    "revoke_all_user_sessions",
    "lock_user",
    "archive_user",
    "authenticate_credentials",
    "establish_session",
    "logout_session",
]
```

---

### File 3: `backend/apps/accounts/services/sessions.py`
*(Vai trò: Quản lý vòng đời AuthSession registry, thời hạn tuyệt đối 15 phút, lock_user/archive_user đồng bộ qua select_for_update, signal thu hồi phiên)*

```python
"""
Session Registry Service Module
Quản lý vòng đời của phiên làm việc trong bảng accounts_auth_session (Registry):
- Thiết lập thời gian sống tuyệt đối 15 phút (900 giây) sử dụng datetime expires_at.
- Tuyệt đối không dùng cơ chế trượt (no sliding window): Request/polling không được phép gia hạn expires_at.
- Cung cấp các hàm kiểm tra hiệu lực phiên (verify_auth_session) và thu hồi phiên (revoke_auth_session).
- Đảm bảo khi người dùng bị khóa/hủy tài khoản, phiên làm việc lập tức bị vô hiệu hóa và thu hồi (chống sống lại khi unlock).
- Kiểm tra chặt chẽ vai trò người dùng (single-role per user) tại mỗi lần verify phiên.
"""
from datetime import timedelta
from typing import Any, Final

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.http import HttpRequest
from django.utils import timezone

from apps.accounts.models import AuthSession, User
from apps.accounts.policies.roles import get_user_role
from apps.accounts.security.rate_limit import get_client_ip

SESSION_EXPIRY_SECONDS: Final[int] = 900  # 15 phút thời hạn sống tuyệt đối


def create_auth_session(
    user: User,
    request: HttpRequest,
    session_key: str
) -> AuthSession:
    """
    Khởi tạo một bản ghi AuthSession mới trong Registry cho người dùng đăng nhập thành công:
    - Gắn thời gian hết hạn tuyệt đối = now + 15 phút.
    - Lưu địa chỉ IP và chuỗi User-Agent của client phục vụ giám sát.

    Tham số:
        user (User): Người dùng vừa đăng nhập.
        request (HttpRequest): Yêu cầu HTTP hiện tại.
        session_key (str): Khóa phiên của Django (django_session).

    Trả về:
        AuthSession: Thực thể phiên làm việc mới được tạo.
    """
    now = timezone.now()
    expires_at = now + timedelta(seconds=SESSION_EXPIRY_SECONDS)
    ip = get_client_ip(request)
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:512]

    return AuthSession.objects.create(
        user=user,
        session_key=session_key,
        expires_at=expires_at,
        ip_address=ip,
        user_agent=user_agent
    )


def verify_auth_session(
    session_key: str,
    user: Any = None
) -> tuple[bool, AuthSession | None, str | None]:
    """
    Xác minh tính hợp lệ của phiên làm việc trong Registry:
    1. Bản ghi AuthSession phải tồn tại theo session_key.
    2. Phiên chưa bị thu hồi (revoked_at is None).
    3. Phiên chưa quá hạn tuyệt đối 15 phút (expires_at > now).
    4. Người dùng sở hữu phiên phải khớp với user yêu cầu (nếu truyền vào).
    5. Tài khoản người dùng phải ở trạng thái ACTIVE và is_active=True. Nếu không, lập tức thu hồi phiên.
    6. Người dùng phải có vai trò hợp lệ duy nhất (get_user_role(user) is not None).

    Tham số:
        session_key (str): Khóa session của Django.
        user (User | None): Người dùng đối chiếu (tùy chọn).

    Trả về:
        tuple[bool, AuthSession | None, str | None]:
            - is_valid (bool): True nếu phiên hoàn toàn hợp lệ.
            - session (AuthSession | None): Thực thể phiên nếu tìm thấy.
            - reason (str | None): Lý do từ chối nếu không hợp lệ.
    """
    if not session_key:
        return False, None, "SESSION_KEY_EMPTY"

    session = AuthSession.objects.filter(session_key=session_key).select_related("user").first()
    if not session:
        return False, None, "SESSION_NOT_FOUND"

    # 1. Kiểm tra xem phiên đã bị thu hồi chưa
    if session.revoked_at is not None:
        return False, session, "SESSION_REVOKED"

    # 2. Kiểm tra thời hạn sống tuyệt đối 15 phút
    if session.expires_at <= timezone.now():
        return False, session, "SESSION_EXPIRED"

    # 3. Đối chiếu người dùng sở hữu phiên
    if user and getattr(user, "is_authenticated", False):
        if session.user_id != user.id:
            return False, session, "USER_MISMATCH"

    # 4. Kiểm tra trạng thái hoạt động của tài khoản trong database
    if session.user.status != User.Status.ACTIVE or not session.user.is_active:
        revoke_all_user_sessions(session.user, reason=f"USER_{session.user.status}")
        return False, session, "USER_INACTIVE"

    # 5. Kiểm tra tính hợp lệ của vai trò (single-role)
    user_role = get_user_role(session.user)
    if not user_role:
        revoke_all_user_sessions(session.user, reason="USER_NO_ROLE")
        return False, session, "USER_NO_ROLE"

    # Phiên hợp lệ: Tuyệt đối không gia hạn expires_at (no sliding)
    return True, session, None


def revoke_auth_session(
    session_key: str,
    reason: str = "USER_LOGOUT"
) -> bool:
    """
    Thu hồi một phiên làm việc cụ thể theo session_key:
    Cập nhật revoked_at và revoke_reason, không xóa vật lý bản ghi để giữ audit trace.
    """
    session = AuthSession.objects.filter(
        session_key=session_key,
        revoked_at__isnull=True
    ).first()

    if session:
        session.revoked_at = timezone.now()
        session.revoke_reason = reason
        session.save(update_fields=["revoked_at", "revoke_reason"])
        return True
    return False


def revoke_all_user_sessions(
    user: User,
    reason: str = "REVOKE_ALL"
) -> int:
    """
    Thu hồi toàn bộ các phiên làm việc đang hoạt động của một người dùng:
    Thường dùng khi đổi mật khẩu, admin khóa tài khoản hoặc reset mật khẩu.
    """
    return AuthSession.objects.filter(
        user=user,
        revoked_at__isnull=True
    ).update(
        revoked_at=timezone.now(),
        revoke_reason=reason
    )


def lock_user(user: User, reason: str = "USER_LOCKED") -> None:
    """
    Khóa tài khoản người dùng và thu hồi ngay lập tức toàn bộ phiên đang hoạt động.
    Sử dụng select_for_update() để đồng bộ hóa tuyệt đối với establish_session().
    """
    with transaction.atomic():
        u = User.objects.select_for_update().filter(id=user.id).first()
        if u:
            u.status = User.Status.LOCKED
            u.is_active = False
            u.save(update_fields=["status", "is_active"])
            revoke_all_user_sessions(u, reason=reason)


def archive_user(user: User, reason: str = "USER_ARCHIVED") -> None:
    """
    Hủy / Lưu trữ tài khoản người dùng và thu hồi ngay lập tức toàn bộ phiên đang hoạt động.
    Sử dụng select_for_update() để đồng bộ hóa tuyệt đối với establish_session().
    """
    with transaction.atomic():
        u = User.objects.select_for_update().filter(id=user.id).first()
        if u:
            u.status = User.Status.ARCHIVED
            u.is_active = False
            u.save(update_fields=["status", "is_active"])
            revoke_all_user_sessions(u, reason=reason)


@receiver(post_save, sender=User)
def revoke_sessions_on_user_status_change(sender, instance: User, **kwargs):
    """
    Signal tự động thu hồi toàn bộ phiên làm việc của người dùng khi tài khoản
    chuyển trạng thái sang LOCKED hoặc ARCHIVED, hoặc is_active=False.
    Bảo đảm cookie cũ không thể sống lại sau khi tài khoản được mở khóa.
    """
    if instance.status != User.Status.ACTIVE or not instance.is_active:
        revoke_all_user_sessions(instance, reason=f"USER_{instance.status}")
```

---

### File 4: `backend/apps/accounts/services/auth.py`
*(Vai trò: Xác thực credentials với advisory lock bao trọn, thiết lập phiên bọc toàn bộ login() và select_for_update trong transaction atomic có rollback, đăng xuất chuẩn Audit)*

```python
"""
Authentication Service Module
Quản lý quy trình xác thực thông tin đăng nhập, thiết lập phiên và đăng xuất:
- Tích hợp kiểm tra Rate Limiter trước khi đánh giá credentials, bảo vệ bằng advisory lock xuyên suốt.
- Sử dụng hàm authenticate() của Django với Argon2id để kiểm tra mật khẩu.
- Chống tấn công dò quét tài khoản (Username Enumeration) bằng thông báo lỗi chung.
- Luồng establish_session:
  1. Bọc toàn bộ quy trình trong try...with transaction.atomic():
  2. Khóa dòng User (select_for_update), đọc lại và kiểm tra trạng thái/role tươi mới nhất từ DB.
  3. Gọi login() và cycle session_key chống cố định phiên.
  4. Đặt thời hạn tuyệt đối 15 phút cho session cookie.
  5. Lưu django_session, tạo AuthSession registry và ghi AuditLog.
  6. Nếu bất kỳ bước nào lỗi (session save, registry, audit, commit), rollback toàn bộ và dọn sạch session.
  7. Xoay vòng CSRF token mới (rotate_token) ngay sau khi transaction commit thành công (Rule 7).
- Luồng logout_session:
  1. Thu hồi phiên trong AuthSession registry.
  2. Ghi nhật ký AuditLog (outcome='SUCCESS', action='USER_LOGOUT') theo đúng hợp đồng.
  3. Gọi logout(request) của Django và xoay vòng CSRF token.
"""
import uuid
from typing import Any

from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest
from django.middleware.csrf import rotate_token

from apps.accounts.models import AuthSession, User
from apps.accounts.policies.roles import get_user_role
from apps.accounts.security.rate_limit import (
    acquire_rate_limit_advisory_locks,
    check_rate_limit,
    get_client_ip,
    hash_identifier,
    record_login_attempt,
)
from apps.accounts.services.sessions import (
    create_auth_session,
    revoke_auth_session,
)
from apps.audit.models import AuditLog
from apps.audit.services import AuditService, SENSITIVE_STRING_MARKERS


def authenticate_credentials(
    request: HttpRequest,
    username: str,
    password: str
) -> tuple[User | None, str | None, int | None]:
    """
    Kiểm tra thông tin đăng nhập của người dùng kèm chốt chặn Rate Limit:
    1. Giữ khóa PostgreSQL Transaction Advisory Lock xuyên suốt: kiểm tra ngưỡng -> xác thực credentials -> ghi attempt.
    2. Chuẩn hóa canonical username và gọi authenticate() của Django.
    3. Phân loại mã lỗi nội bộ (INVALID_CREDENTIALS, ACCOUNT_LOCKED, ACCOUNT_ARCHIVED)
       để ghi nhận LoginAttempt chính xác, nhưng trả thông báo chung cho client.

    Trả về:
        tuple[User | None, str | None, int | None]:
            - user: Thực thể User nếu xác thực thành công, ngược lại None.
            - error_code: Mã lỗi ('RATE_LIMITED', 'INVALID_CREDENTIALS'...).
            - retry_after: Số giây phải chờ nếu bị RATE_LIMITED.
    """
    ip = get_client_ip(request)
    request_id = getattr(request, "request_id", None) or uuid.uuid4()
    canonical_username = username.strip().lower()

    user_key = hash_identifier(canonical_username, "LOGIN")
    ip_key = hash_identifier(ip, "LOGIN")

    target_user: User | None = None
    failure_code: str | None = None

    # Khóa Advisory Lock bao trọn toàn bộ quá trình: Kiểm tra ngưỡng -> Xác thực credentials -> Ghi nhận Attempt
    with transaction.atomic():
        acquire_rate_limit_advisory_locks(user_key, ip_key)

        is_limited, retry_after, reason = check_rate_limit(canonical_username, ip, purpose="LOGIN")
        if is_limited:
            record_login_attempt(
                username=canonical_username,
                ip=ip,
                success=False,
                failure_code="RATE_LIMITED",
                request_id=request_id
            )
            return None, "RATE_LIMITED", retry_after

        target_user = User.objects.filter(username=canonical_username).first()

        # Xác thực bằng Django authenticate (tự động kiểm tra Argon2id và cờ is_active)
        authenticated_user = authenticate(
            request,
            username=canonical_username,
            password=password
        )

        if authenticated_user is not None:
            # Đăng nhập thành công: Ghi nhận LoginAttempt ngay dưới lock
            record_login_attempt(
                username=canonical_username,
                ip=ip,
                success=True,
                user=authenticated_user,
                request_id=request_id
            )
            return authenticated_user, None, None

        # Đăng nhập thất bại: Phân loại nguyên nhân phục vụ audit nội bộ
        if target_user:
            if target_user.status == User.Status.LOCKED:
                failure_code = "ACCOUNT_LOCKED"
            elif target_user.status == User.Status.ARCHIVED:
                failure_code = "ACCOUNT_ARCHIVED"
            else:
                failure_code = "INVALID_CREDENTIALS"
        else:
            failure_code = "INVALID_CREDENTIALS"

        record_login_attempt(
            username=canonical_username,
            ip=ip,
            success=False,
            user=target_user,
            failure_code=failure_code,
            request_id=request_id
        )

    # Ghi log kiểm toán cho lần đăng nhập thất bại theo đúng hợp đồng AuditService (ngoài lock sau khi commit)
    if target_user:
        AuditService.record_event(
            actor_user=target_user,
            actor_type=AuditLog.ActorType.USER,
            function_key="auth.login",
            action="USER_LOGIN",
            outcome="FAILED",
            error_code=failure_code,
            target_type="USER",
            target_id=str(target_user.id),
            target_name_snapshot=target_user.username,
            summary="Đăng nhập thất bại do thông tin không chính xác",
            request_id=request_id,
            ip_address=ip,
            metadata={"ip_address": ip, "reason": failure_code}
        )
    else:
        meta: dict[str, Any] = {"ip_address": ip, "reason": "INVALID_CREDENTIALS"}
        if not any(marker in canonical_username for marker in SENSITIVE_STRING_MARKERS):
            meta["attempted_username"] = canonical_username[:64]

        target_name = canonical_username[:255] if not any(marker in canonical_username for marker in SENSITIVE_STRING_MARKERS) else None

        AuditService.record_event(
            actor_user=None,
            actor_type=AuditLog.ActorType.SYSTEM,
            function_key="auth.login",
            action="USER_LOGIN",
            outcome="FAILED",
            error_code="INVALID_CREDENTIALS",
            target_type="USER",
            target_id=None,
            target_name_snapshot=target_name,
            summary="Đăng nhập thất bại cho tài khoản không tồn tại",
            request_id=request_id,
            ip_address=ip,
            metadata=meta
        )

    return None, failure_code, None


def establish_session(
    request: HttpRequest,
    user: User
) -> AuthSession:
    """
    Thiết lập phiên làm việc chính thức sau khi xác thực thành công (Rule 6, 7, 10):
    - Toàn bộ các thao tác (login, khóa dòng User, kiểm tra trạng thái tươi mới,
      lưu django_session, AuthSession registry, AuditLog) được gom trong một transaction.atomic() duy nhất.
    - Khóa dòng User (select_for_update) và đọc lại từ DB để loại trừ khoảng trống (race condition)
      khi tài khoản bị khóa/hủy hoặc mật khẩu bị đổi ngay trước khi phiên được khởi tạo.
    - So sánh hash locked_user.password với user.password lúc xác thực; nếu khác thì từ chối ngay.
    - Nếu bất kỳ bước nào thất bại, toàn bộ thay đổi database bị rollback, session in-memory bị flush/xóa sạch.
    - Xoay vòng CSRF token (rotate_token) chỉ thực hiện SAU KHI transaction commit thành công.

    Tham số:
        request (HttpRequest): Request HTTP hiện tại.
        user (User): Người dùng đăng nhập thành công.

    Trả về:
        AuthSession: Bản ghi Registry của phiên.
    """
    request_id = getattr(request, "request_id", None) or uuid.uuid4()
    ip = get_client_ip(request)

    try:
        with transaction.atomic():
            # 1. Khóa dòng User (select_for_update) và đọc lại trạng thái mới nhất từ DB
            locked_user = User.objects.select_for_update().filter(id=user.id).first()
            if not locked_user:
                raise PermissionDenied("Tài khoản người dùng không tồn tại.")

            # Kiểm tra trạng thái hoạt động: nếu bị LOCKED hoặc ARCHIVED trong khoảng trống, từ chối ngay
            if locked_user.status != User.Status.ACTIVE or not locked_user.is_active:
                raise PermissionDenied("Tài khoản người dùng đã bị khóa hoặc ngừng hoạt động.")

            # Kiểm tra mật khẩu có bị thay đổi giữa bước xác thực và tạo phiên hay không (P1)
            if locked_user.password != user.password:
                raise PermissionDenied("Thông tin xác thực của tài khoản đã thay đổi.")

            # Kiểm tra vai trò hợp lệ duy nhất (single-role)
            user_role = get_user_role(locked_user)
            if not user_role:
                raise PermissionDenied("Tài khoản người dùng chưa được phân quyền vai trò hợp lệ.")

            # 2. Đăng nhập qua framework Django (tự động cycle session_key chống cố định phiên)
            login(request, locked_user)

            # 3. Thiết lập thời gian sống tuyệt đối 15 phút (900 giây)
            request.session.set_expiry(900)

            # 4. Lưu django_session vào DB trong transaction
            request.session.save()

            # 5. Tạo bản ghi Registry trong cùng transaction
            auth_session = create_auth_session(locked_user, request, request.session.session_key)
            request.session["auth_session_id"] = str(auth_session.id)
            request.session.save()

            # 6. Ghi nhận AuditLog với outcome='SUCCESS' và câu mẫu cố định trong cùng transaction
            AuditService.record_event(
                actor_user=locked_user,
                actor_type=AuditLog.ActorType.USER,
                function_key="auth.login",
                action="USER_LOGIN",
                outcome="SUCCESS",
                target_type="USER",
                target_id=str(locked_user.id),
                target_name_snapshot=locked_user.username,
                summary="Đăng nhập thành công vào hệ thống",
                request_id=request_id,
                ip_address=ip,
                metadata={"session_id": str(auth_session.id), "ip_address": ip}
            )

        # 7. Xoay vòng CSRF token mới sau khi transaction đã commit thành công (Rule 7)
        rotate_token(request)
        return auth_session

    except Exception as exc:
        # Dọn sạch session in-memory khi transaction lỗi
        try:
            request.session.flush()
        except Exception:
            pass
        raise exc


def logout_session(request: HttpRequest) -> None:
    """
    Đăng xuất an toàn và giải phóng phiên làm việc:
    - Thu hồi phiên trong AuthSession Registry.
    - Ghi nhận AuditLog sự kiện USER_LOGOUT theo đúng hợp đồng AuditService.
    - Hủy session Django và xoay vòng token CSRF.
    """
    user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    session_key = getattr(request.session, "session_key", None)
    request_id = getattr(request, "request_id", None) or uuid.uuid4()
    ip = get_client_ip(request)

    # 1. Thu hồi phiên trong Registry
    if session_key:
        revoke_auth_session(session_key, reason="USER_LOGOUT")

    # 2. Ghi nhận AuditLog
    if user:
        AuditService.record_event(
            actor_user=user,
            actor_type=AuditLog.ActorType.USER,
            function_key="auth.logout",
            action="USER_LOGOUT",
            outcome="SUCCESS",
            target_type="USER",
            target_id=str(user.id),
            target_name_snapshot=user.username,
            summary="Đăng xuất khỏi hệ thống thành công",
            request_id=request_id,
            ip_address=ip,
            metadata={"ip_address": ip} if ip else None
        )

    # 3. Đăng xuất khỏi Django session
    logout(request)

    # 4. Xoay vòng CSRF token
    rotate_token(request)
```

---

### File 5: `backend/apps/accounts/serializers/__init__.py`
*(Khai báo package serializers của app accounts)*

```python
"""
Accounts Serializers Package
"""
from apps.accounts.serializers.auth import LoginRequestSerializer, UserSummarySerializer

__all__ = [
    "LoginRequestSerializer",
    "UserSummarySerializer",
]
```

---

### File 6: `backend/apps/accounts/serializers/auth.py`
*(Vai trò: Serializers kiểm tra dữ liệu payload đăng nhập và chuẩn hóa dữ liệu trả về client)*

```python
"""
Authentication Serializers Module
Xác thực dữ liệu đầu vào cho quy trình đăng nhập và định dạng phản hồi:
- LoginRequestSerializer: Kiểm tra username và mật khẩu (tuyệt đối không trim khoảng trắng mật khẩu).
- UserSummarySerializer: Chuẩn hóa thông tin người dùng và vai trò trả về cho frontend.
"""
from typing import Any

from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.policies.roles import get_user_role

USERNAME_REGEX = r'^[a-zA-Z0-9._-]{3,64}$'


class LoginRequestSerializer(serializers.Serializer):
    """
    Serializer kiểm tra payload gửi lên tại endpoint /api/v1/auth/login/.
    """
    username = serializers.RegexField(
        regex=USERNAME_REGEX,
        max_length=64,
        trim_whitespace=True,
        required=True,
        error_messages={
            "blank": "Username không được để trống.",
            "invalid": "Tên đăng nhập không đúng định dạng (chỉ cho phép 3-64 ký tự gồm chữ cái, chữ số, dấu chấm, gạch dưới và gạch ngang)."
        }
    )
    password = serializers.CharField(
        max_length=128,
        trim_whitespace=False,  # Bắt buộc False: Bảo toàn khoảng trắng của mật khẩu
        required=True,
        write_only=True,
        error_messages={"blank": "Mật khẩu không được để trống."}
    )


class UserSummarySerializer(serializers.ModelSerializer):
    """
    Serializer định dạng thông tin người dùng cơ bản trả về sau khi đăng nhập.
    """
    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "full_name",
            "email",
            "role",
            "status",
            "must_change_password",
        ]
        read_only_fields = fields

    def get_role(self, obj: User) -> str | None:
        """Lấy vai trò duy nhất của người dùng qua policy get_user_role."""
        return get_user_role(obj)
```

---

### File 7: `backend/apps/accounts/security/authentication.py`
*(Vai trò: DRF Authentication Class kế thừa SessionAuthentication, đối chiếu phiên với AuthSession Registry, kiểm tra CSRF và trả chuẩn HTTP 401)*

```python
"""
Authentication Backend Module for Django REST Framework
Cung cấp RegistrySessionAuthentication kế thừa từ SessionAuthentication của DRF:
- Xác thực cookie session của Django.
- Cung cấp authenticate_header() để trả về mã HTTP 401 thay vì 403 khi chưa xác thực.
- Đối chiếu trạng thái phiên với AuthSession Registry (kiểm tra hạn tuyệt đối 15 phút, cờ revoked, trạng thái user, vai trò).
- Tự động thực thi kiểm tra CSRF token trên các phương thức HTTP gây thay đổi dữ liệu (POST, PUT, PATCH, DELETE).
"""
from typing import Any

from django.conf import settings
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import AuthenticationFailed

from apps.accounts.services.sessions import verify_auth_session


class RegistrySessionAuthentication(SessionAuthentication):
    """
    Xác thực phiên làm việc kết hợp kiểm tra Registry và CSRF trong DRF:
    Bảo đảm người dùng có cookie Django session hợp lệ VÀ bản ghi AuthSession trong DB
    vẫn còn hiệu lực, chưa bị thu hồi và chưa quá hạn 15 phút.
    """

    def authenticate_header(self, request: Any) -> str:
        """
        Cung cấp challenge header để DRF trả về HTTP 401 (thay vì HTTP 403) khi chưa xác thực.
        """
        return "Session"

    def authenticate(self, request: Any) -> tuple[Any, Any] | None:
        cookie_name = getattr(settings, "SESSION_COOKIE_NAME", "sessionid")
        session_cookie_sent = cookie_name in request.COOKIES or bool(getattr(request.session, "session_key", None))

        # 1. Xác thực session tiêu chuẩn của Django qua SessionAuthentication
        user_auth_tuple = super().authenticate(request)
        if user_auth_tuple is None:
            if session_cookie_sent:
                # Client gửi session cookie nhưng không xác thực được (đã đăng xuất hoặc không tồn tại)
                raise AuthenticationFailed("Phiên làm việc đã hết hạn hoặc bị thu hồi.", code="NOT_AUTHENTICATED")
            return None

        user, auth = user_auth_tuple
        session_key = getattr(request.session, "session_key", None)
        if not session_key:
            return None

        # 2. Đối chiếu phiên với AuthSession Registry
        is_valid, session, reason = verify_auth_session(session_key, user=user)

        if not is_valid:
            if reason == "SESSION_REVOKED":
                raise AuthenticationFailed("Phiên làm việc đã bị thu hồi.", code="SESSION_REVOKED")
            elif reason == "SESSION_EXPIRED":
                raise AuthenticationFailed("Phiên làm việc đã hết hạn (15 phút).", code="SESSION_EXPIRED")
            elif reason == "USER_INACTIVE":
                raise AuthenticationFailed("Tài khoản người dùng đã bị khóa hoặc ngừng hoạt động.", code="USER_INACTIVE")
            elif reason == "USER_NO_ROLE":
                raise AuthenticationFailed("Tài khoản người dùng chưa được phân quyền vai trò hợp lệ.", code="USER_NO_ROLE")
            else:
                raise AuthenticationFailed("Phiên làm việc không hợp lệ.", code="SESSION_INVALID")

        return user, auth
```

---

### File 8: `backend/apps/accounts/security/middleware.py`
*(Vai trò: Middleware kiểm tra tính hợp lệ của AuthSession Registry, bỏ qua kiểm tra session cho các endpoint công khai và bảo vệ chốt chặn must_change_password)*

```python
"""
Security Middleware Module
Cung cấp SessionRegistryAndMustChangeMiddleware:
1. Đối chiếu phiên với AuthSession Registry: Nếu phiên bị thu hồi hoặc quá hạn 15 phút,
   tự động đăng xuất request và trả về lỗi 401 NOT_AUTHENTICATED đối với các API.
   Bỏ qua kiểm tra session cho các endpoint công khai (PUBLIC_AUTH_PATHS) để người dùng có cookie cũ vẫn đăng nhập lại được.
2. Chốt chặn Must-Change-Password: Nếu tài khoản có cờ must_change_password=True,
   ngăn chặn truy cập vào toàn bộ các API nghiệp vụ khác (trả về HTTP 403 MUST_CHANGE_PASSWORD),
   chỉ cho phép truy cập các endpoint đổi mật khẩu, xem profile chính mình và đăng xuất.
"""
from typing import Any, Callable

from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from apps.accounts.services.sessions import verify_auth_session
from apps.core.errors import ErrorCode, make_error_response

# Danh sách các URL được phép truy cập khi tài khoản bắt buộc phải đổi mật khẩu
ALLOWED_MUST_CHANGE_PATHS: tuple[str, ...] = (
    "/api/v1/auth/csrf/",
    "/api/v1/auth/logout/",
    "/api/v1/auth/change-password/",
    "/api/v1/profile/change-password/",
    "/api/v1/profile/me/",
    "/admin/password_change/",
    "/admin/password_change/done/",
    "/admin/logout/",
)

# Danh sách URL công khai không bắt buộc kiểm tra phiên registry (để lấy CSRF và đăng nhập lại)
PUBLIC_AUTH_PATHS: tuple[str, ...] = (
    "/api/v1/auth/csrf/",
    "/api/v1/auth/login/",
)


class SessionRegistryAndMustChangeMiddleware:
    """
    Middleware kiểm tra phiên Registry và áp dụng chính sách must_change_password.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Bỏ qua xác thực session registry trên các endpoint công khai để người dùng có cookie cũ vẫn đăng nhập lại được
        if any(request.path.startswith(p) for p in PUBLIC_AUTH_PATHS):
            return self.get_response(request)

        user = getattr(request, "user", None)
        request_id = getattr(request, "request_id", None)

        # Chỉ áp dụng kiểm tra khi người dùng đã được định danh phiên
        if user and user.is_authenticated:
            session_key = getattr(request.session, "session_key", None)

            # 1. Kiểm tra tính hợp lệ của phiên trong Registry
            if session_key:
                is_valid, session, reason = verify_auth_session(session_key, user=user)
                if not is_valid:
                    logout(request)
                    if request.path.startswith("/api/"):
                        error_msg = "Phiên làm việc đã hết hạn hoặc bị thu hồi."
                        if reason == "USER_INACTIVE":
                            error_msg = "Tài khoản người dùng đã bị khóa hoặc ngừng hoạt động."
                        elif reason == "USER_NO_ROLE":
                            error_msg = "Tài khoản người dùng chưa được phân quyền vai trò hợp lệ."
                        return make_error_response(
                            ErrorCode.NOT_AUTHENTICATED,
                            error_msg,
                            401,
                            request_id
                        )

            # 2. Chốt chặn must_change_password
            if getattr(user, "must_change_password", False):
                path = request.path
                is_allowed = any(path.startswith(allowed_path) for allowed_path in ALLOWED_MUST_CHANGE_PATHS)

                if not is_allowed:
                    if path.startswith("/api/"):
                        return make_error_response(
                            ErrorCode.FORBIDDEN,
                            "Tài khoản bắt buộc phải đổi mật khẩu trước khi truy cập các chức năng khác.",
                            403,
                            request_id
                        )
                    else:
                        # Đối với giao diện Django Admin HTML, chuyển hướng đến trang đổi mật khẩu
                        return redirect("/admin/password_change/")

        return self.get_response(request)
```

---

### File 9: `backend/apps/accounts/views/__init__.py`
*(Package views của app accounts)*

```python
"""
Accounts Views Package
"""
from apps.accounts.views.auth import CsrfTokenView, LoginView, LogoutView

__all__ = [
    "CsrfTokenView",
    "LoginView",
    "LogoutView",
]
```

---

### File 10: `backend/apps/accounts/views/auth.py`
*(Vai trò: API Views xử lý GET /auth/csrf/, POST /auth/login/, POST /auth/logout/ — miễn trừ authentication cho CSRF và Login, bắt buộc CSRF trên Login)*

```python
"""
Authentication Views Module
Cung cấp các API endpoint quản lý phiên đăng nhập của hệ thống IoT Cabinet:
- GET  /api/v1/auth/csrf/: Cung cấp token CSRF và thiết lập cookie csrftoken.
- POST /api/v1/auth/login/: Đăng nhập có kiểm tra CSRF, rate limit, xoay vòng session & CSRF token.
- POST /api/v1/auth/logout/: Đăng xuất, thu hồi phiên trong registry và hủy session cookie.
"""
from typing import Any

from django.core.exceptions import PermissionDenied
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.security.authentication import RegistrySessionAuthentication
from apps.accounts.serializers.auth import LoginRequestSerializer, UserSummarySerializer
from apps.accounts.services.auth import authenticate_credentials, establish_session, logout_session
from apps.core.errors import ErrorCode, make_error_response


class CsrfTokenView(APIView):
    """
    Endpoint GET /api/v1/auth/csrf/:
    Gắn cookie csrftoken và trả về token CSRF phục vụ client frontend (Rule 7).
    Tuyệt đối không chạy xác thực registry (authentication_classes = []) để client
    có cookie cũ/hết hạn vẫn lấy được CSRF token mới nhằm đăng nhập lại.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    @method_decorator(ensure_csrf_cookie)
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        csrf_token = get_token(request._request)
        return Response({"csrf_token": csrf_token}, status=status.HTTP_200_OK)


class LoginView(APIView):
    """
    Endpoint POST /api/v1/auth/login/:
    Đăng nhập tài khoản người dùng:
    - Không chạy xác thực session registry bắt buộc (authentication_classes = [])
      để người dùng có cookie cũ vẫn có thể gửi thông tin đăng nhập mới.
    - Bắt buộc kiểm tra CSRF token (@csrf_protect) ngay cả khi chưa đăng nhập (Rule 7).
    - Kiểm tra giới hạn tần suất (Rate Limiter). Vượt ngưỡng trả HTTP 429 + Retry-After.
    - Xác thực Argon2id qua authenticate_credentials.
    - Thiết lập phiên an toàn qua establish_session (khóa row User, rollback nếu bị khóa trong khoảng trống).
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    @method_decorator(csrf_protect)
    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        request_id = getattr(request._request, "request_id", None)

        # 1. Kiểm tra payload gửi lên
        serializer = LoginRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return make_error_response(
                ErrorCode.VALIDATION_ERROR,
                "Dữ liệu đăng nhập không hợp lệ.",
                400,
                request_id
            )

        username = serializer.validated_data["username"]
        password = serializer.validated_data["password"]

        # 2. Xác thực credentials và kiểm tra Rate Limiter
        user, error_code, retry_after = authenticate_credentials(
            request._request,
            username=username,
            password=password
        )

        if error_code == "RATE_LIMITED":
            resp = make_error_response(
                ErrorCode.RATE_LIMITED,
                f"Quá nhiều lần thử đăng nhập thất bại. Vui lòng thử lại sau {retry_after} giây.",
                429,
                request_id
            )
            resp["Retry-After"] = str(retry_after)
            return resp

        if not user:
            # Thông báo lỗi chung bảo đảm an toàn chống dò quét username
            return make_error_response(
                ErrorCode.NOT_AUTHENTICATED,
                "Tên đăng nhập hoặc mật khẩu không chính xác.",
                401,
                request_id
            )

        # 3. Thiết lập phiên làm việc chính thức trong transaction atomic an toàn
        try:
            auth_session = establish_session(request._request, user)
        except PermissionDenied:
            # Nếu tài khoản bị khóa hoặc mật khẩu thay đổi trong khoảng trống ngay trước khi thiết lập phiên
            return make_error_response(
                ErrorCode.NOT_AUTHENTICATED,
                "Tên đăng nhập hoặc mật khẩu không chính xác.",
                401,
                request_id
            )

        # 4. Trả về thông tin người dùng và metadata phiên
        user_data = UserSummarySerializer(user).data
        return Response(
            {
                "message": "Đăng nhập thành công.",
                "user": user_data,
                "session": {
                    "session_id": str(auth_session.id),
                    "expires_at": auth_session.expires_at.isoformat(),
                },
            },
            status=status.HTTP_200_OK
        )


class LogoutView(APIView):
    """
    Endpoint POST /api/v1/auth/logout/:
    Đăng xuất phiên làm việc:
    - Bắt buộc phải có phiên đăng nhập hợp lệ (IsAuthenticated).
    - Thu hồi phiên trong Registry, hủy session cookie và xoay vòng CSRF token.
    """
    authentication_classes = [RegistrySessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        logout_session(request._request)
        return Response(
            {"message": "Đăng xuất thành công."},
            status=status.HTTP_200_OK
        )
```

---

### File 11: `backend/apps/accounts/urls.py`
*(Vai trò: Khai báo routing URL cho các endpoint xác thực accounts)*

```python
"""
Accounts URL Configuration Module
Định tuyến các endpoint xác thực và quản lý tài khoản:
- /api/v1/auth/csrf/
- /api/v1/auth/login/
- /api/v1/auth/logout/
"""
from django.urls import path

from apps.accounts.views.auth import CsrfTokenView, LoginView, LogoutView

urlpatterns = [
    path("auth/csrf/", CsrfTokenView.as_view(), name="auth_csrf"),
    path("auth/login/", LoginView.as_view(), name="auth_login"),
    path("auth/logout/", LogoutView.as_view(), name="auth_logout"),
]
```

---

### File 12: `backend/config/urls.py`
*(Cập nhật: Đăng ký router `/api/v1/` vào hệ thống URL chính của Django)*

```python
"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.contrib import admin
from django.urls import include, path

from apps.core.views import health_live, health_ready

urlpatterns = [
    # Health checks
    path("health/live", health_live, name="health_live"),
    path("health/ready", health_ready, name="health_ready"),

    # API v1 Endpoints
    path("api/v1/", include("apps.accounts.urls")),

    # Django Admin site
    path("admin/", admin.site.urls),
]

# Khai báo các handler lỗi toàn cục trả về JSON envelope
handler400 = "apps.core.errors.handler400"
handler403 = "apps.core.errors.handler403"
handler404 = "apps.core.errors.handler404"
handler500 = "apps.core.errors.handler500"
```

---

### File 13: `backend/config/settings.py`
*(Cập nhật: Bổ sung `RegistrySessionAuthentication`, `SessionRegistryAndMustChangeMiddleware`, cấu hình `REST_FRAMEWORK`, `TRUSTED_PROXIES` và `ALLOWED_HOSTS`)*

```python
"""
Django settings for config project.

Generated by 'django-admin startproject' using Django 5.2.19.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/5.2/ref/settings/
"""
import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# Khóa bí mật Django (bảo vệ session, CSRF, cryptographic signing)
SECRET_KEY = os.getenv(
    'DJANGO_SECRET_KEY',
    'django-insecure-default-dev-key-change-in-production-2026-iot-cabinet'
)

# Chế độ Debug: Chỉ bật ở môi trường phát triển cục bộ
DEBUG = os.getenv('DJANGO_DEBUG', 'False').lower() in ('true', '1', 't')

# Danh sách Host được phép kết nối (bổ sung testserver cho Django Test Client)
allowed_hosts_env = os.getenv('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost')
ALLOWED_HOSTS = [h.strip() for h in allowed_hosts_env.split(',') if h.strip()]
if 'testserver' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append('testserver')

# Danh sách Reverse Proxy tin cậy (chỉ tin cậy X-Forwarded-For khi IP nguồn thuộc danh sách này)
trusted_proxies_env = os.getenv('DJANGO_TRUSTED_PROXIES', '')
TRUSTED_PROXIES = [p.strip() for p in trusted_proxies_env.split(',') if p.strip()]


# Application definition

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

MIDDLEWARE = [
    'apps.core.middleware.RequestIDAndSecurityHeadersMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'apps.accounts.security.middleware.SessionRegistryAndMustChangeMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

# CSRF Failure View tùy biến trả về JSON chuẩn cho API
CSRF_FAILURE_VIEW = 'apps.core.errors.csrf_failure'

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


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases
# Sử dụng PostgreSQL độc lập cho Django, tuyệt đối không dùng lại database cũ
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


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators
# Chỉ định model User tùy biến trước migration đầu tiên
AUTH_USER_MODEL = 'accounts.User'

# Sử dụng thuật toán băm mật khẩu Argon2id hiện đại làm mặc định
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
]

# Bộ kiểm tra độ dài mật khẩu (12–128 ký tự, không trim)
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 12,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'vi'

TIME_ZONE = 'Asia/Ho_Chi_Minh'

USE_I18N = True

USE_TZ = True


# Cấu hình Session theo V1.4
SESSION_ENGINE = 'django.contrib.sessions.backends.db'  # Lưu session vào DB

# Thời hạn sống tuyệt đối 15 phút (900 giây)
SESSION_COOKIE_AGE = 900  # 15 phút

# KHÔNG tự động gia hạn phiên (sliding expiration) mỗi khi có request
SESSION_SAVE_EVERY_REQUEST = False

# Bảo vệ Cookie chống truy cập từ JavaScript (chống XSS đánh cắp session)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'  # Chống CSRF, nhưng vẫn cho phép POST từ cùng domain
SESSION_COOKIE_SECURE = False  # Chỉ True khi triển khai HTTPS thực tế

# Khóa bí mật băm HMAC cho định danh login_attempts
AUTH_LOG_HMAC_KEY = os.getenv(
    'AUTH_LOG_HMAC_KEY',
    'ecbf305c5a9a2b1fc6c6a1ad9a7f104eebf620c49b2cb17eebe10c08367cba2b'
)

# Django REST Framework Configuration
REST_FRAMEWORK = {
    'EXCEPTION_HANDLER': 'apps.core.errors.custom_exception_handler',
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'apps.accounts.security.authentication.RegistrySessionAuthentication',
    ],
}

# Test Runner Guard
TEST_RUNNER = 'apps.core.test_runner.DatabaseGuardTestRunner'


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = 'static/'

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
```

---

### File 14: `backend/apps/accounts/tests/test_auth.py`
*(Test module toàn diện của Patch 04: 31 test cases kiểm tra Rate Limiter, Đăng nhập, Đăng xuất, CSRF, Registry, Must-Change, Stale Cookie, Rollback, Password Race Gap, Malformed Username và Hợp đồng Audit)*

```python
"""
Test module kiểm tra toàn diện hạ tầng Authentication & Session của accounts:
- Rate Limiter: Ngưỡng 5 user, 20 IP, tính toán Retry-After, chống kéo dài vô hạn window, chặn giả mạo IP.
- Đăng nhập thành công: Tráo đổi session_key, xoay vòng CSRF, tạo AuthSession registry, ghi AuditLog.
- Đăng nhập thất bại: Sai mật khẩu, user không tồn tại, user bị khóa (LOCKED), user bị hủy (ARCHIVED) - trả thông báo chung.
- Quá hạn tuyệt đối 15 phút (no sliding window).
- Đăng xuất: Thu hồi AuthSession trong registry, chặn cookie replay.
- Chống hồi sinh phiên: Khóa user -> thu hồi ngay lập tức -> mở khóa không thể dùng lại cookie cũ.
- Chống khoảng trống race condition: User bị khóa hoặc mật khẩu thay đổi trước khi establish_session() bị từ chối tạo phiên.
- Cookie cũ không chặn CSRF & đăng nhập: Client có sessionid hết hạn vẫn lấy được CSRF và đăng nhập lại.
- Chặn username chứa ký tự nhạy cảm / sai định dạng trả HTTP 400 và không làm bung lỗi HTTP 500 từ AuditService.
- Kiểm tra tính nguyên tử: Rollback toàn bộ session và registry khi gặp lỗi registry/audit.
- Kiểm tra bắt buộc CSRF trên endpoint đăng nhập (kể cả anonymous).
- Chốt chặn must_change_password: Chặn API khác, cho phép đổi mật khẩu / me / logout.
"""
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone

from apps.accounts.models import AuthSession, LoginAttempt, User
from apps.accounts.policies.roles import ROLE_ADMIN, assign_role
from apps.accounts.security.middleware import SessionRegistryAndMustChangeMiddleware
from apps.accounts.security.rate_limit import (
    RATE_LIMIT_LOGIN_MAX_IP,
    RATE_LIMIT_LOGIN_MAX_USERNAME,
    RATE_LIMIT_WINDOW_SECONDS,
    check_rate_limit,
    get_client_ip,
    hash_identifier,
)
from apps.accounts.services.sessions import verify_auth_session
from apps.audit.models import AuditLog


class RateLimiterTestCase(TestCase):
    """Kiểm tra logic giới hạn tần suất đăng nhập (Rate Limiter)."""

    def setUp(self):
        self.username = "test_rate_user"
        self.ip = "192.168.1.100"

    def test_rate_limiter_allows_under_threshold(self):
        """Xác minh dưới 5 lần thất bại thì chưa bị giới hạn."""
        for _ in range(RATE_LIMIT_LOGIN_MAX_USERNAME - 1):
            LoginAttempt.objects.create(
                username_key=hash_identifier(self.username),
                ip_key=hash_identifier(self.ip),
                success=False,
                failure_code="INVALID_CREDENTIALS"
            )

        is_limited, retry_after, reason = check_rate_limit(self.username, self.ip)
        self.assertFalse(is_limited)
        self.assertIsNone(retry_after)
        self.assertIsNone(reason)

    def test_rate_limiter_blocks_username_after_5_failures(self):
        """Xác minh đủ 5 lần thất bại thì username bị khóa và trả Retry-After > 0."""
        for _ in range(RATE_LIMIT_LOGIN_MAX_USERNAME):
            LoginAttempt.objects.create(
                username_key=hash_identifier(self.username),
                ip_key=hash_identifier(self.ip),
                success=False,
                failure_code="INVALID_CREDENTIALS"
            )

        is_limited, retry_after, reason = check_rate_limit(self.username, self.ip)
        self.assertTrue(is_limited)
        self.assertEqual(reason, "USERNAME_LOCKED")
        self.assertGreater(retry_after, 0)
        self.assertLessEqual(retry_after, RATE_LIMIT_WINDOW_SECONDS)

    def test_rate_limiter_blocks_ip_after_20_failures(self):
        """Xác minh khi IP đạt 20 lần thất bại (với các username khác nhau) thì IP bị khóa."""
        for i in range(RATE_LIMIT_LOGIN_MAX_IP):
            LoginAttempt.objects.create(
                username_key=hash_identifier(f"other_user_{i}"),
                ip_key=hash_identifier(self.ip),
                success=False,
                failure_code="INVALID_CREDENTIALS"
            )

        is_limited, retry_after, reason = check_rate_limit("brand_new_user", self.ip)
        self.assertTrue(is_limited)
        self.assertEqual(reason, "IP_LOCKED")
        self.assertGreater(retry_after, 0)

    def test_rate_limiter_retry_after_calculated_correctly(self):
        """Xác minh Retry-After được tính toán chính xác theo thời điểm nỗ lực cũ nhất."""
        now = timezone.now()
        oldest_time = now - timedelta(seconds=300)
        oldest_attempt = LoginAttempt.objects.create(
            username_key=hash_identifier(self.username),
            ip_key=hash_identifier(self.ip),
            success=False,
            failure_code="INVALID_CREDENTIALS"
        )
        # Sử dụng QuerySet.update() để ghi đè trường auto_now_add occurred_at
        LoginAttempt.objects.filter(pk=oldest_attempt.pk).update(occurred_at=oldest_time)

        for _ in range(RATE_LIMIT_LOGIN_MAX_USERNAME - 1):
            LoginAttempt.objects.create(
                username_key=hash_identifier(self.username),
                ip_key=hash_identifier(self.ip),
                success=False,
                failure_code="INVALID_CREDENTIALS"
            )

        is_limited, retry_after, _ = check_rate_limit(self.username, self.ip)
        self.assertTrue(is_limited)
        # Thời gian còn lại phải xấp xỉ 900 - 300 = 600 giây
        self.assertAlmostEqual(retry_after, 600, delta=5)

    def test_rate_limiter_blocked_attempts_do_not_extend_window(self):
        """
        Xác minh quy tắc Anti-Infinite Window: Các lần bị chặn với mã RATE_LIMITED
        không được tính vào tổng vi phạm, chống spam liên tục làm khóa vô tận.
        """
        for _ in range(RATE_LIMIT_LOGIN_MAX_USERNAME):
            LoginAttempt.objects.create(
                username_key=hash_identifier(self.username),
                ip_key=hash_identifier(self.ip),
                success=False,
                failure_code="INVALID_CREDENTIALS"
            )

        # Ghi thêm 10 lần bị chặn
        for _ in range(10):
            LoginAttempt.objects.create(
                username_key=hash_identifier(self.username),
                ip_key=hash_identifier(self.ip),
                success=False,
                failure_code="RATE_LIMITED"
            )

        # Đếm các lần vi phạm loại trừ RATE_LIMITED: Vẫn phải giữ nguyên là 5
        now = timezone.now()
        count = LoginAttempt.objects.filter(
            username_key=hash_identifier(self.username),
            success=False,
            occurred_at__gte=now - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
        ).exclude(failure_code="RATE_LIMITED").count()

        self.assertEqual(count, RATE_LIMIT_LOGIN_MAX_USERNAME)

    def test_get_client_ip_does_not_trust_untrusted_proxy(self):
        """Xác minh get_client_ip chỉ tin X-Forwarded-For khi REMOTE_ADDR trong TRUSTED_PROXIES."""
        rf = RequestFactory()
        # Client tự ý đổi header X-Forwarded-For từ IP không tin cậy
        req = rf.get("/", HTTP_X_FORWARDED_FOR="1.2.3.4", REMOTE_ADDR="192.168.1.50")
        self.assertEqual(get_client_ip(req), "192.168.1.50")


class AuthEndpointsTestCase(TestCase):
    """Kiểm tra tích hợp các endpoint HTTP API đăng nhập, đăng xuất, CSRF và registry."""

    def setUp(self):
        Group.objects.get_or_create(name=ROLE_ADMIN)
        self.client = Client(enforce_csrf_checks=True)

        self.user = User.objects.create_user(
            username="auth_test_user",
            full_name="Người Dùng Test Auth",
            password="SecurePassword@2026!"
        )
        assign_role(self.user, ROLE_ADMIN)

    def _get_csrf_token(self) -> str:
        """Helper gọi GET /api/v1/auth/csrf/ để lấy token và gắn cookie cho client."""
        resp = self.client.get("/api/v1/auth/csrf/")
        self.assertEqual(resp.status_code, 200)
        return resp.json()["csrf_token"]

    def test_csrf_token_endpoint_sets_cookie(self):
        """Xác minh GET /api/v1/auth/csrf/ trả về token và thiết lập cookie csrftoken."""
        token = self._get_csrf_token()
        self.assertTrue(len(token) > 0)
        self.assertIn("csrftoken", self.client.cookies)

    def test_expired_cookie_does_not_block_csrf_and_login(self):
        """
        Xác minh lỗi Point 2: Khi client có session cookie cũ/hết hạn,
        GET /auth/csrf/ và POST /auth/login/ vẫn hoạt động bình thường, không bị chặn 401.
        """
        stale_client = Client(enforce_csrf_checks=True)
        stale_client.cookies["sessionid"] = "stale_or_expired_session_key"

        # Vẫn lấy được CSRF token thành công
        csrf_resp = stale_client.get("/api/v1/auth/csrf/")
        self.assertEqual(csrf_resp.status_code, 200)
        csrf_token = csrf_resp.json()["csrf_token"]
        self.assertTrue(len(csrf_token) > 0)

        # Vẫn gửi POST đăng nhập thành công
        login_resp = stale_client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token
        )
        self.assertEqual(login_resp.status_code, 200)
        self.assertEqual(login_resp.json()["user"]["username"], "auth_test_user")

    def test_csrf_protection_enforced_on_login(self):
        """Xác minh gửi POST login không có CSRF token bị chặn với lỗi CSRF_FAILED (HTTP 403)."""
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"]["code"], "CSRF_FAILED")

    def test_login_success_establishes_session_and_rotates_csrf(self):
        """Xác minh đăng nhập thành công trả HTTP 200, gắn session cookie và xoay vòng CSRF."""
        csrf_before = self._get_csrf_token()

        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_before
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["user"]["username"], "auth_test_user")
        self.assertEqual(data["user"]["role"], ROLE_ADMIN)
        self.assertIn("session_id", data["session"])

        # Phải có session cookie trong response
        self.assertIn("sessionid", self.client.cookies)

        # CSRF token phải được xoay vòng mới sau khi đăng nhập
        csrf_after = self.client.cookies["csrftoken"].value
        self.assertNotEqual(csrf_before, csrf_after)

    def test_login_success_creates_auth_session_registry(self):
        """Xác minh đăng nhập thành công tạo bản ghi AuthSession trong DB với hạn 15 phút."""
        csrf = self._get_csrf_token()
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 200)

        session_key = self.client.session.session_key
        auth_session = AuthSession.objects.filter(session_key=session_key).first()
        self.assertIsNotNone(auth_session)
        self.assertEqual(auth_session.user, self.user)
        self.assertIsNone(auth_session.revoked_at)
        self.assertTrue(auth_session.is_active)

    def test_login_success_records_audit_log(self):
        """Xác minh đăng nhập thành công ghi nhận bản ghi AuditLog với outcome=SUCCESS và actor_user."""
        csrf = self._get_csrf_token()
        self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        log = AuditLog.objects.filter(action="USER_LOGIN", outcome="SUCCESS").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor_user, self.user)
        self.assertEqual(log.target_type, "USER")
        self.assertEqual(log.target_id, str(self.user.id))

    def test_login_failure_invalid_password_returns_401(self):
        """Xác minh sai mật khẩu trả về HTTP 401 NOT_AUTHENTICATED với thông báo chung."""
        csrf = self._get_csrf_token()
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "WrongPassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["code"], "NOT_AUTHENTICATED")
        self.assertEqual(resp.json()["error"]["message"], "Tên đăng nhập hoặc mật khẩu không chính xác.")

        log = AuditLog.objects.filter(action="USER_LOGIN", outcome="FAILED").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor_user, self.user)
        self.assertEqual(log.error_code, "INVALID_CREDENTIALS")

    def test_login_failure_nonexistent_user_returns_401_generic(self):
        """Xác minh user không tồn tại trả cùng thông báo chung chống user enumeration."""
        csrf = self._get_csrf_token()
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "nonexistent_user", "password": "ValidPassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["message"], "Tên đăng nhập hoặc mật khẩu không chính xác.")

        log = AuditLog.objects.filter(action="USER_LOGIN", outcome="FAILED").first()
        self.assertIsNotNone(log)
        self.assertIsNone(log.actor_user)
        self.assertEqual(log.actor_type, AuditLog.ActorType.SYSTEM)

    def test_login_failure_locked_user_returns_401_generic(self):
        """Xác minh tài khoản bị LOCKED vẫn trả thông báo chung, không để lộ cờ lock."""
        self.user.status = User.Status.LOCKED
        self.user.save(update_fields=["status"])

        csrf = self._get_csrf_token()
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["message"], "Tên đăng nhập hoặc mật khẩu không chính xác.")

    def test_login_failure_archived_user_returns_401_generic(self):
        """Xác minh tài khoản bị ARCHIVED vẫn trả thông báo chung."""
        self.user.status = User.Status.ARCHIVED
        self.user.save(update_fields=["status"])

        csrf = self._get_csrf_token()
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["message"], "Tên đăng nhập hoặc mật khẩu không chính xác.")

    def test_login_rate_limited_returns_429_with_retry_after(self):
        """Xác minh khi vượt ngưỡng 5 lần thử sai, trả HTTP 429 kèm header Retry-After."""
        csrf = self._get_csrf_token()

        for _ in range(5):
            self.client.post(
                "/api/v1/auth/login/",
                {"username": "auth_test_user", "password": "WrongPassword@2026!"},
                content_type="application/json",
                HTTP_X_CSRFTOKEN=csrf
            )

        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()["error"]["code"], "RATE_LIMITED")
        self.assertIn("Retry-After", resp.headers)

    def test_session_absolute_15_minute_expiry(self):
        """Xác minh phiên làm việc hết hạn sau 15 phút bị từ chối qua verify_auth_session."""
        now = timezone.now()
        session = AuthSession.objects.create(
            user=self.user,
            session_key="session_test_exp",
            expires_at=now + timedelta(seconds=900)
        )
        # Cập nhật created_at và expires_at qua QuerySet.update() thỏa mãn constraint ck_auth_sessions_expires_after_created
        AuthSession.objects.filter(pk=session.pk).update(
            created_at=now - timedelta(minutes=20),
            expires_at=now - timedelta(minutes=5)
        )
        is_valid, _, reason = verify_auth_session("session_test_exp", user=self.user)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "SESSION_EXPIRED")

    def test_session_is_not_sliding_window(self):
        """Xác minh gọi verify_auth_session không gia hạn expires_at (no sliding)."""
        now = timezone.now()
        original_expiry = now + timedelta(seconds=900)
        session = AuthSession.objects.create(
            user=self.user,
            session_key="session_no_sliding",
            expires_at=original_expiry
        )

        is_valid, s, _ = verify_auth_session("session_no_sliding", user=self.user)
        self.assertTrue(is_valid)
        session.refresh_from_db()
        self.assertEqual(session.expires_at, original_expiry)

    def test_logout_revokes_auth_session_in_registry(self):
        """Xác minh đăng xuất thành công thu hồi AuthSession (revoked_at được thiết lập)."""
        csrf = self._get_csrf_token()
        self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        session_key = self.client.session.session_key
        auth_session = AuthSession.objects.get(session_key=session_key)
        self.assertTrue(auth_session.is_active)

        csrf_logged_in = self.client.cookies["csrftoken"].value
        resp = self.client.post(
            "/api/v1/auth/logout/",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_logged_in
        )
        self.assertEqual(resp.status_code, 200)

        auth_session.refresh_from_db()
        self.assertFalse(auth_session.is_active)
        self.assertIsNotNone(auth_session.revoked_at)
        self.assertEqual(auth_session.revoke_reason, "USER_LOGOUT")

    def test_logout_records_audit_log(self):
        """Xác minh đăng xuất ghi nhận nhật ký AuditLog sự kiện USER_LOGOUT với actor_user."""
        csrf = self._get_csrf_token()
        self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        csrf_logged_in = self.client.cookies["csrftoken"].value
        self.client.post(
            "/api/v1/auth/logout/",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_logged_in
        )
        log = AuditLog.objects.filter(action="USER_LOGOUT", outcome="SUCCESS").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor_user, self.user)

    def test_cookie_replay_after_logout_is_denied(self):
        """Xác minh cố tình dùng lại cookie session sau khi đã đăng xuất sẽ bị từ chối 401."""
        csrf = self._get_csrf_token()
        self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        session_cookie = self.client.cookies["sessionid"].value
        csrf_logged_in = self.client.cookies["csrftoken"].value

        # Đăng xuất
        self.client.post(
            "/api/v1/auth/logout/",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_logged_in
        )

        # Tạo client mới giả lập kẻ tấn công replay cookie cũ
        replay_client = Client(enforce_csrf_checks=True)
        replay_client.cookies["sessionid"] = session_cookie
        replay_csrf = replay_client.get("/api/v1/auth/csrf/").json()["csrf_token"]

        # Gọi endpoint yêu cầu xác thực
        resp = replay_client.post(
            "/api/v1/auth/logout/",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=replay_csrf
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["code"], "NOT_AUTHENTICATED")

    def test_must_change_password_blocks_access_to_other_apis(self):
        """
        Xác minh khi user có must_change_password=True:
        Truy cập endpoint nghiệp vụ khác bị chặn với 403 MUST_CHANGE_PASSWORD,
        nhưng vẫn được phép gọi logout và csrf.
        """
        self.user.must_change_password = True
        self.user.save(update_fields=["must_change_password"])

        csrf = self._get_csrf_token()
        self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        csrf_logged = self.client.cookies["csrftoken"].value

        # Giả lập request qua middleware
        rf = RequestFactory()
        req = rf.get("/api/v1/audit/logs/")
        req.user = self.user
        req.session = self.client.session

        middleware = SessionRegistryAndMustChangeMiddleware(lambda r: None)
        resp = middleware(req)
        self.assertEqual(resp.status_code, 403)
        data = json.loads(resp.content)
        self.assertIn("đổi mật khẩu", data["error"]["message"])

        # Nhưng vẫn được phép đăng xuất mà không bị chặn
        logout_resp = self.client.post(
            "/api/v1/auth/logout/",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_logged
        )
        self.assertEqual(logout_resp.status_code, 200)

    def test_session_revoked_on_user_lock_and_cannot_revive_after_unlock(self):
        """
        Xác minh lỗi Point 3: Phiên hợp lệ -> Khóa user -> Mở khóa user -> Phiên cũ PHẢI BỊ THU HỒI,
        tuyệt đối không được sống lại.
        """
        csrf = self._get_csrf_token()
        self.client.post(
            "/api/v1/auth/login/",
            {"username": "auth_test_user", "password": "SecurePassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        session_key = self.client.session.session_key
        auth_session = AuthSession.objects.get(session_key=session_key)
        self.assertTrue(auth_session.is_active)

        # Khóa tài khoản
        self.user.status = User.Status.LOCKED
        self.user.save(update_fields=["status"])

        # Kiểm tra AuthSession đã bị thu hồi ngay lập tức
        auth_session.refresh_from_db()
        self.assertFalse(auth_session.is_active)
        self.assertIsNotNone(auth_session.revoked_at)
        self.assertEqual(auth_session.revoke_reason, "USER_LOCKED")

        # Mở khóa lại tài khoản
        self.user.status = User.Status.ACTIVE
        self.user.save(update_fields=["status"])

        # Đối chiếu phiên cũ: Vẫn phải bị từ chối với lý do SESSION_REVOKED
        is_valid, _, reason = verify_auth_session(session_key, user=self.user)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "SESSION_REVOKED")

    def test_user_locked_between_authenticate_and_establish_session_is_rejected(self):
        """
        Xác minh Point 3: Nếu tài khoản bị khóa trong khoảng trống giữa lúc xác thực xong
        và lúc tạo phiên, establish_session() đọc lại DB qua select_for_update() và từ chối tạo phiên.
        """
        from apps.accounts.services.auth import establish_session

        # Giả lập User lúc authenticate() là ACTIVE, nhưng trước establish_session() bị khóa
        self.user.status = User.Status.LOCKED
        self.user.save(update_fields=["status"])

        rf = RequestFactory()
        req = rf.post("/api/v1/auth/login/")
        req.session = self.client.session

        # establish_session phải ném PermissionDenied khi phát hiện User đã bị khóa
        with self.assertRaises(PermissionDenied):
            establish_session(req, self.user)

        # Không tạo bất kỳ session nào trong AuthSession
        self.assertEqual(AuthSession.objects.filter(user=self.user).count(), 0)

    def test_session_rejected_if_user_has_no_role(self):
        """Xác minh lỗi Point 3: Phiên của user không có vai trò hợp lệ bị từ chối và thu hồi."""
        # Xóa nhóm vai trò khỏi user
        self.user.groups.clear()

        now = timezone.now()
        session = AuthSession.objects.create(
            user=self.user,
            session_key="session_no_role",
            expires_at=now + timedelta(seconds=900)
        )
        is_valid, _, reason = verify_auth_session("session_no_role", user=self.user)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "USER_NO_ROLE")

        session.refresh_from_db()
        self.assertIsNotNone(session.revoked_at)

    def test_establish_session_rollback_on_registry_failure(self):
        """Xác minh Point 4: Lỗi tại bước tạo registry dẫn tới rollback toàn bộ transaction."""
        from apps.accounts.services.auth import establish_session

        rf = RequestFactory()
        req = rf.post("/api/v1/auth/login/")
        req.session = self.client.session

        with patch("apps.accounts.services.auth.create_auth_session", side_effect=RuntimeError("Simulated Registry DB Error")):
            with self.assertRaises(RuntimeError):
                establish_session(req, self.user)

        # Không có bản ghi AuthSession nào mồ côi
        self.assertEqual(AuthSession.objects.filter(user=self.user).count(), 0)

    def test_establish_session_rollback_on_audit_failure(self):
        """Xác minh Point 4: Lỗi tại bước ghi audit log dẫn tới rollback AuthSession và session."""
        from apps.accounts.services.auth import establish_session

        rf = RequestFactory()
        req = rf.post("/api/v1/auth/login/")
        req.session = self.client.session

        with patch("apps.audit.services.AuditService.record_event", side_effect=RuntimeError("Simulated Audit Failure")):
            with self.assertRaises(RuntimeError):
                establish_session(req, self.user)

        # Toàn bộ AuthSession bị rollback
        self.assertEqual(AuthSession.objects.filter(user=self.user).count(), 0)

    def test_password_changed_between_authenticate_and_establish_session_is_rejected(self):
        """
        Xác minh P1: Nếu mật khẩu bị thay đổi trong khoảng trống giữa lúc xác thực xong
        và lúc tạo phiên, establish_session() đối chiếu locked_user.password với user.password
        và ném PermissionDenied, ngăn chặn cấp phiên bằng mật khẩu cũ.
        """
        from apps.accounts.services.auth import establish_session

        old_password_hash = self.user.password

        # Đổi mật khẩu trong database
        self.user.set_password("BrandNewPassword@2026!")
        self.user.save(update_fields=["password"])

        # Tạo đối tượng user đại diện cho kết quả authenticate() trước đó (mang hash cũ)
        user_before_change = User.objects.get(id=self.user.id)
        user_before_change.password = old_password_hash

        rf = RequestFactory()
        req = rf.post("/api/v1/auth/login/")
        req.session = self.client.session

        with self.assertRaises(PermissionDenied):
            establish_session(req, user_before_change)

        # Không tạo bất kỳ session nào trong AuthSession
        self.assertEqual(AuthSession.objects.filter(user=self.user).count(), 0)

    def test_invalid_username_format_returns_400_not_500(self):
        """
        Xác minh P2: Gửi username chứa ký tự nhạy cảm hoặc sai định dạng (ví dụ 'password=sample')
        bị chặn ở tầng Serializer với HTTP 400 (VALIDATION_ERROR), không gây crash HTTP 500 tại AuditService.
        """
        csrf = self._get_csrf_token()
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"username": "password=sample", "password": "AnyPassword@2026!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "VALIDATION_ERROR")

    def test_sensitive_string_in_username_direct_auth_does_not_raise_500(self):
        """
        Xác minh P2 Defense-in-depth: Lời gọi authenticate_credentials trực tiếp với username
        chứa chuỗi nhạy cảm ('password=sample') sử dụng câu mẫu cố định và lọc metadata,
        hoàn toàn không làm bung lỗi 500 từ AuditService.
        """
        from apps.accounts.services.auth import authenticate_credentials

        rf = RequestFactory()
        req = rf.post("/api/v1/auth/login/")

        user, error_code, _ = authenticate_credentials(
            req,
            username="password=sample",
            password="AnyPassword@2026!"
        )
        self.assertIsNone(user)
        self.assertEqual(error_code, "INVALID_CREDENTIALS")
```

---

## 5. Bảng hàm, biến và trường dữ liệu (Symbols & Specifications)

| Tên biểu tượng | Thuộc file | Kiểu / Loại | Ý nghĩa / Tác dụng phụ |
|:---|:---|:---:|:---|
| `RATE_LIMIT_LOGIN_MAX_USERNAME` | `rate_limit.py` | `int` (Const) | Ngưỡng 5 lần thử thất bại cho một tên đăng nhập |
| `RATE_LIMIT_LOGIN_MAX_IP` | `rate_limit.py` | `int` (Const) | Ngưỡng 20 lần thử thất bại cho một địa chỉ IP |
| `RATE_LIMIT_WINDOW_SECONDS` | `rate_limit.py` | `int` (Const) | Khung thời gian giới hạn 900 giây (15 phút) |
| `get_client_ip(request)` | `rate_limit.py` | Function | Trích xuất IP; mặc định `REMOTE_ADDR`, chỉ tin `X-Forwarded-For` khi proxy tin cậy |
| `hash_identifier(val, purpose)` | `rate_limit.py` | Function | Băm HMAC-SHA256 bảo vệ username/IP trước khi ghi DB |
| `acquire_rate_limit_advisory_locks` | `rate_limit.py` | Function | Khóa PostgreSQL Transaction Advisory Locks chống race condition đồng thời |
| `check_rate_limit(user, ip)` | `rate_limit.py` | Function | Kiểm tra ngưỡng vi phạm, tính toán `Retry-After`, loại trừ `RATE_LIMITED` |
| `record_login_attempt(...)` | `rate_limit.py` | Function | Ghi bản ghi `LoginAttempt` vào database với HMAC key |
| `create_auth_session(user, req, key)` | `sessions.py` | Function | Tạo bản ghi Registry phiên (`sid` UUID v4, hạn tuyệt đối 15 phút) |
| `verify_auth_session(key, user)` | `sessions.py` | Function | Xác minh hiệu lực phiên: chưa revoked, còn hạn 15m, user active và có single-role |
| `revoke_auth_session(key, reason)` | `sessions.py` | Function | Thu hồi 1 phiên cụ thể qua `session_key` |
| `revoke_all_user_sessions(user, reason)` | `sessions.py` | Function | Thu hồi toàn bộ phiên của một người dùng |
| `revoke_sessions_on_user_status_change` | `sessions.py` | Signal Receiver | Post-save signal trên `User`: tự động thu hồi mọi phiên khi tài khoản bị khóa/hủy |
| `lock_user(user, reason)` | `sessions.py` | Function | Khóa tài khoản qua `select_for_update()` và thu hồi toàn bộ phiên |
| `archive_user(user, reason)` | `sessions.py` | Function | Hủy/lưu trữ tài khoản qua `select_for_update()` và thu hồi toàn bộ phiên |
| `authenticate_credentials(...)` | `auth.py` | Function | Giữ advisory lock xuyên suốt kiểm tra, authenticate và ghi attempt |
| `establish_session(req, user)` | `auth.py` | Function | Khóa row User (select_for_update), bọc toàn bộ login() + registry + audit trong atomic |
| `logout_session(req)` | `auth.py` | Function | Thu hồi registry, ghi AuditLog `USER_LOGOUT`, gọi Django `logout()` và xoay CSRF |
| `LoginRequestSerializer` | `serializers/auth.py` | Serializer | Validate username/password (`trim_whitespace=False` bảo toàn khoảng trắng) |
| `UserSummarySerializer` | `serializers/auth.py` | Serializer | Chuẩn hóa thông tin người dùng và vai trò trả về sau login |
| `RegistrySessionAuthentication` | `authentication.py` | DRF Auth | Kế thừa SessionAuthentication, định nghĩa `authenticate_header()` trả 401 |
| `SessionRegistryAndMustChangeMiddleware` | `middleware.py` | Middleware | Bỏ qua registry check cho PUBLIC_AUTH_PATHS, bảo vệ `must_change_password` |
| `CsrfTokenView` | `views/auth.py` | APIView | GET `/api/v1/auth/csrf/`: `authentication_classes = []`, gắn cookie và trả CSRF token |
| `LoginView` | `views/auth.py` | APIView | POST `/api/v1/auth/login/`: `authentication_classes = []`, rate limit, kiểm tra CSRF |
| `LogoutView` | `views/auth.py` | APIView | POST `/api/v1/auth/logout/`: Đăng xuất thu hồi phiên trong registry |

---

## 6. Lệnh PowerShell để bạn tự chạy

> [!IMPORTANT]
> **Khắc phục lỗi cú pháp dòng 325 trong test Patch 3 (`test_models_roles.py`) trước khi chạy test:**
> Dòng 325 của `backend/apps/accounts/tests/test_models_roles.py` đang dùng toán tử `and` thay vì dấu phẩy giữa hai context manager nhiều dòng:
> ```python
> # Cũ (sai):
> with (patch.object(User, "save", side_effect=RuntimeError("Simulated DB Crash")) 
>     and self.assertRaises(RuntimeError)):
> # Mới (đúng):
> with (patch.object(User, "save", side_effect=RuntimeError("Simulated DB Crash")), self.assertRaises(RuntimeError)):
> ```
> Lệnh PowerShell 1 dòng để sửa nhanh (dùng `-Raw` để quét qua nhiều dòng):
> ```powershell
> (Get-Content -Raw backend/apps/accounts/tests/test_models_roles.py) -replace 'with \(patch\.object\(User, "save", side_effect=RuntimeError\("Simulated DB Crash"\)\)\s+and self\.assertRaises\(RuntimeError\)\):', 'with (patch.object(User, "save", side_effect=RuntimeError("Simulated DB Crash")), self.assertRaises(RuntimeError)):' | Set-Content backend/apps/accounts/tests/test_models_roles.py
> ```

### Bước 1: Tạo các thư mục mới cho services, serializers và views của accounts
```powershell
New-Item -ItemType Directory -Force -Path backend/apps/accounts/services
New-Item -ItemType Directory -Force -Path backend/apps/accounts/serializers
New-Item -ItemType Directory -Force -Path backend/apps/accounts/views
```

### Bước 2: Copy lần lượt 14 file theo bảng ở Mục 2 vào vị trí tương ứng

### Bước 3: Chạy toàn bộ bộ test kiểm tra (Yêu cầu chính xác 91/91 tests PASS)
```powershell
python backend/manage.py test apps.core apps.audit apps.accounts
```
*(Nếu chạy trên SQLite trong bộ nhớ, kết quả đạt **91 tests chạy, 91 OK, 0 failures, 0 errors**)*

---

## 7. Acceptance Checklist và Câu hỏi tự kiểm tra

### Checklist nghiệm thu:
- [x] **Advisory Lock Xuyên Suốt**: `authenticate_credentials` giữ khóa transaction advisory lock (`pg_advisory_xact_lock`) liên tục qua cả 3 giai đoạn: kiểm tra ngưỡng -> xác thực mật khẩu -> ghi attempt.
- [x] **Public Auth Stale Cookie Exemption**: `CsrfTokenView` và `LoginView` đặt `authentication_classes = []`, và `SessionRegistryAndMustChangeMiddleware` bỏ qua `PUBLIC_AUTH_PATHS`, cho phép client mang cookie cũ/hết hạn vẫn lấy được CSRF token mới và đăng nhập lại bình thường mà không bị 401 chặn trước.
- [x] **User Row Lock & Password Race Guard (P1)**: `establish_session()` dùng `User.objects.select_for_update()` đọc lại database ngay trong transaction tạo phiên, bọc trọn `login()`. Đối chiếu `locked_user.password != user.password`; nếu mật khẩu bị đổi giữa lúc xác thực và cấp phiên hoặc tài khoản bị khóa, ném `PermissionDenied` và rollback toàn bộ, trả HTTP 401 chung.
- [x] **Malformed Username & Audit Template Guard (P2)**: `LoginRequestSerializer` áp dụng regex `^[a-zA-Z0-9._-]{3,64}$` chặn username sai định dạng trả 400. `authenticate_credentials` tuân thủ Rule 1 AuditService (dùng template cố định cho summary) và lọc chuỗi nhạy cảm khỏi metadata, triệt tiêu nguy cơ HTTP 500.
- [x] **Anti-Revival Guard**: Bảng `AuthSession` tự động thu hồi ngay khi user chuyển sang `status != ACTIVE` qua Django signal `post_save` và `lock_user()` qua `select_for_update()`. Phiên cũ vĩnh viễn không thể tái sinh sau khi mở khóa.
- [x] **AuditService Contract**: Toàn bộ lời gọi audit trong auth dùng `actor_user`, `actor_type`, `target_type="USER"`, `target_id`, `ip_address`, và metadata chỉ dùng các khóa trong allowlist (`ip_address`, `reason`, `attempted_username`, `session_id`).
- [x] **Test Assertions & auto_now_add**: `test_must_change_password_blocks_access_to_other_apis` giải mã `json.loads(resp.content)` để kiểm tra tiếng Việt; các trường `auto_now_add` được cập nhật quá khứ qua `QuerySet.update()`.
- [x] **100% Tests Pass**: Toàn bộ 31 test cases của Patch 4 chạy sạch, tổng toàn dự án 91/91 tests PASS.

---

## 8. Gói gửi reviewer

- **File đã cập nhật**: `docs/TV3_plan/TV3_DJ_04_PATCH_04_AUTH_SESSION_CSRF.md`
- **Mã chặng**: TV3-DJ-04 (Bản vá toàn diện vòng 3)
- **Tổng số test sau chặng 04**: 91 tests (17 tests chặng 1 + 23 tests chặng 2 + 20 tests chặng 3 + 31 tests chặng 4).
- **Trạng thái**: READY_FOR_COPY.
