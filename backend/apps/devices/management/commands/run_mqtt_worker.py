import json
import logging
import signal
import sys
import time
import uuid
from datetime import timedelta
import paho.mqtt.client as mqtt
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.alerts.models import Alert
from apps.audit.models import AuditLog
from apps.audit.services import AuditService
from apps.commands.models import Command as CommandModel, OutboxMessage
from apps.devices.models import Device
from apps.telemetry.models import Telemetry

logger = logging.getLogger('mqtt_worker')


class Command(BaseCommand):
    help = "Chạy nền tiến trình MQTT Worker: Thu thập viễn trắc, xử lý phản hồi ACK và quét gửi lệnh Outbox."

    def add_arguments(self, parser):
        parser.add_argument('--host', type=str, default=settings.MQTT_BROKER_HOST, help='Địa chỉ IP / Hostname MQTT Broker')
        parser.add_argument('--port', type=int, default=settings.MQTT_BROKER_PORT, help='Cổng kết nối MQTT Broker')
        parser.add_argument('--keepalive', type=int, default=settings.MQTT_KEEPALIVE, help='Keepalive interval (giây)')

    def handle(self, *args, **options):
        broker_host = options['host']
        broker_port = options['port']
        keepalive = options['keepalive']

        self.stdout.write(self.style.SUCCESS(
            f"=== BẮT ĐẦU TIẾN TRÌNH DJANGO MQTT WORKER ==="
            f"\nKết nối tới MQTT Broker: {broker_host}:{broker_port}"
        ))

        client = mqtt.Client(client_id="django_mqtt_worker_service")

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                self.stdout.write(self.style.SUCCESS("[MQTT] Kết nối thành công tới Mosquitto Broker."))
                # Đăng ký các topics cốt lõi theo contract
                client.subscribe("cabinet/+/telemetry", qos=1)
                client.subscribe("cabinet/+/status", qos=1)
                client.subscribe("cabinet/+/ack", qos=1)
                self.stdout.write(self.style.NOTICE(
                    "[MQTT] Đã subscribe: cabinet/+/telemetry, cabinet/+/status, cabinet/+/ack"
                ))
            else:
                self.stdout.write(self.style.ERROR(f"[MQTT] Kết nối thất bại với mã lỗi (RC): {rc}"))

        def on_message(client, userdata, msg):
            try:
                topic = msg.topic
                payload_str = msg.payload.decode('utf-8')
                data = json.loads(payload_str)
                parts = topic.split('/')

                if len(parts) >= 3 and parts[0] == 'cabinet':
                    device_id = parts[1]
                    sub_topic = parts[2]

                    if sub_topic == 'telemetry':
                        self.process_telemetry(device_id, data)
                    elif sub_topic == 'ack':
                        self.process_ack(device_id, data)
                    elif sub_topic == 'status':
                        self.process_status(device_id, data)
            except Exception as e:
                logger.error("[MQTT Worker] Lỗi xử lý tin nhắn từ topic %s: %s", msg.topic, e, exc_info=True)

        client.on_connect = on_connect
        client.on_message = on_message

        try:
            client.connect(broker_host, broker_port, keepalive)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[MQTT Worker] Không thể kết nối tới Broker ({broker_host}:{broker_port}): {e}"))
            self.stdout.write(self.style.WARNING("Tiếp tục khởi động client ở chế độ loop để chờ broker online..."))

        client.loop_start()

        def stop_worker(signum, frame):
            self.stdout.write(self.style.WARNING("\nĐang dừng MQTT Worker..."))
            client.loop_stop()
            client.disconnect()
            sys.exit(0)

        signal.signal(signal.SIGINT, stop_worker)
        signal.signal(signal.SIGTERM, stop_worker)

        # Vòng lặp kiểm tra lệnh timeout và quét outbox
        self.stdout.write(self.style.SUCCESS("[MQTT Worker] Worker đang hoạt động. Nhấn Ctrl+C để dừng."))
        while True:
            try:
                self.sweep_pending_outbox(client)
                self.check_command_timeouts()
                time.sleep(5)
            except Exception as e:
                logger.error("[MQTT Worker] Lỗi vòng lặp quét định kỳ: %s", e)
                time.sleep(5)

    def process_telemetry(self, device_id: str, data: dict):
        """Xử lý gói tin viễn trắc gửi lên từ ESP32"""
        temperature = float(data.get('temperature', data.get('temp', 0.0)))
        humidity = float(data.get('humidity', data.get('hum', 0.0)))

        # Validate phạm vi vật lý hợp lệ
        if not (-40.0 <= temperature <= 80.0 and 0.0 <= humidity <= 100.0):
            logger.warning("[Telemetry] Gói tin ngoài phạm vi cảm biến DHT22 cho tủ %s: temp=%s, hum=%s",
                           device_id, temperature, humidity)
            return

        fan_raw = data.get('fan_status', data.get('fan', 'OFF'))
        if str(fan_raw).upper() in ('1', 'ON', 'TRUE'):
            fan_status = 'ON'
        elif str(fan_raw).upper() in ('FAULT', 'ERROR'):
            fan_status = 'FAULT'
        else:
            fan_status = 'OFF'

        door_raw = data.get('door_status', data.get('door', 'CLOSED'))
        door_status = 'OPEN' if str(door_raw).upper() in ('1', 'OPEN', 'TRUE') else 'CLOSED'

        mode = str(data.get('mode', 'AUTO')).upper()
        uptime = int(data.get('uptime', 0))
        seq = int(data.get('seq', 0))
        now = timezone.now()

        with transaction.atomic():
            device, _ = Device.objects.get_or_create(
                id=device_id,
                defaults={
                    'name': f"Tủ Mạng {device_id}",
                    'status': 'ACTIVE',
                    'source': 'REAL'
                }
            )

            # Cập nhật số đo mới nhất vào bản chiếu (projection)
            device.last_seen_at = now
            device.latest_temperature = temperature
            device.latest_humidity = humidity
            device.latest_fan_status = fan_status
            device.latest_door_status = door_status
            device.latest_mode = mode
            device.save(update_fields=[
                'last_seen_at', 'latest_temperature', 'latest_humidity',
                'latest_fan_status', 'latest_door_status', 'latest_mode', 'updated_at'
            ])

            # Lưu vào chuỗi dữ liệu lịch sử Telemetry
            Telemetry.objects.create(
                device=device,
                seq=seq,
                temperature=temperature,
                humidity=humidity,
                fan_status=fan_status,
                door_status=door_status,
                mode=mode,
                uptime=uptime,
                measured_at=now,
                raw_data=data
            )

            # Đánh giá cảnh báo tự động
            self.evaluate_alerts(device, temperature, humidity, fan_status, door_status)

    def evaluate_alerts(self, device: Device, temp: float, hum: float, fan: str, door: str):
        """Kiểm tra các ngưỡng cảnh báo và cập nhật trạng thái Alert"""
        # Lấy ngưỡng cấu hình từ applied_config nếu có, ngược lại dùng ngưỡng mặc định
        cfg = device.applied_config or device.desired_config or {}
        max_temp = float(cfg.get('max_temp', 35.0))
        max_hum = float(cfg.get('max_hum', 80.0))

        # 1. Cảnh báo nhiệt độ cao (HIGH_TEMP)
        if temp > max_temp:
            active_alert = Alert.objects.filter(device=device, alert_type='HIGH_TEMP', status__in=['OPEN', 'ACKNOWLEDGED']).first()
            if not active_alert:
                Alert.objects.create(
                    device=device,
                    alert_type='HIGH_TEMP',
                    severity='CRITICAL' if temp > max_temp + 5.0 else 'WARNING',
                    status='OPEN',
                    message=f"Nhiệt độ tủ {device.id} đạt {temp}°C, vượt ngưỡng an toàn ({max_temp}°C)!",
                    threshold=max_temp,
                    current_value=temp
                )
        elif temp <= (max_temp - 1.0):
            # Tự động khắc phục cảnh báo khi nhiệt độ hạ về mức an toàn
            Alert.objects.filter(device=device, alert_type='HIGH_TEMP', status__in=['OPEN', 'ACKNOWLEDGED']).update(
                status='RESOLVED',
                resolved_at=timezone.now()
            )

        # 2. Cảnh báo độ ẩm cao (HIGH_HUMIDITY)
        if hum > max_hum:
            active_alert = Alert.objects.filter(device=device, alert_type='HIGH_HUMIDITY', status__in=['OPEN', 'ACKNOWLEDGED']).first()
            if not active_alert:
                Alert.objects.create(
                    device=device,
                    alert_type='HIGH_HUMIDITY',
                    severity='WARNING',
                    status='OPEN',
                    message=f"Độ ẩm tủ {device.id} đạt {hum}%, vượt ngưỡng an toàn ({max_hum}%)!",
                    threshold=max_hum,
                    current_value=hum
                )
        elif hum <= (max_hum - 2.0):
            Alert.objects.filter(device=device, alert_type='HIGH_HUMIDITY', status__in=['OPEN', 'ACKNOWLEDGED']).update(
                status='RESOLVED',
                resolved_at=timezone.now()
            )

        # 3. Cảnh báo mở cửa tủ (DOOR_OPEN)
        if door == 'OPEN':
            active_alert = Alert.objects.filter(device=device, alert_type='DOOR_OPEN', status__in=['OPEN', 'ACKNOWLEDGED']).first()
            if not active_alert:
                Alert.objects.create(
                    device=device,
                    alert_type='DOOR_OPEN',
                    severity='WARNING',
                    status='OPEN',
                    message=f"Cửa tủ mạng {device.id} đang ở trạng thái MỞ!"
                )
        elif door == 'CLOSED':
            Alert.objects.filter(device=device, alert_type='DOOR_OPEN', status__in=['OPEN', 'ACKNOWLEDGED']).update(
                status='RESOLVED',
                resolved_at=timezone.now()
            )

    def process_ack(self, device_id: str, data: dict):
        """Xử lý gói tin ACK từ thiết bị khi đã thực thi xong lệnh điều khiển"""
        correlation_id = data.get('correlation_id') or data.get('command_id')
        if not correlation_id:
            logger.warning("[MQTT ACK] Gói tin ACK thiếu correlation_id: %s", data)
            return

        cmd = CommandModel.objects.filter(correlation_id=correlation_id).first()
        if not cmd:
            logger.warning("[MQTT ACK] Không tìm thấy lệnh tương ứng với correlation_id: %s", correlation_id)
            return

        now = timezone.now()
        is_success = str(data.get('status', 'SUCCESS')).upper() in ('SUCCESS', 'OK', 'APPLIED')

        with transaction.atomic():
            cmd.status = 'ACKED' if is_success else 'FAILED'
            cmd.acked_at = now
            if not is_success:
                cmd.error_message = data.get('message', 'Thiết bị báo lỗi thực thi.')
            cmd.save(update_fields=['status', 'acked_at', 'error_message'])

            # Đánh dấu OutboxMessage đã hoàn tất
            OutboxMessage.objects.filter(command=cmd).update(
                status='PUBLISHED',
                published_at=now
            )

            # Ghi nhận vào AuditLog
            AuditService.record_event(
                function_key="DEVICE_CONTROL",
                action="COMMAND_ACKNOWLEDGED",
                outcome="SUCCESS" if is_success else "FAILED",
                summary=f"Nhận phản hồi ACK từ tủ {device_id} cho lệnh #{cmd.id}",
                request_id=str(uuid.uuid4()),
                actor_user=cmd.actor,
                actor_type=AuditLog.ActorType.SYSTEM,
                target_type="DEVICE",
                target_id=device_id,
                device_id=device_id,
                correlation_id=correlation_id,
                metadata={'command_id': str(cmd.id), 'device_id': device_id}
            )

        self.stdout.write(self.style.SUCCESS(
            f"[MQTT ACK] Khớp thành công lệnh #{cmd.id} (Tủ: {device_id}) -> Trạng thái: {cmd.status}"
        ))

    def process_status(self, device_id: str, data: dict):
        """Xử lý gói tin heartbeat / status từ node"""
        now = timezone.now()
        Device.objects.filter(id=device_id).update(last_seen_at=now)

    def sweep_pending_outbox(self, client):
        """Quét các tin nhắn chưa gửi trong Outbox để xuất bản lại tới MQTT Broker"""
        pending_messages = OutboxMessage.objects.filter(status='PENDING').select_related('command')[:10]
        for msg in pending_messages:
            try:
                topic = msg.topic
                payload_str = json.dumps(msg.payload, ensure_ascii=False)
                info = client.publish(topic, payload_str, qos=msg.qos)
                msg.status = 'PUBLISHED'
                msg.published_at = timezone.now()
                msg.retry_count += 1
                msg.save(update_fields=['status', 'published_at', 'retry_count'])

                msg.command.status = 'SENT'
                msg.command.sent_at = timezone.now()
                msg.command.save(update_fields=['status', 'sent_at'])
            except Exception as e:
                msg.retry_count += 1
                msg.last_error = str(e)
                msg.save(update_fields=['retry_count', 'last_error'])

    def check_command_timeouts(self):
        """Đánh dấu TIMEOUT cho các lệnh đã gửi quá 15 giây mà không nhận được ACK từ phần cứng"""
        timeout_threshold = timezone.now() - timedelta(seconds=15)
        timed_out_commands = CommandModel.objects.filter(
            status='SENT',
            sent_at__lt=timeout_threshold
        )
        for cmd in timed_out_commands:
            cmd.status = 'TIMEOUT'
            cmd.error_message = 'Quá 15 giây không nhận được phản hồi ACK từ phần cứng ESP32.'
            cmd.save(update_fields=['status', 'error_message'])
            self.stdout.write(self.style.WARNING(
                f"[TIMEOUT] Lệnh #{cmd.id} gửi tới tủ {cmd.device_id} đã hết thời gian chờ ACK."
            ))
