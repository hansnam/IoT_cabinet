"""
MQTT Worker Process (Thành viên 2 chủ trì - Mục 8, 10, 12.4)
Tiến trình nền độc lập (một instance duy nhất):
1. Subscribe các topic cabinet/v3/devices/+/{telemetry,status,acks} từ Broker Mosquitto.
2. Ingest dữ liệu hợp lệ vào PostgreSQL (schema_version=3) qua MqttIngestService.
3. Quét Transactional Outbox (bảng outbox_messages) mỗi 0.5s để dispatch lệnh điều khiển,
   lập lịch retry (+3s, +6s) và đánh dấu timeout (+10s / expires_at).
4. Quét thiết bị mất kết nối quá 45s mỗi 1.0s để cập nhật trạng thái OFFLINE.
"""
from __future__ import annotations

import logging
import signal
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

# Đảm bảo import được module từ thư mục gốc của dự án
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app import create_app
from backend.app.config import BaseConfig
from backend.app.extensions import db
from backend.app.mqtt.dispatcher import DispatchBatchResult, MqttDispatcherService
from backend.app.mqtt.ingest import IngestResult, MqttIngestService
from backend.app.mqtt.topics import WORKER_SUBSCRIBE_TOPICS

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [MQTT_WORKER] %(message)s",
)
logger = logging.getLogger("MQTTWorker")

running = True


def handle_shutdown(signum: int, frame: Any) -> None:
    global running
    logger.info("Nhận tín hiệu dừng tiến trình (%s). Đang đóng kết nối an toàn...", signum)
    running = False


class MqttWorkerRunner:
    """Bộ điều phối vòng đời MQTT Client + Ingest + Outbox Dispatcher + Offline Sweeper."""

    def __init__(
        self,
        app: Any,
        *,
        worker_id: str | None = None,
        outbox_poll_interval_s: float = 0.5,
        offline_sweep_interval_s: float = 1.0,
        offline_timeout_s: int = 45,
    ) -> None:
        self.app = app
        self.worker_id = worker_id or BaseConfig.MQTT_WORKER_CLIENT_ID or "backend-mqtt-worker"
        self.outbox_poll_interval_s = outbox_poll_interval_s
        self.offline_sweep_interval_s = offline_sweep_interval_s
        self.offline_timeout_s = offline_timeout_s
        self.connected = False
        self._last_outbox_tick = 0.0
        self._last_offline_tick = 0.0

        self.client = mqtt.Client(
            client_id=self.worker_id,
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        if BaseConfig.MQTT_WORKER_USERNAME:
            self.client.username_pw_set(
                BaseConfig.MQTT_WORKER_USERNAME,
                BaseConfig.MQTT_WORKER_PASSWORD,
            )

        if BaseConfig.MQTT_BROKER_USE_TLS:
            ca_certs = BaseConfig.MQTT_CA_CERT_PATH
            self.client.tls_set(
                ca_certs=ca_certs,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: dict[str, Any],
        rc: int,
    ) -> None:
        if rc == 0:
            self.connected = True
            logger.info("Đã kết nối MQTT Broker thành công (client_id=%s).", self.worker_id)
            for topic_filter, qos in WORKER_SUBSCRIBE_TOPICS:
                client.subscribe(topic_filter, qos=qos)
                logger.info("Đã đăng ký lắng nghe topic: %s (QoS %d)", topic_filter, qos)
        else:
            self.connected = False
            logger.error("Kết nối MQTT Broker thất bại với mã lỗi rc=%d.", rc)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        rc: int,
    ) -> None:
        self.connected = False
        if rc != 0:
            logger.warning("Mất kết nối đột ngột với MQTT Broker (rc=%d). Sẽ tự động thử lại...", rc)
        else:
            logger.info("Đã ngắt kết nối MQTT Broker an toàn.")

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        self.process_incoming_message(
            topic=msg.topic,
            payload=msg.payload,
            is_retained=bool(msg.retain),
        )

    def process_incoming_message(
        self,
        topic: str,
        payload: bytes | str,
        *,
        is_retained: bool = False,
        received_at: datetime | None = None,
    ) -> IngestResult:
        """Xử lý một thông điệp MQTT trong ngữ cảnh Flask app_context."""
        with self.app.app_context():
            try:
                res = MqttIngestService.ingest_message(
                    topic=topic,
                    raw_payload=payload,
                    is_retained=is_retained,
                    received_at=received_at,
                    db_session=db.session,
                    commit=True,
                )
                if not res.accepted:
                    logger.warning(
                        "Từ chối bản tin trên topic=%s: code=%s, message=%s",
                        topic,
                        res.reason_code,
                        res.message,
                    )
                return res
            except Exception as exc:
                db.session.rollback()
                logger.exception("Lỗi ngoại lệ khi xử lý bản tin MQTT trên topic=%s: %s", topic, exc)
                return IngestResult(
                    accepted=False,
                    outcome="ERROR",
                    reason_code="INTERNAL_WORKER_ERROR",
                    message=str(exc),
                )

    def publish_to_broker(
        self,
        topic: str,
        payload_json: str,
        qos: int = 1,
        retained: bool = False,
    ) -> bool:
        """Hàm callback gửi bản tin lệnh sang Mosquitto."""
        if not self.connected:
            return False
        info = self.client.publish(
            topic,
            payload=payload_json.encode("utf-8"),
            qos=qos,
            retain=retained,
        )
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def run_single_tick(
        self,
        *,
        now_utc: datetime | None = None,
        publish_fn: Any = None,
    ) -> tuple[DispatchBatchResult, int]:
        """Thực thi một nhịp quét Outbox + Timeout + Retry và quét thiết bị Offline."""
        with self.app.app_context():
            now = now_utc if now_utc is not None else datetime.now(timezone.utc)
            pub_cb = publish_fn if publish_fn is not None else self.publish_to_broker
            dispatch_stats = MqttDispatcherService.dispatch_pending_outbox(
                pub_cb,
                worker_id=self.worker_id,
                now_utc=now,
                db_session=db.session,
                commit=True,
            )
            offline_count = MqttDispatcherService.sweep_offline_devices(
                now_utc=now,
                offline_timeout_s=self.offline_timeout_s,
                db_session=db.session,
                commit=True,
            )
            return dispatch_stats, offline_count

    def run_forever(self) -> None:
        """Vòng lặp chính của tiến trình MQTT Worker."""
        global running
        host = BaseConfig.MQTT_BROKER_HOST
        port = (
            BaseConfig.MQTT_BROKER_TLS_PORT
            if BaseConfig.MQTT_BROKER_USE_TLS
            else BaseConfig.MQTT_BROKER_PORT
        )
        logger.info("Đang kết nối tới MQTT Broker tại %s:%d (TLS=%s)...", host, port, BaseConfig.MQTT_BROKER_USE_TLS)

        try:
            self.client.connect(host, port, keepalive=30)
        except Exception as exc:
            logger.error("Không thể kết nối ban đầu tới MQTT Broker (%s:%d): %s", host, port, exc)

        while running:
            try:
                rc = self.client.loop(timeout=0.1)
                if rc != mqtt.MQTT_ERR_SUCCESS and running:
                    try:
                        self.client.reconnect()
                    except Exception:
                        time.sleep(1.0)
            except Exception as exc:
                logger.warning("Lỗi trong vòng lặp mạng MQTT: %s", exc)

            mono_now = time.monotonic()
            now_utc = datetime.now(timezone.utc)

            if mono_now - self._last_outbox_tick >= self.outbox_poll_interval_s:
                self._last_outbox_tick = mono_now
                with self.app.app_context():
                    try:
                        MqttDispatcherService.dispatch_pending_outbox(
                            self.publish_to_broker,
                            worker_id=self.worker_id,
                            now_utc=now_utc,
                            db_session=db.session,
                            commit=True,
                        )
                    except Exception as exc:
                        db.session.rollback()
                        logger.exception("Lỗi khi quét Outbox Dispatcher: %s", exc)

            if mono_now - self._last_offline_tick >= self.offline_sweep_interval_s:
                self._last_offline_tick = mono_now
                with self.app.app_context():
                    try:
                        MqttDispatcherService.sweep_offline_devices(
                            now_utc=now_utc,
                            offline_timeout_s=self.offline_timeout_s,
                            db_session=db.session,
                            commit=True,
                        )
                    except Exception as exc:
                        db.session.rollback()
                        logger.exception("Lỗi khi quét thiết bị Offline: %s", exc)

        try:
            self.client.disconnect()
        except Exception:
            pass
        logger.info("MQTT Worker đã dừng thành công.")


def run_worker() -> None:
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    app = create_app()
    runner = MqttWorkerRunner(app)
    runner.run_forever()


if __name__ == "__main__":
    run_worker()
