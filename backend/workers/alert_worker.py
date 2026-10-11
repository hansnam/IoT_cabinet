"""
Alert Worker Process (Thành viên 4 chủ trì)
Tiến trình nền độc lập:
1. Định kỳ quét trạng thái đo lường và kết nối của các thiết bị.
2. Đánh giá 4 quy tắc cảnh báo CORE (HIGH_TEMPERATURE, HIGH_HUMIDITY, OFFLINE, SENSOR_FAULT).
3. Mở/Cập nhật/Đóng các bản ghi trong bảng alerts và alert_events.
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

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [ALERT_WORKER] %(message)s"
)
logger = logging.getLogger("AlertWorker")

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
        logger.info("Alert Worker khởi động.")
        logger.info("Chờ triển khai Rule Evaluator ở Pha C...")

        while running:
            # Vòng lặp quét cảnh báo (sẽ được bổ sung ở Pha C)
            time.sleep(2)

        logger.info("Alert Worker đã dừng thành công.")

if __name__ == "__main__":
    run_worker()
