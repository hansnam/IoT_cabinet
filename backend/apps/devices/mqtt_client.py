import json
import logging
import re
import uuid
import paho.mqtt.client as mqtt
from django.conf import settings

logger = logging.getLogger(__name__)

DYNSEC_CONTROL_TOPIC = '$CONTROL/dynamic-security/v1'
_DEVICE_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def _create_connected_mqtt_client(client_prefix: str = 'django-pub') -> mqtt.Client:
    """Tạo MQTT client sử dụng cấu hình runtime thống nhất (UI-05)."""
    from apps.devices.broker_config import get_runtime_mqtt_config
    cfg = get_runtime_mqtt_config(for_admin=False)
    client = mqtt.Client(client_id=f"{client_prefix}-{uuid.uuid4().hex[:12]}")
    if cfg.get('username'):
        client.username_pw_set(cfg['username'], cfg.get('password', ''))
    configure_mqtt_tls(client, runtime_cfg=cfg)
    client.connect(
        cfg['host'],
        cfg['port'],
        cfg.get('keepalive', 60),
    )
    client.loop_start()
    return client


def publish_command_to_broker(device_id: str, payload: dict, qos: int = 1) -> bool:
    """Gửi một lệnh V3; True nghĩa là broker đã nhận, chưa phải ESP32 đã thực thi."""
    topic_v3 = f"cabinet/v3/devices/{device_id}/commands"
    json_data = json.dumps(payload, ensure_ascii=False)
    client = None

    try:
        client = _create_connected_mqtt_client('django-pub')
        info_v3 = client.publish(topic_v3, json_data, qos=qos)
        info_v3.wait_for_publish(timeout=3.0)
        return info_v3.rc == mqtt.MQTT_ERR_SUCCESS and info_v3.is_published()
    except Exception as e:
        logger.warning("Không thể kết nối hoặc xuất bản tới MQTT Broker (%s:%s): %s",
                       settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT, e)
        return False
    finally:
        if client is not None:
            client.disconnect()
            client.loop_stop()


def build_dynsec_client_status_payload(device_id: str, device_status: str) -> dict | None:
    """Tạo payload lệnh Mosquitto Dynamic Security enableClient/disableClient theo trạng thái tủ."""
    if not _DEVICE_ID_RE.fullmatch(device_id or ''):
        return None
    if device_status in ('LOCKED', 'DECOMMISSIONED'):
        command_name = 'disableClient'
    elif device_status == 'ACTIVE':
        command_name = 'enableClient'
    else:
        return None
    return {'commands': [{'command': command_name, 'username': device_id}]}


def sync_device_mqtt_access(device_id: str, device_status: str) -> bool:
    """Đồng bộ trạng thái khóa/kích hoạt client thiết bị xuống Mosquitto Dynamic Security."""
    payload = build_dynsec_client_status_payload(device_id, device_status)
    if payload is None:
        return False

    command_name = payload['commands'][0]['command']
    if getattr(settings, 'MQTT_ADMIN_USERNAME', '') and getattr(settings, 'MQTT_ADMIN_PASSWORD', ''):
        from apps.devices.broker_security import broker_command
        try:
            broker_command(command_name, username=device_id)
            return True
        except Exception as e:
            logger.warning("Không thể đồng bộ Dynamic Security qua tài khoản admin cho %s (%s): %s",
                           device_id, device_status, e)
            return False

    client = None
    try:
        client = _create_connected_mqtt_client('django-dynsec')
        info = client.publish(DYNSEC_CONTROL_TOPIC, json.dumps(payload, ensure_ascii=False), qos=1)
        info.wait_for_publish(timeout=3.0)
        return info.rc == mqtt.MQTT_ERR_SUCCESS and info.is_published()
    except Exception as e:
        logger.warning("Không thể đồng bộ Dynamic Security cho thiết bị %s (%s): %s",
                       device_id, device_status, e)
        return False
    finally:
        if client is not None:
            client.disconnect()
            client.loop_stop()


def configure_mqtt_tls(client, runtime_cfg=None):
    """TLS follows the selected configuration; system CA is used when no custom CA exists."""
    import ssl
    cfg = runtime_cfg if runtime_cfg is not None else {'use_tls': bool(getattr(settings, 'MQTT_CA_FILE', '')), 'ca_file': getattr(settings, 'MQTT_CA_FILE', '')}
    if not cfg.get('use_tls'):
        return
    context = ssl.create_default_context()
    if cfg.get('ca_cert_pem'):
        context.load_verify_locations(cadata=cfg['ca_cert_pem'])
    elif cfg.get('ca_file'):
        context.load_verify_locations(cafile=cfg['ca_file'])
    client.tls_set_context(context)
