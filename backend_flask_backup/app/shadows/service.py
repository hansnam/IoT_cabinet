"""
Shadow Service Module (Thành viên 2 phụ trách - Pha B)
Quản lý trạng thái Device Shadow (desired / reported), tính toán độ tươi (stale)
và trạng thái đồng bộ cấu hình của thiết bị.
"""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.extensions import db
from backend.app.models.device import Device, DeviceConfig
from backend.app.models.telemetry import DeviceShadow
from backend.app.permissions.service import PermissionService
from backend.app.security.context import AuthContext


def format_utc_iso(dt: datetime | None) -> str | None:
    """Chuẩn hóa datetime sang chuỗi UTC ISO8601 có hậu tố Z."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def compute_telemetry_stale(
    device: Device,
    reference_time: datetime | None,
    *,
    telemetry_interval_ms: int | None = None,
    now: datetime | None = None,
) -> bool:
    """Tính toán cờ stale cho dữ liệu thiết bị theo Đặc tả V1.3 (Mục 4.1, 8.3, 9.2):
    - Nếu thiết bị không ONLINE hoặc chưa từng có bản tin reference_time -> stale = True.
    - Với FAN_MONITOR (P1): mẫu quá 6 giây -> stale = True.
    - Với CONTROLLER (P0): quá max(3 * telemetry_interval_s, 15s) -> stale = True.
    """
    if reference_time is None:
        return True
    if device.connection_state != "ONLINE":
        return True

    now_utc = now if now is not None else datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    ref_utc = reference_time
    if ref_utc.tzinfo is None:
        ref_utc = ref_utc.replace(tzinfo=timezone.utc)
    else:
        ref_utc = ref_utc.astimezone(timezone.utc)

    age_seconds = (now_utc - ref_utc).total_seconds()
    if age_seconds < 0:
        return False

    if device.device_type == "FAN_MONITOR":
        return age_seconds > 6.0

    interval_ms = telemetry_interval_ms if (telemetry_interval_ms and telemetry_interval_ms > 0) else 5000
    threshold_s = max((interval_ms / 1000.0) * 3.0, 15.0)
    return age_seconds > threshold_s


class ShadowService:
    """Dịch vụ đọc và tổng hợp trạng thái Device Shadow."""

    @staticmethod
    def get_device_shadow(
        device_id: str,
        *,
        actor: AuthContext,
        db_session: Session | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Đọc trạng thái desired/reported của thiết bị có kiểm tra quyền và SQL scope."""
        session = db_session if db_session is not None else db.session
        now_utc = now if now is not None else datetime.now(timezone.utc)

        # 1. Kiểm tra quyền hạn và phạm vi thiết bị (404 nếu ngoài VIEW scope)
        PermissionService.require(
            "device.read",
            device_id=device_id,
            actor=actor,
            db_session=session,
            now=now_utc,
        )

        # 2. Truy vấn Device qua SQL scope của PermissionService
        visible_stmt = PermissionService.visible_devices_query(
            actor, db_session=session, now=now_utc
        ).where(Device.device_id == str(device_id))
        device = session.execute(visible_stmt).scalar_one_or_none()
        if device is None:
            from backend.app.permissions.service import DeviceScopeNotFound
            raise DeviceScopeNotFound()

        shadow = session.execute(
            select(DeviceShadow).where(DeviceShadow.device_id == device.device_id)
        ).scalar_one_or_none()

        # Lấy telemetry_interval_ms từ cấu hình đã áp dụng hoặc mong muốn
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

        reported_at = shadow.reported_at if shadow is not None else None
        ref_time = reported_at or device.last_telemetry_at or device.last_seen_at
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

        if shadow is None:
            drift_status = "PENDING" if config_pending else "UNKNOWN"
            return {
                "device_id": device.device_id,
                "desired": {},
                "reported": {},
                "desired_version": 0,
                "reported_version": 0,
                "desired_at": None,
                "reported_at": None,
                "reported_boot_id": None,
                "reported_seq": None,
                "drift_status": drift_status,
                "desired_config_version": device.desired_config_version,
                "applied_config_version": device.applied_config_version,
                "config_pending": config_pending,
                "stale": is_stale,
                "updated_at": format_utc_iso(device.updated_at),
            }

        drift_status = shadow.drift_status
        if is_stale and drift_status == "IN_SYNC":
            drift_status = "STALE"

        return {
            "device_id": device.device_id,
            "desired": shadow.desired if isinstance(shadow.desired, dict) else {},
            "reported": shadow.reported if isinstance(shadow.reported, dict) else {},
            "desired_version": int(shadow.desired_version),
            "reported_version": int(shadow.reported_version),
            "desired_at": format_utc_iso(shadow.desired_at),
            "reported_at": format_utc_iso(shadow.reported_at),
            "reported_boot_id": str(shadow.reported_boot_id) if shadow.reported_boot_id else None,
            "reported_seq": int(shadow.reported_seq) if shadow.reported_seq is not None else None,
            "drift_status": drift_status,
            "desired_config_version": device.desired_config_version,
            "applied_config_version": device.applied_config_version,
            "config_pending": config_pending,
            "stale": is_stale,
            "updated_at": format_utc_iso(shadow.updated_at),
        }

