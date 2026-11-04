from __future__ import annotations

import json
from uuid import UUID, uuid4
from flask import g, has_request_context, request
from sqlalchemy.orm import Session

from backend.app.models.logs import AuditLog
from backend.app.security.context import AuthContext
from backend.app.audit.messages import AuditMessage

ALLOWED_AUDIT_ACTOR_TYPES = frozenset({"USER", "SYSTEM", "SERVICE"})

FORBIDDEN_DETAIL_KEYS = frozenset({
    "password", "password_hash", "token", "access_token", "refresh_token",
    "secret", "secret_key", "jwt_secret_key", "authorization", "cookie",
    "credential", "mqtt_password", "dsn", "database_url", "uri",
    "raw_request", "raw_body", "body"
})

ALLOWED_AUDIT_DETAIL_KEYS = frozenset({
    "attempted_username", "created_username", "target_username", "username",
    "role", "role_code", "old_role_code", "access_level",
    "device_id", "location_id", "device_type", "credential_version",
    "operation_id", "command_id", "command_type", "config_version",
    "alert_id", "session_id", "target_session_id", "target_user_id",
    "ip_address", "failure_count", "row_count", "time_range", "fields",
    "reason", "scope", "error", "note"
})

DSN_MARKERS = ("postgresql://", "postgres://", "postgresql+", "mysql://", "sqlite://")

MAX_DETAIL_BYTES = 16384  # 16 KB max bounded payload

def _coerce_uuid(val: UUID | str | None) -> UUID | None:
    if val is None:
        return None
    if isinstance(val, UUID):
        return val
    try:
        return UUID(str(val))
    except (ValueError, TypeError) as exc:
        raise ValueError(AuditMessage.LOG_INVALID_FIELD_TYPE.format(field="UUID", expected="valid UUID")) from exc

def _check_string_for_secrets(val: str, key_name: str) -> None:
    """Kiểm tra chuỗi không chứa DSN hoặc token Bearer."""
    val_lower = val.lower()
    if any(marker in val_lower for marker in DSN_MARKERS):
        raise ValueError(AuditMessage.LOG_SENSITIVE_DATA_DETECTED.format(key=key_name))
    if "bearer " in val_lower:
        raise ValueError(AuditMessage.LOG_SENSITIVE_DATA_DETECTED.format(key=key_name))

def _validate_and_sanitize_detail(detail: dict | None) -> dict:
    """Kiểm tra và chuẩn hóa audit detail theo danh sách allowlist và cấu trúc phẳng.
    Từ chối các cấu trúc lồng nhau (dict/list) ngoại trừ trường danh sách tên cột 'fields'.
    """
    if detail is None:
        return {}

    if not isinstance(detail, dict):
        raise ValueError(AuditMessage.LOG_INVALID_FIELD_TYPE.format(field="detail", expected="dict"))

    clean: dict[str, str | int | float | bool | list[str]] = {}

    for raw_key, value in detail.items():
        k = str(raw_key).strip()
        k_lower = k.lower()

        # 1. Chặn khóa bí mật tuyệt đối
        if k_lower in FORBIDDEN_DETAIL_KEYS:
            raise ValueError(AuditMessage.LOG_SENSITIVE_DATA_DETECTED.format(key=k))

        # 2. Allowlist khóa được phép
        if k not in ALLOWED_AUDIT_DETAIL_KEYS:
            raise ValueError(AuditMessage.LOG_UNALLOWED_KEY.format(key=k, target="audit_detail"))

        # 3. Kiểm tra kiểu dữ liệu của giá trị (chặn nested dict)
        if isinstance(value, dict):
            raise ValueError(
                AuditMessage.LOG_INVALID_FIELD_TYPE.format(field=k, expected="scalar (nested dict forbidden)"))

        if isinstance(value, (list, tuple)):
            # Chỉ cho phép trường 'fields' chứa danh sách tên cột string phẳng
            if k != "fields":
                raise ValueError(
                    AuditMessage.LOG_INVALID_FIELD_TYPE.format(field=k, expected="scalar (nested list forbidden)"))
            if len(value) > 20:
                raise ValueError(AuditMessage.LOG_INVALID_FIELD_TYPE.format(field=k, expected="list with <= 20 items"))
            clean_list: list[str] = []
            for item in value:
                if not isinstance(item, str) or len(item) > 64:
                    raise ValueError(
                        AuditMessage.LOG_INVALID_FIELD_TYPE.format(field=k, expected="list of strings (<= 64 chars)"))
                _check_string_for_secrets(item, k)
                clean_list.append(item)
            clean[k] = clean_list
            continue

        if isinstance(value, str):
            if len(value) > 512:
                raise ValueError(AuditMessage.LOG_INVALID_FIELD_TYPE.format(field=k, expected="string <= 512 chars"))
            _check_string_for_secrets(value, k)
            clean[k] = value
        elif isinstance(value, UUID):
            clean[k] = str(value)
        elif isinstance(value, (int, float, bool)):
            clean[k] = value
        elif value is None:
            clean[k] = None
        else:
            raise ValueError(AuditMessage.LOG_INVALID_FIELD_TYPE.format(field=k, expected="string, number, boolean, or UUID"))

    # 4. Kiểm tra khả năng serialize JSON thực thụ (không dùng default=str)
    try:
        serialized = json.dumps(clean, ensure_ascii=False)
    except Exception as exc:
        raise ValueError(AuditMessage.LOG_PAYLOAD_SERIALIZATION_FAILED.format(field="detail")) from exc

    if len(serialized.encode("utf-8")) > MAX_DETAIL_BYTES:
        raise ValueError(AuditMessage.LOG_PAYLOAD_SERIALIZATION_FAILED.format(field="detail (exceeds 16KB)"))

    return clean

class AuditService:
    """Service ghi nhận kiểm toán nhạy cảm (audit_logs)."""

    @staticmethod
    def record(
            *,
            action: str,
            target_type: str,
            target_id: str,
            success: bool,
            request_id: UUID | str | None = None,
            actor: AuthContext | None = None,
            actor_user_id: UUID | str | None = None,
            actor_session_id: UUID | str | None = None,
            actor_type: str | None = None,
            actor_name_snapshot: str | None = None,
            device_id: str | None = None,
            reason_code: str | None = None,
            ip_address: str | None = None,
            detail: dict | None = None,
            db_session: Session,
    ) -> AuditLog:
        """Ghi nhận một bản ghi kiểm toán bảo mật.
        - db_session: Caller quản lý session; hàm này chỉ add và flush, KHÔNG commit/rollback.
        - actor: AuthContext đã xác minh hoặc thông tin định danh server tường minh.
        - detail: Giới hạn kích thước và từ chối các trường bí mật/nhạy cảm.
        """

        if db_session is None:
            raise ValueError(AuditMessage.LOG_SESSION_REQUIRED)

        if not action or not isinstance(action, str) or not action.strip():
            raise ValueError(AuditMessage.LOG_FIELD_REQUIRED.format(field="action"))
        if not target_type or not isinstance(target_type, str) or not target_type.strip():
            raise ValueError(AuditMessage.LOG_FIELD_REQUIRED.format(field="target_type"))
        if not target_id or not isinstance(target_id, str) or not target_id.strip():
            raise ValueError(AuditMessage.LOG_FIELD_REQUIRED.format(field="target_id"))
        if not isinstance(success, bool):
            raise ValueError(AuditMessage.LOG_INVALID_FIELD_TYPE.format(field="success", expected="boolean"))

        # Resolve actor attributes
        final_user_id = _coerce_uuid(actor_user_id)
        final_session_id = _coerce_uuid(actor_session_id)
        final_actor_type = actor_type

        if actor is not None:
            if not isinstance(actor, AuthContext):
                raise ValueError(
                    AuditMessage.LOG_INVALID_FIELD_TYPE.format(field="actor", expected="AuthContext"))
            # Chặn giả mạo: không cho phép override actor_type thành SYSTEM/SERVICE khi actor là USER
            if actor_type is not None and actor_type != actor.actor_type:
                raise ValueError(AuditMessage.LOG_ACTOR_SPOOFING_DETECTED)
            # Chặn mâu thuẫn ID
            if final_user_id is not None and final_user_id != actor.user_id:
                raise ValueError(AuditMessage.LOG_ACTOR_CONFLICT)
            if final_session_id is not None and final_session_id != actor.session_id:
                raise ValueError(AuditMessage.LOG_ACTOR_CONFLICT)

            final_user_id = actor.user_id
            final_session_id = actor.session_id
            final_actor_type = actor.actor_type
            if request_id is None:
                request_id = actor.request_id
        else:
            # Caller không truyền actor: bắt buộc phải truyền actor_type tường minh
            if final_actor_type is None:
                raise ValueError(AuditMessage.LOG_ACTOR_TYPE_REQUIRED)

        if final_actor_type not in ALLOWED_AUDIT_ACTOR_TYPES:
            raise ValueError(AuditMessage.LOG_INVALID_ACTOR_TYPE.format(actor_type=final_actor_type))

        # Actor hệ thống SYSTEM / SERVICE không được gắn user_id hoặc session_id của người dùng
        if final_actor_type in ("SYSTEM", "SERVICE"):
            if final_user_id is not None or final_session_id is not None:
                raise ValueError(AuditMessage.LOG_ACTOR_CONFLICT)

        # Resolve request_id
        if request_id is None and has_request_context():
            request_id = getattr(g, "request_id", None) or getattr(request, "request_id", None)

        if request_id is None:
            raise ValueError(AuditMessage.LOG_REQUEST_ID_REQUIRED)

        req_uuid = _coerce_uuid(request_id)

        # Resolve IP address
        final_ip = ip_address
        if final_ip is None and has_request_context():
            final_ip = getattr(g, "client_ip", None) or getattr(request, "remote_addr", None)

        # Sanitize reason_code
        if reason_code is not None:
            if not isinstance(reason_code, str) or len(reason_code) > 128:
                raise ValueError(
                    AuditMessage.LOG_INVALID_FIELD_TYPE.format(field="reason_code", expected="string <= 128 chars"))
            _check_string_for_secrets(reason_code, "reason_code")

        # Sanitize actor_name_snapshot
        if actor_name_snapshot is not None:
            if not isinstance(actor_name_snapshot, str) or len(actor_name_snapshot) > 128:
                raise ValueError(AuditMessage.LOG_INVALID_FIELD_TYPE.format(field="actor_name_snapshot",
                                                                            expected="string <= 128 chars"))
            _check_string_for_secrets(actor_name_snapshot, "actor_name_snapshot")

        # Sanitize and validate detail
        clean_detail = _validate_and_sanitize_detail(detail)

        entry = AuditLog(
            id=uuid4(),
            actor_user_id=final_user_id,
            actor_session_id=final_session_id,
            actor_type=final_actor_type,
            actor_name_snapshot=actor_name_snapshot.strip() if actor_name_snapshot is not None else None,
            device_id=str(device_id).strip() if device_id is not None else None,
            action=action.strip(),
            target_type=target_type.strip(),
            target_id=target_id.strip(),
            success=success,
            reason_code=reason_code.strip() if reason_code is not None else None,
            request_id=req_uuid,
            ip_address=final_ip,
            detail=clean_detail
        )

        db_session.add(entry)
        db_session.flush()
        return entry