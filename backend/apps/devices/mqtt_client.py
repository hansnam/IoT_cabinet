import json
import logging
import threading
import uuid
import paho.mqtt.client as mqtt
from django.conf import settings

logger = logging.getLogger(__name__)

DYNSEC_CONTROL_TOPIC = "$CONTROL/dynamic-security/v1"
DYNSEC_RESPONSE_TOPIC = "$CONTROL/dynamic-security/v1/response"


def _create_connected_mqtt_client(client_prefix: str) -> mqtt.Client:
    """Khởi tạo và kết nối paho MQTT client tới Broker theo cấu hình Django settings."""
    client = mqtt.Client(client_id=f"{client_prefix}_{uuid.uuid4().hex[:8]}")
    mqtt_user = getattr(settings, 'MQTT_USERNAME', '')
    mqtt_pass = getattr(settings, 'MQTT_PASSWORD', '')
    if mqtt_user:
        client.username_pw_set(mqtt_user, mqtt_pass)
    client.connect(
        settings.MQTT_BROKER_HOST,
        settings.MQTT_BROKER_PORT,
        settings.MQTT_KEEPALIVE
    )
    return client


def publish_command_to_broker(device_id: str, payload: dict, qos: int = 1) -> bool:
    """
    Xuất bản lệnh điều khiển tới MQTT Broker.
    Hỗ trợ đồng thời:
    - Topic chuẩn V1.4: cabinet/v3/devices/{device_id}/commands
    - Topic tương thích cũ: cabinet/{device_id}/command
    """
    topic_v3 = f"cabinet/v3/devices/{device_id}/commands"
    topic_v1 = f"cabinet/{device_id}/command"
    json_data = json.dumps(payload, ensure_ascii=False)

    try:
        client = _create_connected_mqtt_client(f"django_api_publisher_{device_id}")
        info_v3 = client.publish(topic_v3, json_data, qos=qos)
        client.publish(topic_v1, json_data, qos=qos)
        info_v3.wait_for_publish(timeout=3.0)
        client.disconnect()
        logger.info("Đã xuất bản lệnh thành công tới các topic [%s, %s]: %s", topic_v3, topic_v1, payload)
        return True
    except Exception as e:
        logger.warning("Không thể kết nối hoặc xuất bản tới MQTT Broker (%s:%s): %s",
                       settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT, e)
        return False


def build_dynsec_client_status_payload(device_id: str, device_status: str) -> dict | None:
    """
    Tạo payload JSON điều khiển Mosquitto Dynamic Security plugin theo vòng đời thiết bị:
    - LOCKED / DECOMMISSIONED -> disableClient (thu hồi quyền kết nối ngay trên broker)
    - ACTIVE -> enableClient (mở khóa cho phép thiết bị kết nối lại)
    """
    if device_status in ('LOCKED', 'DECOMMISSIONED'):
        return {
            "commands": [
                {"command": "disableClient", "username": device_id}
            ]
        }
    if device_status == 'ACTIVE':
        return {
            "commands": [
                {"command": "enableClient", "username": device_id}
            ]
        }
    return None


def sync_device_mqtt_access(device_id: str, device_status: str) -> bool:
    """
    Đồng bộ trạng thái khóa/mở khóa thiết bị lên Mosquitto Dynamic Security
    qua topic $CONTROL/dynamic-security/v1.
    """
    payload = build_dynsec_client_status_payload(device_id, device_status)
    if payload is None:
        return False

    cmd_name = payload["commands"][0]["command"]
    done_event = threading.Event()
    broker_response: dict = {}

    try:
        client = _create_connected_mqtt_client(f"django_dynsec_{device_id}")

        def on_message(_client, _userdata, msg):
            if msg.topic == DYNSEC_RESPONSE_TOPIC:
                try:
                    broker_response["data"] = json.loads(msg.payload.decode("utf-8"))
                except Exception:
                    pass
                done_event.set()

        client.on_message = on_message
        client.subscribe(DYNSEC_RESPONSE_TOPIC, qos=1)
        client.loop_start()
        try:
            info = client.publish(DYNSEC_CONTROL_TOPIC, json.dumps(payload), qos=1)
            info.wait_for_publish(timeout=2.0)
            done_event.wait(timeout=1.0)
        finally:
            client.loop_stop()
            client.disconnect()

        resp_data = broker_response.get("data")
        if isinstance(resp_data, dict):
            responses = resp_data.get("responses") or []
            if responses and responses[0].get("error"):
                logger.warning(
                    "[MQTT DynSec] Lệnh %s cho '%s' bị broker từ chối: %s",
                    cmd_name, device_id, responses[0].get("error")
                )
                return False

        logger.info("[MQTT DynSec] Đã gửi lệnh %s cho client '%s' tới %s.", cmd_name, device_id, DYNSEC_CONTROL_TOPIC)
        return True
    except Exception as e:
        logger.warning(
            "[MQTT DynSec] Không thể đồng bộ %s cho '%s' tới Broker (%s:%s): %s",
            cmd_name, device_id, settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT, e
        )
        return False
