"""
MQTT Payload Validators & Strict JSON Parser (Thành viên 2 phụ trách - Mục 8 & DATA-01)
Đảm bảo từ chối NaN/Infinity, kiểm tra schema_version=3, đối chiếu device_id topic==payload,
kiểm tra kiểu dữ liệu/dải đo hữu hạn và ràng buộc tách biệt profile CONTROLLER / FAN_MONITOR.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any

MAX_MQTT_PAYLOAD_BYTES = 2048
SHA256_HEX_REGEX = re.compile(r"^[0-9a-fA-F]{64}$")

CONTROLLER_FORBIDDEN_FIELDS = (
    "rpm",
    "rpm_min",
    "rpm_max",
    "pulse_count",
    "rpm_window_ms",
    "pulses_per_revolution",
    "demand_on",
    "demand_valid",
    "controller_signal_age_ms",
    "rpm_sensor_status",
    "fan_state",
)

MONITOR_FORBIDDEN_FIELDS = (
    "temperature_c",
    "humidity_pct",
    "sensor_ok",
    "fan_output",
)

VALID_FAN_STATES = frozenset({
    "STARTING",
    "RUNNING",
    "NO_ROTATION_FEEDBACK",
    "STOPPING",
    "STOPPED",
    "UNEXPECTED_ROTATION",
    "UNKNOWN",
})


class MqttValidationError(ValueError):
    """Ngoại lệ khi payload MQTT vi phạm hợp đồng dữ liệu v3."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _reject_non_finite_constant(token: str) -> Any:
    raise MqttValidationError(
        "NON_FINITE_NUMBER",
        f"Payload JSON chứa giá trị số không hữu hạn không hợp lệ: {token}.",
    )


def parse_strict_json(raw_payload: bytes | str, *, max_bytes: int = MAX_MQTT_PAYLOAD_BYTES) -> dict[str, Any]:
    """Giải mã JSON nghiêm ngặt: giới hạn kích thước <= 2048 bytes và cấm NaN/Infinity."""
    if isinstance(raw_payload, bytes):
        if len(raw_payload) > max_bytes:
            raise MqttValidationError(
                "PAYLOAD_TOO_LARGE",
                f"Kích thước bản tin ({len(raw_payload)} bytes) vượt quá giới hạn {max_bytes} bytes.",
            )
        try:
            text = raw_payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MqttValidationError("INVALID_UTF8", "Payload không phải chuỗi UTF-8 hợp lệ.") from exc
    elif isinstance(raw_payload, str):
        encoded = raw_payload.encode("utf-8")
        if len(encoded) > max_bytes:
            raise MqttValidationError(
                "PAYLOAD_TOO_LARGE",
                f"Kích thước bản tin ({len(encoded)} bytes) vượt quá giới hạn {max_bytes} bytes.",
            )
        text = raw_payload
    else:
        raise MqttValidationError("INVALID_PAYLOAD_TYPE", "Payload phải là bytes hoặc str.")

    try:
        parsed = json.loads(text, parse_constant=_reject_non_finite_constant)
    except MqttValidationError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise MqttValidationError("INVALID_JSON", f"Payload JSON lỗi cú pháp: {exc}") from exc

    if not isinstance(parsed, dict):
        raise MqttValidationError("INVALID_JSON_ROOT", "Payload JSON bắt buộc là một object (dict).")

    return parsed


def compute_payload_hash(payload: dict[str, Any]) -> str:
    """Tính SHA-256 hex digest của canonical JSON phục vụ phát hiện trùng/xung đột (DATA-02)."""
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_strict_int(value: Any, field_name: str, *, min_value: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MqttValidationError(
            "INVALID_FIELD_TYPE",
            f"Trường '{field_name}' phải là số nguyên (int).",
        )
    if min_value is not None and value < min_value:
        raise MqttValidationError(
            "FIELD_OUT_OF_RANGE",
            f"Trường '{field_name}' phải >= {min_value}.",
        )
    return value


def _require_strict_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise MqttValidationError(
            "INVALID_FIELD_TYPE",
            f"Trường '{field_name}' phải là kiểu boolean.",
        )
    return value


def _require_finite_float(
    value: Any,
    field_name: str,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MqttValidationError(
            "INVALID_FIELD_TYPE",
            f"Trường '{field_name}' phải là số thực hữu hạn.",
        )
    fval = float(value)
    if not math.isfinite(fval):
        raise MqttValidationError(
            "NON_FINITE_NUMBER",
            f"Trường '{field_name}' không được là NaN hoặc Infinity.",
        )
    if min_value is not None and fval < min_value:
        raise MqttValidationError(
            "FIELD_OUT_OF_RANGE",
            f"Trường '{field_name}' ({fval}) nhỏ hơn giới hạn tối thiểu {min_value}.",
        )
    if max_value is not None and fval > max_value:
        raise MqttValidationError(
            "FIELD_OUT_OF_RANGE",
            f"Trường '{field_name}' ({fval}) vượt quá giới hạn tối đa {max_value}.",
        )
    return fval


def _require_uuid(value: Any, field_name: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str) or not value.strip():
        raise MqttValidationError(
            "INVALID_UUID",
            f"Trường '{field_name}' phải là chuỗi UUID hợp lệ.",
        )
    try:
        return uuid.UUID(value.strip())
    except ValueError as exc:
        raise MqttValidationError(
            "INVALID_UUID",
            f"Trường '{field_name}' không đúng định dạng UUID: {value}.",
        ) from exc


def _parse_iso_utc(value: Any, field_name: str, *, allow_none: bool = True) -> datetime | None:
    if value is None:
        if allow_none:
            return None
        raise MqttValidationError("MISSING_TIMESTAMP", f"Trường '{field_name}' không được để trống.")
    if not isinstance(value, str) or not value.strip():
        raise MqttValidationError("INVALID_TIMESTAMP", f"Trường '{field_name}' phải là chuỗi ISO-8601.")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise MqttValidationError("INVALID_TIMESTAMP", f"Trường '{field_name}' sai định dạng ISO-8601.") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _validate_config_pair(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    cfg_ver = payload.get("config_version")
    cfg_hash = payload.get("config_hash")
    if cfg_ver is None and cfg_hash is None:
        return None, None
    if cfg_ver is None or cfg_hash is None:
        raise MqttValidationError(
            "INVALID_CONFIG_PAIR",
            "config_version và config_hash phải cùng null (factory) hoặc cùng có giá trị hợp lệ.",
        )
    version = _require_strict_int(cfg_ver, "config_version", min_value=1)
    if not isinstance(cfg_hash, str) or not SHA256_HEX_REGEX.match(cfg_hash):
        raise MqttValidationError(
            "INVALID_CONFIG_HASH",
            "config_hash phải là chuỗi SHA-256 hex 64 ký tự.",
        )
    return version, cfg_hash.lower()


def _validate_common_envelope(topic_device_id: str, payload: dict[str, Any]) -> None:
    schema_version = _require_strict_int(payload.get("schema_version"), "schema_version")
    if schema_version != 3:
        raise MqttValidationError(
            "UNSUPPORTED_SCHEMA_VERSION",
            f"Chỉ chấp nhận schema_version=3 (nhận được: {schema_version}).",
        )
    payload_device_id = payload.get("device_id")
    if not isinstance(payload_device_id, str) or payload_device_id != topic_device_id:
        raise MqttValidationError(
            "DEVICE_ID_MISMATCH",
            f"device_id trong payload ('{payload_device_id}') không khớp với topic ('{topic_device_id}').",
        )


def validate_telemetry_payload(
    topic_device_id: str,
    payload: dict[str, Any],
    *,
    fan_monitor_enabled: bool = False,
) -> dict[str, Any]:
    """Kiểm tra tính hợp lệ của bản tin telemetry v3 (Mục 8.1, 8.2 & DATA-01)."""
    _validate_common_envelope(topic_device_id, payload)

    profile = payload.get("profile")
    if profile not in ("CONTROLLER", "FAN_MONITOR"):
        raise MqttValidationError("INVALID_PROFILE", f"Profile không hợp lệ: {profile}.")

    if profile == "FAN_MONITOR" and not fan_monitor_enabled:
        raise MqttValidationError("P1_MONITOR_DISABLED", "Module FAN_MONITOR (P1) hiện đang tắt.")

    boot_id = _require_uuid(payload.get("boot_id"), "boot_id")
    connection_id = _require_uuid(payload.get("connection_id"), "connection_id")
    seq = _require_strict_int(payload.get("seq"), "seq", min_value=0)
    uptime_ms = _require_strict_int(payload.get("uptime_ms"), "uptime_ms", min_value=0)
    clock_synced = _require_strict_bool(payload.get("clock_synced"), "clock_synced")

    measured_at = _parse_iso_utc(
        payload.get("measured_at"),
        "measured_at",
        allow_none=not clock_synced,
    )
    if not clock_synced and measured_at is not None:
        # Mục 4.3: Clock chưa sync -> measured_at=null/clock_synced=false
        measured_at = None

    config_version, config_hash = _validate_config_pair(payload)

    raw_binding_id = payload.get("fan_binding_id")
    if raw_binding_id is not None:
        if not fan_monitor_enabled:
            raise MqttValidationError(
                "BINDING_NOT_ALLOWED_IN_P0",
                "Giai đoạn P0 yêu cầu fan_binding_id=null.",
            )
        fan_binding_id = _require_uuid(raw_binding_id, "fan_binding_id")
    else:
        fan_binding_id = None

    fault_code = payload.get("fault_code")
    if fault_code is not None and not isinstance(fault_code, str):
        raise MqttValidationError("INVALID_FIELD_TYPE", "fault_code phải là chuỗi hoặc null.")

    extra = payload.get("extra", {})
    if not isinstance(extra, dict):
        raise MqttValidationError("INVALID_FIELD_TYPE", "extra phải là JSON object.")

    is_backfill = payload.get("is_backfill", False)
    if not isinstance(is_backfill, bool):
        raise MqttValidationError("INVALID_FIELD_TYPE", "is_backfill phải là boolean.")

    normalized: dict[str, Any] = {
        "schema_version": 3,
        "device_id": topic_device_id,
        "profile": profile,
        "fan_binding_id": fan_binding_id,
        "boot_id": boot_id,
        "connection_id": connection_id,
        "seq": seq,
        "measured_at": measured_at,
        "clock_synced": clock_synced,
        "uptime_ms": uptime_ms,
        "config_version": config_version,
        "config_hash": config_hash,
        "fault_code": fault_code,
        "extra": extra,
        "is_backfill": is_backfill,
        "raw_payload_hash": compute_payload_hash(payload),
    }

    if profile == "CONTROLLER":
        for forbidden_key in CONTROLLER_FORBIDDEN_FIELDS:
            if payload.get(forbidden_key) is not None:
                raise MqttValidationError(
                    "CROSS_PROFILE_FIELD",
                    f"Node CONTROLLER không được gửi trường '{forbidden_key}' của FAN_MONITOR.",
                )

        sensor_ok = _require_strict_bool(payload.get("sensor_ok"), "sensor_ok")
        fan_output = _require_strict_bool(payload.get("fan_output"), "fan_output")
        mode = payload.get("mode")
        if mode not in ("AUTO", "MANUAL"):
            raise MqttValidationError(
                "INVALID_MODE",
                "Node CONTROLLER chỉ chấp nhận mode 'AUTO' hoặc 'MANUAL'.",
            )

        raw_temp = payload.get("temperature_c")
        raw_hum = payload.get("humidity_pct")
        if sensor_ok:
            if raw_temp is None or raw_hum is None:
                raise MqttValidationError(
                    "MISSING_SENSOR_VALUES",
                    "Khi sensor_ok=true, temperature_c và humidity_pct không được null.",
                )
            temperature_c = _require_finite_float(
                raw_temp, "temperature_c", min_value=-40.0, max_value=80.0
            )
            humidity_pct = _require_finite_float(
                raw_hum, "humidity_pct", min_value=0.0, max_value=100.0
            )
        else:
            if raw_temp is not None or raw_hum is not None:
                raise MqttValidationError(
                    "FAULT_SENSOR_VALUES_MUST_BE_NULL",
                    "Khi sensor_ok=false (lỗi DHT22), temperature_c và humidity_pct bắt buộc là null.",
                )
            temperature_c = None
            humidity_pct = None

        normalized.update({
            "temperature_c": temperature_c,
            "humidity_pct": humidity_pct,
            "sensor_ok": sensor_ok,
            "fan_output": fan_output,
            "mode": mode,
            "rpm": None,
            "rpm_min": None,
            "rpm_max": None,
            "pulse_count": None,
            "rpm_window_ms": None,
            "pulses_per_revolution": None,
            "demand_on": None,
            "demand_valid": None,
            "controller_signal_age_ms": None,
            "rpm_sensor_status": None,
            "fan_state": None,
        })
    else:
        # Profile FAN_MONITOR (P1)
        for forbidden_key in MONITOR_FORBIDDEN_FIELDS:
            if payload.get(forbidden_key) is not None:
                raise MqttValidationError(
                    "CROSS_PROFILE_FIELD",
                    f"Node FAN_MONITOR không được gửi trường '{forbidden_key}' của CONTROLLER.",
                )
        mode = payload.get("mode")
        if mode != "MONITOR":
            raise MqttValidationError("INVALID_MODE", "Node FAN_MONITOR bắt buộc mode='MONITOR'.")

        rpm_sensor_status = payload.get("rpm_sensor_status")
        if rpm_sensor_status not in ("OK", "FAULT"):
            raise MqttValidationError("INVALID_RPM_SENSOR_STATUS", "rpm_sensor_status phải là OK hoặc FAULT.")

        fan_state = payload.get("fan_state")
        if fan_state not in VALID_FAN_STATES:
            raise MqttValidationError("INVALID_FAN_STATE", f"fan_state không hợp lệ: {fan_state}.")

        demand_valid = _require_strict_bool(payload.get("demand_valid"), "demand_valid")
        raw_demand_on = payload.get("demand_on")
        if not demand_valid:
            if raw_demand_on is not None or fan_state != "UNKNOWN":
                raise MqttValidationError(
                    "INVALID_DEMAND_STATE",
                    "Khi demand_valid=false, demand_on phải null và fan_state phải là UNKNOWN.",
                )
            demand_on = None
        else:
            demand_on = _require_strict_bool(raw_demand_on, "demand_on")

        if rpm_sensor_status == "OK":
            rpm = _require_finite_float(payload.get("rpm"), "rpm", min_value=0.0)
            rpm_min = _require_finite_float(payload.get("rpm_min"), "rpm_min", min_value=0.0)
            rpm_max = _require_finite_float(payload.get("rpm_max"), "rpm_max", min_value=0.0)
            pulse_count = _require_strict_int(payload.get("pulse_count"), "pulse_count", min_value=0)
            rpm_window_ms = _require_strict_int(payload.get("rpm_window_ms"), "rpm_window_ms", min_value=250)
            ppr = _require_strict_int(payload.get("pulses_per_revolution"), "pulses_per_revolution", min_value=1)
        else:
            rpm = None
            rpm_min = None
            rpm_max = None
            pulse_count = None
            rpm_window_ms = (
                _require_strict_int(payload.get("rpm_window_ms"), "rpm_window_ms", min_value=250)
                if payload.get("rpm_window_ms") is not None
                else None
            )
            ppr = (
                _require_strict_int(payload.get("pulses_per_revolution"), "pulses_per_revolution", min_value=1)
                if payload.get("pulses_per_revolution") is not None
                else None
            )

        sig_age = payload.get("controller_signal_age_ms")
        controller_signal_age_ms = (
            _require_strict_int(sig_age, "controller_signal_age_ms", min_value=0)
            if sig_age is not None
            else None
        )

        normalized.update({
            "temperature_c": None,
            "humidity_pct": None,
            "sensor_ok": None,
            "fan_output": None,
            "mode": mode,
            "rpm": rpm,
            "rpm_min": rpm_min,
            "rpm_max": rpm_max,
            "pulse_count": pulse_count,
            "rpm_window_ms": rpm_window_ms,
            "pulses_per_revolution": ppr,
            "demand_on": demand_on,
            "demand_valid": demand_valid,
            "controller_signal_age_ms": controller_signal_age_ms,
            "rpm_sensor_status": rpm_sensor_status,
            "fan_state": fan_state,
        })

    return normalized


def validate_status_payload(
    topic_device_id: str,
    payload: dict[str, Any],
    *,
    fan_monitor_enabled: bool = False,
) -> dict[str, Any]:
    """Kiểm tra bản tin /status (bao gồm cả ONLINE heartbeat và OFFLINE LWT - Mục 8.3)."""
    _validate_common_envelope(topic_device_id, payload)

    connection = payload.get("connection")
    if connection not in ("ONLINE", "OFFLINE"):
        raise MqttValidationError("INVALID_CONNECTION_STATE", f"connection phải là ONLINE hoặc OFFLINE (nhận: {connection}).")

    boot_id = _require_uuid(payload.get("boot_id"), "boot_id")
    connection_id = _require_uuid(payload.get("connection_id"), "connection_id")

    if connection == "OFFLINE":
        reason = payload.get("reason", "LWT")
        if reason is not None and not isinstance(reason, str):
            raise MqttValidationError("INVALID_FIELD_TYPE", "reason phải là chuỗi.")
        status_seq = (
            _require_strict_int(payload.get("status_seq"), "status_seq", min_value=0)
            if payload.get("status_seq") is not None
            else None
        )
        return {
            "schema_version": 3,
            "device_id": topic_device_id,
            "connection": "OFFLINE",
            "boot_id": boot_id,
            "connection_id": connection_id,
            "status_seq": status_seq,
            "reason": reason or "LWT",
        }

    # connection == "ONLINE"
    profile = payload.get("profile")
    if profile not in ("CONTROLLER", "FAN_MONITOR"):
        raise MqttValidationError("INVALID_PROFILE", f"Profile không hợp lệ: {profile}.")
    if profile == "FAN_MONITOR" and not fan_monitor_enabled:
        raise MqttValidationError("P1_MONITOR_DISABLED", "Module FAN_MONITOR (P1) hiện đang tắt.")

    status_seq = _require_strict_int(payload.get("status_seq"), "status_seq", min_value=0)
    uptime_ms = _require_strict_int(payload.get("uptime_ms"), "uptime_ms", min_value=0)
    clock_synced = _require_strict_bool(payload.get("clock_synced"), "clock_synced")

    health = payload.get("health")
    if health not in ("OK", "FAULT", "UNKNOWN"):
        raise MqttValidationError("INVALID_HEALTH_STATE", f"health phải là OK, FAULT hoặc UNKNOWN (nhận: {health}).")

    config_version, config_hash = _validate_config_pair(payload)

    firmware_version = payload.get("firmware_version")
    if firmware_version is not None and not isinstance(firmware_version, str):
        raise MqttValidationError("INVALID_FIELD_TYPE", "firmware_version phải là chuỗi.")

    hardware_version = payload.get("hardware_version")
    if hardware_version is not None and not isinstance(hardware_version, str):
        raise MqttValidationError("INVALID_FIELD_TYPE", "hardware_version phải là chuỗi.")

    fault_code = payload.get("fault_code")
    if fault_code is not None and not isinstance(fault_code, str):
        raise MqttValidationError("INVALID_FIELD_TYPE", "fault_code phải là chuỗi hoặc null.")

    return {
        "schema_version": 3,
        "device_id": topic_device_id,
        "profile": profile,
        "connection": "ONLINE",
        "boot_id": boot_id,
        "connection_id": connection_id,
        "status_seq": status_seq,
        "config_version": config_version,
        "config_hash": config_hash,
        "firmware_version": firmware_version,
        "hardware_version": hardware_version,
        "uptime_ms": uptime_ms,
        "clock_synced": clock_synced,
        "health": health,
        "fault_code": fault_code,
    }


def validate_ack_payload(topic_device_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Kiểm tra bản tin /acks từ thiết bị phản hồi lệnh (Mục 8.4 & CMD-02)."""
    _validate_common_envelope(topic_device_id, payload)

    command_id = _require_uuid(payload.get("command_id"), "command_id")
    status = payload.get("status")
    if status not in ("APPLIED", "REJECTED"):
        raise MqttValidationError("INVALID_ACK_STATUS", f"Trạng thái ACK phải là APPLIED hoặc REJECTED (nhận: {status}).")

    cmd_type = payload.get("type")
    if cmd_type not in ("SET_MODE", "SET_FAN", "APPLY_CONFIG"):
        raise MqttValidationError("INVALID_COMMAND_TYPE", f"Loại lệnh ACK không hợp lệ: {cmd_type}.")

    boot_id = _require_uuid(payload.get("boot_id"), "boot_id")
    connection_id = _require_uuid(payload.get("connection_id"), "connection_id")
    device_command_seq = _require_strict_int(
        payload.get("device_command_seq"), "device_command_seq", min_value=1
    )

    executed_at = _parse_iso_utc(payload.get("executed_at"), "executed_at", allow_none=True)

    result = payload.get("result")
    if result is not None and not isinstance(result, dict):
        raise MqttValidationError("INVALID_FIELD_TYPE", "Trường 'result' trong ACK phải là object hoặc null.")

    error_code = payload.get("error_code")
    if error_code is not None and not isinstance(error_code, str):
        raise MqttValidationError("INVALID_FIELD_TYPE", "Trường 'error_code' phải là chuỗi hoặc null.")

    return {
        "schema_version": 3,
        "device_id": topic_device_id,
        "command_id": command_id,
        "status": status,
        "type": cmd_type,
        "boot_id": boot_id,
        "connection_id": connection_id,
        "device_command_seq": device_command_seq,
        "executed_at": executed_at,
        "result": result or {},
        "error_code": error_code,
    }
