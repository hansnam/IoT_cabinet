"""
MQTT Package (Thành viên 2 chủ trì)
Các module hỗ trợ định dạng topic, mã hóa/giải mã payload JSON schema v3,
luồng Ingest dữ liệu từ Broker Mosquitto và luồng Outbox Dispatcher.
"""
from backend.app.mqtt.dispatcher import DispatchBatchResult, MqttDispatcherService
from backend.app.mqtt.ingest import IngestResult, MqttIngestService
from backend.app.mqtt.topics import (
    ParsedTopic,
    WORKER_SUBSCRIBE_TOPICS,
    build_acks_topic,
    build_commands_topic,
    build_status_topic,
    build_telemetry_topic,
    is_valid_device_id,
    parse_incoming_topic,
)
from backend.app.mqtt.validators import (
    MqttValidationError,
    compute_payload_hash,
    parse_strict_json,
    validate_ack_payload,
    validate_status_payload,
    validate_telemetry_payload,
)

__all__ = [
    "DispatchBatchResult",
    "IngestResult",
    "MqttDispatcherService",
    "MqttIngestService",
    "MqttValidationError",
    "ParsedTopic",
    "WORKER_SUBSCRIBE_TOPICS",
    "build_acks_topic",
    "build_commands_topic",
    "build_status_topic",
    "build_telemetry_topic",
    "compute_payload_hash",
    "is_valid_device_id",
    "parse_incoming_topic",
    "parse_strict_json",
    "validate_ack_payload",
    "validate_status_payload",
    "validate_telemetry_payload",
]
