import json
import logging
import time
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from django.db import close_old_connections
import paho.mqtt.client as mqtt
from cabinet.models import Device, Telemetry, Alert, CommandLog

logger = logging.getLogger("MQTTWorker")


class Command(BaseCommand):
    help = "Chạy tiến trình lắng nghe MQTT Broker (ESP32 Telemetry & Status)"

    def handle(self, *args, **options):
        broker_host = getattr(settings, "MQTT_BROKER_HOST", "localhost")
        broker_port = getattr(settings, "MQTT_BROKER_PORT", 1883)
        client_id = "django-mqtt-worker"

        self.stdout.write(self.style.SUCCESS(f"[*] Đang khởi động MQTT Worker... Kết nối tới {broker_host}:{broker_port}"))

        client = mqtt.Client(client_id=client_id, clean_session=True)

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                self.stdout.write(self.style.SUCCESS("[+] Đã kết nối MQTT Broker thành công!"))
                topics = [
                    ("cabinet/v3/devices/+/telemetry", 0),
                    ("cabinet/devices/+/telemetry", 0),
                    ("cabinet/v3/devices/+/status", 0),
                    ("cabinet/devices/+/status", 0),
                    ("cabinet/v3/devices/+/acks", 0),
                ]
                for topic, qos in topics:
                    client.subscribe(topic, qos=qos)
                    self.stdout.write(f" -> Lắng nghe topic: {topic}")
            else:
                self.stdout.write(self.style.ERROR(f"[-] Kết nối MQTT Broker thất bại, mã lỗi: {rc}"))

        def on_message(client, userdata, msg):
            close_old_connections()
            topic = msg.topic
            payload_str = msg.payload.decode("utf-8", errors="ignore")
            
            try:
                data = json.loads(payload_str)
            except Exception:
                return

            parts = topic.split("/")
            # Phân tích device_id từ topic (cabinet/v3/devices/{device_id}/... hoặc cabinet/devices/{device_id}/...)
            device_id = None
            if "devices" in parts:
                idx = parts.index("devices")
                if idx + 1 < len(parts):
                    device_id = parts[idx + 1]

            if not device_id and "device_id" in data:
                device_id = data["device_id"]

            if not device_id:
                return

            now = timezone.now()
            device, _ = Device.objects.get_or_create(
                device_id=device_id,
                defaults={"name": f"Tủ Mạng {device_id}", "location": "Lab IoT"}
            )
            device.connection_state = "online"
            device.last_seen_at = now

            channel = parts[-1]
            if channel == "telemetry":
                # Nhận dữ liệu nhiệt độ, độ ẩm từ ESP32
                temp = data.get("temperature_c", data.get("temperature"))
                hum = data.get("humidity_pct", data.get("humidity"))
                fan = data.get("fan_output", data.get("fan_state", device.fan_output))
                mode = data.get("mode", device.mode)

                if temp is not None:
                    try:
                        temp = float(temp)
                    except (ValueError, TypeError):
                        temp = None
                if hum is not None:
                    try:
                        hum = float(hum)
                    except (ValueError, TypeError):
                        hum = None

                device.fan_output = bool(fan)
                device.mode = mode
                device.save(update_fields=["connection_state", "last_seen_at", "fan_output", "mode", "updated_at"])

                # Lưu Telemetry
                Telemetry.objects.create(
                    device=device,
                    temperature_c=temp,
                    humidity_pct=hum,
                    fan_output=bool(fan),
                    mode=mode,
                )

                # Tự động đánh giá cảnh báo
                if temp is not None and temp >= device.temp_alert_thresh:
                    # Kiểm tra xem đã có cảnh báo nhiệt cao nào đang mở chưa
                    if not Alert.objects.filter(device=device, alert_type="HIGH_TEMPERATURE", status="open").exists():
                        Alert.objects.create(
                            device=device,
                            alert_type="HIGH_TEMPERATURE",
                            severity="critical" if temp >= (device.temp_alert_thresh + 3) else "high",
                            message=f"Nhiệt độ tủ vượt ngưỡng cho phép: {temp:.1f} °C (Ngưỡng: {device.temp_alert_thresh} °C)",
                            status="open",
                        )
                elif temp is not None and temp <= device.temp_threshold_off:
                    # Tự động đóng cảnh báo nhiệt nếu nhiệt độ đã an toàn
                    Alert.objects.filter(device=device, alert_type="HIGH_TEMPERATURE", status="open").update(
                        status="resolved", resolved_at=now
                    )

                if hum is not None and hum >= device.humidity_alert_thresh:
                    if not Alert.objects.filter(device=device, alert_type="HIGH_HUMIDITY", status="open").exists():
                        Alert.objects.create(
                            device=device,
                            alert_type="HIGH_HUMIDITY",
                            severity="medium",
                            message=f"Độ ẩm trong tủ cao: {hum:.1f} % (Ngưỡng: {device.humidity_alert_thresh} %)",
                            status="open",
                        )

                if data.get("sensor_ok") is False:
                    if not Alert.objects.filter(device=device, alert_type="SENSOR_FAULT", status="open").exists():
                        Alert.objects.create(
                            device=device,
                            alert_type="SENSOR_FAULT",
                            severity="critical",
                            message="Sự cố cảm biến DHT22 tại tủ mạng",
                            status="open",
                        )
                
                self.stdout.write(f"[{device.device_id}] Nhận Telemetry: {temp}°C - {hum}% | Quạt: {fan}")

            elif channel == "status":
                conn = data.get("connection", "ONLINE").lower()
                device.connection_state = "online" if conn == "online" else "offline"
                health = data.get("health", "OK").lower()
                device.health_state = "fault" if health == "fault" else "normal"
                device.save(update_fields=["connection_state", "health_state", "last_seen_at", "updated_at"])
                self.stdout.write(f"[{device.device_id}] Cập nhật trạng thái: {conn.upper()}")

            elif channel == "acks":
                cmd_id = data.get("command_id")
                if cmd_id:
                    CommandLog.objects.filter(device=device, id=cmd_id).update(
                        status="APPLIED",
                        executed_at=now,
                    )
                    self.stdout.write(f"[{device.device_id}] Đã nhận phản hồi ACK cho lệnh: {cmd_id}")

        client.on_connect = on_connect
        client.on_message = on_message

        try:
            client.connect(broker_host, broker_port, keepalive=60)
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Chưa kết nối được tới MQTT Broker ({e}). Sẽ tự thử lại..."))

        try:
            client.loop_forever()
        except KeyboardInterrupt:
            self.stdout.write(self.style.SUCCESS("Đã dừng MQTT Worker."))
