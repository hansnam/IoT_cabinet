"""Gửi lệnh tới plugin Dynamic Security có sẵn của Mosquitto; không lưu mật khẩu vào DB."""
import json
import re
import threading
import uuid

import paho.mqtt.client as mqtt
from django.conf import settings
from django.core.management.base import CommandError

from apps.devices.mqtt_client import configure_mqtt_tls

CONTROL_TOPIC = '$CONTROL/dynamic-security/v1'


def device_acl(device_id):
    """Mỗi tủ chỉ gửi số đo/ACK của nó và nhận lệnh trên đúng topic của nó."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', device_id):
        raise CommandError('Mã tủ chỉ được gồm chữ, số, _ và -.')
    root = f'cabinet/v3/devices/{device_id}'
    rules = [
        {'acltype': kind, 'topic': '#', 'allow': False, 'priority': -1}
        for kind in ('publishClientSend', 'publishClientReceive', 'subscribePattern')
    ]
    rules += [
        {'acltype': 'publishClientSend', 'topic': f'{root}/{suffix}', 'allow': True, 'priority': 10}
        for suffix in ('telemetry', 'status', 'acks')
    ]
    rules += [
        {'acltype': kind, 'topic': f'{root}/commands', 'allow': True, 'priority': 10}
        for kind in ('subscribeLiteral', 'publishClientReceive')
    ]
    return rules


def broker_command(command, **fields):
    """Chờ phản hồi đúng correlationData; lỗi kết nối không được coi là đã áp dụng."""
    if not settings.MQTT_ADMIN_USERNAME or not settings.MQTT_ADMIN_PASSWORD:
        raise CommandError('Cần MQTT_ADMIN_USERNAME và MQTT_ADMIN_PASSWORD trong backend/.env.')
    correlation = uuid.uuid4().hex
    finished = threading.Event()
    result = {}
    client = mqtt.Client(client_id=f'django-admin-{correlation[:12]}')
    client.username_pw_set(settings.MQTT_ADMIN_USERNAME, settings.MQTT_ADMIN_PASSWORD)
    configure_mqtt_tls(client)

    def on_connect(connection, userdata, flags, rc):
        if rc != 0:
            result['error'] = 'Broker từ chối tài khoản quản trị.'
            finished.set()
        else:
            connection.subscribe(CONTROL_TOPIC + '/response', qos=1)

    def on_subscribe(connection, userdata, mid, granted_qos):
        if 128 in granted_qos:
            result['error'] = 'Broker từ chối nhận phản hồi quản trị.'
            finished.set()
            return
        connection.publish(CONTROL_TOPIC, json.dumps({'commands': [
            {'command': command, 'correlationData': correlation, **fields}
        ]}), qos=1)

    def on_message(connection, userdata, message):
        try:
            responses = json.loads(message.payload).get('responses', [])
            for response in responses:
                if response.get('correlationData') == correlation and response.get('command') == command:
                    result.update(response)
                    finished.set()
        except (ValueError, TypeError, AttributeError):
            pass  # Bỏ qua gói không đúng định dạng, tiếp tục chờ phản hồi của lệnh này.

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    try:
        client.connect(settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT, settings.MQTT_KEEPALIVE)
        client.loop_start()
        if not finished.wait(5):
            raise CommandError('Không nhận được phản hồi quản trị broker sau 5 giây.')
        if result.get('error'):
            raise CommandError(f'Broker từ chối lệnh {command}; kiểm tra tài khoản, ACL và tên client.')
        return result.get('data', {})
    except (OSError, RuntimeError) as exc:
        raise CommandError('Không kết nối được MQTT broker.') from exc
    finally:
        client.disconnect()
        client.loop_stop()
