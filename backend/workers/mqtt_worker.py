"""
MQTT Worker Process (Thành viên 2 chủ trì)
Tiến trình nền độc lập:
1. Subscribe các topic telemetry, status, acks từ broker Mosquitto.
2. Ingest dữ liệu hợp lệ vào PostgreSQL (schema_version 3).
3. Quét Transactional Outbox (bảng outbox_messages) để dispatch lệnh điều khiển qua MQTT.
"""

import sys
import os
import time
import signal
import logging
from pathlib import Path

# Đảm bảo import được module từ thư mục gốc
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from backend.app import create_app
from backend.app.config import BaseConfig

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [MQTT_WORKER] %(message)s"
)
logger = logging.getLogger("MQTTWorker")

running = True

def handle_shutdown(signum, frame):
    global running
    logger.info("Nhận tín hiệu dừng tiến trình. Đang đóng kết nối an toàn...")
    running = False

def run_worker():
    global running
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    app = create_app()
    with app.app_context():
        logger.info(
            f"MQTT Worker khởi động. Broker: {BaseConfig.MQTT_BROKER_HOST}:{BaseConfig.MQTT_BROKER_PORT}"
        )
        logger.info("Chờ triển khai Ingestion & Outbox Dispatcher ở Pha B...")

        while running:
            # Vòng lặp làm việc chính (sẽ được bổ sung ở Pha B)
            time.sleep(1)

        logger.info("MQTT Worker đã dừng thành công.")

if __name__ == "__main__":
    run_worker()
