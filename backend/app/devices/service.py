"""
Device Read Service (Thành viên 2 phụ trách - Pha B)
Thực hiện các nghiệp vụ đọc danh sách thiết bị, chi tiết thiết bị và trạng thái mới nhất (latest).
Tuân thủ nghiêm ngặt:
- Kiểm tra quyền hạn qua PermissionService.require.
- Lọc phạm vi thiết bị trực tiếp tại tầng SQL qua PermissionService.visible_devices_query.
- Không biến giá trị đo NULL thành 0 (DATA-01).
- Không ghi audit/user_activity_logs cho các thao tác đọc/poll (LOG-03).
"""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from backend.app.devices.schemas import (
    DeviceListQueryParams,
    finite_float_or_none,
    serialize_device,
)
from backend.app.extensions import db
from backend.app.models.device import Device, DeviceConfig
from backend.app.models.telemetry import Telemetry
from backend.app.permissions.service import DeviceScopeNotFound, PermissionService
from backend.app.security.context import AuthContext
from backend.app.shadows.service import compute_telemetry_stale, format_utc_iso


SORT_COLUMN_MAP = {
    "device_id": Device.device_id,
    "name": Device.name,
    "created_at": Device.created_at,
    "last_seen_at": Device.last_seen_at,
}


class DeviceReadService:
    """Service xử lý các truy vấn đọc thông tin thiết bị và trạng thái mới nhất."""

    @staticmethod
    def list_devices(
        params: DeviceListQueryParams,
        *,
        actor: AuthContext,
        db_session: Session | None = None,
        now: datetime | None = None,
    ) -> tuple[list[dict], dict]:
        """Lấy danh sách thiết bị trong phạm vi quyền của người dùng (SQL scope filter)."""
        session = db_session if db_session is not None else db.session
        now_utc = now if now is not None else datetime.now(timezone.utc)

        # 1. Kiểm tra quyền đọc thiết bị ở mức role
        PermissionService.require(
            "device.read",
            actor=actor,
            db_session=session,
            now=now_utc,
        )

        with session.no_autoflush:
            # 2. Khởi tạo câu truy vấn lọc theo scope ngay tại SQL
            stmt = PermissionService.visible_devices_query(
                actor, db_session=session, now=now_utc
            )

            if params.lifecycle_state is not None:
                stmt = stmt.where(Device.lifecycle_state == params.lifecycle_state)
            if params.connection_state is not None:
                stmt = stmt.where(Device.connection_state == params.connection_state)
            if params.health_state is not None:
                stmt = stmt.where(Device.health_state == params.health_state)
            if params.device_type is not None:
                stmt = stmt.where(Device.device_type == params.device_type)
            if params.data_source is not None:
                stmt = stmt.where(Device.data_source == params.data_source)
            if params.location_id is not None:
                stmt = stmt.where(Device.location_id == params.location_id)

            # 3. Đếm tổng số bản ghi trong phạm vi quyền (không lộ count ngoài scope - IAM-02)
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = session.execute(count_stmt).scalar_one()

            # 4. Sắp xếp và phân trang
            sort_col = SORT_COLUMN_MAP.get(params.sort, Device.device_id)
            order_fn = desc if params.order == "desc" else asc
            stmt = stmt.order_by(order_fn(sort_col), asc(Device.device_id))

            offset = (params.page - 1) * params.page_size
            stmt = stmt.offset(offset).limit(params.page_size)

            devices = session.execute(stmt).scalars().all()
            items = [serialize_device(d) for d in devices]

            meta = {
                "page": params.page,
                "page_size": params.page_size,
                "total": int(total),
            }
            return items, meta

    @staticmethod
    def get_device(
        device_id: str,
        *,
        actor: AuthContext,
        db_session: Session | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Lấy thông tin chi tiết của một thiết bị theo device_id."""
        session = db_session if db_session is not None else db.session
        now_utc = now if now is not None else datetime.now(timezone.utc)

        # 1. Kiểm tra quyền và VIEW scope (trả về 404 nếu ngoài scope)
        PermissionService.require(
            "device.read",
            device_id=device_id,
            actor=actor,
            db_session=session,
            now=now_utc,
        )

        with session.no_autoflush:
            # 2. Truy vấn thông qua SQL scope filter để đảm bảo tuyệt đối không lộ dữ liệu
            stmt = PermissionService.visible_devices_query(
                actor, db_session=session, now=now_utc
            ).where(Device.device_id == str(device_id))
            device = session.execute(stmt).scalar_one_or_none()
            if device is None:
                raise DeviceScopeNotFound()

            return serialize_device(device)

    @staticmethod
    def get_device_latest(
        device_id: str,
        *,
        actor: AuthContext,
        db_session: Session | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Lấy trạng thái đo lường mới nhất (latest) theo profile của thiết bị (Mục 7.2 & 8.1):
        - Nguồn data_source ('REAL'/'SIMULATED') luôn lấy từ Device trong DB, không tin node.
        - Giá trị đo bị lỗi (NULL) giữ nguyên None, tuyệt đối không biến thành 0 (DATA-01).
        - Chỉ lấy từ bản tin live (is_backfill = False).
        """
        session = db_session if db_session is not None else db.session
        now_utc = now if now is not None else datetime.now(timezone.utc)

        # 1. Kiểm tra quyền telemetry.read và VIEW scope trên thiết bị
        PermissionService.require(
            "telemetry.read",
            device_id=device_id,
            actor=actor,
            db_session=session,
            now=now_utc,
        )

        with session.no_autoflush:
            # 2. Đọc thiết bị qua SQL scope filter
            device_stmt = PermissionService.visible_devices_query(
                actor, db_session=session, now=now_utc
            ).where(Device.device_id == str(device_id))
            device = session.execute(device_stmt).scalar_one_or_none()
            if device is None:
                raise DeviceScopeNotFound()

            # 3. Lấy chu kỳ gửi telemetry từ cấu hình đã áp dụng hoặc mong muốn
            cfg_ver = device.applied_config_version or device.desired_config_version
            interval_ms = 5000 if device.device_type == "CONTROLLER" else 2000
            if cfg_ver is not None:
                cfg_interval = session.execute(
                    select(DeviceConfig.telemetry_interval_ms).where(
                        DeviceConfig.device_id == device.device_id,
                        DeviceConfig.version == cfg_ver,
                    )
                ).scalar_one_or_none()
                if cfg_interval is not None:
                    interval_ms = cfg_interval

            # 4. Truy vấn bản ghi Telemetry live mới nhất của thiết bị
            latest_telemetry = session.execute(
                select(Telemetry)
                .where(
                    Telemetry.device_id == device.device_id,
                    Telemetry.is_backfill.is_(False),
                )
                .order_by(desc(Telemetry.received_at), desc(Telemetry.seq))
                .limit(1)
            ).scalar_one_or_none()

            ref_time = (
                latest_telemetry.received_at
                if latest_telemetry is not None
                else (device.last_telemetry_at or device.last_seen_at)
            )
            is_stale = compute_telemetry_stale(
                device,
                ref_time,
                telemetry_interval_ms=interval_ms,
                now=now_utc,
            )
            config_pending = (
                device.desired_config_version is not None
                and device.desired_config_version != device.applied_config_version
            )

            base_payload = {
                "device_id": device.device_id,
                "name": device.name,
                "profile": device.device_type,
                "device_type": device.device_type,
                "data_source": device.data_source,
                "lifecycle_state": device.lifecycle_state,
                "connection_state": device.connection_state,
                "health_state": device.health_state,
                "desired_config_version": device.desired_config_version,
                "applied_config_version": device.applied_config_version,
                "config_pending": config_pending,
                "last_seen_at": format_utc_iso(device.last_seen_at),
                "last_telemetry_at": format_utc_iso(device.last_telemetry_at),
                "stale": is_stale,
                "telemetry_id": str(latest_telemetry.id) if latest_telemetry else None,
                "boot_id": (
                    str(latest_telemetry.boot_id)
                    if latest_telemetry
                    else (str(device.current_boot_id) if device.current_boot_id else None)
                ),
                "connection_id": (
                    str(latest_telemetry.connection_id)
                    if latest_telemetry
                    else (str(device.current_connection_id) if device.current_connection_id else None)
                ),
                "seq": (
                    int(latest_telemetry.seq)
                    if latest_telemetry is not None
                    else (int(device.latest_seq) if device.latest_seq is not None else None)
                ),
                "measured_at": format_utc_iso(latest_telemetry.measured_at) if latest_telemetry else None,
                "received_at": format_utc_iso(latest_telemetry.received_at) if latest_telemetry else None,
                "clock_synced": bool(latest_telemetry.clock_synced) if latest_telemetry is not None else None,
                "uptime_ms": int(latest_telemetry.uptime_ms) if latest_telemetry is not None else None,
                "config_version": latest_telemetry.config_version if latest_telemetry else device.applied_config_version,
                "config_hash": latest_telemetry.config_hash if latest_telemetry else None,
                "fault_code": latest_telemetry.fault_code if latest_telemetry else None,
            }

            if device.device_type == "CONTROLLER":
                base_payload.update({
                    "temperature_c": finite_float_or_none(latest_telemetry.temperature_c) if latest_telemetry else None,
                    "humidity_pct": finite_float_or_none(latest_telemetry.humidity_pct) if latest_telemetry else None,
                    "sensor_ok": latest_telemetry.sensor_ok if latest_telemetry else None,
                    "fan_output": latest_telemetry.fan_output if latest_telemetry else None,
                    "mode": latest_telemetry.mode if latest_telemetry else None,
                })
            else:
                # FAN_MONITOR (P1)
                base_payload.update({
                    "rpm": finite_float_or_none(latest_telemetry.rpm) if latest_telemetry else None,
                    "rpm_min": finite_float_or_none(latest_telemetry.rpm_min) if latest_telemetry else None,
                    "rpm_max": finite_float_or_none(latest_telemetry.rpm_max) if latest_telemetry else None,
                    "pulse_count": (
                        int(latest_telemetry.pulse_count)
                        if (latest_telemetry and latest_telemetry.pulse_count is not None)
                        else None
                    ),
                    "rpm_window_ms": latest_telemetry.rpm_window_ms if latest_telemetry else None,
                    "pulses_per_revolution": latest_telemetry.pulses_per_revolution if latest_telemetry else None,
                    "demand_on": latest_telemetry.demand_on if latest_telemetry else None,
                    "demand_valid": latest_telemetry.demand_valid if latest_telemetry else None,
                    "controller_signal_age_ms": (
                        int(latest_telemetry.controller_signal_age_ms)
                        if (latest_telemetry and latest_telemetry.controller_signal_age_ms is not None)
                        else None
                    ),
                    "rpm_sensor_status": latest_telemetry.rpm_sensor_status if latest_telemetry else None,
                    "fan_state": (
                        "STALE"
                        if (is_stale and latest_telemetry is not None)
                        else (latest_telemetry.fan_state if latest_telemetry else "UNKNOWN")
                    ),
                })

            return base_payload

