"""
MQTT Topic Helpers & Parser (Thành viên 2 phụ trách - Mục 8)
Định nghĩa cấu trúc topic v3: cabinet/v3/devices/{device_id}/{channel}
và kiểm tra tính hợp lệ của device_id trên đường dẫn MQTT.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DEVICE_ID_REGEX = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
TOPIC_PREFIX = "cabinet/v3/devices"

VALID_INCOMING_CHANNELS = frozenset({"telemetry", "status", "acks"})

# Danh sách (topic_filter, qos) mà MQTT Worker đăng ký lắng nghe từ Mosquitto
WORKER_SUBSCRIBE_TOPICS: list[tuple[str, int]] = [
    (f"{TOPIC_PREFIX}/+/telemetry", 0),
    (f"{TOPIC_PREFIX}/+/status", 1),
    (f"{TOPIC_PREFIX}/+/acks", 0),
]


@dataclass(frozen=True)
class ParsedTopic:
    """Kết quả phân tách một MQTT topic hợp lệ theo schema v3."""

    device_id: str
    channel: str
    raw_topic: str


def is_valid_device_id(device_id: str) -> bool:
    """Kiểm tra định dạng device_id theo Mục 6.1: ^[a-z0-9][a-z0-9_-]{2,63}$."""
    if not isinstance(device_id, str):
        return False
    return bool(DEVICE_ID_REGEX.match(device_id))


def parse_incoming_topic(topic: str) -> ParsedTopic | None:
    """Phân tích MQTT topic đầu vào (cabinet/v3/devices/{device_id}/{channel}).
    Trả về None nếu không khớp prefix v3, sai định dạng device_id hoặc channel lạ.
    """
    if not isinstance(topic, str) or not topic:
        return None
    parts = topic.split("/")
    if len(parts) != 5:
        return None
    if parts[0] != "cabinet" or parts[1] != "v3" or parts[2] != "devices":
        return None
    device_id, channel = parts[3], parts[4]
    if not is_valid_device_id(device_id):
        return None
    if channel not in VALID_INCOMING_CHANNELS:
        return None
    return ParsedTopic(device_id=device_id, channel=channel, raw_topic=topic)


def build_telemetry_topic(device_id: str) -> str:
    return f"{TOPIC_PREFIX}/{device_id}/telemetry"


def build_status_topic(device_id: str) -> str:
    return f"{TOPIC_PREFIX}/{device_id}/status"


def build_commands_topic(device_id: str) -> str:
    return f"{TOPIC_PREFIX}/{device_id}/commands"


def build_acks_topic(device_id: str) -> str:
    return f"{TOPIC_PREFIX}/{device_id}/acks"

