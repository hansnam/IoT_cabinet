"""
Dịch vụ quản trị vòng đời tài khoản MQTT cho từng tủ mạng (UI-06).
Tách riêng logic nghiệp vụ dùng chung cho cả REST API và CLI management command.
Tuân thủ đặc tả Mosquitto Dynamic Security (Mosquitto 2.0.18):
- Tạo Role gắn ACL chặt chẽ (không wildcard).
- Quy trình tạo Client an toàn (createClient không password -> disableClient -> setClientPassword).
- Mật khẩu sinh ra/tiếp nhận KHÔNG BAO GIỜ lưu vào CSDL, URL hoặc AuditLog.
"""
import secrets
import string

from django.core.management.base import CommandError

from apps.devices.broker_config import get_runtime_mqtt_config
from apps.devices.broker_security import broker_command, device_acl
from apps.devices.models import Device


def generate_secure_mqtt_password(length: int = 16) -> str:
    """Tạo mật khẩu ngẫu nhiên an toàn gồm chữ hoa, chữ thường và chữ số."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        password = ''.join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in password)
                and any(c.isupper() for c in password)
                and any(c.isdigit() for c in password)):
            return password


def get_device_mqtt_metadata(device: Device) -> dict:
    """
    Lấy thông tin tài khoản MQTT của tủ mạng:
    - Cấu hình Host/Port/TLS hiện hành của Broker.
    - Trạng thái tài khoản trên Broker (đã cấp hay chưa, đang bật hay tắt).
    - Quy tắc ACL topic tương ứng.
    """
    runtime_cfg = get_runtime_mqtt_config(for_admin=False)
    device_id = device.id

    info = {
        'device_id': device_id,
        'broker_host': runtime_cfg['host'],
        'broker_port': runtime_cfg['port'],
        'use_tls': runtime_cfg['use_tls'],
        'username': device_id,
        'client_id': device_id,
        'topic_prefix': f"cabinet/v3/devices/{device_id}",
        'topics': {
            'telemetry': f"cabinet/v3/devices/{device_id}/telemetry",
            'status': f"cabinet/v3/devices/{device_id}/status",
            'acks': f"cabinet/v3/devices/{device_id}/acks",
            'commands': f"cabinet/v3/devices/{device_id}/commands",
        },
        'broker_connected': False,
        'account_exists': False,
        'is_disabled': True,
        'broker_error': '',
    }

    try:
        data = broker_command('getClient', username=device_id)
        info['broker_connected'] = True
        client_data = data.get('client', {})
        if client_data:
            info['account_exists'] = True
            info['is_disabled'] = bool(client_data.get('disabled', False))
            info['roles'] = client_data.get('roles', [])
        else:
            info['account_exists'] = False
    except CommandError as e:
        err_msg = str(e)
        if 'từ chối' in err_msg or 'not found' in err_msg.lower():
            # Broker hoạt động bình thường nhưng chưa có tài khoản cho thiết bị này
            info['broker_connected'] = True
            info['account_exists'] = False
        else:
            info['broker_connected'] = False
            info['broker_error'] = err_msg
    except Exception as e:
        info['broker_connected'] = False
        info['broker_error'] = str(e)

    return info


def provision_device_mqtt(device: Device, password: str | None = None) -> tuple[str, dict]:
    """
    Khởi tạo tài khoản MQTT cho thiết bị:
    1. Tạo Role với ACL đúng topic tủ (không cấp wildcard).
    2. Tạo Client với role tương ứng.
    3. Disable ngay lập tức để bảo đảm an toàn.
    4. Đặt mật khẩu xác thực.
    5. Cập nhật trạng thái Device trong DB thành REGISTERED.
    Trả về: (mật khẩu dạng plaintext, metadata)
    """
    device_id = device.id
    if not password:
        password = generate_secure_mqtt_password(16)
    elif len(password) < 12:
        raise CommandError('Mật khẩu MQTT cho tủ cần có độ dài tối thiểu 12 ký tự.')

    rules = device_acl(device_id)
    role_name = f'device-{device_id}'

    # 1. Tạo role trước
    try:
        broker_command('createRole', rolename=role_name, acls=rules)
    except CommandError:
        pass  # Nếu role đã tồn tại từ trước thì bỏ qua

    # 2. Tạo Client không kèm password (Mosquitto 2.0.18 compatibility)
    try:
        broker_command('createClient', username=device_id, clientid=device_id, roles=[{'rolename': role_name}])
    except CommandError as e:
        # Nếu client đã tồn tại, không ghi đè bừa bãi
        raise CommandError(f"Tài khoản '{device_id}' đã tồn tại trên Broker. Dùng chức năng Đặt lại mật khẩu nếu muốn đổi.") from e

    # 3. Disable client và đặt password
    try:
        broker_command('disableClient', username=device_id)
        broker_command('setClientPassword', username=device_id, password=password)
    except Exception:
        # Dọn dẹp nếu bước sau thất bại
        try:
            broker_command('deleteClient', username=device_id)
            broker_command('deleteRole', rolename=role_name)
        except Exception:
            pass
        raise

    device.status = 'REGISTERED'
    device.save(update_fields=['status'])

    return password, {
        'username': device_id,
        'client_id': device_id,
        'status': 'REGISTERED',
        'is_disabled': True,
    }


def enable_device_mqtt(device: Device) -> dict:
    """Kích hoạt tài khoản MQTT của tủ trên Broker và chuyển trạng thái tủ sang ACTIVE."""
    if device.status == 'DECOMMISSIONED':
        raise CommandError('Không thể kích hoạt tài khoản của tủ đã ngừng sử dụng (DECOMMISSIONED).')

    broker_command('enableClient', username=device.id)
    device.status = 'ACTIVE'
    device.save(update_fields=['status'])
    return {'username': device.id, 'status': 'ACTIVE', 'is_disabled': False}


def disable_device_mqtt(device: Device) -> dict:
    """Vô hiệu hóa tài khoản MQTT trên Broker (ngắt kết nối thật) và chuyển trạng thái tủ sang LOCKED."""
    broker_command('disableClient', username=device.id)
    device.status = 'LOCKED'
    device.save(update_fields=['status'])
    return {'username': device.id, 'status': 'LOCKED', 'is_disabled': True}


def reset_device_mqtt_password(device: Device, new_password: str | None = None) -> str:
    """Đặt lại mật khẩu MQTT cho tủ trên Broker. Trả về mật khẩu mới (chỉ hiển thị một lần)."""
    if not new_password:
        new_password = generate_secure_mqtt_password(16)
    elif len(new_password) < 12:
        raise CommandError('Mật khẩu MQTT cần có độ dài tối thiểu 12 ký tự.')

    broker_command('setClientPassword', username=device.id, password=new_password)
    return new_password
