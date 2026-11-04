from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4
from flask import g, has_request_context, request
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.models.logs import UserActivityLog
from backend.app.security.context import AuthContext
from backend.app.user_activity.messages import ActivityMessage
from backend.app.user_activity.catalog import (
    ALLOWED_ACTIONS,
    ALLOWED_ACTOR_TYPES,
    ALLOWED_OUTCOMES,
    get_activity_spec,
    render_summary,
    validate_and_sanitize_metadata,
    _check_string_for_secrets,
)


def _coerce_uuid(val: UUID | str | None) -> UUID | None:
    if val is None:
        return None
    if isinstance(val, UUID):
        return val
    try:
        return UUID(str(val))
    except (ValueError, TypeError) as exc:
        raise ValueError(ActivityMessage.LOG_INVALID_FIELD_TYPE.format(field="UUID", expected="valid UUID")) from exc


class UserActivityService:
    """Service ghi nhận nhật ký làm việc (user_activity_logs)."""

    @staticmethod
    def record(
        *,
        function_key: str,
        action: str,
        outcome: str,
        target_type: str | None = None,
        target_id: str | None = None,
        target_name_snapshot: str | None = None,
        device_id: str | None = None,
        summary_params: dict | None = None,
        actor: AuthContext | None = None,
        actor_user_id: UUID | str | None = None,
        actor_session_id: UUID | str | None = None,
        actor_type: str | None = None,
        actor_name_snapshot: str | None = None,
        request_id: UUID | str | None = None,
        correlation_id: UUID | str | None = None,
        related_audit_log_id: UUID | str | None = None,
        occurred_at: datetime | None = None,
        duration_ms: int | None = None,
        error_code: str | None = None,
        metadata: dict | None = None,
        db_session: Session,
    ) -> UserActivityLog:
        """Ghi nhận một bản ghi nhật ký làm việc.

        - db_session: Caller quản lý session; hàm này chỉ add và flush, KHÔNG commit/rollback.
        - actor: AuthContext đã xác minh hoặc actor hệ thống explicit (chỉ USER/SYSTEM).
        - summary: Bắt buộc render từ catalog server qua summary_params, không nhận summary thô.
        - metadata_: Lưu vào thuộc tính ORM metadata_, đã lọc qua allowlist phẳng.
        """
        if db_session is None:
            raise ValueError(ActivityMessage.LOG_SESSION_REQUIRED)

        # 1. Catalog spec validation
        spec = get_activity_spec(function_key)

        # 2. Async 202 contract validation (kiểm tra chuyên biệt trước kiểm tra outcome chung)
        if not outcome or not isinstance(outcome, str):
            raise ValueError(ActivityMessage.LOG_FIELD_REQUIRED.format(field="outcome"))
        out = outcome.strip()
        if spec.is_async and out == "SUCCESS":
            raise ValueError(ActivityMessage.LOG_ASYNC_OUTCOME_MISMATCH)

        # 3. Action validation
        if not action or not isinstance(action, str) or not action.strip():
            raise ValueError(ActivityMessage.LOG_FIELD_REQUIRED.format(field="action"))
        act = action.strip()
        if act not in ALLOWED_ACTIONS or act not in spec.allowed_actions:
            raise ValueError(ActivityMessage.LOG_INVALID_ACTION.format(action=act))

        # 4. Outcome validation
        if out not in ALLOWED_OUTCOMES or out not in spec.allowed_outcomes:
            raise ValueError(ActivityMessage.LOG_INVALID_OUTCOME.format(outcome=out))

        # 5. Actor validation & spoofing guard
        final_user_id = _coerce_uuid(actor_user_id)
        final_session_id = _coerce_uuid(actor_session_id)
        final_actor_type = actor_type

        if actor is not None:
            if not isinstance(actor, AuthContext):
                raise ValueError(ActivityMessage.LOG_INVALID_FIELD_TYPE.format(field="actor", expected="AuthContext"))
            # Chặn giả mạo: không cho phép override actor_type thành SYSTEM khi actor là USER
            if actor_type is not None and actor_type != actor.actor_type:
                raise ValueError(ActivityMessage.LOG_ACTOR_SPOOFING_DETECTED)
            # Chặn mâu thuẫn ID
            if final_user_id is not None and final_user_id != actor.user_id:
                raise ValueError(ActivityMessage.LOG_ACTOR_CONFLICT)
            if final_session_id is not None and final_session_id != actor.session_id:
                raise ValueError(ActivityMessage.LOG_ACTOR_CONFLICT)

            final_user_id = actor.user_id
            final_session_id = actor.session_id
            final_actor_type = actor.actor_type
            if request_id is None:
                request_id = actor.request_id
        else:
            # Caller không truyền actor: bắt buộc phải truyền actor_type tường minh
            if final_actor_type is None:
                raise ValueError(ActivityMessage.LOG_ACTOR_TYPE_REQUIRED)

        if final_actor_type not in ALLOWED_ACTOR_TYPES:
            raise ValueError(ActivityMessage.LOG_INVALID_ACTOR_TYPE.format(actor_type=final_actor_type))

        # Actor SYSTEM không được gắn user_id hoặc session_id của người dùng
        if final_actor_type == "SYSTEM":
            if final_user_id is not None or final_session_id is not None:
                raise ValueError(ActivityMessage.LOG_ACTOR_CONFLICT)

        # 6. Request ID resolution
        if request_id is None and has_request_context():
            request_id = getattr(g, "request_id", None) or getattr(request, "request_id", None)

        if request_id is None:
            raise ValueError(ActivityMessage.LOG_REQUEST_ID_REQUIRED)

        req_uuid = _coerce_uuid(request_id)
        corr_uuid = _coerce_uuid(correlation_id)
        audit_uuid = _coerce_uuid(related_audit_log_id)

        # 7. Sanitize snapshot and error strings
        if actor_name_snapshot is not None:
            if not isinstance(actor_name_snapshot, str) or len(actor_name_snapshot) > 128:
                raise ValueError(ActivityMessage.LOG_INVALID_FIELD_TYPE.format(field="actor_name_snapshot", expected="string <= 128 chars"))
            _check_string_for_secrets(actor_name_snapshot, "actor_name_snapshot")

        if target_name_snapshot is not None:
            if not isinstance(target_name_snapshot, str) or len(target_name_snapshot) > 128:
                raise ValueError(ActivityMessage.LOG_INVALID_FIELD_TYPE.format(field="target_name_snapshot", expected="string <= 128 chars"))
            _check_string_for_secrets(target_name_snapshot, "target_name_snapshot")

        if error_code is not None:
            if not isinstance(error_code, str) or len(error_code) > 64:
                raise ValueError(ActivityMessage.LOG_INVALID_FIELD_TYPE.format(field="error_code", expected="string <= 64 chars"))
            _check_string_for_secrets(error_code, "error_code")

        # 8. Summary rendering (Bắt buộc sinh từ template của server, không nhận summary thô)
        final_summary = render_summary(spec, summary_params, out)

        # 9. Metadata sanitization and allowlisting (mapped to ORM attribute metadata_)
        clean_metadata = validate_and_sanitize_metadata(spec, metadata)

        # 10. Create entry and flush
        entry = UserActivityLog(
            id=uuid4(),
            actor_user_id=final_user_id,
            actor_session_id=final_session_id,
            actor_type=final_actor_type,
            actor_name_snapshot=actor_name_snapshot.strip() if actor_name_snapshot is not None else None,
            function_key=spec.function_key[:80],
            action=act,
            target_type=str(target_type).strip() if target_type is not None else None,
            target_id=str(target_id).strip() if target_id is not None else None,
            target_name_snapshot=target_name_snapshot.strip() if target_name_snapshot is not None else None,
            device_id=str(device_id).strip()[:64] if device_id is not None else None,
            outcome=out,
            summary=final_summary,
            request_id=req_uuid,
            correlation_id=corr_uuid,
            related_audit_log_id=audit_uuid,
            occurred_at=occurred_at or func.now(),
            duration_ms=int(duration_ms) if duration_ms is not None else None,
            error_code=error_code.strip() if error_code is not None else None,
            metadata_=clean_metadata,
        )

        db_session.add(entry)
        db_session.flush()
        return entry