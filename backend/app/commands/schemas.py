"""
Config & Command Schemas, Validators & Serializers (Thành viên 2 phụ trách - Pha B)
Tuân thủ Đặc tả V1.3 (Mục 6.3, 6.4, 7.2, 8.4, 8.5, 15.3):
- Kiểm tra chặt Idempotency-Key (1..128 ký tự).
- Kiểm tra cấu hình CONTROLLER (fan_off_c < fan_on_c, miền nhiệt độ -40..80, độ ẩm 0..100,
  telemetry_interval_ms 1000..60000, manual_timeout_s 10..300, extra={}).
- Sinh canonical_json và SHA-256 content_hash khớp tuyệt đối với fixture Mục 8.5.
- Kiểm tra lệnh điều khiển SET_MODE (AUTO / MANUAL duration_s 10..300) và SET_FAN (on: bool).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping
from uuid import UUID

from backend.app.devices.schemas import finite_float_or_none
from backend.app.models.command import Command, CommandAttempt
from backend.app.models.device import DeviceConfig
from backend.app.shadows.service import format_utc_iso
from backend.app.telemetry.schemas import parse_utc_timestamp


ALLOWED_CONTROLLER_CONFIG_KEYS = frozenset({
    "base_version",
    "fan_on_c",
    "fan_off_c",
    "temp_alert_c",
    "humidity_alert_pct",
    "temp_clear_margin_c",
    "humidity_clear_margin_pct",
    "telemetry_interval_ms",
    "manual_timeout_s",
    "extra",
})

REQUIRED_CONTROLLER_CONFIG_KEYS = frozenset({
    "base_version",
    "fan_on_c",
    "fan_off_c",
    "temp_alert_c",
    "humidity_alert_pct",
    "temp_clear_margin_c",
    "humidity_clear_margin_pct",
    "telemetry_interval_ms",
    "manual_timeout_s",
})

ALLOWED_COMMAND_BODY_KEYS = frozenset({"type", "params"})
ALLOWED_USER_COMMAND_TYPES = frozenset({"SET_MODE", "SET_FAN"})
ALLOWED_COMMAND_STATUSES = frozenset({
    "PENDING", "SENT", "APPLIED", "APPLIED_LATE", "REJECTED", "TIMEOUT", "FAILED", "CANCELLED"
})
ALLOWED_ALL_COMMAND_TYPES = frozenset({"SET_MODE", "SET_FAN", "APPLY_CONFIG"})
ALLOWED_COMMAND_LIST_QUERY_KEYS = frozenset({
    "status", "type", "from", "to", "page", "page_size", "order", "sort"
})


class CommandValidationError(ValueError):
    """Lỗi dữ liệu đầu vào của cấu hình hoặc lệnh điều khiển (HTTP 400 Bad Request)."""


class CommandConflictError(ValueError):
    """Lỗi xung đột trạng thái, phiên bản hoặc Idempotency-Key (HTTP 409 Conflict)."""
    def __init__(self, message: str = "Conflict", code: str = "CONFLICT"):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class ValidatedControllerConfigInput:
    base_version: int
    fan_on_c: int | float
    fan_off_c: int | float
    temp_alert_c: int | float
    humidity_alert_pct: int | float
    temp_clear_margin_c: int | float
    humidity_clear_margin_pct: int | float
    telemetry_interval_ms: int
    manual_timeout_s: int
    extra: dict
    request_hash: str


@dataclass(frozen=True)
class ValidatedCommandInput:
    command_type: str
    params: dict
    request_hash: str


@dataclass(frozen=True)
class CommandListQueryParams:
    status: str | None = None
    command_type: str | None = None
    from_utc: Any = None
    to_utc: Any = None
    page: int = 1
    page_size: int = 100
    order: str = "desc"


def validate_idempotency_key(raw_key: str | None) -> str:
    """Kiểm tra header Idempotency-Key bắt buộc cho các thao tác mutation (Mục 7.2)."""
    if raw_key is None or not isinstance(raw_key, str):
        raise CommandValidationError("Missing required Idempotency-Key header")
    cleaned = raw_key.strip()
    if not cleaned or len(cleaned) > 128:
        raise CommandValidationError("Idempotency-Key must be 1..128 characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        raise CommandValidationError("Idempotency-Key contains invalid control characters")
    return cleaned


def _require_strict_int(val: Any, field_name: str) -> int:
    if isinstance(val, bool) or not isinstance(val, int):
        raise CommandValidationError(f"{field_name} must be an integer")
    return int(val)


def _require_finite_number(val: Any, field_name: str) -> int | float:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise CommandValidationError(f"{field_name} must be a finite number")
    f_val = float(val)
    if not math.isfinite(f_val):
        raise CommandValidationError(f"{field_name} must be finite (no NaN/Infinity)")
    if f_val.is_integer():
        return int(f_val)
    return round(f_val, 4)


def compute_canonical_json_and_hash(payload: dict) -> tuple[str, str]:
    """Sinh chuỗi canonical JSON và mã băm SHA-256 theo đúng quy tắc Mục 8.5."""
    try:
        canonical_str = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CommandValidationError("Payload contains non-serializable or NaN/Infinity values") from exc
    digest = hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()
    return canonical_str, digest


def build_controller_config_snapshot(
    *,
    config_version: int,
    fan_on_c: int | float,
    fan_off_c: int | float,
    temp_alert_c: int | float,
    humidity_alert_pct: int | float,
    temp_clear_margin_c: int | float,
    humidity_clear_margin_pct: int | float,
    telemetry_interval_ms: int,
    manual_timeout_s: int,
    extra: dict | None = None,
) -> tuple[dict, str, str]:
    """Tạo snapshot cấu hình CONTROLLER P0 (Mục 8.5) cùng canonical_json và content_hash."""
    snapshot = {
        "schema_version": 3,
        "config_version": int(config_version),
        "profile": "CONTROLLER",
        "fan_binding_id": None,
        "fan_on_c": _require_finite_number(fan_on_c, "fan_on_c"),
        "fan_off_c": _require_finite_number(fan_off_c, "fan_off_c"),
        "temp_alert_c": _require_finite_number(temp_alert_c, "temp_alert_c"),
        "humidity_alert_pct": _require_finite_number(humidity_alert_pct, "humidity_alert_pct"),
        "temp_clear_margin_c": _require_finite_number(temp_clear_margin_c, "temp_clear_margin_c"),
        "humidity_clear_margin_pct": _require_finite_number(
            humidity_clear_margin_pct, "humidity_clear_margin_pct"
        ),
        "telemetry_interval_ms": int(telemetry_interval_ms),
        "manual_timeout_s": int(manual_timeout_s),
        "extra": extra if isinstance(extra, dict) else {},
    }
    canonical_json, content_hash = compute_canonical_json_and_hash(snapshot)
    return snapshot, canonical_json, content_hash


def validate_controller_config_payload(payload: Any) -> ValidatedControllerConfigInput:
    """Kiểm tra body JSON cho PUT /api/v1/devices/<device_id>/config (Mục 6.3 & 7.2)."""
    if not isinstance(payload, dict):
        raise CommandValidationError("Request body must be a JSON object")

    unknown_keys = set(payload.keys()) - ALLOWED_CONTROLLER_CONFIG_KEYS
    if unknown_keys:
        raise CommandValidationError(f"Unsupported config fields: {sorted(unknown_keys)}")

    missing_keys = REQUIRED_CONTROLLER_CONFIG_KEYS - set(payload.keys())
    if missing_keys:
        raise CommandValidationError(f"Missing required config fields: {sorted(missing_keys)}")

    base_version = _require_strict_int(payload["base_version"], "base_version")
    if base_version < 0:
        raise CommandValidationError("base_version must be >= 0")

    fan_on_c = _require_finite_number(payload["fan_on_c"], "fan_on_c")
    fan_off_c = _require_finite_number(payload["fan_off_c"], "fan_off_c")
    temp_alert_c = _require_finite_number(payload["temp_alert_c"], "temp_alert_c")
    humidity_alert_pct = _require_finite_number(payload["humidity_alert_pct"], "humidity_alert_pct")
    temp_clear_margin_c = _require_finite_number(
        payload["temp_clear_margin_c"], "temp_clear_margin_c"
    )
    humidity_clear_margin_pct = _require_finite_number(
        payload["humidity_clear_margin_pct"], "humidity_clear_margin_pct"
    )
    telemetry_interval_ms = _require_strict_int(
        payload["telemetry_interval_ms"], "telemetry_interval_ms"
    )
    manual_timeout_s = _require_strict_int(payload["manual_timeout_s"], "manual_timeout_s")

    # Kiểm tra miền giá trị vật lý (Mục 4.1 & 6.3)
    for name, val in [
        ("fan_on_c", fan_on_c),
        ("fan_off_c", fan_off_c),
        ("temp_alert_c", temp_alert_c),
    ]:
        if val < -40.0 or val > 80.0:
            raise CommandValidationError(f"{name} must be between -40 and 80 C")

    if fan_off_c >= fan_on_c:
        raise CommandValidationError("fan_off_c must be strictly less than fan_on_c")

    if humidity_alert_pct < 0.0 or humidity_alert_pct > 100.0:
        raise CommandValidationError("humidity_alert_pct must be between 0 and 100")

    if temp_clear_margin_c <= 0.0 or temp_clear_margin_c > 20.0:
        raise CommandValidationError("temp_clear_margin_c must be in (0, 20]")

    if humidity_clear_margin_pct <= 0.0 or humidity_clear_margin_pct > 50.0:
        raise CommandValidationError("humidity_clear_margin_pct must be in (0, 50]")

    if telemetry_interval_ms < 1000 or telemetry_interval_ms > 60000:
        raise CommandValidationError("telemetry_interval_ms must be between 1000 and 60000")

    if manual_timeout_s < 10 or manual_timeout_s > 300:
        raise CommandValidationError("manual_timeout_s must be between 10 and 300")

    extra = payload.get("extra", {})
    if not isinstance(extra, dict) or extra != {}:
        raise CommandValidationError("extra must be an empty object {} in P0 CORE")

    _, req_hash = compute_canonical_json_and_hash({
        "base_version": base_version,
        "fan_on_c": fan_on_c,
        "fan_off_c": fan_off_c,
        "temp_alert_c": temp_alert_c,
        "humidity_alert_pct": humidity_alert_pct,
        "temp_clear_margin_c": temp_clear_margin_c,
        "humidity_clear_margin_pct": humidity_clear_margin_pct,
        "telemetry_interval_ms": telemetry_interval_ms,
        "manual_timeout_s": manual_timeout_s,
        "extra": {},
    })

    return ValidatedControllerConfigInput(
        base_version=base_version,
        fan_on_c=fan_on_c,
        fan_off_c=fan_off_c,
        temp_alert_c=temp_alert_c,
        humidity_alert_pct=humidity_alert_pct,
        temp_clear_margin_c=temp_clear_margin_c,
        humidity_clear_margin_pct=humidity_clear_margin_pct,
        telemetry_interval_ms=telemetry_interval_ms,
        manual_timeout_s=manual_timeout_s,
        extra={},
        request_hash=req_hash,
    )


def validate_command_payload(
    payload: Any,
    *,
    default_manual_timeout_s: int = 60,
) -> ValidatedCommandInput:
    """Kiểm tra body JSON cho POST /api/v1/devices/<device_id>/commands (Mục 7.2 & 8.4)."""
    if not isinstance(payload, dict):
        raise CommandValidationError("Request body must be a JSON object")

    if set(payload.keys()) != ALLOWED_COMMAND_BODY_KEYS:
        raise CommandValidationError("Request body must contain exactly 'type' and 'params'")

    raw_type = payload.get("type")
    if not isinstance(raw_type, str) or not raw_type.strip():
        raise CommandValidationError("Command 'type' must be a non-empty string")
    cmd_type = raw_type.strip().upper()
    if cmd_type not in ALLOWED_USER_COMMAND_TYPES:
        raise CommandValidationError("Unsupported command type; allowed: SET_MODE, SET_FAN")

    raw_params = payload.get("params")
    if not isinstance(raw_params, dict):
        raise CommandValidationError("Command 'params' must be a JSON object")

    if cmd_type == "SET_MODE":
        allowed_keys = {"mode", "duration_s"}
        if not set(raw_params.keys()).issubset(allowed_keys) or "mode" not in raw_params:
            raise CommandValidationError("SET_MODE params only allow 'mode' and optional 'duration_s'")

        raw_mode = raw_params.get("mode")
        if not isinstance(raw_mode, str):
            raise CommandValidationError("mode must be a string")
        mode = raw_mode.strip().upper()
        if mode not in ("AUTO", "MANUAL"):
            raise CommandValidationError("mode must be 'AUTO' or 'MANUAL'")

        if mode == "MANUAL":
            raw_dur = raw_params.get("duration_s", default_manual_timeout_s)
            duration_s = _require_strict_int(raw_dur, "duration_s")
            if duration_s < 10 or duration_s > 300:
                raise CommandValidationError("duration_s must be between 10 and 300 seconds")
            normalized_params = {"mode": "MANUAL", "duration_s": duration_s}
        else:
            if "duration_s" in raw_params and raw_params["duration_s"] is not None:
                raise CommandValidationError("duration_s is not applicable when mode is AUTO")
            normalized_params = {"mode": "AUTO"}

    else:
        # SET_FAN
        if set(raw_params.keys()) != {"on"}:
            raise CommandValidationError("SET_FAN params must contain only boolean 'on'")
        on_val = raw_params.get("on")
        if not isinstance(on_val, bool):
            raise CommandValidationError("SET_FAN 'on' parameter must be a boolean")
        normalized_params = {"on": on_val}

    _, req_hash = compute_canonical_json_and_hash({
        "type": cmd_type,
        "params": normalized_params,
    })

    return ValidatedCommandInput(
        command_type=cmd_type,
        params=normalized_params,
        request_hash=req_hash,
    )


def parse_command_list_query_params(args: Mapping[str, str]) -> CommandListQueryParams:
    """Xác thực tham số truy vấn cho GET /api/v1/devices/<device_id>/commands."""
    for key in args.keys():
        if key not in ALLOWED_COMMAND_LIST_QUERY_KEYS:
            raise CommandValidationError(f"Unsupported query parameter: {key}")

    status = args.get("status")
    if status is not None:
        status = status.strip().upper()
        if status not in ALLOWED_COMMAND_STATUSES:
            raise CommandValidationError("Invalid command status filter")

    cmd_type = args.get("type")
    if cmd_type is not None:
        cmd_type = cmd_type.strip().upper()
        if cmd_type not in ALLOWED_ALL_COMMAND_TYPES:
            raise CommandValidationError("Invalid command type filter")

    raw_from = args.get("from")
    raw_to = args.get("to")
    try:
        from_utc = parse_utc_timestamp(raw_from, "from") if raw_from is not None else None
        to_utc = parse_utc_timestamp(raw_to, "to") if raw_to is not None else None
    except ValueError as exc:
        raise CommandValidationError(str(exc)) from exc

    if from_utc is not None and to_utc is not None and from_utc >= to_utc:
        raise CommandValidationError("'from' must be earlier than 'to'")

    try:
        page = int(args.get("page", "1"))
        page_size = int(args.get("page_size", "100"))
    except (ValueError, TypeError) as exc:
        raise CommandValidationError("page and page_size must be integers") from exc

    if page < 1:
        raise CommandValidationError("page must be >= 1")
    if page_size < 1 or page_size > 500:
        raise CommandValidationError("page_size must be between 1 and 500")

    raw_order = args.get("order") or args.get("sort") or "desc"
    order = raw_order.strip().lower()
    if order not in ("asc", "desc"):
        raise CommandValidationError("order must be 'asc' or 'desc'")

    return CommandListQueryParams(
        status=status,
        command_type=cmd_type,
        from_utc=from_utc,
        to_utc=to_utc,
        page=page,
        page_size=page_size,
        order=order,
    )


def serialize_device_config(cfg: DeviceConfig | None) -> dict | None:
    if cfg is None:
        return None
    payload = {
        "id": str(cfg.id),
        "device_id": cfg.device_id,
        "version": int(cfg.version),
        "schema_version": int(cfg.schema_version),
        "profile": cfg.profile,
        "fan_binding_id": str(cfg.fan_binding_id) if cfg.fan_binding_id else None,
        "telemetry_interval_ms": int(cfg.telemetry_interval_ms),
        "extra": cfg.extra if isinstance(cfg.extra, dict) else {},
        "canonical_json": cfg.canonical_json,
        "content_hash": cfg.content_hash,
        "created_by": str(cfg.created_by) if cfg.created_by else None,
        "created_at": format_utc_iso(cfg.created_at),
    }
    if cfg.profile == "CONTROLLER":
        payload.update({
            "fan_on_c": finite_float_or_none(cfg.fan_on_c),
            "fan_off_c": finite_float_or_none(cfg.fan_off_c),
            "temp_alert_c": finite_float_or_none(cfg.temp_alert_c),
            "humidity_alert_pct": finite_float_or_none(cfg.humidity_alert_pct),
            "temp_clear_margin_c": finite_float_or_none(cfg.temp_clear_margin_c),
            "humidity_clear_margin_pct": finite_float_or_none(cfg.humidity_clear_margin_pct),
            "manual_timeout_s": int(cfg.manual_timeout_s) if cfg.manual_timeout_s is not None else None,
        })
    else:
        payload.update({
            "rpm_window_ms": cfg.rpm_window_ms,
            "pulses_per_revolution": cfg.pulses_per_revolution,
            "startup_grace_ms": cfg.startup_grace_ms,
            "no_rotation_persistence_ms": cfg.no_rotation_persistence_ms,
            "stop_grace_ms": cfg.stop_grace_ms,
            "controller_heartbeat_timeout_ms": cfg.controller_heartbeat_timeout_ms,
        })
    return payload


def serialize_command_attempt(attempt: CommandAttempt) -> dict:
    return {
        "id": str(attempt.id),
        "command_id": str(attempt.command_id),
        "attempt_no": int(attempt.attempt_no),
        "dispatch_started_at": format_utc_iso(attempt.dispatch_started_at),
        "broker_confirmed_at": format_utc_iso(attempt.broker_confirmed_at),
        "ack_received_at": format_utc_iso(attempt.ack_received_at),
        "outcome": attempt.outcome,
        "error_code": attempt.error_code,
        "worker_id": attempt.worker_id,
    }


def serialize_command(
    cmd: Command,
    *,
    attempts_list: list[CommandAttempt] | None = None,
) -> dict:
    data = {
        "command_id": str(cmd.command_id),
        "device_id": cmd.device_id,
        "requested_by": str(cmd.requested_by) if cmd.requested_by else None,
        "source": cmd.source,
        "requesting_session_id": str(cmd.requesting_session_id) if cmd.requesting_session_id else None,
        "type": cmd.type,
        "params": cmd.params if isinstance(cmd.params, dict) else {},
        "config_version": cmd.config_version,
        "expected_boot_id": str(cmd.expected_boot_id) if cmd.expected_boot_id else None,
        "expected_connection_id": str(cmd.expected_connection_id) if cmd.expected_connection_id else None,
        "device_command_seq": int(cmd.device_command_seq),
        "idempotency_key": cmd.idempotency_key,
        "status": cmd.status,
        "requested_at": format_utc_iso(cmd.requested_at),
        "expires_at": format_utc_iso(cmd.expires_at),
        "sent_at": format_utc_iso(cmd.sent_at),
        "ack_at": format_utc_iso(cmd.ack_at),
        "executed_at": format_utc_iso(cmd.executed_at),
        "attempts": int(cmd.attempts or 0),
        "result": cmd.result if isinstance(cmd.result, dict) else None,
        "error_code": cmd.error_code,
    }
    if attempts_list is not None:
        data["attempt_history"] = [serialize_command_attempt(a) for a in attempts_list]
    return data
