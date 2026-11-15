import json
import logging
import uuid
import paho.mqtt.client as mqtt
from django.conf import settings

logger = logging.getLogger(__name__)


def publish_command_to_broker(device_id: str, payload: dict, qos: int = 1) -> bool:
    """Gửi một lệnh V3; True nghĩa là broker đã nhận, chưa phải ESP32 đã thực thi."""
    topic_v3 = f"cabinet/v3/devices/{device_id}/commands"
    json_data = json.dumps(payload, ensure_ascii=False)

    try:
        client = mqtt.Client(client_id=f"django-pub-{uuid.uuid4().hex[:12]}")
        mqtt_user = getattr(settings, 'MQTT_USERNAME', '')
        mqtt_pass = getattr(settings, 'MQTT_PASSWORD', '')
        if mqtt_user:
            client.username_pw_set(mqtt_user, mqtt_pass)
        configure_mqtt_tls(client)
        client.connect(
            settings.MQTT_BROKER_HOST,
            settings.MQTT_BROKER_PORT,
            settings.MQTT_KEEPALIVE
        )
        # QoS 1 cần network loop để nhận CONNACK/PUBACK từ broker.
        client.loop_start()
        info_v3 = client.publish(topic_v3, json_data, qos=qos)
        info_v3.wait_for_publish(timeout=3.0)
        return info_v3.rc == mqtt.MQTT_ERR_SUCCESS and info_v3.is_published()
    except Exception as e:
        logger.warning("Không thể kết nối hoặc xuất bản tới MQTT Broker (%s:%s): %s",
                       settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT, e)
        return False
    finally:
        if 'client' in locals():
            client.disconnect()
            client.loop_stop()


def configure_mqtt_tls(client):
    """Bật TLS khi có CA; giữ xác minh hostname mặc định, không dùng tls_insecure_set(True)."""
    ca_file = getattr(settings, 'MQTT_CA_FILE', '')
    if ca_file:
        client.tls_set(ca_certs=ca_file)
