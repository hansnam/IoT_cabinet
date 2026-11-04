"""
Telemetry Service Module (Thành viên 2 phụ trách - Pha B)
Thực hiện các nghiệp vụ:
1. Truy vấn lịch sử telemetry theo khoảng [from, to) tối đa 31 ngày (query_history).
2. Tính toán thống kê min/max/avg/count và độ phủ dữ liệu (query_statistics).
3. Xuất dữ liệu telemetry ra CSV kèm ghi nhận AuditLog & UserActivityLog (export_csv).
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
from sqlalchemy import asc, case, desc, func, select
from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.devices.schemas import finite_float_or_none
from backend.app.extensions import db
from backend.app.models.device import Device, DeviceConfig
from backend.app.models.iam import User
from backend.app.models.telemetry import Telemetry
from backend.app.permissions.service import DeviceScopeNotFound, PermissionService
from backend.app.security.context import AuthContext
from backend.app.shadows.service import format_utc_iso
from backend.app.telemetry.schemas import (
    MAX_EXPORT_ROWS,
    TelemetryExportQueryParams,
    TelemetryHistoryQueryParams,
    TelemetryQueryValidationError,
    TelemetryStatsQueryParams,
    sanitize_csv_cell,
    serialize_telemetry_row,
)
from backend.app.user_activity.service import UserActivityService


METRIC_COLUMN_MAP = {
    "temperature_c": Telemetry.temperature_c,
    "humidity_pct": Telemetry.humidity_pct,
    "fan_output": Telemetry.fan_output,
    "sensor_ok": Telemetry.sensor_ok,
    "rpm": Telemetry.rpm,
}

CSV_EXPORT_HEADERS = [
    "device_id",
    "profile",
    "data_source",
    "received_at",
    "measured_at",
    "clock_synced",
    "is_backfill",
    "seq",
    "boot_id",
    "connection_id",
    "uptime_ms",
    "temperature_c",
    "temperature_unit",
    "humidity_pct",
    "humidity_unit",
    "sensor_ok",
    "fan_output",
    "mode",
    "config_version",
    "config_hash",
    "fault_code",
]


class TelemetryService:
    """Service xử lý truy vấn lịch sử, thống kê và xuất CSV cho Telemetry."""

    @staticmethod
    def _get_scoped_device(
        session: Session,
        *,
        device_id: str,
        actor: AuthContext,
        now_utc: datetime,
    ) -> Device:
        """Lấy Device qua SQL scope filter của PermissionService."""
        visible_stmt = PermissionService.visible_devices_query(
            actor, db_session=session, now=now_utc
        ).where(Device.device_id == str(device_id))
        device = session.execute(visible_stmt).scalar_one_or_none()
        if device is None:
            raise DeviceScopeNotFound()
        return device

    @staticmethod
    def query_history(
        device_id: str,
        params: TelemetryHistoryQueryParams,
        *,
        actor: AuthContext,
        db_session: Session | None = None,
        now: datetime | None = None,
    ) -> tuple[list[dict], dict]:
        """Truy vấn lịch sử telemetry của thiết bị theo khoảng [from, to)."""
        session = db_session if db_session is not None else db.session
        now_utc = now if now is not None else datetime.now(timezone.utc)

        # 1. Kiểm tra quyền telemetry.read và VIEW scope (trả 404 nếu ngoài scope)
        PermissionService.require(
            "telemetry.read",
            device_id=device_id,
            actor=actor,
            db_session=session,
            now=now_utc,
        )

        with session.no_autoflush:
            # 2. Xác nhận thiết bị qua SQL scope filter
            device = TelemetryService._get_scoped_device(
                session, device_id=device_id, actor=actor, now_utc=now_utc
            )

            # Nếu caller lọc theo source nhưng không khớp data_source của thiết bị trong DB
            if params.source is not None and params.source != device.data_source:
                meta = {
                    "page": params.page,
                    "page_size": params.page_size,
                    "total": 0,
                    "from": format_utc_iso(params.from_utc),
                    "to": format_utc_iso(params.to_utc),
                    "time_field": params.time_field,
                }
                return [], meta

            time_col = (
                Telemetry.measured_at
                if params.time_field == "measured_at"
                else Telemetry.received_at
            )

            # 3. Xây dựng câu lệnh SQL kết hợp subquery thiết bị được phép xem (SQL filter chứng minh IAM-02)
            visible_devices_subq = (
                PermissionService.visible_devices_query(actor, db_session=session, now=now_utc)
                .with_only_columns(Device.device_id)
                .subquery()
            )

            stmt = select(Telemetry).where(
                Telemetry.device_id == device.device_id,
                Telemetry.device_id.in_(select(visible_devices_subq.c.device_id)),
                time_col.isnot(None),
                time_col >= params.from_utc,
                time_col < params.to_utc,
            )

            if not params.include_backfill:
                stmt = stmt.where(Telemetry.is_backfill.is_(False))

            if params.metric is not None:
                metric_col = METRIC_COLUMN_MAP[params.metric]
                stmt = stmt.where(metric_col.isnot(None))

            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = session.execute(count_stmt).scalar_one()

            order_fn = asc if params.order == "asc" else desc
            stmt = stmt.order_by(order_fn(time_col), order_fn(Telemetry.seq))

            offset = (params.page - 1) * params.page_size
            stmt = stmt.offset(offset).limit(params.page_size)

            rows = session.execute(stmt).scalars().all()
            items = [
                serialize_telemetry_row(row, data_source=device.data_source)
                for row in rows
            ]

            meta = {
                "page": params.page,
                "page_size": params.page_size,
                "total": int(total),
                "from": format_utc_iso(params.from_utc),
                "to": format_utc_iso(params.to_utc),
                "time_field": params.time_field,
            }
            return items, meta

    @staticmethod
    def query_statistics(
        device_id: str,
        params: TelemetryStatsQueryParams,
        *,
        actor: AuthContext,
        db_session: Session | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Tính thống kê min/max/avg/count và coverage/gaps trong khoảng [from, to) (Mục 7.2):
        - Tuyệt đối không biến giá trị NULL thành 0 (DATA-01).
        - Lọc phạm vi thiết bị trực tiếp tại tầng SQL (IAM-02).
        """
        session = db_session if db_session is not None else db.session
        now_utc = now if now is not None else datetime.now(timezone.utc)

        # 1. Kiểm tra quyền telemetry.read và VIEW scope
        PermissionService.require(
            "telemetry.read",
            device_id=device_id,
            actor=actor,
            db_session=session,
            now=now_utc,
        )

        with session.no_autoflush:
            device = TelemetryService._get_scoped_device(
                session, device_id=device_id, actor=actor, now_utc=now_utc
            )

            # Lấy chu kỳ gửi mẫu dự kiến từ cấu hình đã áp dụng hoặc mong muốn
            cfg_ver = device.applied_config_version or device.desired_config_version
            interval_ms = 5000 if device.device_type == "CONTROLLER" else 2000
            if cfg_ver is not None:
                cfg_interval = session.execute(
                    select(DeviceConfig.telemetry_interval_ms).where(
                        DeviceConfig.device_id == device.device_id,
                        DeviceConfig.version == cfg_ver,
                    )
                ).scalar_one_or_none()
                if cfg_interval is not None and cfg_interval > 0:
                    interval_ms = int(cfg_interval)

            window_seconds = max((params.to_utc - params.from_utc).total_seconds(), 0.0)

            if params.source is not None and params.source != device.data_source:
                return TelemetryService._build_empty_statistics(
                    device=device,
                    params=params,
                    interval_ms=interval_ms,
                    window_seconds=window_seconds,
                )

            time_col = (
                Telemetry.measured_at
                if params.time_field == "measured_at"
                else Telemetry.received_at
            )

            visible_devices_subq = (
                PermissionService.visible_devices_query(actor, db_session=session, now=now_utc)
                .with_only_columns(Device.device_id)
                .subquery()
            )

            filters = [
                Telemetry.device_id == device.device_id,
                Telemetry.device_id.in_(select(visible_devices_subq.c.device_id)),
                time_col.isnot(None),
                time_col >= params.from_utc,
                time_col < params.to_utc,
            ]
            if not params.include_backfill:
                filters.append(Telemetry.is_backfill.is_(False))

            agg_row = session.execute(
                select(
                    func.count(Telemetry.id).label("total_samples"),
                    func.min(Telemetry.temperature_c).label("temp_min"),
                    func.max(Telemetry.temperature_c).label("temp_max"),
                    func.avg(Telemetry.temperature_c).label("temp_avg"),
                    func.count(Telemetry.temperature_c).label("temp_count"),
                    func.min(Telemetry.humidity_pct).label("hum_min"),
                    func.max(Telemetry.humidity_pct).label("hum_max"),
                    func.avg(Telemetry.humidity_pct).label("hum_avg"),
                    func.count(Telemetry.humidity_pct).label("hum_count"),
                    func.min(Telemetry.rpm).label("rpm_min"),
                    func.max(Telemetry.rpm).label("rpm_max"),
                    func.avg(Telemetry.rpm).label("rpm_avg"),
                    func.count(Telemetry.rpm).label("rpm_count"),
                    func.sum(
                        case((Telemetry.fan_output.is_(True), 1), else_=0)
                    ).label("fan_on_count"),
                    func.sum(
                        case((Telemetry.sensor_ok.is_(False), 1), else_=0)
                    ).label("sensor_fault_count"),
                ).where(*filters)
            ).one()

            total_samples = int(agg_row.total_samples or 0)
            sample_period_s = interval_ms / 1000.0
            covered_seconds = min(round(total_samples * sample_period_s, 2), round(window_seconds, 2))
            gap_seconds = max(round(window_seconds - covered_seconds, 2), 0.0)
            coverage_ratio = (
                round(covered_seconds / window_seconds, 4)
                if window_seconds > 0
                else 0.0
            )

            metrics_data: dict[str, dict] = {}
            if device.device_type == "CONTROLLER":
                if params.metric in (None, "temperature_c"):
                    metrics_data["temperature_c"] = {
                        "min": finite_float_or_none(agg_row.temp_min),
                        "max": finite_float_or_none(agg_row.temp_max),
                        "avg": finite_float_or_none(agg_row.temp_avg),
                        "count": int(agg_row.temp_count or 0),
                        "unit": "C",
                    }
                if params.metric in (None, "humidity_pct"):
                    metrics_data["humidity_pct"] = {
                        "min": finite_float_or_none(agg_row.hum_min),
                        "max": finite_float_or_none(agg_row.hum_max),
                        "avg": finite_float_or_none(agg_row.hum_avg),
                        "count": int(agg_row.hum_count or 0),
                        "unit": "%RH",
                    }
            else:
                if params.metric in (None, "rpm"):
                    metrics_data["rpm"] = {
                        "min": finite_float_or_none(agg_row.rpm_min),
                        "max": finite_float_or_none(agg_row.rpm_max),
                        "avg": finite_float_or_none(agg_row.rpm_avg),
                        "count": int(agg_row.rpm_count or 0),
                        "unit": "RPM",
                    }

            return {
                "device_id": device.device_id,
                "profile": device.device_type,
                "data_source": device.data_source,
                "from": format_utc_iso(params.from_utc),
                "to": format_utc_iso(params.to_utc),
                "time_field": params.time_field,
                "total_samples": total_samples,
                "fan_on_count": int(agg_row.fan_on_count or 0),
                "sensor_fault_count": int(agg_row.sensor_fault_count or 0),
                "metrics": metrics_data,
                "coverage": {
                    "expected_interval_ms": interval_ms,
                    "window_seconds": round(window_seconds, 2),
                    "covered_seconds": covered_seconds,
                    "gap_seconds": gap_seconds,
                    "coverage_ratio": coverage_ratio,
                },
            }

    @staticmethod
    def _build_empty_statistics(
        *,
        device: Device,
        params: TelemetryStatsQueryParams,
        interval_ms: int,
        window_seconds: float,
    ) -> dict:
        metrics_data: dict[str, dict] = {}
        if device.device_type == "CONTROLLER":
            if params.metric in (None, "temperature_c"):
                metrics_data["temperature_c"] = {
                    "min": None,
                    "max": None,
                    "avg": None,
                    "count": 0,
                    "unit": "C",
                }
            if params.metric in (None, "humidity_pct"):
                metrics_data["humidity_pct"] = {
                    "min": None,
                    "max": None,
                    "avg": None,
                    "count": 0,
                    "unit": "%RH",
                }
        else:
            if params.metric in (None, "rpm"):
                metrics_data["rpm"] = {
                    "min": None,
                    "max": None,
                    "avg": None,
                    "count": 0,
                    "unit": "RPM",
                }
        return {
            "device_id": device.device_id,
            "profile": device.device_type,
            "data_source": device.data_source,
            "from": format_utc_iso(params.from_utc),
            "to": format_utc_iso(params.to_utc),
            "time_field": params.time_field,
            "total_samples": 0,
            "fan_on_count": 0,
            "sensor_fault_count": 0,
            "metrics": metrics_data,
            "coverage": {
                "expected_interval_ms": interval_ms,
                "window_seconds": round(window_seconds, 2),
                "covered_seconds": 0.0,
                "gap_seconds": round(window_seconds, 2),
                "coverage_ratio": 0.0,
            },
        }

    @staticmethod
    def export_csv(
        device_id: str,
        params: TelemetryExportQueryParams,
        *,
        actor: AuthContext,
        db_session: Session | None = None,
        now: datetime | None = None,
    ) -> str:
        """Xuất dữ liệu lịch sử telemetry của thiết bị ra chuỗi CSV (Mục 6.10, 7.2, 15.1):
        - Kiểm tra quyền telemetry.export và VIEW scope (trả 404 nếu ngoài scope).
        - Giới hạn tối đa 100.000 dòng đồng bộ.
        - Làm sạch mọi ô văn bản chống CSV Formula Injection.
        - Ghi nhận AuditLog và UserActivityLog (function_key='telemetry.export', action='EXPORT').
        """
        session = db_session if db_session is not None else db.session
        now_utc = now if now is not None else datetime.now(timezone.utc)

        # 1. Kiểm tra quyền telemetry.export và VIEW scope
        PermissionService.require(
            "telemetry.export",
            device_id=device_id,
            actor=actor,
            db_session=session,
            now=now_utc,
        )

        with session.no_autoflush:
            device = TelemetryService._get_scoped_device(
                session, device_id=device_id, actor=actor, now_utc=now_utc
            )

            actor_username = session.execute(
                select(User.username).where(User.id == actor.user_id)
            ).scalar_one_or_none()

            time_col = (
                Telemetry.measured_at
                if params.time_field == "measured_at"
                else Telemetry.received_at
            )

            visible_devices_subq = (
                PermissionService.visible_devices_query(actor, db_session=session, now=now_utc)
                .with_only_columns(Device.device_id)
                .subquery()
            )

            if params.source is not None and params.source != device.data_source:
                rows = []
            else:
                stmt = select(Telemetry).where(
                    Telemetry.device_id == device.device_id,
                    Telemetry.device_id.in_(select(visible_devices_subq.c.device_id)),
                    time_col.isnot(None),
                    time_col >= params.from_utc,
                    time_col < params.to_utc,
                )
                if not params.include_backfill:
                    stmt = stmt.where(Telemetry.is_backfill.is_(False))
                if params.metric is not None:
                    stmt = stmt.where(METRIC_COLUMN_MAP[params.metric].isnot(None))

                # Kiểm tra giới hạn 100.000 dòng trước khi nạp toàn bộ
                count_stmt = select(func.count()).select_from(stmt.subquery())
                total_rows = int(session.execute(count_stmt).scalar_one() or 0)
                if total_rows > MAX_EXPORT_ROWS:
                    raise TelemetryQueryValidationError(
                        f"Export row count ({total_rows}) exceeds limit of {MAX_EXPORT_ROWS}"
                    )

                order_fn = asc if params.order == "asc" else desc
                stmt = stmt.order_by(order_fn(time_col), order_fn(Telemetry.seq)).limit(MAX_EXPORT_ROWS)
                rows = session.execute(stmt).scalars().all()

        # 2. Sinh nội dung CSV với bảo vệ CSV Formula Injection
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(CSV_EXPORT_HEADERS)

        for row in rows:
            temp_val = finite_float_or_none(row.temperature_c)
            hum_val = finite_float_or_none(row.humidity_pct)
            writer.writerow([
                sanitize_csv_cell(row.device_id),
                sanitize_csv_cell(row.profile),
                sanitize_csv_cell(device.data_source),
                sanitize_csv_cell(format_utc_iso(row.received_at)),
                sanitize_csv_cell(format_utc_iso(row.measured_at)),
                sanitize_csv_cell(bool(row.clock_synced)),
                sanitize_csv_cell(bool(row.is_backfill)),
                sanitize_csv_cell(int(row.seq)),
                sanitize_csv_cell(str(row.boot_id)),
                sanitize_csv_cell(str(row.connection_id)),
                sanitize_csv_cell(int(row.uptime_ms)),
                sanitize_csv_cell(temp_val),
                "C" if temp_val is not None else "",
                sanitize_csv_cell(hum_val),
                "%RH" if hum_val is not None else "",
                sanitize_csv_cell(row.sensor_ok),
                sanitize_csv_cell(row.fan_output),
                sanitize_csv_cell(row.mode),
                sanitize_csv_cell(row.config_version),
                sanitize_csv_cell(row.config_hash),
                sanitize_csv_cell(row.fault_code),
            ])

        csv_content = output.getvalue()

        # 3. Ghi nhận AuditLog và UserActivityLog trong cùng transaction (Mục 6.10 & LOG-01)
        time_range_str = f"{format_utc_iso(params.from_utc)}..{format_utc_iso(params.to_utc)}"
        audit_entry = AuditService.record(
            action="TELEMETRY_EXPORT",
            target_type="DEVICE",
            target_id=device.device_id,
            device_id=device.device_id,
            success=True,
            actor=actor,
            actor_name_snapshot=actor_username,
            detail={
                "device_id": device.device_id,
                "row_count": len(rows),
                "time_range": time_range_str,
            },
            db_session=session,
        )
        UserActivityService.record(
            function_key="telemetry.export",
            action="EXPORT",
            outcome="SUCCESS",
            target_type="DEVICE",
            target_id=device.device_id,
            target_name_snapshot=device.name,
            device_id=device.device_id,
            actor=actor,
            actor_name_snapshot=actor_username,
            related_audit_log_id=audit_entry.id,
            summary_params={"device_id": device.device_id},
            metadata={
                "device_id": device.device_id,
                "row_count": len(rows),
                "time_range": time_range_str,
            },
            db_session=session,
        )
        session.commit()

        return csv_content

