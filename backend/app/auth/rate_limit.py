"""
PostgreSQL Distributed Rate Limiting Engine (TV3-03B)
Chịu trách nhiệm:
1. Tạo khóa định danh phân tách domain (HMAC-SHA256 với AUTH_LOG_HMAC_KEY) cho username và IP.
2. Đồng bộ hóa kiểm soát truy cập đồng thời qua PostgreSQL Transaction Advisory Locks (pg_advisory_xact_lock).
3. Đếm số lần đăng nhập thất bại trong cửa sổ trượt 15 phút (900 giây).
4. Tính toán chính xác thời gian chờ Retry-After cho HTTP 429 Too Many Requests.
5. Ghi nhận lịch sử thử đăng nhập vào bảng login_attempts (không chứa username/password thô).
"""
from __future__ import annotations

import hashlib
import hmac
import math
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from flask import current_app, has_app_context
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.auth.messages import AuthFailureCode
from backend.app.models.iam import LoginAttempt
from backend.app.security.messages import ErrorMessage

# Cấu hình ngưỡng rate limit theo đặc tả TV3
RATE_LIMIT_WINDOW_SECONDS = 900  # 15 phút
MAX_FAILED_ATTEMPTS_USERNAME = 5  # Tối đa 5 lần thử sai cho cùng một tài khoản
MAX_FAILED_ATTEMPTS_IP = 20  # Tối đa 20 lần thử sai cho cùng một địa chỉ IP
RATE_LIMIT_PURPOSE = "login"  # Phân tách domain cho mục đích đăng nhập
RATE_LIMIT_PURPOSE_CHANGE_PASSWORD = "change-password"  # Phân tách domain cho mục đích đổi mật khẩu (TV3-03C)



def compute_rate_limit_keys(
        normalized_username: str,
        client_ip: str,
        purpose: str = RATE_LIMIT_PURPOSE,
        secret_key: str | None = None,
) -> tuple[str, str]:
    """Tạo cặp khóa HMAC-SHA256 định danh tài khoản và địa chỉ IP có phân tách domain.

    Quy tắc bảo mật:
    - Sử dụng bí mật AUTH_LOG_HMAC_KEY từ cấu hình ứng dụng hoặc biến môi trường.
    - Tiền tố domain f"{purpose}:username:{username}" và f"{purpose}:ip:{ip}" giúp
      các bước TV3-03C sau này (đổi mật khẩu) sử dụng chung bảng mà không bị xung đột khóa.
    - Trả về 2 chuỗi hex 64 ký tự an toàn, không chứa thông tin thô (raw data).
    """
    secret = secret_key
    if not secret:
        if has_app_context():
            secret = current_app.config.get("AUTH_LOG_HMAC_KEY")
        if not secret:
            secret = os.getenv("AUTH_LOG_HMAC_KEY")

    if not secret:
        raise ValueError(ErrorMessage.INVALID_CONFIG.format(name="AUTH_LOG_HMAC_KEY"))

    secret_bytes = secret.encode("utf-8")
    u_msg = f"{purpose}:username:{normalized_username}".encode("utf-8")
    ip_msg = f"{purpose}:ip:{client_ip}".encode("utf-8")

    username_key = hmac.new(secret_bytes, u_msg, hashlib.sha256).hexdigest()
    ip_key = hmac.new(secret_bytes, ip_msg, hashlib.sha256).hexdigest()

    return username_key, ip_key


def _key_to_lock_id(hex_key: str) -> int:
    """Chuyển đổi 16 ký tự đầu của khóa HMAC thành số nguyên có dấu 64-bit (bigint) của PostgreSQL."""
    return int.from_bytes(bytes.fromhex(hex_key[:16]), byteorder="big", signed=True)


def acquire_rate_limit_advisory_locks(
        db_session: Session,
        username_key: str,
        ip_key: str,
) -> None:
    """Khóa PostgreSQL Transaction Advisory Locks theo thứ tự tăng dần cố định.

    - Sử dụng hàm pg_advisory_xact_lock(bigint) để khóa tự động giải phóng khi transaction kết thúc (commit hoặc rollback).
    - Luôn sắp xếp các số định danh khóa theo thứ tự tăng dần (sorted) để loại trừ nguy cơ deadlock
      khi có nhiều request gửi đồng thời từ các luồng khác nhau.
    """
    k_user = _key_to_lock_id(username_key)
    k_ip = _key_to_lock_id(ip_key)

    unique_locks = sorted(list({k_user, k_ip}))
    for lock_id in unique_locks:
        db_session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id}
        )


def check_rate_limit(
        db_session: Session,
        username_key: str,
        ip_key: str,
        now: datetime | None = None,
) -> tuple[bool, int]:
    """Kiểm tra xem tên đăng nhập hoặc IP đã vượt quá ngưỡng thất bại trong 15 phút hay chưa.

    Quy tắc thực thi:
    1. Xác định mốc thời gian bắt đầu cửa sổ trượt: cutoff = now - 15 phút.
    2. Chỉ đếm các lần thử sai nghiệp vụ (success=False và failure_code != 'RATE_LIMITED').
       Việc loại trừ failure_code == 'RATE_LIMITED' đảm bảo rằng các request bị chặn sau đó
       KHÔNG kéo dài vô tận cửa sổ khóa của người dùng.
    3. Nếu số lần thử sai >= ngưỡng (5 cho user, 20 cho IP):
       - Tìm thời điểm của lần thử sai sớm nhất trong nhóm vi phạm.
       - Tính toán thời gian Retry-After = (mốc sớm nhất + 15 phút) - now.
       - Trả về (True, retry_after_giây).
    4. Nếu chưa chạm ngưỡng, trả về (False, 0).
    """
    now_utc = now if now is not None else datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    cutoff = now_utc - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)

    # 1. Đếm số lần thất bại của username
    user_fail_query = (
        select(LoginAttempt.occurred_at)
        .where(
            LoginAttempt.username_key == username_key,
            LoginAttempt.success.is_(False),
            LoginAttempt.failure_code != AuthFailureCode.RATE_LIMITED,
            LoginAttempt.occurred_at >= cutoff,
        )
        .order_by(LoginAttempt.occurred_at.asc())
    )
    user_failures = db_session.execute(user_fail_query).scalars().all()
    user_fail_count = len(user_failures)

    # 2. Đếm số lần thất bại của IP
    ip_fail_query = (
        select(LoginAttempt.occurred_at)
        .where(
            LoginAttempt.ip_key == ip_key,
            LoginAttempt.success.is_(False),
            LoginAttempt.failure_code != AuthFailureCode.RATE_LIMITED,
            LoginAttempt.occurred_at >= cutoff,
        )
        .order_by(LoginAttempt.occurred_at.asc())
    )
    ip_failures = db_session.execute(ip_fail_query).scalars().all()
    ip_fail_count = len(ip_failures)
    ip_fail_count = len(ip_failures)

    user_blocked = user_fail_count >= MAX_FAILED_ATTEMPTS_USERNAME
    ip_blocked = ip_fail_count >= MAX_FAILED_ATTEMPTS_IP

    if not user_blocked and not ip_blocked:
        return False, 0

    retry_seconds = 1
    # Tính thời gian còn lại đến khi lần vi phạm đầu tiên hết hạn 15 phút
    if user_blocked and user_failures:
        oldest_contributing = user_failures[0]
        if oldest_contributing.tzinfo is None:
            oldest_contributing = oldest_contributing.replace(tzinfo=timezone.utc)
        time_until_expiry = (oldest_contributing + timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)) - now_utc
        retry_seconds = max(retry_seconds, math.ceil(time_until_expiry.total_seconds()))

    if ip_blocked and ip_failures:
        oldest_contributing_ip = ip_failures[0]
        if oldest_contributing_ip.tzinfo is None:
            oldest_contributing_ip = oldest_contributing_ip.replace(tzinfo=timezone.utc)
        time_until_expiry_ip = (oldest_contributing_ip + timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)) - now_utc
        retry_seconds = max(retry_seconds, math.ceil(time_until_expiry_ip.total_seconds()))

    retry_seconds = max(1, min(RATE_LIMIT_WINDOW_SECONDS, retry_seconds))
    return True, retry_seconds


def record_login_attempt(
        db_session: Session,
        *,
        username_key: str,
        ip_key: str,
        user_id: UUID | None,
        success: bool,
        failure_code: str | None,
        request_id: UUID | str,
        occurred_at: datetime | None = None,
) -> LoginAttempt:
    """Thêm một bản ghi vào bảng login_attempts (không tự commit)."""
    req_uuid = UUID(str(request_id)) if not isinstance(request_id, UUID) else request_id
    attempt = LoginAttempt(
        id=uuid4(),
        username_key=username_key,
        ip_key=ip_key,
        user_id=user_id,
        success=success,
        failure_code=failure_code,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        request_id=req_uuid,
    )
    db_session.add(attempt)
    db_session.flush()
    return attempt
