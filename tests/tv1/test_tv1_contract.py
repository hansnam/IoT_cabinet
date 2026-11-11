import hashlib
import json
import uuid
from app.mqtt.topics import parse_device_topic
from app.mqtt.validators import (
    validate_ack_payload,
    validate_status_payload,
    validate_telemetry_payload,
)

# Hằng số cấu hình mặc định P0 của Node 1
EXPECTED_P0_CONFIG_HASH = "3520a5738ade36fecf0a3f72ecf34e9f53fb2b2fe23e0a20245393e6a57a91cb"


def test_tv1_p0_canonical_config_hash():
    """Kiểm tra cách tạo canonical JSON và SHA-256 trên thiết bị khớp tuyệt đối với đặc tả V1.3."""
    snapshot = {
        "schema_version": 3,
        "config_version": 1,
        "profile": "CONTROLLER",
        "fan_binding_id": None,
        "fan_on_c": 30,
        "fan_off_c": 28,
        "temp_alert_c": 35,
        "humidity_alert_pct": 80,
        "temp_clear_margin_c": 1,
        "humidity_clear_margin_pct": 3,
        "telemetry_interval_ms": 5000,
        "manual_timeout_s": 60,
        "extra": {},
    }
    canonical = json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert digest == EXPECTED_P0_CONFIG_HASH


def test_tv1_mqtt_topics_parsing():
    """Kiểm tra định dạng 4 topic của thiết bị cabinet-001 khớp với bộ phân tích topic của Backend."""
    for channel in ("telemetry", "status", "commands", "acks"):
        topic = f"cabinet/v3/devices/cabinet-001/{channel}"
        parsed = parse_device_topic(topic)
        assert parsed is not None
        assert parsed.device_id == "cabinet-001"
        assert parsed.channel == channel


def test_tv1_valid_controller_telemetry_payload():
    """Kiểm tra bản tin Telemetry do firmware ESP32 phát ra vượt qua bộ validator của Backend."""
    payload = {
        "schema_version": 3,
        "device_id": "cabinet-001",
        "profile": "CONTROLLER",
        "fan_binding_id": None,
        "boot_id": str(uuid.uuid4()),
        "connection_id": str(uuid.uuid4()),
        "seq": 12,
        "measured_at": "2026-10-09T13:00:00Z",
        "clock_synced": True,
        "uptime_ms": 60000,
        "temperature_c": 31.2,
        "humidity_pct": 62.0,
        "sensor_ok": True,
        "fan_output": True,
        "mode": "AUTO",
        "config_version": 1,
        "config_hash": EXPECTED_P0_CONFIG_HASH,
        "fault_code": None,
        "extra": {},
    }
    validated = validate_telemetry_payload("cabinet-001", payload)
    assert validated["device_id"] == "cabinet-001"
    assert validated["temperature_c"] == 31.2
    assert validated["fan_output"] is True


def test_tv1_fault_telemetry_sends_null_not_zero():
    """Kiểm tra khi DHT22 lỗi (FAULT), firmware gửi null thay vì 0 và bật quạt an toàn."""
    payload = {
        "schema_version": 3,
        "device_id": "cabinet-001",
        "profile": "CONTROLLER",
        "fan_binding_id": None,
        "boot_id": str(uuid.uuid4()),
        "connection_id": str(uuid.uuid4()),
        "seq": 15,
        "measured_at": "2026-10-09T13:00:15Z",
        "clock_synced": True,
        "uptime_ms": 75000,
        "temperature_c": None,
        "humidity_pct": None,
        "sensor_ok":elace_false if False else False,
        "fan_output": True,
        "mode": "AUTO",
        "config_version": 1,
        "config_hash": EXPECTED_P0_CONFIG_HASH,
        "fault_code": "DHT_READ_ERROR",
        "extra": {},
    }
    validated = validate_telemetry_payload("cabinet-001", payload)
    assert validated["sensor_ok"] is False
    assert validated["temperature_c"] is None
    assert validated["humidity_pct"] is None
    assert validated["fan_output"] is True


def test_tv1_status_and_ack_payloads():
    """Kiểm tra bản tin Status/Heartbeat và bản tin ACK từ thiết bị."""
    boot_id = str(uuid.uuid4())
    conn_id = str(uuid.uuid4())
    cmd_id = str(uuid.uuid4())

    status_payload = {
        "schema_version": 3,
        "device_id": "cabinet-001",
        "profile": "CONTROLLER",
        "connection": "ONLINE",
        "boot_id": boot_id,
        "connection_id": conn_id,
        "status_seq": 1,
        "config_version": 1,
        "config_hash": EXPECTED_P0_CONFIG_HASH,
        "firmware_version": "1.2.0",
        "hardware_version": "esp32-wroom32-dht22-relay-controller-v1",
        "uptime_ms": 60000,
        "clock_synced": True,
        "health": "OK",
        "fault_code": None,
    }
    validated_status = validate_status_payload("cabinet-001", status_payload)
    assert validated_status["connection"] == "ONLINE"

    ack_payload = {
        "schema_version": 3,
        "device_id": "cabinet-001",
        "command_id": cmd_id,
        "status": "APPLIED",
        "type": "SET_FAN",
        "boot_id": boot_id,
        "connection_id": conn_id,
        "device_command_seq": 8,
        "executed_at": "2026-10-09T13:00:01Z",
        "result": {
            "fan_output": True,
            "mode": "MANUAL",
            "config_version": 1,
            "config_hash": EXPECTED_P0_CONFIG_HASH,
        },
    }
    validated_ack = validate_ack_payload("cabinet-001", ack_payload)
    assert validated_ack["status"] == "APPLIED"
    assert validated_ack["device_command_seq"] == 8
