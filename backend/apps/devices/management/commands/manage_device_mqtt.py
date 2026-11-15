"""Quản lý MQTT qua CLI để tránh thêm màn hình và bảng chứa credential."""
from getpass import getpass

from django.core.management.base import BaseCommand, CommandError

from apps.devices.broker_security import broker_command, device_acl
from apps.devices.models import Device


class Command(BaseCommand):
    help = 'MQTT: manage_device_mqtt <device_id> provision|enable|disable. Provision chỉ dành cho client mới.'

    def add_arguments(self, parser):
        parser.add_argument('device_id')
        parser.add_argument('action', choices=['provision', 'enable', 'disable'])

    def handle(self, *args, **options):
        """Chỉ cập nhật trạng thái DB sau khi broker xác nhận enable/disable thành công."""
        device_id, action = options['device_id'], options['action']
        rules = device_acl(device_id)
        try:
            device = Device.objects.get(pk=device_id)
        except Device.DoesNotExist as exc:
            raise CommandError('Tạo thiết bị trong Django Admin trước.') from exc
        if action == 'provision':
            password = getpass('Mật khẩu MQTT cho ESP32 (không lưu vào database): ')
            if len(password) < 12:
                raise CommandError('Mật khẩu cần ít nhất 12 ký tự.')
            # Role được tạo trước client. Nếu client đã tồn tại, broker từ chối thay vì ghi đè mật khẩu.
            role = f'device-{device_id}'
            broker_command('createRole', rolename=role, acls=rules)
            try:
                # Mosquitto 2.0.18 không đọc disabled trong createClient.
                # Tạo client CHƯA có mật khẩu, disable rồi mới đặt mật khẩu để không có cửa sổ đăng nhập.
                broker_command('createClient', username=device_id, clientid=device_id,
                               roles=[{'rolename': role}])
            except CommandError:
                # Dọn role vừa tạo nếu client không được tạo; không sửa client cũ.
                broker_command('deleteRole', rolename=role)
                raise
            broker_command('disableClient', username=device_id)
            broker_command('setClientPassword', username=device_id, password=password)
            device.status = 'REGISTERED'
            device.save(update_fields=['status'])
            self.stdout.write('Client mới đang bị vô hiệu hóa. Cấu hình ESP32 rồi chạy enable để bật.')
        else:
            if action == 'enable' and device.status == 'DECOMMISSIONED':
                raise CommandError('Không bật lại tủ đã ngừng sử dụng.')
            broker_command('enableClient' if action == 'enable' else 'disableClient', username=device_id)
            device.status = 'ACTIVE' if action == 'enable' else 'LOCKED'
            device.save(update_fields=['status'])
        self.stdout.write(self.style.SUCCESS(f'Broker đã xác nhận {action} cho {device_id}.'))
