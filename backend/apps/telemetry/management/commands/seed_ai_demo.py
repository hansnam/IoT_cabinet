"""
Management command: `python manage.py seed_ai_demo`
Tạo hoặc cập nhật các tủ mạng mẫu trên dashboard kèm dữ liệu viễn trắc (Telemetry),
cảnh báo (Alert) và lệnh (Command) cho các kịch bản AI V1 & V2:
  1. `normal`: Tủ vận hành ổn định, quạt làm mát hiệu quả -> AI báo NORMAL.
  2. `cooling_degradation`: Quạt đã bật liên tục >= 5 phút trên ngưỡng nhưng nhiệt độ vẫn tăng -> AI báo WARNING / COOLING_DEGRADATION.
  3. `sensor_anomaly`: Cảm biến bị đứng số (flatline) hoặc nhảy vọt phi vật lý -> AI báo WARNING / SENSOR_ANOMALY.
  4. `recovering`: Quạt đang bật và hạ nhiệt hiệu quả (slope âm rõ rệt).
  5. `door_open`: Mở cửa tủ làm độ ẩm và nhiệt độ môi trường xâm nhập nhanh.
  6. `manual_off`: Tủ đang ở chế độ MANUAL (quạt tắt thủ công) -> kiểm thử khóa dự báo (MANUAL_MODE).
  7. `all` (mặc định): Khởi tạo đầy đủ kịch bản trên các tủ mẫu và đồng bộ cho `cabinet-001`.

Hỗ trợ cờ `--noise` (bổ sung nhiễu Gaussian thực tế ±0.1°C và chu kỳ đóng/ngắt hysteresis)
và chế độ `--live` (chạy nền đẩy mẫu đo mới định kỳ để biểu đồ và AI dự báo luôn sống động).
"""
from __future__ import annotations

from datetime import timedelta
import math
import random
import time
import uuid
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.alerts.models import Alert
from apps.commands.models import Command as DeviceCommand
from apps.devices.models import Device, DeviceConfig
from apps.telemetry.anomaly import (
    evaluate_and_sync_device_anomaly_alerts,
    evaluate_device_anomaly,
)
from apps.telemetry.models import Telemetry


class Command(BaseCommand):
    help = "Bơm dữ liệu mẫu đa kịch bản cho tính năng AI Phát hiện bất thường (Hướng 1) và Trợ lý AI V2 (Hướng 4)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--scenario",
            type=str,
            default="all",
            choices=[
                "all",
                "normal",
                "cooling_degradation",
                "sensor_anomaly",
                "recovering",
                "door_open",
                "manual_off",
            ],
            help="Kịch bản dữ liệu AI muốn bơm (mặc định: all).",
        )
        parser.add_argument(
            "--noise",
            action="store_true",
            help="Thêm nhiễu Gaussian thực tế (±0.1°C) và dao động hysteresis tự nhiên vào chuỗi mẫu đo.",
        )
        parser.add_argument(
            "--live",
            action="store_true",
            help="Chạy liên tục đẩy dữ liệu viễn trắc mới mỗi --interval giây để trải nghiệm trực tiếp trên giao diện.",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=10,
            help="Chu kỳ bơm mẫu mới khi bật --live (giây, mặc định: 10).",
        )

    def _ensure_cabinet_config(
        self,
        device: Device,
        *,
        fan_on_c: float = 30.0,
        fan_off_c: float = 28.0,
        temp_alert_c: float = 35.0,
        humidity_alert_pct: float = 80.0,
    ) -> None:
        now = timezone.now()
        cfg_data = {
            "fan_on_c": fan_on_c,
            "fan_off_c": fan_off_c,
            "temp_alert_c": temp_alert_c,
            "humidity_alert_pct": humidity_alert_pct,
            "telemetry_interval_ms": 5000,
        }
        Device.objects.filter(pk=device.pk).update(
            desired_config=cfg_data,
            applied_config=cfg_data,
            latest_mode="AUTO",
            latest_door_status="CLOSED",
        )
        device.desired_config = cfg_data
        device.applied_config = cfg_data
        device.latest_mode = "AUTO"
        device.latest_door_status = "CLOSED"

        # Xóa các phiên bản cấu hình cũ/mới khác để tránh vướng Settle Rule 120s
        DeviceConfig.objects.filter(device=device).exclude(version=1).delete()
        hist, _ = DeviceConfig.objects.update_or_create(
            device=device,
            version=1,
            defaults={
                "config_data": cfg_data,
                "applied": True,
                "applied_at": now - timedelta(hours=2),
            },
        )
        DeviceConfig.objects.filter(pk=hist.pk).update(created_at=now - timedelta(hours=2))

    def _seed_window_samples(
        self,
        device: Device,
        scenario: str,
        *,
        with_noise: bool = False,
        rng: random.Random | None = None,
    ) -> None:
        """Tạo 20 mẫu đo trong cửa sổ 10 phút gần nhất (mỗi mẫu cách nhau 30 giây)."""
        now = timezone.now()
        gen = rng or random.Random(42)
        Telemetry.objects.filter(
            device=device,
            measured_at__gte=now - timedelta(minutes=15),
        ).delete()

        records: list[Telemetry] = []
        n_samples = 120
        step_s = 5
        latest_temp = 27.5
        latest_humid = 53.0
        latest_fan = "OFF"
        latest_door = "CLOSED"
        latest_mode = "AUTO"

        for i in range(n_samples):
            ts = now - timedelta(seconds=(n_samples - 1 - i) * step_s)
            progress = i / float(max(1, n_samples - 1))
            t_noise = gen.gauss(0.0, 0.08) if with_noise else 0.0
            h_noise = gen.gauss(0.0, 0.25) if with_noise else 0.0

            if scenario == "normal":
                temp = round(27.4 + 0.35 * math.sin(i * 0.15) + t_noise, 2)
                humid = round(52.0 + 0.8 * math.cos(i * 0.12) + h_noise, 1)
                fan_status = "ON" if temp >= 30.0 else "OFF"
                door_status = "CLOSED"
                mode = "AUTO"
            elif scenario == "cooling_degradation":
                temp = round(32.0 + 3.8 * progress + t_noise, 2)
                humid = round(68.0 + 2.8 * progress + h_noise, 1)
                fan_status = "ON"
                door_status = "CLOSED"
                mode = "AUTO"
            elif scenario == "sensor_anomaly":
                temp = 28.20
                humid = 54.1
                fan_status = "OFF"
                door_status = "CLOSED"
                mode = "AUTO"
            elif scenario == "recovering":
                temp = round(33.4 - 4.8 * progress + t_noise, 2)
                humid = round(58.0 - 2.8 * progress + h_noise, 1)
                fan_status = "ON" if temp > 28.0 else "OFF"
                door_status = "CLOSED"
                mode = "AUTO"
            elif scenario == "door_open":
                temp = round(28.5 + 2.1 * progress + t_noise, 2)
                humid = round(54.0 + 26.5 * progress + h_noise, 1)
                fan_status = "ON" if temp >= 30.0 else "OFF"
                door_status = "OPEN"
                mode = "AUTO"
            elif scenario == "manual_off":
                temp = round(31.2 + 1.5 * progress + t_noise, 2)
                humid = round(61.0 + 1.9 * progress + h_noise, 1)
                fan_status = "OFF"
                door_status = "CLOSED"
                mode = "MANUAL"
            else:
                temp = round(27.5 + t_noise, 2)
                humid = round(53.0 + h_noise, 1)
                fan_status = "OFF"
                door_status = "CLOSED"
                mode = "AUTO"

            latest_temp, latest_humid, latest_fan, latest_door, latest_mode = (
                temp,
                humid,
                fan_status,
                door_status,
                mode,
            )
            records.append(
                Telemetry(
                    device=device,
                    msg_id=f"seed-{device.id}-{scenario}-{i}-{uuid.uuid4().hex[:6]}",
                    seq=i + 1,
                    measured_at=ts,
                    temperature=temp,
                    humidity=humid,
                    fan_status=fan_status,
                    door_status=door_status,
                    mode=mode,
                )
            )

        Telemetry.objects.bulk_create(records)
        Device.objects.filter(pk=device.pk).update(
            status="ACTIVE",
            last_seen_at=now,
            latest_temperature=latest_temp,
            latest_humidity=latest_humid,
            latest_fan_status=latest_fan,
            latest_door_status=latest_door,
            latest_mode=latest_mode,
        )
        device.refresh_from_db()

    def _seed_all_scenarios(self, *, with_noise: bool = False) -> list[tuple[Device, str]]:
        """Tạo/cập nhật các tủ mẫu tương ứng với các kịch bản AI để hiển thị ngay trên UI."""
        now = timezone.now()
        rng = random.Random(2026)

        cab1, _ = Device.objects.update_or_create(
            id="CABINET-01",
            defaults={
                "name": "Tủ Mạng Phòng Server Lab 01",
                "status": "ACTIVE",
                "hardware_version": "ESP32-WROOM-32D",
                "firmware_version": "v1.4.0",
                "last_seen_at": now,
            },
        )
        self._ensure_cabinet_config(cab1)

        cab_norm, _ = Device.objects.update_or_create(
            id="CABINET-05",
            defaults={
                "name": "Tủ Mạng Tầng 3 Core Switch (AI Bình thường)",
                "status": "ACTIVE",
                "hardware_version": "ESP32-WROOM-32E",
                "firmware_version": "v1.4.0",
                "last_seen_at": now,
            },
        )
        self._ensure_cabinet_config(cab_norm)

        cab_sensor, _ = Device.objects.update_or_create(
            id="CABINET-06",
            defaults={
                "name": "Tủ Mạng Khu Vực B (AI Cảnh báo Cảm biến)",
                "status": "ACTIVE",
                "hardware_version": "ESP32-WROOM-32E",
                "firmware_version": "v1.4.0",
                "last_seen_at": now,
            },
        )
        self._ensure_cabinet_config(cab_sensor)

        pairs: list[tuple[Device, str]] = [
            (cab1, "cooling_degradation"),
            (cab_norm, "normal"),
            (cab_sensor, "sensor_anomaly"),
        ]

        cab_wokwi = Device.objects.filter(id="cabinet-001").first()
        if cab_wokwi is not None:
            self._ensure_cabinet_config(cab_wokwi)
            pairs.append((cab_wokwi, "cooling_degradation"))

        for dev, sc in pairs:
            self._seed_window_samples(dev, sc, with_noise=with_noise, rng=rng)
            if sc in ("cooling_degradation", "sensor_anomaly"):
                evaluate_and_sync_device_anomaly_alerts(dev, as_of=now)
                if sc == "cooling_degradation" and not DeviceCommand.objects.filter(device=dev).exists():
                    DeviceCommand.objects.create(
                        device=dev,
                        command_type="SET_FAN",
                        payload={"state": "ON"},
                        status="ACKED",
                        correlation_id=f"seed-corr-{dev.id}",
                        sent_at=now - timedelta(minutes=18),
                        acked_at=now - timedelta(minutes=18) + timedelta(seconds=1),
                    )
            else:
                Alert.objects.filter(
                    device=dev,
                    alert_type__in=["COOLING_DEGRADATION", "SENSOR_ANOMALY"],
                    status__in=["OPEN", "ACKNOWLEDGED"],
                ).update(status="RESOLVED", resolved_at=now)

        return pairs

    def handle(self, *args: Any, **options: Any) -> None:
        scenario = options["scenario"]
        with_noise = bool(options.get("noise"))
        live = bool(options["live"])
        interval = max(3, int(options["interval"]))
        rng = random.Random(2026)

        if scenario == "all":
            pairs = self._seed_all_scenarios(with_noise=with_noise)
        else:
            dev = Device.objects.filter(id__in=["cabinet-001", "CABINET-01"]).order_by("id").first()
            if dev is None:
                dev, _ = Device.objects.update_or_create(
                    id="CABINET-01",
                    defaults={
                        "name": "Tủ Mạng Phòng Server Lab 01",
                        "status": "ACTIVE",
                        "last_seen_at": timezone.now(),
                    },
                )
            self._ensure_cabinet_config(dev)
            self._seed_window_samples(dev, scenario, with_noise=with_noise, rng=rng)
            if scenario in ("cooling_degradation", "sensor_anomaly"):
                evaluate_and_sync_device_anomaly_alerts(dev, as_of=timezone.now())
            pairs = [(dev, scenario)]

        self.stdout.write(self.style.SUCCESS("=== KẾT QUẢ BƠM DỮ LIỆU DEMO AI ==="))
        for dev, sc in pairs:
            ev = evaluate_device_anomaly(dev, as_of=timezone.now())
            self.stdout.write(
                f"  - Tủ [{dev.id}] ({dev.name}) | Kịch bản: {sc} | "
                f"Trạng thái AI: {ev['status']} | Loại: {ev.get('anomaly_type')} | "
                f"ml_score: {ev.get('ml_score')} | Lý do: {ev.get('reason')}"
            )

        if not live:
            return

        self.stdout.write(
            self.style.WARNING(
                f"\n[LIVE DEMO] Đang đẩy mẫu viễn trắc mới mỗi {interval} giây cho {len(pairs)} tủ (Nhấn Ctrl+C để dừng)..."
            )
        )
        step = 0
        try:
            while True:
                time.sleep(interval)
                step += 1
                now = timezone.now()
                for dev, sc in pairs:
                    if sc == "normal":
                        temp = round(27.5 + 0.4 * math.sin(step * 0.35) + rng.gauss(0.0, 0.08), 2)
                        humid = round(52.5 + 0.9 * math.cos(step * 0.25) + rng.gauss(0.0, 0.25), 1)
                        fan_status = "OFF"
                        mode = "AUTO"
                    elif sc == "cooling_degradation":
                        temp = round(35.2 + 0.05 * (step % 15) + rng.gauss(0.0, 0.08), 2)
                        humid = round(70.5 + 0.1 * (step % 10) + rng.gauss(0.0, 0.25), 1)
                        fan_status = "ON"
                        mode = "AUTO"
                    elif sc == "sensor_anomaly":
                        temp = 28.20
                        humid = 54.1
                        fan_status = "OFF"
                        mode = "AUTO"
                    elif sc == "recovering":
                        temp = round(max(27.5, 31.0 - 0.15 * (step % 20)) + rng.gauss(0.0, 0.08), 2)
                        humid = round(55.0 + rng.gauss(0.0, 0.25), 1)
                        fan_status = "ON" if temp > 28.0 else "OFF"
                        mode = "AUTO"
                    elif sc == "door_open":
                        temp = round(30.2 + rng.gauss(0.0, 0.12), 2)
                        humid = round(min(85.0, 74.0 + 0.4 * (step % 15)) + rng.gauss(0.0, 0.3), 1)
                        fan_status = "ON"
                        mode = "AUTO"
                    else:
                        temp = round(31.8 + rng.gauss(0.0, 0.08), 2)
                        humid = round(62.0 + rng.gauss(0.0, 0.25), 1)
                        fan_status = "OFF"
                        mode = "MANUAL"

                    Telemetry.objects.create(
                        device=dev,
                        msg_id=f"live-{dev.id}-{step}-{uuid.uuid4().hex[:6]}",
                        seq=100 + step,
                        measured_at=now,
                        temperature=temp,
                        humidity=humid,
                        fan_status=fan_status,
                        door_status="CLOSED",
                        mode=mode,
                    )
                    Device.objects.filter(pk=dev.pk).update(
                        last_seen_at=now,
                        latest_temperature=temp,
                        latest_humidity=humid,
                        latest_fan_status=fan_status,
                        latest_mode=mode,
                    )
        except KeyboardInterrupt:
            self.stdout.write(self.style.SUCCESS("\nĐã dừng chế độ bơm dữ liệu AI trực tiếp."))
