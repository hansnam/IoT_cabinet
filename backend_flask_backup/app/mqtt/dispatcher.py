"""
MQTT Outbox Dispatcher, Retry Scheduler & Timeout/Offline Sweepers
(Thành viên 2 phụ trách - Mục 6.4, 7.2, 8.4, CMD-01, CMD-03, CMD-04, OPS-01)
Thực hiện:
1. Reclaim các outbox_messages bị kẹt LEASED khi worker crash (CMD-03).
2. Kiểm tra lại quyền/session của người gửi và trạng thái thiết bị trước khi gửi (CMD-04).
3. Đảm bảo mỗi thiết bị chỉ có tối đa 1 lệnh đang SENT chờ ACK (uq_commands_single_sent_per_device).
4. Lập lịch retry tại +3s và +6s kể từ sent_at (tối đa 3 attempts), timeout tại +10s hoặc expires_at (Mục 8.4).
5. Quét thiết bị mất kết nối quá 45s (HEARTBEAT_TIMEOUT_45S) khi chưa nhận LWT (Mục 8.3 & 9.2).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.extensions import db
from backend.app.models.command import Command, CommandAttempt, OutboxMessage
from backend.app.models.device import Device, DeviceAccess
from backend.app.models.iam import AuthSession, Permission, Role, RolePermission, User, UserRole
from backend.app.models.telemetry import DeviceEvent, DeviceShadow
from backend.app.shadows.service import compute_telemetry_stale, format_utc_iso

COMMAND_RETRY_OFFSETS_S = (3, 6)
COMMAND_ACK_TIMEOUT_S = 10
MAX_COMMAND_ATTEMPTS = 3
DEFAULT_OFFLINE_TIMEOUT_S = 45


@dataclass
class DispatchBatchResult:
    """Thống kê một chu kỳ chạy của Outbox Dispatcher."""

    reclaimed_leases: int = 0
    scheduled_retries: int = 0
    timed_out_commands: int = 0
    cancelled_commands: int = 0
    published_commands: int = 0
    failed_publishes: int = 0
    dispatched_command_ids: list[uuid.UUID] = field(default_factory=list)


class MqttDispatcherService:
    """Dịch vụ điều phối gửi lệnh từ Outbox, lập lịch retry, xử lý timeout và quét offline."""

    @staticmethod
    def _user_has_permission_and_operate_scope(
        user_id: uuid.UUID,
        permission_code: str,
        device_id: str,
        now_utc: datetime,
        db_session: Session,
    ) -> bool:
        """Kiểm tra trực tiếp trong DB xem user còn giữ role có permission_code và quyền OPERATE trên device_id không."""
        role_codes = (
            db_session.execute(
                select(Role.code)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == user_id)
            )
            .scalars()
            .all()
        )
        if not role_codes:
            return False

        if "ADMIN" in role_codes:
            return True

        has_perm = (
            db_session.execute(
                select(Permission.id)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .join(UserRole, UserRole.role_id == RolePermission.role_id)
                .where(
                    UserRole.user_id == user_id,
                    Permission.code == permission_code,
                )
                .limit(1)
            )
            .scalars()
            .first()
        )
        if has_perm is None:
            return False

        grant = (
            db_session.execute(
                select(DeviceAccess.id).where(
                    DeviceAccess.device_id == device_id,
                    DeviceAccess.user_id == user_id,
                    DeviceAccess.access_level == "OPERATE",
                    or_(
                        DeviceAccess.expires_at.is_(None),
                        DeviceAccess.expires_at > now_utc,
                    ),
                )
            )
            .scalars()
            .first()
        )
        return grant is not None

    @classmethod
    def _verify_command_actor_authorization(
        cls,
        command: Command,
        device: Device,
        now_utc: datetime,
        db_session: Session,
    ) -> tuple[bool, str | None]:
        """Kiểm tra lại quyền và phiên của người tạo lệnh trước khi gửi lần đầu (Mục 7.2, 8.4 & CMD-04)."""
        if command.source == "SYSTEM":
            if command.type != "APPLY_CONFIG":
                return False, "INVALID_SYSTEM_COMMAND_TYPE"
            return True, None

        if command.requested_by is None:
            return False, "MISSING_ACTOR"

        user = (
            db_session.execute(
                select(User).where(User.id == command.requested_by)
            )
            .scalars()
            .first()
        )
        is_apply_config = command.type == "APPLY_CONFIG"

        if user is None or user.status != "ACTIVE" or bool(user.must_change_password):
            return False, ("BLOCKED_AUTHORIZATION" if is_apply_config else "ACTOR_INACTIVE")

        # Với lệnh thủ công (SET_MODE, SET_FAN), phiên đăng nhập phải còn hiệu lực (Mục 8.4 & CMD-04)
        if not is_apply_config:
            if command.requesting_session_id is None:
                return False, "MISSING_ACTOR_SESSION"
            session_row = (
                db_session.execute(
                    select(AuthSession).where(AuthSession.id == command.requesting_session_id)
                )
                .scalars()
                .first()
            )
            if (
                session_row is None
                or session_row.user_id != user.id
                or session_row.revoked_at is not None
                or session_row.expires_at <= now_utc
                or int(session_row.token_version) != int(user.token_version)
            ):
                return False, "ACTOR_SESSION_REVOKED"

        required_perm = "device.configure" if is_apply_config else "device.control"
        if not cls._user_has_permission_and_operate_scope(
            user.id, required_perm, device.device_id, now_utc, db_session
        ):
            return False, (
                "BLOCKED_AUTHORIZATION" if is_apply_config else "ACTOR_PERMISSION_REVOKED"
            )

        return True, None

    @staticmethod
    def _verify_device_ready_for_command(
        command: Command,
        device: Device,
        now_utc: datetime,
        db_session: Session,
    ) -> tuple[bool, str | None]:
        """Kiểm tra trạng thái thiết bị trước khi gửi lệnh lần đầu (Mục 7.2 & 8.4)."""
        if device.lifecycle_state in ("LOCKED", "DECOMMISSIONED", "REGISTERED", "PROVISIONED"):
            return False, "DEVICE_LIFECYCLE_NOT_READY"

        if command.type == "APPLY_CONFIG":
            if device.lifecycle_state not in ("ACTIVE", "MAINTENANCE"):
                return False, "DEVICE_LIFECYCLE_NOT_READY"
            return True, None

        # Lệnh thủ công SET_MODE / SET_FAN
        if device.device_type != "CONTROLLER":
            return False, "UNSUPPORTED_CAPABILITY"
        if device.lifecycle_state != "ACTIVE":
            return False, "DEVICE_NOT_ACTIVE"
        if device.connection_state != "ONLINE":
            return False, "DEVICE_OFFLINE"
        if device.health_state == "FAULT":
            return False, "DEVICE_IN_FAULT"

        shadow = (
            db_session.execute(
                select(DeviceShadow).where(DeviceShadow.device_id == device.device_id)
            )
            .scalars()
            .first()
        )
        reported = shadow.reported if (shadow and isinstance(shadow.reported, dict)) else {}
        if reported.get("mode") == "FAULT" or reported.get("sensor_ok") is False:
            return False, "DEVICE_IN_FAULT"
        if reported.get("clock_synced") is False:
            return False, "CLOCK_NOT_SYNCED"
        if compute_telemetry_stale(
            device,
            device.last_telemetry_at or device.last_seen_at,
            now=now_utc,
        ):
            return False, "TELEMETRY_STALE"

        return True, None

    @classmethod
    def reclaim_expired_outbox_leases(
        cls,
        *,
        now_utc: datetime | None = None,
        db_session: Session | None = None,
        commit: bool = False,
    ) -> int:
        """Thu hồi các bản ghi outbox_messages có status='LEASED' nhưng đã quá hạn lease_until (CMD-03)."""
        sess = db_session if db_session is not None else db.session
        now = now_utc if now_utc is not None else datetime.now(timezone.utc)

        expired_rows = (
            sess.execute(
                select(OutboxMessage)
                .where(
                    OutboxMessage.status == "LEASED",
                    OutboxMessage.lease_until.isnot(None),
                    OutboxMessage.lease_until <= now,
                )
                .with_for_update()
            )
            .scalars()
            .all()
        )
        if not expired_rows:
            return 0

        for ob in expired_rows:
            ob.status = "PENDING"
            ob.lease_until = None
            ob.locked_by = None
            ob.available_at = now
            ob.last_error = "LEASE_EXPIRED_RECLAIMED"

            # Đánh dấu các CommandAttempt bị treo chưa có kết quả khi worker trước crash
            dangling_attempts = (
                sess.execute(
                    select(CommandAttempt)
                    .where(
                        CommandAttempt.command_id == ob.command_id,
                        CommandAttempt.outcome.is_(None),
                    )
                    .with_for_update()
                )
                .scalars()
                .all()
            )
            for att in dangling_attempts:
                att.outcome = "LEASE_EXPIRED"
                att.error_code = "WORKER_LEASE_EXPIRED"

        sess.flush()
        if commit:
            sess.commit()
        return len(expired_rows)

    @classmethod
    def schedule_command_retries(
        cls,
        *,
        now_utc: datetime | None = None,
        db_session: Session | None = None,
        commit: bool = False,
    ) -> int:
        """Chuyển OutboxMessage từ PUBLISHED về PENDING khi lệnh đang SENT chưa nhận ACK
        và đã đến mốc retry +3s (attempt 2) hoặc +6s (attempt 3) kể từ sent_at (Mục 6.4 & 8.4).
        """
        sess = db_session if db_session is not None else db.session
        now = now_utc if now_utc is not None else datetime.now(timezone.utc)

        rows = (
            sess.execute(
                select(Command, OutboxMessage)
                .join(OutboxMessage, OutboxMessage.command_id == Command.command_id)
                .where(
                    Command.status == "SENT",
                    Command.sent_at.isnot(None),
                    Command.attempts < MAX_COMMAND_ATTEMPTS,
                    Command.expires_at > now,
                    OutboxMessage.status == "PUBLISHED",
                )
                .with_for_update()
            )
            .all()
        )
        scheduled_count = 0
        for cmd, ob in rows:
            if cmd.sent_at is None:
                continue
            # Không retry nếu đã chạm ngưỡng ACK timeout 10s
            if now >= cmd.sent_at + timedelta(seconds=COMMAND_ACK_TIMEOUT_S):
                continue

            attempts_done = int(cmd.attempts or 1)
            if attempts_done == 1:
                retry_due_at = cmd.sent_at + timedelta(seconds=COMMAND_RETRY_OFFSETS_S[0])
            elif attempts_done == 2:
                retry_due_at = cmd.sent_at + timedelta(seconds=COMMAND_RETRY_OFFSETS_S[1])
            else:
                continue

            if now >= retry_due_at:
                ob.status = "PENDING"
                ob.available_at = now
                scheduled_count += 1

        if scheduled_count > 0:
            sess.flush()
            if commit:
                sess.commit()
        return scheduled_count

    @classmethod
    def sweep_command_timeouts(
        cls,
        *,
        now_utc: datetime | None = None,
        db_session: Session | None = None,
        commit: bool = False,
    ) -> int:
        """Đánh dấu TIMEOUT cho các lệnh SENT quá 10s từ sent_at (hoặc quá expires_at)
        và các lệnh PENDING đã quá expires_at (Mục 8.4 & CMD-01).
        """
        sess = db_session if db_session is not None else db.session
        now = now_utc if now_utc is not None else datetime.now(timezone.utc)

        timed_out_count = 0

        # 1. Các lệnh đang SENT quá 10s từ sent_at hoặc quá expires_at
        sent_cmds = (
            sess.execute(
                select(Command)
                .where(Command.status == "SENT")
                .with_for_update()
            )
            .scalars()
            .all()
        )
        for cmd in sent_cmds:
            ack_deadline = (
                cmd.sent_at + timedelta(seconds=COMMAND_ACK_TIMEOUT_S)
                if cmd.sent_at is not None
                else cmd.expires_at
            )
            if now >= ack_deadline or now >= cmd.expires_at:
                cmd.status = "TIMEOUT"
                cmd.error_code = "ACK_TIMEOUT"
                timed_out_count += 1

                latest_att = (
                    sess.execute(
                        select(CommandAttempt)
                        .where(CommandAttempt.command_id == cmd.command_id)
                        .order_by(CommandAttempt.attempt_no.desc())
                        .limit(1)
                        .with_for_update()
                    )
                    .scalars()
                    .first()
                )
                if latest_att is not None and latest_att.ack_received_at is None:
                    latest_att.outcome = "TIMEOUT"
                    latest_att.error_code = "ACK_TIMEOUT"

                ob = (
                    sess.execute(
                        select(OutboxMessage)
                        .where(OutboxMessage.command_id == cmd.command_id)
                        .with_for_update()
                    )
                    .scalars()
                    .first()
                )
                if ob is not None and ob.status in ("PENDING", "LEASED"):
                    ob.status = "FAILED"
                    ob.lease_until = None
                    ob.locked_by = None
                    ob.last_error = "ACK_TIMEOUT"

        # 2. Các lệnh còn PENDING nhưng đã vượt quá expires_at (15s từ requested_at)
        expired_pending_cmds = (
            sess.execute(
                select(Command)
                .where(
                    Command.status == "PENDING",
                    Command.expires_at <= now,
                )
                .with_for_update()
            )
            .scalars()
            .all()
        )
        for cmd in expired_pending_cmds:
            cmd.status = "TIMEOUT"
            cmd.error_code = "COMMAND_EXPIRED"
            timed_out_count += 1

            ob = (
                sess.execute(
                    select(OutboxMessage)
                    .where(OutboxMessage.command_id == cmd.command_id)
                    .with_for_update()
                )
                .scalars()
                .first()
            )
            if ob is not None and ob.status in ("PENDING", "LEASED"):
                ob.status = "CANCELLED"
                ob.lease_until = None
                ob.locked_by = None
                ob.last_error = "COMMAND_EXPIRED"

        if timed_out_count > 0:
            sess.flush()
            if commit:
                sess.commit()
        return timed_out_count

    @classmethod
    def sweep_offline_devices(
        cls,
        *,
        now_utc: datetime | None = None,
        offline_timeout_s: int = DEFAULT_OFFLINE_TIMEOUT_S,
        db_session: Session | None = None,
        commit: bool = False,
    ) -> int:
        """Quét các thiết bị đang ONLINE nhưng không có gói tin hợp lệ trong 45s để chuyển sang OFFLINE (Mục 8.3 & 12.4)."""
        sess = db_session if db_session is not None else db.session
        now = now_utc if now_utc is not None else datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=offline_timeout_s)

        stale_online_devices = (
            sess.execute(
                select(Device)
                .where(
                    Device.connection_state == "ONLINE",
                    Device.last_seen_at.isnot(None),
                    Device.last_seen_at <= cutoff,
                )
                .with_for_update()
            )
            .scalars()
            .all()
        )
        if not stale_online_devices:
            return 0

        for device in stale_online_devices:
            device.connection_state = "OFFLINE"
            device.updated_at = now

            last_seen_ts = int(device.last_seen_at.timestamp()) if device.last_seen_at else 0
            dedup_key = f"offline_timeout:{device.current_connection_id or 'none'}:{last_seen_ts}"

            existing_ev = (
                sess.execute(
                    select(DeviceEvent.id).where(
                        DeviceEvent.device_id == device.device_id,
                        DeviceEvent.dedup_key == dedup_key,
                    )
                )
                .scalars()
                .first()
            )
            if existing_ev is None:
                sess.add(
                    DeviceEvent(
                        id=uuid.uuid4(),
                        device_id=device.device_id,
                        event_type="OFFLINE",
                        boot_id=device.current_boot_id,
                        connection_id=device.current_connection_id,
                        seq=device.latest_seq,
                        occurred_at=now,
                        received_at=now,
                        source="SYSTEM",
                        details={
                            "reason": "HEARTBEAT_TIMEOUT_45S",
                            "last_seen_at": format_utc_iso(device.last_seen_at),
                        },
                        dedup_key=dedup_key,
                    )
                )

            shadow = (
                sess.execute(
                    select(DeviceShadow)
                    .where(DeviceShadow.device_id == device.device_id)
                    .with_for_update()
                )
                .scalars()
                .first()
            )
            if shadow is not None:
                shadow.drift_status = "STALE"
                shadow.updated_at = now

        sess.flush()
        if commit:
            sess.commit()
        return len(stale_online_devices)

    @classmethod
    def dispatch_pending_outbox(
        cls,
        publish_fn: Callable[[str, str, int, bool], bool],
        *,
        worker_id: str = "mqtt-worker-1",
        lease_seconds: int = 5,
        batch_size: int = 20,
        now_utc: datetime | None = None,
        db_session: Session | None = None,
        commit: bool = True,
    ) -> DispatchBatchResult:
        """Chu kỳ xử lý chính của Outbox Dispatcher:
        1. Thu hồi lease hết hạn.
        2. Quét timeout lệnh quá hạn.
        3. Lập lịch retry (+3s, +6s).
        4. Claim và publish các bản tin PENDING theo đúng thứ tự device_command_seq.
        """
        sess = db_session if db_session is not None else db.session
        now = now_utc if now_utc is not None else datetime.now(timezone.utc)
        stats = DispatchBatchResult()

        stats.reclaimed_leases = cls.reclaim_expired_outbox_leases(
            now_utc=now, db_session=sess, commit=False
        )
        stats.timed_out_commands = cls.sweep_command_timeouts(
            now_utc=now, db_session=sess, commit=False
        )
        stats.scheduled_retries = cls.schedule_command_retries(
            now_utc=now, db_session=sess, commit=False
        )

        candidates = (
            sess.execute(
                select(OutboxMessage, Command)
                .join(Command, Command.command_id == OutboxMessage.command_id)
                .where(
                    OutboxMessage.status == "PENDING",
                    OutboxMessage.available_at <= now,
                )
                .order_by(Command.device_command_seq.asc(), OutboxMessage.created_at.asc())
                .limit(batch_size)
                .with_for_update()
            )
            .all()
        )

        for ob, cmd in candidates:
            device = (
                sess.execute(
                    select(Device)
                    .where(Device.device_id == cmd.device_id)
                    .with_for_update()
                )
                .scalars()
                .first()
            )
            if device is None:
                cmd.status = "CANCELLED"
                cmd.error_code = "UNKNOWN_DEVICE"
                ob.status = "CANCELLED"
                ob.last_error = "UNKNOWN_DEVICE"
                stats.cancelled_commands += 1
                continue

            if cmd.status not in ("PENDING", "SENT"):
                ob.status = "CANCELLED"
                ob.last_error = f"COMMAND_ALREADY_{cmd.status}"
                continue

            # Đảm bảo chỉ có tối đa 1 lệnh đang SENT trên mỗi thiết bị (uq_commands_single_sent_per_device)
            other_sent = (
                sess.execute(
                    select(Command.command_id)
                    .where(
                        Command.device_id == cmd.device_id,
                        Command.status == "SENT",
                        Command.command_id != cmd.command_id,
                    )
                    .limit(1)
                )
                .scalars()
                .first()
            )
            if other_sent is not None:
                # Chờ lệnh đang SENT hoàn tất hoặc timeout trước khi gửi lệnh kế tiếp
                continue

            # Nếu là lần gửi đầu tiên (cmd.status == 'PENDING'), kiểm tra lại quyền/session và trạng thái thiết bị
            if cmd.status == "PENDING":
                auth_ok, auth_err = cls._verify_command_actor_authorization(
                    cmd, device, now, sess
                )
                if not auth_ok:
                    cmd.status = "CANCELLED"
                    cmd.error_code = auth_err
                    ob.status = "CANCELLED"
                    ob.last_error = auth_err
                    stats.cancelled_commands += 1
                    continue

                ready_ok, ready_err = cls._verify_device_ready_for_command(
                    cmd, device, now, sess
                )
                if not ready_ok:
                    cmd.status = "CANCELLED"
                    cmd.error_code = ready_err
                    ob.status = "CANCELLED"
                    ob.last_error = ready_err
                    stats.cancelled_commands += 1
                    continue

                # Với APPLY_CONFIG: nếu thiết bị đang OFFLINE thì giữ PENDING chờ reconnect hoặc hết hạn
                if cmd.type == "APPLY_CONFIG" and device.connection_state != "ONLINE":
                    continue

            max_att_no = (
                sess.execute(
                    select(func.max(CommandAttempt.attempt_no)).where(
                        CommandAttempt.command_id == cmd.command_id
                    )
                )
                .scalars()
                .first()
            )
            attempt_no = int(max_att_no or 0) + 1
            if attempt_no > MAX_COMMAND_ATTEMPTS:
                continue

            # Cập nhật expected_boot_id / expected_connection_id nếu lúc tạo lệnh còn trống
            payload_dict = dict(ob.payload) if isinstance(ob.payload, dict) else {}
            if cmd.expected_boot_id is None and device.current_boot_id is not None:
                cmd.expected_boot_id = device.current_boot_id
                payload_dict["expected_boot_id"] = str(device.current_boot_id)
            if cmd.expected_connection_id is None and device.current_connection_id is not None:
                cmd.expected_connection_id = device.current_connection_id
                payload_dict["expected_connection_id"] = str(device.current_connection_id)
            ob.payload = payload_dict

            # Claim lease trước khi publish sang MQTT
            ob.status = "LEASED"
            ob.locked_by = worker_id
            ob.lease_until = now + timedelta(seconds=lease_seconds)

            attempt_row = CommandAttempt(
                id=uuid.uuid4(),
                command_id=cmd.command_id,
                attempt_no=attempt_no,
                dispatch_started_at=now,
                worker_id=worker_id,
            )
            sess.add(attempt_row)
            sess.flush()

            payload_json = json.dumps(
                payload_dict,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )

            try:
                pub_ok = bool(
                    publish_fn(
                        str(ob.topic),
                        payload_json,
                        int(ob.qos),
                        bool(ob.retained),
                    )
                )
            except Exception as exc:
                pub_ok = False
                ob.last_error = f"PUBLISH_EXCEPTION: {exc}"

            if pub_ok:
                confirmed_at = datetime.now(timezone.utc)
                attempt_row.broker_confirmed_at = confirmed_at
                attempt_row.outcome = "PUBLISHED"

                cmd.status = "SENT"
                cmd.attempts = attempt_no
                if cmd.sent_at is None:
                    cmd.sent_at = now

                ob.status = "PUBLISHED"
                ob.publish_count = int(ob.publish_count or 0) + 1
                ob.published_at = confirmed_at
                ob.lease_until = None
                ob.locked_by = None
                ob.last_error = None

                stats.published_commands += 1
                stats.dispatched_command_ids.append(cmd.command_id)
            else:
                attempt_row.outcome = "PUBLISH_FAILED"
                attempt_row.error_code = "BROKER_PUBLISH_FAILED"

                ob.status = "PENDING"
                ob.available_at = now + timedelta(seconds=1)
                ob.lease_until = None
                ob.locked_by = None
                if not ob.last_error:
                    ob.last_error = "BROKER_PUBLISH_FAILED"

                stats.failed_publishes += 1

        sess.flush()
        if commit:
            sess.commit()

        return stats
