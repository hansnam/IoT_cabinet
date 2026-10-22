"""
Device Read Schemas & Validators (Thành viên 2 phụ trách - Pha B)
Xác thực tham số truy vấn cho danh sách thiết bị và chuẩn hóa dữ liệu trả về.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping
from uuid import UUID

from backend.app.models.device import Device
from backend.app.shadows.service import format_utc_iso


ALLOWED_LIFECYCLE_STATES = frozenset({
    "REGISTERED", "PROVISIONED", "ACTIVE", "MAINTENANCE", "LOCKED", "DECOMMISSIONED"
})
ALLOWED_CONNECTION_STATES = frozenset({"UNKNOWN", "ONLINE", "OFFLINE"})
ALLOWED_HEALTH_STATES = frozenset({"UNKNOWN", "OK", "FAULT"})
ALLOWED_DEVICE_TYPES = frozenset({"CONTROLLER", "FAN_MONITOR"})
ALLOWED_DATA_SOURCES = frozenset({"REAL", "SIMULATED"})
ALLOWED_DEVICE_SORT_FIELDS = frozenset({"device_id", "name", "created_at", "last_seen_at"})
ALLOWED_SORT_ORDERS = frozenset({"asc", "desc"})

ALLOWED_LIST_QUERY_KEYS = frozenset({
    "page",
    "page_size",
    "lifecycle_state",
    "connection_state",
    "health_state",
    "device_type",
    "data_source",
    "location_id",
    "sort",
    "order",
})


class DeviceQueryValidationError(ValueError):
    """Lỗi xác thực tham số truy vấn thiết bị (HTTP 400 Bad Request)."""


@dataclass(frozen=True)
class DeviceListQueryParams:
    page: int = 1
    page_size: int = 100
    lifecycle_state: str | None = None
    connection_state: str | None = None
    health_state: str | None = None
    device_type: str | None = None
    data_source: str | None = None
    location_id: UUID | None = None
    sort: str = "device_id"
    order: str = "asc"


def parse_device_list_query_params(args: Mapping[str, str]) -> DeviceListQueryParams:
    """Kiểm tra và chuẩn hóa tham số query cho GET /api/v1/devices."""
    for key in args.keys():
        if key not in ALLOWED_LIST_QUERY_KEYS:
            raise DeviceQueryValidationError(f"Unsupported query parameter: {key}")

    raw_page = args.get("page", "1")
    raw_page_size = args.get("page_size", "100")
    try:
        page = int(raw_page)
        page_size = int(raw_page_size)
    except (ValueError, TypeError) as exc:
        raise DeviceQueryValidationError("page and page_size must be integers") from exc

    if page < 1:
        raise DeviceQueryValidationError("page must be >= 1")
    if page_size < 1 or page_size > 500:
        raise DeviceQueryValidationError("page_size must be between 1 and 500")

    lifecycle_state = args.get("lifecycle_state")
    if lifecycle_state is not None:
        lifecycle_state = lifecycle_state.strip().upper()
        if lifecycle_state not in ALLOWED_LIFECYCLE_STATES:
            raise DeviceQueryValidationError("Invalid lifecycle_state")

    connection_state = args.get("connection_state")
    if connection_state is not None:
        connection_state = connection_state.strip().upper()
        if connection_state not in ALLOWED_CONNECTION_STATES:
            raise DeviceQueryValidationError("Invalid connection_state")

    health_state = args.get("health_state")
    if health_state is not None:
        health_state = health_state.strip().upper()
        if health_state not in ALLOWED_HEALTH_STATES:
            raise DeviceQueryValidationError("Invalid health_state")

    device_type = args.get("device_type")
    if device_type is not None:
        device_type = device_type.strip().upper()
        if device_type not in ALLOWED_DEVICE_TYPES:
            raise DeviceQueryValidationError("Invalid device_type")

    data_source = args.get("data_source")
    if data_source is not None:
        data_source = data_source.strip().upper()
        if data_source not in ALLOWED_DATA_SOURCES:
            raise DeviceQueryValidationError("Invalid data_source")

    location_uuid: UUID | None = None
    raw_location_id = args.get("location_id")
    if raw_location_id is not None:
        try:
            location_uuid = UUID(str(raw_location_id).strip())
        except (ValueError, TypeError) as exc:
            raise DeviceQueryValidationError("Invalid location_id UUID") from exc

    sort = args.get("sort", "device_id").strip().lower()
    if sort not in ALLOWED_DEVICE_SORT_FIELDS:
        raise DeviceQueryValidationError("Invalid sort field")

    order = args.get("order", "asc").strip().lower()
    if order not in ALLOWED_SORT_ORDERS:
        raise DeviceQueryValidationError("Invalid sort order")

    return DeviceListQueryParams(
        page=page,
        page_size=page_size,
        lifecycle_state=lifecycle_state,
        connection_state=connection_state,
        health_state=health_state,
        device_type=device_type,
        data_source=data_source,
        location_id=location_uuid,
        sort=sort,
        order=order,
    )


def finite_float_or_none(val: float | int | None) -> float | None:
    """Chuyển đổi số thực hữu hạn; giữ nguyên None (không biến null thành 0)."""
    if val is None:
        return None
    try:
        f_val = float(val)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(f_val):
        return None
    return round(f_val, 4)


def serialize_device(device: Device) -> dict:
    """Chuẩn hóa đối tượng Device sang JSON snake_case theo hợp đồng /api/v1."""
    config_pending = (
        device.desired_config_version is not None
        and device.desired_config_version != device.applied_config_version
    )
    return {
        "device_id": device.device_id,
        "name": device.name,
        "location_id": str(device.location_id) if device.location_id else None,
        "owner_user_id": str(device.owner_user_id) if device.owner_user_id else None,
        "device_type": device.device_type,
        "profile": device.device_type,
        "data_source": device.data_source,
        "lifecycle_state": device.lifecycle_state,
        "connection_state": device.connection_state,
        "health_state": device.health_state,
        "hardware_version": device.hardware_version,
        "firmware_version": device.firmware_version,
        "capabilities": device.capabilities if isinstance(device.capabilities, dict) else {},
        "current_boot_id": str(device.current_boot_id) if device.current_boot_id else None,
        "current_connection_id": str(device.current_connection_id) if device.current_connection_id else None,
        "latest_seq": int(device.latest_seq) if device.latest_seq is not None else None,
        "latest_status_seq": int(device.latest_status_seq) if device.latest_status_seq is not None else None,
        "last_command_seq": int(device.last_command_seq or 0),
        "last_seen_at": format_utc_iso(device.last_seen_at),
        "last_telemetry_at": format_utc_iso(device.last_telemetry_at),
        "desired_config_version": device.desired_config_version,
        "applied_config_version": device.applied_config_version,
        "config_pending": config_pending,
        "broker_desired_enabled": bool(device.broker_desired_enabled),
        "broker_actual_enabled": (
            bool(device.broker_actual_enabled) if device.broker_actual_enabled is not None else None
        ),
        "broker_sync_state": device.broker_sync_state,
        "state_version": int(device.state_version or 1),
        "maintenance_note": device.maintenance_note,
        "created_at": format_utc_iso(device.created_at),
        "updated_at": format_utc_iso(device.updated_at),
    }

