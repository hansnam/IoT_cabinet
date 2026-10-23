"""
Entry-point tương thích cho MQTT Worker (ủy quyền sang backend.workers.mqtt_worker).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.workers.mqtt_worker import MqttWorkerRunner, handle_shutdown, run_worker

__all__ = ["MqttWorkerRunner", "handle_shutdown", "run_worker"]

if __name__ == "__main__":
    run_worker()

