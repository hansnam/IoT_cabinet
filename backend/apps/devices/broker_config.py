"""
Quản lý cấu hình MQTT Broker tại thời điểm chạy (Runtime) và kiểm tra kết nối.
Hỗ trợ chuyển tiếp liền mạch giữa cấu hình môi trường (.env) và CSDL (UI-05).
"""
import ipaddress
import logging
import re
import socket
import tempfile
import threading
import uuid

import paho.mqtt.client as mqtt
from django.conf import settings

from apps.devices.crypto import decrypt_secret

logger = logging.getLogger(__name__)

# Giới hạn kích thước chứng chỉ CA (32 KB)
MAX_CA_CERT_SIZE = 32 * 1024

_HOSTNAME_RE = re.compile(
    r'^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$|^[a-zA-Z0-9-]{1,63}$'
)


def validate_broker_host(host: str) -> str:
    """Xác thực hostname hoặc địa chỉ IP an toàn, ngăn chặn chèn URL hay lệnh lạ."""
    if not host or not isinstance(host, str):
        raise ValueError("Địa chỉ host không được để trống.")
    cleaned = host.strip()
    # Kiểm tra nếu là địa chỉ IPv4/IPv6 hợp lệ
    try:
        ipaddress.ip_address(cleaned)
        return cleaned
    except ValueError:
        pass
    # Kiểm tra hostname hợp lệ
    if not _HOSTNAME_RE.fullmatch(cleaned):
        raise ValueError("Địa chỉ host không hợp lệ (chỉ chấp nhận hostname hoặc địa chỉ IP).")
    return cleaned


def validate_broker_port(port: int | str) -> int:
    """Xác thực cổng mạng hợp lệ (1 - 65535)."""
    try:
        p = int(port)
        if 1 <= p <= 65535:
            return p
    except (ValueError, TypeError):
        pass
    raise ValueError("Cổng kết nối phải là số nguyên từ 1 đến 65535.")


def validate_ca_pem(pem: str) -> str:
    """Xác thực chuỗi chứng chỉ PEM cơ bản và giới hạn kích thước."""
    if not pem:
        return ''
    cleaned = pem.strip()
    if len(cleaned.encode('utf-8')) > MAX_CA_CERT_SIZE:
        raise ValueError(f"Chứng chỉ CA vượt quá dung lượng cho phép ({MAX_CA_CERT_SIZE // 1024} KB).")
    if 'BEGIN CERTIFICATE' not in cleaned or 'END CERTIFICATE' not in cleaned:
        raise ValueError("Nội dung chứng chỉ CA không đúng định dạng PEM tiêu chuẩn.")
    if 'PRIVATE KEY' in cleaned:
        raise ValueError("Cảnh báo bảo mật: Không được tải lên Private Key trong trường chứng chỉ CA công khai.")
    import ssl
    try:
        ssl.create_default_context(cadata=cleaned)
    except ssl.SSLError as exc:
        raise ValueError('Chứng chỉ CA không hợp lệ hoặc không đọc được.') from exc
    return cleaned


def get_runtime_mqtt_config(for_admin: bool = False) -> dict:
    """
    Đọc cấu hình MQTT Broker đang hoạt động.
    Ưu tiên bản ghi trong CSDL; nếu chưa có, dùng giá trị trong settings/.env.
    """
    from apps.devices.models import BrokerConnection
    try:
        conn = BrokerConnection.objects.first()
    except Exception:
        conn = None

    if not conn:
        return {
            'host': getattr(settings, 'MQTT_BROKER_HOST', '127.0.0.1'),
            'port': int(getattr(settings, 'MQTT_BROKER_PORT', 1883)),
            'use_tls': bool(getattr(settings, 'MQTT_CA_FILE', '')),
            'ca_file': getattr(settings, 'MQTT_CA_FILE', ''),
            'ca_cert_pem': '',
            'username': getattr(settings, 'MQTT_ADMIN_USERNAME' if for_admin else 'MQTT_USERNAME', ''),
            'password': getattr(settings, 'MQTT_ADMIN_PASSWORD' if for_admin else 'MQTT_PASSWORD', ''),
            'keepalive': int(getattr(settings, 'MQTT_KEEPALIVE', 60)),
            'revision': 0,
            'source': 'SETTINGS',
        }

    username = conn.admin_username if for_admin else conn.worker_username
    enc_pw = conn.admin_password_encrypted if for_admin else conn.worker_password_encrypted
    password = ''
    if enc_pw:
        try:
            password = decrypt_secret(enc_pw)
        except Exception as e:
            logger.error("Không thể giải mã mật khẩu MQTT từ CSDL: %s", e)

    return {
        'host': conn.host,
        'port': conn.port,
        'use_tls': conn.use_tls,
        'ca_file': getattr(settings, 'MQTT_CA_FILE', ''),
        'ca_cert_pem': conn.ca_cert_pem,
        'username': username,
        'password': password,
        'keepalive': int(getattr(settings, 'MQTT_KEEPALIVE', 60)),
        'revision': conn.revision,
        'source': 'DATABASE',
    }


def test_broker_connection(draft: dict) -> dict:
    """
    Kiểm tra thử nghiệm cấu hình Broker (TCP, Worker Auth, Admin DynSec).
    Không lưu hay ảnh hưởng cấu hình đang chạy.
    """
    host = draft.get('host', '127.0.0.1')
    port = draft.get('port', 1883)
    use_tls = bool(draft.get('use_tls', False))
    ca_pem = draft.get('ca_cert_pem', '')
    worker_user = draft.get('worker_username', '')
    worker_pass = draft.get('worker_password', '')
    admin_user = draft.get('admin_username', '')
    admin_pass = draft.get('admin_password', '')

    steps = []

    # 1. Bước 1: Kiểm tra kết nối TCP socket
    tcp_ok = False
    try:
        with socket.create_connection((host, int(port)), timeout=3.0):
            tcp_ok = True
        steps.append({'name': 'TCP Socket', 'ok': True, 'message': f'Cổng {host}:{port} phản hồi thành công.'})
    except Exception as e:
        steps.append({'name': 'TCP Socket', 'ok': False, 'message': f'Không thể mở kết nối tới {host}:{port}: {e}'})
        return {
            'success': False,
            'steps': steps,
            'message': f'Lỗi kết nối mạng cấp thấp tới cổng {port}.',
        }

    # Helper ghi CA tạm nếu có PEM
    ca_temp_file = None
    if use_tls and ca_pem:
        ca_temp_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.pem')
        ca_temp_file.write(ca_pem)
        ca_temp_file.flush()
        ca_temp_file.close()

    # 2. Bước 2: Kiểm tra kết nối MQTT Worker
    try:
        client_worker = mqtt.Client(client_id=f"django-test-worker-{uuid.uuid4().hex[:8]}")
        if worker_user:
            client_worker.username_pw_set(worker_user, worker_pass)
        if use_tls:
            if ca_temp_file:
                client_worker.tls_set(ca_certs=ca_temp_file.name)
            elif getattr(settings, 'MQTT_CA_FILE', ''):
                client_worker.tls_set(ca_certs=settings.MQTT_CA_FILE)
            else:
                client_worker.tls_set()  # Vẫn bật TLS, sử dụng CA của hệ thống.

        conn_event = threading.Event()
        conn_result = {'rc': None}

        def on_connect_worker(c, userdata, flags, rc):
            conn_result['rc'] = rc
            conn_event.set()

        client_worker.on_connect = on_connect_worker
        client_worker.connect_async(host, int(port), keepalive=10)
        client_worker.loop_start()

        got_response = conn_event.wait(timeout=3.5)
        client_worker.loop_stop()
        client_worker.disconnect()

        if got_response and conn_result['rc'] == 0:
            steps.append({'name': 'Xác thực Worker', 'ok': True, 'message': 'Đăng nhập Worker thành công.'})
        elif got_response:
            rc_names = {1: 'Sai phiên bản protocol', 2: 'Sai định danh', 3: 'Broker không khả dụng', 4: 'Sai tài khoản/mật khẩu', 5: 'Không có quyền truy cập'}
            reason = rc_names.get(conn_result['rc'], f'Mã lỗi {conn_result["rc"]}')
            steps.append({'name': 'Xác thực Worker', 'ok': False, 'message': f'Broker từ chối tài khoản Worker: {reason}'})
        else:
            steps.append({'name': 'Xác thực Worker', 'ok': False, 'message': 'Hết thời gian chờ phản hồi đăng nhập từ Broker (Timeout).'})
    except Exception as e:
        steps.append({'name': 'Xác thực Worker', 'ok': False, 'message': f'Lỗi kết nối Worker: {e}'})

    # 3. Bước 3: Kiểm tra tài khoản Quản trị Dynamic Security (nếu cấu hình)
    if admin_user:
        try:
            client_admin = mqtt.Client(client_id=f"django-test-admin-{uuid.uuid4().hex[:8]}")
            client_admin.username_pw_set(admin_user, admin_pass)
            if use_tls:
                if ca_temp_file:
                    client_admin.tls_set(ca_certs=ca_temp_file.name)
                elif getattr(settings, 'MQTT_CA_FILE', ''):
                    client_admin.tls_set(ca_certs=settings.MQTT_CA_FILE)
                else:
                    client_admin.tls_set()

            admin_event = threading.Event()
            admin_result = {'ok': False, 'reason': ''}
            correlation = uuid.uuid4().hex

            def on_connect_admin(c, userdata, flags, rc):
                if rc == 0:
                    c.subscribe('$CONTROL/dynamic-security/v1/response', qos=1)
                else:
                    admin_result['reason'] = f'Sai tài khoản hoặc từ chối kết nối (RC={rc})'
                    admin_event.set()

            def on_message_admin(c, userdata, msg):
                try:
                    payload = json.loads(msg.payload.decode('utf-8'))
                    res = payload.get('responses', [{}])[0]
                    if res.get('correlationData') == correlation and res.get('command') == 'listClients':
                        admin_result['ok'] = not bool(res.get('error'))
                        if not admin_result['ok']:
                            admin_result['reason'] = 'Broker từ chối lệnh kiểm tra quyền quản trị.'
                        admin_event.set()
                except Exception:
                    pass

            def on_subscribe_admin(c, userdata, mid, qos_list):
                if 128 in qos_list:
                    admin_result['reason'] = 'Broker từ chối cấp quyền nhận phản hồi Dynamic Security.'
                    admin_event.set()
                else:
                    # Gửi lệnh listClients để xác nhận quyền admin
                    probe_payload = {
                        'commands': [{'command': 'listClients', 'correlationData': correlation, 'count': 1}]
                    }
                    c.publish('$CONTROL/dynamic-security/v1', json.dumps(probe_payload), qos=1)

            import json
            client_admin.on_connect = on_connect_admin
            client_admin.on_subscribe = on_subscribe_admin
            client_admin.on_message = on_message_admin
            client_admin.connect_async(host, int(port), keepalive=10)
            client_admin.loop_start()

            got_admin = admin_event.wait(timeout=3.5)
            client_admin.loop_stop()
            client_admin.disconnect()

            if got_admin and admin_result['ok']:
                steps.append({'name': 'Quyền Quản trị Mosquitto', 'ok': True, 'message': 'Xác thực tài khoản quản trị và phản hồi Dynamic Security thành công.'})
            elif got_admin:
                steps.append({'name': 'Quyền Quản trị Mosquitto', 'ok': False, 'message': admin_result['reason'] or 'Tài khoản không đủ quyền quản trị Dynamic Security.'})
            else:
                steps.append({'name': 'Quyền Quản trị Mosquitto', 'ok': False, 'message': 'Không nhận được phản hồi Dynamic Security từ Broker (Timeout).'})
        except Exception as e:
            steps.append({'name': 'Quyền Quản trị Mosquitto', 'ok': False, 'message': f'Lỗi kiểm tra quyền quản trị: {e}'})

    # Dọn dẹp file CA tạm nếu có
    if ca_temp_file:
        import os
        try:
            os.remove(ca_temp_file.name)
        except OSError:
            pass

    overall_ok = all(s['ok'] for s in steps)
    return {
        'success': overall_ok,
        'steps': steps,
        'message': 'Kiểm tra hoàn tất: tất cả các bước đều thành công.' if overall_ok else 'Kiểm tra thất bại ở một hoặc nhiều bước.',
    }
