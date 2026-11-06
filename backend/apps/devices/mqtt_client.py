import json
import logging
import paho.mqtt.client as mqtt
from django.conf import settings

logger = logging.getLogger(__name__)


def publish_command_to_broker(device_id: str, payload: dict, qos: int = 1) -> bool:
    """
    Xuất bản lệnh điều khiển tới MQTT Broker qua topic: cabinet/{device_id}/command

    Tham số:
        device_id (str): Mã tủ (ví dụ: 'CABINET-01')
        payload (dict): Nội dung lệnh (chứa correlation_id, command, fan, mode...)
        qos (int): Mức QoS (mặc định 1)

    Trả về:
        bool: True nếu gửi thành công, False nếu mất kết nối tới broker.
    """
    topic = f"cabinet/{device_id}/command"
    json_data = json.dumps(payload, ensure_ascii=False)

    try:
        client = mqtt.Client(client_id=f"django_api_publisher_{device_id}")
        client.connect(
            settings.MQTT_BROKER_HOST,
            settings.MQTT_BROKER_PORT,
            settings.MQTT_KEEPALIVE
        )
        info = client.publish(topic, json_data, qos=qos)
        info.wait_for_publish(timeout=3.0)
        client.disconnect()
        logger.info("Đã xuất bản lệnh thành công tới topic %s: %s", topic, payload)
        return True
    except Exception as e:
        logger.warning("Không thể kết nối hoặc xuất bản tới MQTT Broker (%s:%s): %s",
                       settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT, e)
        return False
