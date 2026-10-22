"""
Telemetry Schemas, Query Validators & CSV Sanitizer (Thành viên 2 phụ trách - Pha B)
Tuân thủ Đặc tả V1.3 (Mục 6.4, 7.2, 15.1, 15.3):
- Khoảng truy vấn [from, to) tối đa 31 ngày.
- Phân trang page_size mặc định 100, tối đa 500.
- Allowlist chặt cho time_field, metric, source, sort/order.
- Chống CSV formula injection (=, +, -, @, \t, \r).
- Không biến giá trị đo NULL thành 0 (DATA-01).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Mapping

from backend.app.devices.schemas import finite_float_or_none
from backend.app.models.telemetry import Telemetry
from backend.app.shadows.service import format_utc_iso


MAX_INTERACTIVE_DAYS = 31
MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 100
MAX_EXPORT_ROWS = 100_000

ALLOWED_TIME_FIELDS = frozenset({"received_at", "measured_at"})
ALLOWED_METRICS = frozenset({"temperature_c", "humidity_pct", "fan_output", "sensor_ok", "rpm"})
ALLOWED_STAT_METRICS = frozenset({"temperature_c", "humidity_pct", "rpm"})
ALLOWED_DATA_SOURCES = frozenset({"REAL", "SIMULATED"})
ALLOWED_SORT_ORDERS = frozenset({"asc", "desc"})

ALLOWED_HISTORY_QUERY_KEYS = frozenset({
    "from",
    "to",
    "time_field",
    "metric",
    "source",
    "include_backfill",
    "sort",
    "order",
    "page",
    "page_size",
})

ALLOWED_STATISTICS_QUERY_KEYS = frozenset({
    "from",
    "to",
    "time_field",
    "metric",
    "source",
    "include_backfill",
})

ALLOWED_EXPORT_QUERY_KEYS = frozenset({
    "from",
    "to",
    "time_field",
    "metric",
    "source",
    "include_backfill",
    "sort",
    "order",
})

CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class TelemetryQueryValidationError(ValueError):
    """Lỗi xác thực tham số truy vấn telemetry (HTTP 400 Bad Request)."""


@dataclass(frozen=True)
class TelemetryHistoryQueryParams:
    from_utc: datetime
    to_utc: datetime
    time_field: str = "received_at"
    metric: str | None = None
    source: str | None = None
    include_backfill: bool = True
    order: str = "desc"
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE


@dataclass(frozen=True)
class TelemetryStatsQueryParams:
    from_utc: datetime
    to_utc: datetime
    time_field: str = "received_at"
    metric: str | None = None
    source: str | None = None
    include_backfill: bool = True


@dataclass(frozen=True)
class TelemetryExportQueryParams:
    from_utc: datetime
    to_utc: datetime
    time_field: str = "received_at"
    metric: str | None = None
    source: str | None = None
    include_backfill: bool = True
    order: str = "desc"


def parse_utc_timestamp(raw: str, field_name: str) -> datetime:
    """Phân tích chuỗi ISO8601 sang datetime UTC."""
    if not isinstance(raw, str) or not raw.strip():
        raise TelemetryQueryValidationError(f"Invalid {field_name} timestamp")
    s = raw.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise TelemetryQueryValidationError(f"Invalid ISO8601 timestamp for {field_name}") from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def parse_bool_param(raw: str | None, default: bool, field_name: str) -> bool:
    if raw is None:
        return default
    val = str(raw).strip().lower()
    if val in ("true", "1"):
        return True
    if val in ("false", "0"):
        return False
    raise TelemetryQueryValidationError(f"Invalid boolean for {field_name}")


def _parse_time_window(
    args: Mapping[str, str],
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    now_utc = now if now is not None else datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    raw_to = args.get("to")
    raw_from = args.get("from")

    to_utc = parse_utc_timestamp(raw_to, "to") if raw_to is not None else now_utc
    from_utc = (
        parse_utc_timestamp(raw_from, "from")
        if raw_from is not None
        else (to_utc - timedelta(hours=24))
    )

    if from_utc >= to_utc:
        raise TelemetryQueryValidationError("'from' must be strictly earlier than 'to' ([from, to))")

    if (to_utc - from_utc) > timedelta(days=MAX_INTERACTIVE_DAYS):
        raise TelemetryQueryValidationError(
            f"Time window cannot exceed {MAX_INTERACTIVE_DAYS} days"
        )

    time_field = args.get("time_field", "received_at").strip().lower()
    if time_field not in ALLOWED_TIME_FIELDS:
        raise TelemetryQueryValidationError("Invalid time_field; allowed: received_at, measured_at")

    return from_utc, to_utc, time_field


def parse_telemetry_history_params(
    args: Mapping[str, str],
    *,
    now: datetime | None = None,
) -> TelemetryHistoryQueryParams:
    for key in args.keys():
        if key not in ALLOWED_HISTORY_QUERY_KEYS:
            raise TelemetryQueryValidationError(f"Unsupported query parameter: {key}")

    from_utc, to_utc, time_field = _parse_time_window(args, now=now)

    metric = args.get("metric")
    if metric is not None:
        metric = metric.strip().lower()
        if metric not in ALLOWED_METRICS:
            raise TelemetryQueryValidationError("Invalid metric parameter")

    source = args.get("source")
    if source is not None:
        source = source.strip().upper()
        if source not in ALLOWED_DATA_SOURCES:
            raise TelemetryQueryValidationError("Invalid source parameter")

    include_backfill = parse_bool_param(args.get("include_backfill"), True, "include_backfill")

    raw_order = args.get("order") or args.get("sort") or "desc"
    order = raw_order.strip().lower()
    if order not in ALLOWED_SORT_ORDERS:
        raise TelemetryQueryValidationError("Invalid sort order; allowed: asc, desc")

    try:
        page = int(args.get("page", "1"))
        page_size = int(args.get("page_size", str(DEFAULT_PAGE_SIZE)))
    except (ValueError, TypeError) as exc:
        raise TelemetryQueryValidationError("page and page_size must be integers") from exc

    if page < 1:
        raise TelemetryQueryValidationError("page must be >= 1")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise TelemetryQueryValidationError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

    return TelemetryHistoryQueryParams(
        from_utc=from_utc,
        to_utc=to_utc,
        time_field=time_field,
        metric=metric,
        source=source,
        include_backfill=include_backfill,
        order=order,
        page=page,
        page_size=page_size,
    )


def parse_telemetry_stats_params(
    args: Mapping[str, str],
    *,
    now: datetime | None = None,
) -> TelemetryStatsQueryParams:
    for key in args.keys():
        if key not in ALLOWED_STATISTICS_QUERY_KEYS:
            raise TelemetryQueryValidationError(f"Unsupported query parameter: {key}")

    from_utc, to_utc, time_field = _parse_time_window(args, now=now)

    metric = args.get("metric")
    if metric is not None:
        metric = metric.strip().lower()
        if metric not in ALLOWED_STAT_METRICS:
            raise TelemetryQueryValidationError("Invalid metric parameter for statistics")

    source = args.get("source")
    if source is not None:
        source = source.strip().upper()
        if source not in ALLOWED_DATA_SOURCES:
            raise TelemetryQueryValidationError("Invalid source parameter")

    include_backfill = parse_bool_param(args.get("include_backfill"), True, "include_backfill")

    return TelemetryStatsQueryParams(
        from_utc=from_utc,
        to_utc=to_utc,
        time_field=time_field,
        metric=metric,
        source=source,
        include_backfill=include_backfill,
    )


def parse_telemetry_export_params(
    args: Mapping[str, str],
    *,
    now: datetime | None = None,
) -> TelemetryExportQueryParams:
    for key in args.keys():
        if key not in ALLOWED_EXPORT_QUERY_KEYS:
            raise TelemetryQueryValidationError(f"Unsupported query parameter: {key}")

    from_utc, to_utc, time_field = _parse_time_window(args, now=now)

    metric = args.get("metric")
    if metric is not None:
        metric = metric.strip().lower()
        if metric not in ALLOWED_METRICS:
            raise TelemetryQueryValidationError("Invalid metric parameter")

    source = args.get("source")
    if source is not None:
        source = source.strip().upper()
        if source not in ALLOWED_DATA_SOURCES:
            raise TelemetryQueryValidationError("Invalid source parameter")

    include_backfill = parse_bool_param(args.get("include_backfill"), True, "include_backfill")

    raw_order = args.get("order") or args.get("sort") or "desc"
    order = raw_order.strip().lower()
    if order not in ALLOWED_SORT_ORDERS:
        raise TelemetryQueryValidationError("Invalid sort order; allowed: asc, desc")

    return TelemetryExportQueryParams(
        from_utc=from_utc,
        to_utc=to_utc,
        time_field=time_field,
        metric=metric,
        source=source,
        include_backfill=include_backfill,
        order=order,
    )


def serialize_telemetry_row(row: Telemetry, *, data_source: str) -> dict:
    """Chuẩn hóa một dòng Telemetry sang JSON snake_case theo profile."""
    payload = {
        "id": str(row.id),
        "device_id": row.device_id,
        "profile": row.profile,
        "data_source": data_source,
        "boot_id": str(row.boot_id),
        "connection_id": str(row.connection_id),
        "seq": int(row.seq),
        "schema_version": int(row.schema_version),
        "measured_at": format_utc_iso(row.measured_at),
        "received_at": format_utc_iso(row.received_at),
        "uptime_ms": int(row.uptime_ms),
        "clock_synced": bool(row.clock_synced),
        "config_version": row.config_version,
        "config_hash": row.config_hash,
        "fault_code": row.fault_code,
        "is_backfill": bool(row.is_backfill),
    }

    if row.profile == "CONTROLLER":
        payload.update({
            "temperature_c": finite_float_or_none(row.temperature_c),
            "humidity_pct": finite_float_or_none(row.humidity_pct),
            "sensor_ok": row.sensor_ok,
            "fan_output": row.fan_output,
            "mode": row.mode,
        })
    else:
        payload.update({
            "fan_binding_id": str(row.fan_binding_id) if row.fan_binding_id else None,
            "rpm": finite_float_or_none(row.rpm),
            "rpm_min": finite_float_or_none(row.rpm_min),
            "rpm_max": finite_float_or_none(row.rpm_max),
            "pulse_count": int(row.pulse_count) if row.pulse_count is not None else None,
            "rpm_window_ms": row.rpm_window_ms,
            "pulses_per_revolution": row.pulses_per_revolution,
            "demand_on": row.demand_on,
            "demand_valid": row.demand_valid,
            "controller_signal_age_ms": (
                int(row.controller_signal_age_ms)
                if row.controller_signal_age_ms is not None
                else None
            ),
            "rpm_sensor_status": row.rpm_sensor_status,
            "fan_state": row.fan_state,
            "mode": row.mode,
        })

    return payload


def sanitize_csv_cell(value: Any) -> str:
    """Làm sạch giá trị ô CSV chống tấn công CSV Formula Injection (Mục 7.2 & 15.1):
    - None -> chuỗi rỗng "" (tuyệt đối không biến null thành 0).
    - bool -> "true" / "false".
    - int / float hữu hạn -> chuỗi số (số âm hợp lệ như -10.5 được giữ nguyên).
    - Chuỗi văn bản bắt đầu bằng '=', '+', '-', '@', '\\t', '\\r' (kể cả sau khoảng trắng đầu)
      bị thêm dấu nháy đơn \"'\" ở đầu để trình bảng tính không thực thi công thức.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return str(round(value, 4))

    s = str(value)
    if not s:
        return ""

    stripped = s.lstrip(" ")
    if s.startswith(CSV_FORMULA_PREFIXES) or stripped.startswith(CSV_FORMULA_PREFIXES):
        return "'" + s
    return s

