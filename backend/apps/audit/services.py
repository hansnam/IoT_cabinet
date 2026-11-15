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
        raise TypeError("Metadata bắt buộc phải là một từ điển (dict).")

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
            raise TypeError(f"Metadata không được chứa dictionary lồng nhau tại khóa '{k}'.")

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

        log_entry.save()
        return log_entry