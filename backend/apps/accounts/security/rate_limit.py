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
    raw_payload = f"{purpose}:{value.strip().lower()}".encode()
    return hmac.new(key_bytes, raw_payload, hashlib.sha256).hexdigest()

def _key_to_lock_id(hex_key: str) -> int:
    """Chuyển đổi 16 ký tự đầu của khóa HMAC thành số nguyên có dấu 64-bit (bigint) của PostgreSQL."""
    return int.from_bytes(bytes.fromhex(hex_key[:16]), byteorder="big", signed=True)

def acquire_rate_limit_advisory_locks(username_key: str, ip_key: str) -> None:
    """
    Khóa PostgreSQL Transaction Advisory Locks (pg_advisory_xact_lock) theo thứ tự tăng dần cố định.
    - Tự động giải phóng khi transaction kết thúc (commit hoặc rollback).
    - Luôn sắp xếp khóa theo thứ tự tăng dần để loại trừ nguy cơ deadlock khi có nhiều request đồng thời.
    """
    if connection.vendor != "postgresql":
        return

    k_user = _key_to_lock_id(username_key)
    k_ip = _key_to_lock_id(ip_key)

    unique_locks = sorted({k_user, k_ip})
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