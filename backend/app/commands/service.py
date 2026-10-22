"""
Config & Command Service Module (Thành viên 2 phụ trách - Pha B)
Thực hiện các nghiệp vụ:
1. Đọc cấu hình desired/applied của thiết bị (get_device_config).
2. Cập nhật cấu hình thiết bị với khóa dòng SELECT ... FOR UPDATE, kiểm tra base_version (409 CFG-01),
   Idempotency-Key, tạo bản ghi DeviceConfig + Command(APPLY_CONFIG) + OutboxMessage + Audit/Activity (update_device_config).
3. Tạo lệnh điều khiển thủ công SET_MODE / SET_FAN với kiểm tra capability, trạng thái thiết bị,
   chế độ MANUAL APPLIED, Idempotency-Key (CMD-01), khóa dòng tăng last_command_seq (create_command).
4. Tra cứu lịch sử lệnh theo thiết bị và chi tiết từng lệnh theo SQL scope (list_device_commands, get_command).
5. Ghi nhận DENIED transaction riêng khi người dùng có VIEW nhưng thiếu quyền điều khiển/cấu hình (IAM-01 & LOG-03).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.commands.schemas import (
    CommandConflictError,
    CommandListQueryParams,
    CommandValidationError,
    build_controller_config_snapshot,
    serialize_command,
    serialize_device_config,
    validate_command_payload,
    validate_controller_config_payload,
    validate_idempotency_key,
)
from backend.app.extensions import db
from backend.app.models.command import Command, CommandAttempt
from backend.app.models.device import Device, DeviceConfig
from backend.app.models.iam import User
from backend.app.models.telemetry import DeviceShadow, Telemetry
from backend.app.outbox.service import OutboxService
from backend.app.permissions.service import (
    DeviceScopeNotFound,
    PermissionDenied,
    PermissionService,
)
from backend.app.security.context import AuthContext
from backend.app.shadows.service import compute_telemetry_stale
from backend.app.user_activity.service import UserActivityService


COMMAND_EXPIRY_SECONDS = 15


class CommandService:
    """Service xử lý cấu hình thiết bị và lệnh điều khiển bất đồng bộ qua Transactional Outbox."""

    @staticmethod
    def _record_denied_mutation_and_commit(
        session: Session,
        *,
        actor: AuthContext,
        device_id: str,
        audit_action: str,
        function_key: str,
        activity_action: str,
        summary_params: dict,
        metadata: dict,
    ) -> None:
        """Ghi nhận AuditLog (success=False) và UserActivityLog (outcome='DENIED')
        khi bị từ chối quyền điều khiển/cấu hình (Mục 6.10, IAM-01, LOG-03).
        """
        actor_username = session.execute(
            select(User.username).where(User.id == actor.user_id)
        ).scalar_one_or_none()
        dev_name = session.execute(
            select(Device.name).where(Device.device_id == str(device_id))
        ).scalar_one_or_none()

        audit_entry = AuditService.record(
            action=audit_action,
            target_type="DEVICE",
            target_id=str(device_id),
            device_id=str(device_id),
            success=False,
            reason_code="FORBIDDEN",
            actor=actor,
            actor_name_snapshot=actor_username,
            detail=metadata,
            db_session=session,
        )
        UserActivityService.record(
            function_key=function_key,
            action=activity_action,
            outcome="DENIED",
            target_type="DEVICE",
            target_id=str(device_id),
            target_name_snapshot=dev_name,
            device_id=str(device_id),
            actor=actor,
            actor_name_snapshot=actor_username,
            related_audit_log_id=audit_entry.id,
            error_code="FORBIDDEN",
            summary_params=summary_params,
            metadata=metadata,
            db_session=session,
        )
        session.commit()

    @staticmethod
    def get_device_config(
        device_id: str,
        *,
        actor: AuthContext,
        db_session: Session | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Đọc cấu hình desired hiện tại, applied version và trạng thái pending (Mục 7.2)."""
        session = db_session if db_session is not None else db.session
        now_utc = now if now is not None else datetime.now(timezone.utc)

        PermissionService.require(
            "device.read",
            device_id=device_id,
            actor=actor,
            db_session=session,
            now=now_utc,
        )

        with session.no_autoflush:
            stmt = PermissionService.visible_devices_query(
                actor, db_session=session, now=now_utc
            ).where(Device.device_id == str(device_id))
            device = session.execute(stmt).scalar_one_or_none()
            if device is None:
                raise DeviceScopeNotFound()

            desired_cfg = None
            if device.desired_config_version is not None:
                desired_cfg = session.execute(
                    select(DeviceConfig).where(
                        DeviceConfig.device_id == device.device_id,
                        DeviceConfig.version == device.desired_config_version,
                    )
                ).scalar_one_or_none()

            applied_cfg = None
            if device.applied_config_version is not None:
                if (
                    desired_cfg is not None
                    and device.applied_config_version == device.desired_config_version
                ):
                    applied_cfg = desired_cfg
                else:
                    applied_cfg = session.execute(
                        select(DeviceConfig).where(
                            DeviceConfig.device_id == device.device_id,
                            DeviceConfig.version == device.applied_config_version,
                        )
                    ).scalar_one_or_none()

            pending = (
                device.desired_config_version is not None
                and device.desired_config_version != device.applied_config_version
            )

            return {
                "device_id": device.device_id,
                "profile": device.device_type,
                "desired_config_version": device.desired_config_version,
                "applied_config_version": device.applied_config_version,
                "pending": pending,
                "desired_config": serialize_device_config(desired_cfg),
                "applied_config": serialize_device_config(applied_cfg),
            }

    @staticmethod
    def update_device_config(
        device_id: str,
        raw_payload: Any,
        *,
        idempotency_key: str | None,
        actor: AuthContext,
        db_session: Session | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Cập nhật cấu hình thiết bị (PUT /api/v1/devices/<device_id>/config - Mục 6.3, 7.2, 8.5, CFG-01)."""
        session = db_session if db_session is not None else db.session
        now_utc = now if now is not None else datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        # 1. Kiểm tra quyền và VIEW/OPERATE scope trước khi thực hiện bất kỳ thao tác ghi nào
        try:
            PermissionService.require(
                "device.configure",
                device_id=device_id,
                actor=actor,
                db_session=session,
                now=now_utc,
            )
        except PermissionDenied:
            raw_base = (
                raw_payload.get("base_version", 0)
                if isinstance(raw_payload, dict)
                else 0
            )
            safe_ver = str(raw_base)[:16] if isinstance(raw_base, (int, str)) else "0"
            CommandService._record_denied_mutation_and_commit(
                session,
                actor=actor,
                device_id=device_id,
                audit_action="DEVICE_CONFIGURE",
                function_key="device.configure",
                activity_action="UPDATE",
                summary_params={"device_id": device_id, "config_version": safe_ver},
                metadata={"device_id": device_id, "reason": "Forbidden: missing configure permission or OPERATE scope"},
            )
            raise

        # 2. Validate Idempotency-Key và payload cấu hình
        idem_key = validate_idempotency_key(idempotency_key)
        validated_cfg = validate_controller_config_payload(raw_payload)

        # 3. Khóa dòng Device bằng SELECT ... FOR UPDATE (Mục 6.6)
        device = session.execute(
            select(Device)
            .where(Device.device_id == str(device_id))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if device is None:
            raise DeviceScopeNotFound()

        # Tái kiểm tra quyền sau khi khóa dòng để chống race condition thu hồi quyền
        PermissionService.require(
            "device.configure",
            device_id=device_id,
            actor=actor,
            db_session=session,
            now=now_utc,
        )

        if device.device_type != "CONTROLLER":
            raise CommandConflictError(
                "Unsupported profile for thermal controller configuration",
                code="UNSUPPORTED_CAPABILITY",
            )

        # 4. Kiểm tra Idempotency-Key trước khi kiểm tra base_version (cho phép retry cùng key trả lại kết quả cũ)
        existing_cmd = session.execute(
            select(Command).where(
                Command.requested_by == actor.user_id,
                Command.device_id == device.device_id,
                Command.idempotency_key == idem_key,
            )
        ).scalar_one_or_none()

        if existing_cmd is not None:
            if (
                existing_cmd.type != "APPLY_CONFIG"
                or existing_cmd.request_hash != validated_cfg.request_hash
            ):
                raise CommandConflictError(
                    "Idempotency-Key already used with a different request payload",
                    code="CONFLICT",
                )
            existing_cfg = session.execute(
                select(DeviceConfig).where(
                    DeviceConfig.device_id == device.device_id,
                    DeviceConfig.version == existing_cmd.config_version,
                )
            ).scalar_one_or_none()
            return {
                "device_id": device.device_id,
                "desired_config_version": device.desired_config_version,
                "applied_config_version": device.applied_config_version,
                "pending": device.desired_config_version != device.applied_config_version,
                "config": serialize_device_config(existing_cfg),
                "command": serialize_command(existing_cmd),
            }

        # 5. Chỉ cho phép lưu cấu hình mới khi lifecycle_state == 'ACTIVE' (Mục 7.2 dòng 595)
        if device.lifecycle_state != "ACTIVE":
            raise CommandConflictError(
                "Configuration can only be updated when device lifecycle is ACTIVE",
                code="CONFLICT",
            )

        # 6. Kiểm tra base_version (CFG-01: base_version cũ trả 409 CONFLICT)
        current_desired = int(device.desired_config_version or 0)
        if validated_cfg.base_version != current_desired:
            raise CommandConflictError(
                f"Stale base_version: expected {current_desired}, got {validated_cfg.base_version}",
                code="CONFLICT",
            )

        new_version = current_desired + 1
        snapshot, canonical_json, content_hash = build_controller_config_snapshot(
            config_version=new_version,
            fan_on_c=validated_cfg.fan_on_c,
            fan_off_c=validated_cfg.fan_off_c,
            temp_alert_c=validated_cfg.temp_alert_c,
            humidity_alert_pct=validated_cfg.humidity_alert_pct,
            temp_clear_margin_c=validated_cfg.temp_clear_margin_c,
            humidity_clear_margin_pct=validated_cfg.humidity_clear_margin_pct,
            telemetry_interval_ms=validated_cfg.telemetry_interval_ms,
            manual_timeout_s=validated_cfg.manual_timeout_s,
            extra=validated_cfg.extra,
        )

        # 7. Hủy các lệnh APPLY_CONFIG cũ chưa gửi (superseded) của thiết bị này
        OutboxService.cancel_pending_config_commands(device.device_id, db_session=session)

        # 8. Lưu bản ghi DeviceConfig mới
        new_cfg = DeviceConfig(
            id=uuid4(),
            device_id=device.device_id,
            version=new_version,
            profile="CONTROLLER",
            fan_binding_id=None,
            fan_on_c=float(validated_cfg.fan_on_c),
            fan_off_c=float(validated_cfg.fan_off_c),
            temp_alert_c=float(validated_cfg.temp_alert_c),
            humidity_alert_pct=float(validated_cfg.humidity_alert_pct),
            temp_clear_margin_c=float(validated_cfg.temp_clear_margin_c),
            humidity_clear_margin_pct=float(validated_cfg.humidity_clear_margin_pct),
            manual_timeout_s=validated_cfg.manual_timeout_s,
            telemetry_interval_ms=validated_cfg.telemetry_interval_ms,
            schema_version=3,
            extra={},
            canonical_json=canonical_json,
            content_hash=content_hash,
            created_by=actor.user_id,
            created_at=now_utc,
        )
        session.add(new_cfg)
        session.flush()

        # 9. Cập nhật con trỏ desired_config_version và tăng last_command_seq (KHÔNG đổi applied_config_version)
        next_cmd_seq = int(device.last_command_seq or 0) + 1
        device.last_command_seq = next_cmd_seq
        device.desired_config_version = new_version
        device.state_version = int(device.state_version or 1) + 1
        device.updated_at = now_utc

        # Cập nhật DeviceShadow desired
        shadow = session.execute(
            select(DeviceShadow).where(DeviceShadow.device_id == device.device_id).with_for_update()
        ).scalar_one_or_none()
        if shadow is None:
            shadow = DeviceShadow(
                device_id=device.device_id,
                desired=snapshot,
                reported={},
                desired_version=1,
                reported_version=0,
                desired_at=now_utc,
                drift_status="PENDING",
                updated_at=now_utc,
            )
            session.add(shadow)
        else:
            shadow.desired = snapshot
            shadow.desired_version = int(shadow.desired_version or 0) + 1
            shadow.desired_at = now_utc
            shadow.drift_status = "PENDING"
            shadow.updated_at = now_utc

        # 10. Tạo lệnh APPLY_CONFIG và đẩy vào Outbox
        cmd = Command(
            command_id=uuid4(),
            device_id=device.device_id,
            requested_by=actor.user_id,
            source="USER",
            requesting_session_id=actor.session_id,
            type="APPLY_CONFIG",
            params={
                "config_version": new_version,
                "config_json": canonical_json,
                "content_hash": content_hash,
            },
            config_version=new_version,
            expected_boot_id=device.current_boot_id,
            expected_connection_id=device.current_connection_id,
            device_command_seq=next_cmd_seq,
            idempotency_key=idem_key,
            request_hash=validated_cfg.request_hash,
            status="PENDING",
            requested_at=now_utc,
            expires_at=now_utc + timedelta(seconds=COMMAND_EXPIRY_SECONDS),
            attempts=0,
        )
        session.add(cmd)
        session.flush()

        OutboxService.enqueue_command(cmd, db_session=session, available_at=now_utc)

        # 11. Ghi nhận AuditLog và UserActivityLog (outcome='ACCEPTED' cho async 202)
        actor_username = session.execute(
            select(User.username).where(User.id == actor.user_id)
        ).scalar_one_or_none()

        audit_entry = AuditService.record(
            action="DEVICE_CONFIGURE",
            target_type="DEVICE",
            target_id=device.device_id,
            device_id=device.device_id,
            success=True,
            actor=actor,
            actor_name_snapshot=actor_username,
            detail={
                "device_id": device.device_id,
                "config_version": new_version,
                "command_id": str(cmd.command_id),
            },
            db_session=session,
        )
        UserActivityService.record(
            function_key="device.configure",
            action="UPDATE",
            outcome="ACCEPTED",
            target_type="DEVICE",
            target_id=device.device_id,
            target_name_snapshot=device.name,
            device_id=device.device_id,
            actor=actor,
            actor_name_snapshot=actor_username,
            related_audit_log_id=audit_entry.id,
            summary_params={
                "device_id": device.device_id,
                "config_version": new_version,
            },
            metadata={
                "device_id": device.device_id,
                "config_version": new_version,
                "command_id": str(cmd.command_id),
            },
            db_session=session,
        )

        session.commit()

        return {
            "device_id": device.device_id,
            "desired_config_version": device.desired_config_version,
            "applied_config_version": device.applied_config_version,
            "pending": True,
            "config": serialize_device_config(new_cfg),
            "command": serialize_command(cmd),
        }

    @staticmethod
    def create_command(
        device_id: str,
        raw_payload: Any,
        *,
        idempotency_key: str | None,
        actor: AuthContext,
        db_session: Session | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Tạo lệnh điều khiển thủ công SET_MODE / SET_FAN (POST /api/v1/devices/<device_id>/commands):
        - Kiểm tra quyền device.control + OPERATE scope (nếu chỉ có VIEW -> ghi DENIED log và trả 403 - IAM-01).
        - Kiểm tra Idempotency-Key (CMD-01).
        - Kiểm tra capability CONTROLLER (FAN_MONITOR -> 409 UNSUPPORTED_CAPABILITY).
        - Từ chối khi OFFLINE, LOCKED, DECOMMISSIONED, MAINTENANCE, FAULT, clock chưa sync hoặc dữ liệu stale.
        - Lệnh SET_FAN yêu cầu thiết bị đang ở chế độ MANUAL đã APPLIED.
        """
        session = db_session if db_session is not None else db.session
        now_utc = now if now is not None else datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        # 1. Kiểm tra quyền và VIEW/OPERATE scope (ghi DENIED log nếu bị từ chối 403 - IAM-01)
        try:
            PermissionService.require(
                "device.control",
                device_id=device_id,
                actor=actor,
                db_session=session,
                now=now_utc,
            )
        except PermissionDenied:
            raw_type = (
                raw_payload.get("type", "UNKNOWN")
                if isinstance(raw_payload, dict)
                else "UNKNOWN"
            )
            safe_type = (
                str(raw_type).strip().upper()[:32]
                if isinstance(raw_type, str) and str(raw_type).strip()
                else "UNKNOWN"
            )
            CommandService._record_denied_mutation_and_commit(
                session,
                actor=actor,
                device_id=device_id,
                audit_action="DEVICE_CONTROL",
                function_key="device.control",
                activity_action="CONTROL",
                summary_params={"device_id": device_id, "command_type": safe_type},
                metadata={
                    "device_id": device_id,
                    "command_type": safe_type,
                    "reason": "Forbidden: missing control permission or OPERATE scope",
                },
            )
            raise

        # 2. Validate Idempotency-Key
        idem_key = validate_idempotency_key(idempotency_key)

        # 3. Khóa dòng Device bằng SELECT ... FOR UPDATE
        device = session.execute(
            select(Device)
            .where(Device.device_id == str(device_id))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if device is None:
            raise DeviceScopeNotFound()

        # Tái kiểm tra quyền sau khi khóa dòng
        PermissionService.require(
            "device.control",
            device_id=device_id,
            actor=actor,
            db_session=session,
            now=now_utc,
        )

        # Lấy manual_timeout_s từ cấu hình đã áp dụng hoặc mong muốn (mặc định 60s)
        cfg_ver = device.applied_config_version or device.desired_config_version
        default_manual_timeout = 60
        interval_ms = 5000
        if cfg_ver is not None:
            cfg_row = session.execute(
                select(
                    DeviceConfig.manual_timeout_s,
                    DeviceConfig.telemetry_interval_ms,
                ).where(
                    DeviceConfig.device_id == device.device_id,
                    DeviceConfig.version == cfg_ver,
                )
            ).first()
            if cfg_row is not None:
                if cfg_row.manual_timeout_s is not None:
                    default_manual_timeout = int(cfg_row.manual_timeout_s)
                if cfg_row.telemetry_interval_ms is not None:
                    interval_ms = int(cfg_row.telemetry_interval_ms)

        validated_cmd = validate_command_payload(
            raw_payload,
            default_manual_timeout_s=default_manual_timeout,
        )

        # 4. Kiểm tra capability (FAN_MONITOR không hỗ trợ SET_MODE / SET_FAN -> 409 UNSUPPORTED_CAPABILITY)
        if device.device_type != "CONTROLLER":
            raise CommandConflictError(
                "Device profile does not support SET_MODE or SET_FAN",
                code="UNSUPPORTED_CAPABILITY",
            )

        # 5. Kiểm tra Idempotency-Key (CMD-01: cùng key + cùng payload trả lại cùng command_id, không tăng TTL)
        existing_cmd = session.execute(
            select(Command).where(
                Command.requested_by == actor.user_id,
                Command.device_id == device.device_id,
                Command.idempotency_key == idem_key,
            )
        ).scalar_one_or_none()

        if existing_cmd is not None:
            if (
                existing_cmd.type != validated_cmd.command_type
                or existing_cmd.request_hash != validated_cmd.request_hash
            ):
                raise CommandConflictError(
                    "Idempotency-Key already used with a different command payload",
                    code="CONFLICT",
                )
            return serialize_command(existing_cmd)

        # 6. Kiểm tra trạng thái thiết bị (Mục 7.2 dòng 594):
        # Từ chối khi không ACTIVE, không ONLINE, đang FAULT, clock chưa tin cậy hoặc dữ liệu stale
        if device.lifecycle_state != "ACTIVE":
            raise CommandConflictError(
                f"Cannot issue manual command in lifecycle_state={device.lifecycle_state}",
                code="CONFLICT",
            )
        if device.connection_state != "ONLINE":
            raise CommandConflictError(
                "Cannot issue manual command while device is not ONLINE",
                code="CONFLICT",
            )
        if device.health_state == "FAULT":
            raise CommandConflictError(
                "Cannot issue manual command while device is in FAULT state",
                code="CONFLICT",
            )

        latest_telemetry = session.execute(
            select(Telemetry)
            .where(
                Telemetry.device_id == device.device_id,
                Telemetry.is_backfill.is_(False),
            )
            .order_by(desc(Telemetry.received_at), desc(Telemetry.seq))
            .limit(1)
        ).scalar_one_or_none()

        shadow = session.execute(
            select(DeviceShadow).where(DeviceShadow.device_id == device.device_id)
        ).scalar_one_or_none()

        ref_time = (
            latest_telemetry.received_at
            if latest_telemetry is not None
            else (
                (shadow.reported_at if shadow is not None else None)
                or device.last_telemetry_at
                or device.last_seen_at
            )
        )
        if compute_telemetry_stale(
            device,
            ref_time,
            telemetry_interval_ms=interval_ms,
            now=now_utc,
        ):
            raise CommandConflictError(
                "Cannot issue manual command when device state snapshot is stale",
                code="CONFLICT",
            )

        if latest_telemetry is not None:
            if latest_telemetry.sensor_ok is False:
                raise CommandConflictError(
                    "Cannot issue manual command while sensor fault is active",
                    code="CONFLICT",
                )
            if latest_telemetry.clock_synced is False:
                raise CommandConflictError(
                    "Cannot issue manual command when device clock is not synchronized",
                    code="CONFLICT",
                )

        # 7. Với SET_FAN: bắt buộc thiết bị đang ở chế độ MANUAL đã APPLIED (Mục 7.2 dòng 591 & Mục 8.4 dòng 744)
        if validated_cmd.command_type == "SET_FAN":
            reported_mode = None
            if latest_telemetry is not None and latest_telemetry.mode is not None:
                reported_mode = latest_telemetry.mode
            elif shadow is not None and isinstance(shadow.reported, dict):
                reported_mode = shadow.reported.get("mode")

            if reported_mode != "MANUAL":
                # Kiểm tra xem có lệnh SET_MODE(MANUAL) đã APPLIED trên cùng boot/connection gần nhất không
                last_mode_cmd = session.execute(
                    select(Command)
                    .where(
                        Command.device_id == device.device_id,
                        Command.type == "SET_MODE",
                        Command.status == "APPLIED",
                    )
                    .order_by(desc(Command.device_command_seq))
                    .limit(1)
                ).scalar_one_or_none()

                is_manual_applied = (
                    last_mode_cmd is not None
                    and isinstance(last_mode_cmd.params, dict)
                    and last_mode_cmd.params.get("mode") == "MANUAL"
                    and (
                        device.current_boot_id is None
                        or last_mode_cmd.expected_boot_id == device.current_boot_id
                    )
                )
                if not is_manual_applied:
                    raise CommandConflictError(
                        "SET_FAN requires device to be in APPLIED MANUAL mode first",
                        code="CONFLICT",
                    )

        # 8. Tăng device_command_seq và tạo Command + OutboxMessage trong cùng transaction
        next_cmd_seq = int(device.last_command_seq or 0) + 1
        device.last_command_seq = next_cmd_seq
        device.updated_at = now_utc

        cmd = Command(
            command_id=uuid4(),
            device_id=device.device_id,
            requested_by=actor.user_id,
            source="USER",
            requesting_session_id=actor.session_id,
            type=validated_cmd.command_type,
            params=validated_cmd.params,
            config_version=None,
            expected_boot_id=device.current_boot_id,
            expected_connection_id=device.current_connection_id,
            device_command_seq=next_cmd_seq,
            idempotency_key=idem_key,
            request_hash=validated_cmd.request_hash,
            status="PENDING",
            requested_at=now_utc,
            expires_at=now_utc + timedelta(seconds=COMMAND_EXPIRY_SECONDS),
            attempts=0,
        )
        session.add(cmd)
        session.flush()

        OutboxService.enqueue_command(cmd, db_session=session, available_at=now_utc)

        # 9. Ghi nhận AuditLog và UserActivityLog (outcome='ACCEPTED' cho async 202)
        actor_username = session.execute(
            select(User.username).where(User.id == actor.user_id)
        ).scalar_one_or_none()

        metadata_payload: dict = {
            "device_id": device.device_id,
            "command_id": str(cmd.command_id),
            "command_type": cmd.type,
        }
        if cmd.type == "SET_MODE":
            metadata_payload["mode"] = str(cmd.params.get("mode"))
        elif cmd.type == "SET_FAN":
            metadata_payload["fan_state"] = "ON" if cmd.params.get("on") else "OFF"

        audit_entry = AuditService.record(
            action="DEVICE_CONTROL",
            target_type="DEVICE",
            target_id=device.device_id,
            device_id=device.device_id,
            success=True,
            actor=actor,
            actor_name_snapshot=actor_username,
            detail={
                "device_id": device.device_id,
                "command_id": str(cmd.command_id),
                "command_type": cmd.type,
            },
            db_session=session,
        )
        UserActivityService.record(
            function_key="device.control",
            action="CONTROL",
            outcome="ACCEPTED",
            target_type="DEVICE",
            target_id=device.device_id,
            target_name_snapshot=device.name,
            device_id=device.device_id,
            actor=actor,
            actor_name_snapshot=actor_username,
            related_audit_log_id=audit_entry.id,
            summary_params={
                "device_id": device.device_id,
                "command_type": cmd.type,
            },
            metadata=metadata_payload,
            db_session=session,
        )

        session.commit()
        return serialize_command(cmd)

    @staticmethod
    def list_device_commands(
        device_id: str,
        params: CommandListQueryParams,
        *,
        actor: AuthContext,
        db_session: Session | None = None,
        now: datetime | None = None,
    ) -> tuple[list[dict], dict]:
        """Lấy danh sách lịch sử lệnh của thiết bị trong phạm vi quyền (Mục 7.2)."""
        session = db_session if db_session is not None else db.session
        now_utc = now if now is not None else datetime.now(timezone.utc)

        PermissionService.require(
            "command.read",
            device_id=device_id,
            actor=actor,
            db_session=session,
            now=now_utc,
        )

        with session.no_autoflush:
            visible_devices_subq = (
                PermissionService.visible_devices_query(actor, db_session=session, now=now_utc)
                .with_only_columns(Device.device_id)
                .subquery()
            )

            stmt = select(Command).where(
                Command.device_id == str(device_id),
                Command.device_id.in_(select(visible_devices_subq.c.device_id)),
            )

            if params.status is not None:
                stmt = stmt.where(Command.status == params.status)
            if params.command_type is not None:
                stmt = stmt.where(Command.type == params.command_type)
            if params.from_utc is not None:
                stmt = stmt.where(Command.requested_at >= params.from_utc)
            if params.to_utc is not None:
                stmt = stmt.where(Command.requested_at < params.to_utc)

            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = int(session.execute(count_stmt).scalar_one() or 0)

            order_fn = asc if params.order == "asc" else desc
            stmt = stmt.order_by(order_fn(Command.requested_at), order_fn(Command.device_command_seq))

            offset = (params.page - 1) * params.page_size
            stmt = stmt.offset(offset).limit(params.page_size)

            cmds = session.execute(stmt).scalars().all()
            items = [serialize_command(c) for c in cmds]

            meta = {
                "page": params.page,
                "page_size": params.page_size,
                "total": total,
            }
            return items, meta

    @staticmethod
    def get_command_by_id(
        command_id: str,
        *,
        actor: AuthContext,
        db_session: Session | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Lấy chi tiết một lệnh kèm lịch sử command_attempts theo command_id (Mục 7.2 & IAM-02)."""
        session = db_session if db_session is not None else db.session
        now_utc = now if now is not None else datetime.now(timezone.utc)

        try:
            cmd_uuid = UUID(str(command_id).strip())
        except (ValueError, TypeError) as exc:
            raise DeviceScopeNotFound() from exc

        with session.no_autoflush:
            cmd = session.execute(
                select(Command).where(Command.command_id == cmd_uuid)
            ).scalar_one_or_none()
            if cmd is None:
                raise DeviceScopeNotFound()

            # Kiểm tra quyền command.read và VIEW scope trên thiết bị đích (trả 404 nếu ngoài scope - IAM-02)
            PermissionService.require(
                "command.read",
                device_id=cmd.device_id,
                actor=actor,
                db_session=session,
                now=now_utc,
            )

            visible_devices_subq = (
                PermissionService.visible_devices_query(actor, db_session=session, now=now_utc)
                .with_only_columns(Device.device_id)
                .subquery()
            )
            scoped_cmd = session.execute(
                select(Command).where(
                    Command.command_id == cmd_uuid,
                    Command.device_id.in_(select(visible_devices_subq.c.device_id)),
                )
            ).scalar_one_or_none()
            if scoped_cmd is None:
                raise DeviceScopeNotFound()

            attempts_list = (
                session.execute(
                    select(CommandAttempt)
                    .where(CommandAttempt.command_id == scoped_cmd.command_id)
                    .order_by(asc(CommandAttempt.attempt_no))
                )
                .scalars()
                .all()
            )

            return serialize_command(scoped_cmd, attempts_list=list(attempts_list))
