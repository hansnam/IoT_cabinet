"""
Outbox Service Module (Thành viên 2 phụ trách - Pha B)
Quản lý việc đóng gói bản tin lệnh MQTT v3 (Mục 8.4) và ghi nhận vào bảng outbox_messages
cùng transaction với commands.
"""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.command import Command, OutboxMessage
from backend.app.shadows.service import format_utc_iso


def build_mqtt_command_topic(device_id: str) -> str:
    """Sinh MQTT topic nhận lệnh của thiết bị theo hợp đồng v3 (Mục 8)."""
    return f"cabinet/v3/devices/{device_id}/commands"


def build_mqtt_command_payload(command: Command) -> dict:
    """Đóng gói JSON payload chuẩn MQTT v3 (schema_version=3) cho lệnh điều khiển/cấu hình (Mục 8.4)."""
    return {
        "schema_version": 3,
        "device_id": command.device_id,
        "command_id": str(command.command_id),
        "type": command.type,
        "device_command_seq": int(command.device_command_seq),
        "expected_boot_id": str(command.expected_boot_id) if command.expected_boot_id else None,
        "expected_connection_id": (
            str(command.expected_connection_id) if command.expected_connection_id else None
        ),
        "params": command.params if isinstance(command.params, dict) else {},
        "issued_at": format_utc_iso(command.requested_at),
        "expires_at": format_utc_iso(command.expires_at),
    }


class OutboxService:
    """Dịch vụ quản lý hàng đợi Transactional Outbox trong PostgreSQL."""

    @staticmethod
    def enqueue_command(
        command: Command,
        *,
        db_session: Session,
        available_at: datetime | None = None,
    ) -> OutboxMessage:
        """Tạo bản ghi OutboxMessage gắn 1-1 với Command trong cùng transaction (QoS 1, retained=False)."""
        now_utc = available_at if available_at is not None else datetime.now(timezone.utc)
        outbox_entry = OutboxMessage(
            command_id=command.command_id,
            topic=build_mqtt_command_topic(command.device_id),
            payload=build_mqtt_command_payload(command),
            qos=1,
            retained=False,
            status="PENDING",
            available_at=now_utc,
            publish_count=0,
            created_at=now_utc,
        )
        db_session.add(outbox_entry)
        db_session.flush()
        return outbox_entry

    @staticmethod
    def cancel_pending_config_commands(
        device_id: str,
        *,
        db_session: Session,
        reason_code: str = "SUPERSEDED_BY_NEWER_CONFIG",
    ) -> int:
        """Hủy các lệnh APPLY_CONFIG cũ còn đang PENDING khi có phiên bản cấu hình mới hơn (Mục 7.2)."""
        pending_cmds = (
            db_session.execute(
                select(Command)
                .where(
                    Command.device_id == str(device_id),
                    Command.type == "APPLY_CONFIG",
                    Command.status == "PENDING",
                )
                .with_for_update()
            )
            .scalars()
            .all()
        )
        if not pending_cmds:
            return 0

        cmd_ids = [c.command_id for c in pending_cmds]
        for cmd in pending_cmds:
            cmd.status = "CANCELLED"
            cmd.error_code = reason_code

        outbox_rows = (
            db_session.execute(
                select(OutboxMessage)
                .where(
                    OutboxMessage.command_id.in_(cmd_ids),
                    OutboxMessage.status.in_(("PENDING", "LEASED")),
                )
                .with_for_update()
            )
            .scalars()
            .all()
        )
        for ob in outbox_rows:
            ob.status = "CANCELLED"
            ob.lease_until = None
            ob.locked_by = None
            ob.last_error = reason_code

        db_session.flush()
        return len(pending_cmds)
