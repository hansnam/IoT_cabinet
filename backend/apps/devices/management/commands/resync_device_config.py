"""Gửi lại đúng cấu hình thiết bị đã xác nhận, không tự áp dụng cấu hình mới chưa duyệt."""
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.commands.dispatch import dispatch_outbox
from apps.commands.models import Command as DeviceCommand, OutboxMessage
from apps.audit.services import AuditService
from apps.devices.models import Device, DeviceConfig
from apps.devices.mqtt_client import publish_command_to_broker
from apps.devices.views import build_esp32_config_json_and_hash


class Command(BaseCommand):
    help = 'Gửi lại cấu hình đã APPLIED: resync_device_config <device_id>'

    def add_arguments(self, parser):
        parser.add_argument('device_id')

    def handle(self, *args, **options):
        """Tạo lệnh SYSTEM tường minh từ snapshot đã applied; vẫn qua guard trước publish."""
        with transaction.atomic():
            device = Device.objects.select_for_update().filter(pk=options['device_id']).first()
            if not device or device.status != 'ACTIVE':
                raise CommandError('Tủ phải tồn tại và đang ACTIVE.')
            config = DeviceConfig.objects.filter(device=device, applied=True).order_by('-version').first()
            if not config:
                raise CommandError('Chưa có cấu hình nào được thiết bị xác nhận APPLIED.')
            config_json, content_hash = build_esp32_config_json_and_hash(config.config_data)
            correlation = str(uuid.uuid4())
            payload = {
                'schema_version': 3, 'command_id': correlation, 'correlation_id': correlation,
                'device_id': device.pk, 'type': 'APPLY_CONFIG', 'command': 'APPLY_CONFIG',
                'device_command_seq': DeviceCommand.objects.filter(device=device).count() + 1,
                'config_version': config.version, 'config': config.config_data,
                'params': {'config_version': config.version, 'config_json': config_json, 'content_hash': content_hash},
                'timestamp': timezone.now().isoformat(), '_source': 'APPLIED_CONFIG_RESYNC',
            }
            command = DeviceCommand.objects.create(device=device, command_type='APPLY_CONFIG',
                actor=None, payload=payload, correlation_id=correlation)
            outbox = OutboxMessage.objects.create(command=command, payload=payload,
                topic=f'cabinet/v3/devices/{device.pk}/commands')
            AuditService.record_event(
                actor_type='SYSTEM', function_key='DEVICE_CONFIG', action='CONFIG_RESYNC', outcome='ACCEPTED',
                summary='Gửi lại cấu hình đã được thiết bị xác nhận', request_id=uuid.uuid4(),
                device_id=device.pk, metadata={'command_id': str(command.pk)},
            )
        sent = dispatch_outbox(outbox.pk, publish_command_to_broker)
        self.stdout.write(f'Lệnh {command.pk}: {"SENT" if sent else "chưa gửi; kiểm tra outbox"}.')
