import json
import logging
from django.conf import settings
import paho.mqtt.publish as publish

logger = logging.getLogger("MQTTClient")


def send_mqtt_command(device_id: str, command_type: str, params: dict) -> bool:
    """Gửi lệnh điều khiển xuống thiết bị qua MQTT Broker"""
    payload = {
        "command_id": str(device_id),
        "type": command_type,
        "params": params,
    }
    
    # Gửi đồng thời tới cả topic chuẩn v3 và topic rút gọn để tương thích mọi phiên bản firmware ESP32
    topics = [
        f"cabinet/v3/devices/{device_id}/commands",
        f"cabinet/devices/{device_id}/commands",
    ]
    
    broker_host = getattr(settings, "MQTT_BROKER_HOST", "localhost")
    broker_port = getattr(settings, "MQTT_BROKER_PORT", 1883)
    
    try:
        for topic in topics:
            publish.single(
                topic,
                payload=json.dumps(payload),
                hostname=broker_host,
                port=broker_port,
                qos=1,
                retain=False,
            )
        logger.info(f"Đã gửi lệnh {command_type} tới {device_id}")
        return True
    except Exception as e:
        logger.warning(f"Không thể gửi MQTT lệnh tới {device_id}: {e}")
        return False
