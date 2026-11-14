import json
import logging
import paho.mqtt.client as mqtt
from django.conf import settings

logger = logging.getLogger(__name__)


def publish_command_to_broker(device_id: str, payload: dict, qos: int = 1) -> bool:
    """
    Xuất bản lệnh điều khiển tới MQTT Broker.
    Hỗ trợ đồng thời:
    - Topic chuẩn V1.4: cabinet/v3/devices/{device_id}/commands
    - Topic tương thích cũ: cabinet/{device_id}/command

    Tham số:
        device_id (str): Mã tủ (ví dụ: 'CABINET-01')
        payload (dict): Nội dung lệnh (chứa correlation_id, command, on, fan, mode...)
        qos (int): Mức QoS (mặc định 1)

    Trả về:
        bool: True nếu gửi thành công ít nhất một topic, False nếu mất kết nối tới broker.
    """
    topic_v3 = f"cabinet/v3/devices/{device_id}/commands"
    topic_v1 = f"cabinet/{device_id}/command"
    json_data = json.dumps(payload, ensure_ascii=False)

    try:
        client = mqtt.Client(client_id=f"django_api_publisher_{device_id}")
        mqtt_user = getattr(settings, 'MQTT_USERNAME', '')
        mqtt_pass = getattr(settings, 'MQTT_PASSWORD', '')
        if mqtt_user:
            client.username_pw_set(mqtt_user, mqtt_pass)
        client.connect(
            settings.MQTT_BROKER_HOST,
            settings.MQTT_BROKER_PORT,
            settings.MQTT_KEEPALIVE
        )
        info_v3 = client.publish(topic_v3, json_data, qos=qos)
        info_v1 = client.publish(topic_v1, json_data, qos=qos)
        info_v3.wait_for_publish(timeout=3.0)
        client.disconnect()
        logger.info("Đã xuất bản lệnh thành công tới các topic [%s, %s]: %s", topic_v3, topic_v1, payload)
        return True
    except Exception as e:
        logger.warning("Không thể kết nối hoặc xuất bản tới MQTT Broker (%s:%s): %s",
                       settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT, e)
        return False
