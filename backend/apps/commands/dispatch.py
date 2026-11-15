"""Một đường gửi outbox dùng chung cho API và worker, tránh gửi trùng từ hai nơi."""
import uuid

from django.db import transaction
from django.utils import timezone

from apps.accounts.policies.dispatch import dispatch_decision
from apps.audit.services import AuditService
from apps.commands.models import Command, OutboxMessage


@transaction.atomic
def dispatch_outbox(outbox_id, publisher):
    """Khóa outbox, kiểm quyền mới nhất rồi gửi; MQTT PUBACK chưa phải ESP32 đã thực thi."""
    command_id = OutboxMessage.objects.values_list('command_id', flat=True).get(pk=outbox_id)
    # ACK cũng khóa Command trước Outbox: cùng thứ tự khóa để tránh deadlock.
    command = Command.objects.select_for_update(of=('self',)).select_related('device').get(pk=command_id)
    msg = OutboxMessage.objects.select_for_update().get(pk=outbox_id)
    if msg.status != 'PENDING':
        return msg.status == 'PUBLISHED'
    allowed, reason = dispatch_decision(command)
    if not allowed:
        msg.status, msg.last_error = 'FAILED', reason
        msg.save(update_fields=['status', 'last_error'])
        if command.status == 'PENDING':
            command.status, command.error_message = 'CANCELLED', reason
            command.save(update_fields=['status', 'error_message'])
        AuditService.record_event(
            actor_type='SYSTEM', function_key='COMMAND_DISPATCH', action='DISPATCH', outcome='DENIED',
            summary='Từ chối gửi lệnh do quyền hoặc trạng thái đã thay đổi', request_id=uuid.uuid4(),
            device_id=command.device_id, metadata={'command_id': str(command.pk), 'reason': reason},
        )
        return False
    # Khóa '_' chỉ phục vụ backend, không gửi xuống ESP32.
    payload = {key: value for key, value in command.payload.items() if not key.startswith('_')}
    sent = publisher(command.device_id, payload)
    msg.retry_count += 1
    if sent:
        msg.status, msg.published_at, msg.last_error = 'PUBLISHED', timezone.now(), ''
        # ACK có thể đến rất nhanh; không ghi đè trạng thái APPLIED.
        type(command).objects.filter(pk=command.pk, status='PENDING').update(status='SENT', sent_at=timezone.now())
    else:
        msg.last_error = 'BROKER_UNAVAILABLE'
    msg.save(update_fields=['status', 'published_at', 'retry_count', 'last_error'])
    return sent
