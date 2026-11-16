"""Quản lý MQTT qua CLI và REST API thông qua device_mqtt_service."""
from getpass import getpass

from django.core.management.base import BaseCommand, CommandError

from apps.devices.device_mqtt_service import (
    disable_device_mqtt,
    enable_device_mqtt,
    provision_device_mqtt,
)
from apps.devices.models import Device


class Command(BaseCommand):
    help = 'MQTT: manage_device_mqtt <device_id> provision|enable|disable. Provision chỉ dành cho client mới.'

    def add_arguments(self, parser):
        parser.add_argument('device_id')
        parser.add_argument('action', choices=['provision', 'enable', 'disable'])

    def handle(self, *args, **options):
        """Chỉ cập nhật trạng thái DB sau khi broker xác nhận enable/disable thành công."""
        device_id, action = options['device_id'], options['action']
        try:
            device = Device.objects.get(pk=device_id)
        except Device.DoesNotExist as exc:
            raise CommandError('Tạo thiết bị trong hệ thống trước.') from exc

        if action == 'provision':
            password = getpass('Mật khẩu MQTT cho ESP32 (không lưu vào database): ')
            if len(password) < 12:
                raise CommandError('Mật khẩu cần ít nhất 12 ký tự.')
            provision_device_mqtt(device, password)
            self.stdout.write('Client mới đang bị vô hiệu hóa. Cấu hình ESP32 rồi chạy enable để bật.')
        elif action == 'enable':
            enable_device_mqtt(device)
        else:
            disable_device_mqtt(device)

        self.stdout.write(self.style.SUCCESS(f'Broker đã xác nhận {action} cho {device_id}.'))
