import json
import logging
import signal
import sys
import time
import uuid
from datetime import timedelta, timezone as dt_timezone
import paho.mqtt.client as mqtt
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.alerts.models import Alert
from apps.audit.models import AuditLog
from apps.audit.services import AuditService
from apps.commands.models import Command as CommandModel, OutboxMessage
from apps.devices.models import DEVICE_OFFLINE_THRESHOLD_SECONDS, Device, DeviceConfig
from apps.telemetry.models import Telemetry

logger = logging.getLogger('mqtt_worker')


class Command(BaseCommand):
    help = "Chạy nền tiến trình MQTT Worker: Thu thập viễn trắc V1.4, xử lý Application ACK và quét gửi lệnh Outbox."

    def add_arguments(self, parser):
        parser.add_argument('--host', type=str, default=settings.MQTT_BROKER_HOST, help='Địa chỉ IP / Hostname MQTT Broker')
        parser.add_argument('--port', type=int, default=settings.MQTT_BROKER_PORT, help='Cổng kết nối MQTT Broker')
        parser.add_argument('--keepalive', type=int, default=settings.MQTT_KEEPALIVE, help='Keepalive interval (giây)')

    def handle(self, *args, **options):
        broker_host = options['host']
        broker_port = options['port']
        keepalive = options['keepalive']

        self.stdout.write(self.style.SUCCESS(
            f"=== BẮT ĐẦU TIẾN TRÌNH DJANGO MQTT WORKER (ĐẶC TẢ V1.4) ==="
            f"\nKết nối tới MQTT Broker: {broker_host}:{broker_port}"
        ))

        client = mqtt.Client(client_id="django_mqtt_worker_service")
        mqtt_user = getattr(settings, 'MQTT_USERNAME', '')
        mqtt_pass = getattr(settings, 'MQTT_PASSWORD', '')
        if mqtt_user:
            client.username_pw_set(mqtt_user, mqtt_pass)

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                self.stdout.write(self.style.SUCCESS("[MQTT] Kết nối thành công tới Mosquitto Broker."))
                # Đăng ký các topics theo Đặc tả Kỹ thuật V1.4 và hỗ trợ tương thích ngược
                topics = [
                    ("cabinet/v3/devices/+/telemetry", 1),
                    ("cabinet/v3/devices/+/status", 1),
                    ("cabinet/v3/devices/+/acks", 1),
                    ("cabinet/+/telemetry", 1),
                    ("cabinet/+/status", 1),
                    ("cabinet/+/ack", 1),
                ]
                client.subscribe(topics)
                self.stdout.write(self.style.NOTICE(
                    "[MQTT] Đã subscribe topics V1.4 (cabinet/v3/devices/+/...) và tương thích cũ."
                ))
            else:
                self.stdout.write(self.style.ERROR(f"[MQTT] Kết nối thất bại với mã lỗi (RC): {rc}"))

        def on_message(client, userdata, msg):
            try:
                close_old_connections()
                topic = msg.topic
                payload_str = msg.payload.decode('utf-8')
                data = json.loads(payload_str)
                parts = topic.split('/')

                device_id = None
                sub_topic = None

                if len(parts) >= 5 and parts[0] == 'cabinet' and parts[1] == 'v3' and parts[2] == 'devices':
                    device_id = parts[3]
                    sub_topic = parts[4]
                elif len(parts) >= 3 and parts[0] == 'cabinet':
                    device_id = parts[1]
                    sub_topic = parts[2]

                if not device_id or not sub_topic:
                    return

                if sub_topic in ('telemetry',):
                    self.process_telemetry(device_id, data)
                elif sub_topic in ('acks', 'ack'):
                    self.process_ack(device_id, data)
                elif sub_topic in ('status',):
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
                close_old_connections()
                self.sweep_pending_outbox(client)
                self.check_command_timeouts()
                self.check_offline_devices()
                time.sleep(1)
            except Exception as e:
                logger.error("[MQTT Worker] Lỗi vòng lặp quét định kỳ: %s", e)
                time.sleep(2)

    def process_telemetry(self, device_id: str, data: dict):
        """
        Xử lý gói tin viễn trắc gửi lên từ ESP32.
        Hỗ trợ dual-parser cho định dạng V1.4 (temperature_c, humidity_pct, fan_output: bool, sensor_ok)
        và định dạng tương thích (temp, hum, fan).
        Chặn thiết bị lạ không thuộc whitelist hoặc chưa ACTIVE trong DB.
        """
        # 1. Whitelist & lifecycle validation: Chỉ nhận dữ liệu từ tủ mạng đã kích hoạt (ACTIVE)
        device = Device.objects.filter(id=device_id).first()
        if not device:
            logger.warning("[MQTT Worker] Bỏ qua gói tin viễn trắc từ thiết bị chưa đăng ký: %s", device_id)
            return

        if device.status != 'ACTIVE':
            logger.warning("[MQTT Worker] Thiết bị %s đang ở trạng thái '%s', từ chối nhận viễn trắc.",
                           device_id, device.status)
            return

        sensor_ok = bool(data.get('sensor_ok', True))

        # Dual-parser nhiệt độ & độ ẩm
        temp_raw = data.get('temperature_c')
        if temp_raw is None:
            temp_raw = data.get('temperature', data.get('temp'))

        hum_raw = data.get('humidity_pct')
        if hum_raw is None:
            hum_raw = data.get('humidity', data.get('hum'))

        if sensor_ok:
            temperature = float(temp_raw) if temp_raw is not None else None
            humidity = float(hum_raw) if hum_raw is not None else None
        else:
            temperature = None
            humidity = None

        # Validate phạm vi vật lý của cảm biến DHT22 nếu cảm biến hoạt động bình thường
        if sensor_ok and temperature is not None and humidity is not None:
            if not (-40.0 <= temperature <= 80.0 and 0.0 <= humidity <= 100.0):
                logger.warning("[Telemetry] Gói tin ngoài phạm vi cảm biến DHT22 cho tủ %s: temp=%s, hum=%s",
                               device_id, temperature, humidity)
                return

        # Dual-parser trạng thái quạt
        fan_raw = data.get('fan_output')
        if fan_raw is None:
            fan_raw = data.get('fan_status', data.get('fan', 'OFF'))

        if isinstance(fan_raw, bool):
            fan_status = 'ON' if fan_raw else 'OFF'
        elif str(fan_raw).upper() in ('1', 'ON', 'TRUE'):
            fan_status = 'ON'
        elif str(fan_raw).upper() in ('FAULT', 'ERROR'):
            fan_status = 'FAULT'
        else:
            fan_status = 'OFF'

        door_raw = data.get('door_status', data.get('door', 'CLOSED'))
        door_status = 'OPEN' if str(door_raw).upper() in ('1', 'OPEN', 'TRUE') else 'CLOSED'

        mode = str(data.get('mode', 'AUTO')).upper()
        if data.get('uptime_ms') is not None:
            uptime = int(data['uptime_ms']) // 1000
        else:
            uptime = int(data.get('uptime', 0))
        seq = int(data.get('seq', 0))
        now = timezone.now()

        measured_at = now
        raw_measured = data.get('measured_at')
        if isinstance(raw_measured, str) and raw_measured.strip():
            parsed_dt = parse_datetime(raw_measured)
            if parsed_dt is not None:
                if timezone.is_naive(parsed_dt):
                    parsed_dt = timezone.make_aware(parsed_dt, dt_timezone.utc)
                measured_at = parsed_dt

        with transaction.atomic():
            # Cập nhật bản chiếu số đo mới nhất (projection)
            device.last_seen_at = now
            if sensor_ok:
                if temperature is not None:
                    device.latest_temperature = temperature
                if humidity is not None:
                    device.latest_humidity = humidity
            else:
                device.latest_temperature = None
                device.latest_humidity = None
            device.latest_fan_status = fan_status
            device.latest_door_status = door_status
            device.latest_mode = mode
            device.save(update_fields=[
                'last_seen_at', 'latest_temperature', 'latest_humidity',
                'latest_fan_status', 'latest_door_status', 'latest_mode', 'updated_at'
            ])

            # Lưu vào bảng dữ liệu chuỗi thời gian Telemetry
            Telemetry.objects.create(
                device=device,
                seq=seq,
                temperature=temperature,
                humidity=humidity,
                fan_status=fan_status,
                door_status=door_status,
                mode=mode,
                uptime=uptime,
                measured_at=measured_at,
                raw_data=data
            )

            # Đánh giá cảnh báo tự động
            self.evaluate_alerts(device, temperature, humidity, fan_status, door_status, sensor_ok)

    def evaluate_alerts(self, device: Device, temp, hum, fan: str, door: str, sensor_ok: bool = True):
        """
        Động cơ đánh giá cảnh báo tự động với Hysteresis chống dao động ngưỡng:
        - HIGH_TEMP: Ngưỡng cấu hình (mặc định 35°C), trễ phục hồi temp_clear_margin_c (mặc định 1.0°C)
        - HIGH_HUMIDITY: Ngưỡng cấu hình (mặc định 80%), trễ phục hồi humidity_clear_margin_pct (mặc định 3.0%)
        - SENSOR_FAULT: Cảm biến DHT22 báo lỗi
        - DOOR_OPEN: Trạng thái cửa tủ đang mở
        """
        cfg = device.applied_config or device.desired_config or {}
        max_temp = float(cfg.get('temp_alert_c', cfg.get('max_temp', 35.0)))
        max_hum = float(cfg.get('humidity_alert_pct', cfg.get('max_hum', 80.0)))
        temp_margin = float(cfg.get('temp_clear_margin_c', 1.0))
        hum_margin = float(cfg.get('humidity_clear_margin_pct', 3.0))

        # 1. Cảnh báo lỗi cảm biến (SENSOR_FAULT)
        if not sensor_ok:
            active_fault = Alert.objects.filter(device=device, alert_type='SENSOR_FAULT', status__in=['OPEN', 'ACKNOWLEDGED']).first()
            if not active_fault:
                Alert.objects.create(
                    device=device,
                    alert_type='SENSOR_FAULT',
                    severity='CRITICAL',
                    status='OPEN',
                    message=f"Cảm biến DHT22 trên tủ {device.id} báo lỗi phần cứng hoặc mất kết nối."
                )
        else:
            Alert.objects.filter(device=device, alert_type='SENSOR_FAULT', status__in=['OPEN', 'ACKNOWLEDGED']).update(
                status='RESOLVED',
                resolved_at=timezone.now()
            )

        # 2. Cảnh báo nhiệt độ cao (HIGH_TEMP)
        if temp is not None:
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
            elif temp <= (max_temp - temp_margin):
                # Tự động khắc phục khi nhiệt độ hạ qua biên trễ
                Alert.objects.filter(device=device, alert_type='HIGH_TEMP', status__in=['OPEN', 'ACKNOWLEDGED']).update(
                    status='RESOLVED',
                    resolved_at=timezone.now()
                )

        # 3. Cảnh báo độ ẩm cao (HIGH_HUMIDITY)
        if hum is not None:
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
            elif hum <= (max_hum - hum_margin):
                # Tự động khắc phục khi độ ẩm hạ qua biên trễ
                Alert.objects.filter(device=device, alert_type='HIGH_HUMIDITY', status__in=['OPEN', 'ACKNOWLEDGED']).update(
                    status='RESOLVED',
                    resolved_at=timezone.now()
                )

        # 4. Cảnh báo mở cửa tủ (DOOR_OPEN)
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
        """
        Xử lý gói tin Application ACK từ thiết bị khi đã thực thi xong lệnh điều khiển.
        Nếu ACK đến muộn sau khi lệnh đã chuyển sang TIMEOUT, cập nhật thành APPLIED_LATE.
        """
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
            if is_success:
                # Nếu lệnh đã bị đánh dấu TIMEOUT trước đó, chuyển sang APPLIED_LATE
                if cmd.status == 'TIMEOUT':
                    cmd.status = 'APPLIED_LATE'
                else:
                    cmd.status = 'APPLIED'
            else:
                cmd.status = 'FAILED'
                cmd.error_message = (
                    data.get('error_code')
                    or data.get('reason')
                    or data.get('detail')
                    or data.get('message')
                    or 'Thiết bị báo lỗi thực thi.'
                )

            cmd.acked_at = now
            cmd.save(update_fields=['status', 'acked_at', 'error_message'])

            # Đồng bộ trạng thái quạt, chế độ và applied_config khi ACK thành công
            device = cmd.device
            update_fields = ['last_seen_at']
            device.last_seen_at = now

            result_data = data.get('result')
            if isinstance(result_data, dict):
                if 'fan_output' in result_data and result_data['fan_output'] is not None:
                    device.latest_fan_status = 'ON' if bool(result_data['fan_output']) else 'OFF'
                    update_fields.append('latest_fan_status')
                if 'mode' in result_data and result_data['mode']:
                    device.latest_mode = str(result_data['mode']).upper()
                    update_fields.append('latest_mode')

            if is_success and cmd.command_type == 'APPLY_CONFIG':
                applied_cfg = cmd.payload.get('config')
                if not isinstance(applied_cfg, dict):
                    cfg_json_str = (cmd.payload.get('params') or {}).get('config_json')
                    if isinstance(cfg_json_str, str):
                        try:
                            applied_cfg = json.loads(cfg_json_str)
                        except json.JSONDecodeError:
                            applied_cfg = None
                if isinstance(applied_cfg, dict):
                    device.applied_config = applied_cfg
                    update_fields.append('applied_config')

                cfg_version = cmd.payload.get('config_version') or (cmd.payload.get('params') or {}).get('config_version')
                if not cfg_version and isinstance(result_data, dict):
                    cfg_version = result_data.get('config_version')
                if cfg_version:
                    DeviceConfig.objects.filter(device=device, version=cfg_version).update(
                        applied=True,
                        applied_at=now
                    )

            device.save(update_fields=update_fields)

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
                summary=f"Nhận phản hồi ACK từ tủ {device_id} cho lệnh #{cmd.id} (Trạng thái: {cmd.status})",
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
        """Xử lý gói tin heartbeat / status / LWT từ node ESP32"""
        device = Device.objects.filter(id=device_id).first()
        if not device or device.status in ('LOCKED', 'DECOMMISSIONED', 'MAINTENANCE'):
            return

        now = timezone.now()
        conn_str = str(data.get('connection', '')).upper()
        is_offline = (conn_str == 'OFFLINE') or (data.get('online') is False)

        if is_offline:
            # Bản tin LWT hoặc báo ngắt kết nối: lùi last_seen_at quá ngưỡng offline và phát cảnh báo DEVICE_OFFLINE
            device.last_seen_at = now - timedelta(seconds=DEVICE_OFFLINE_THRESHOLD_SECONDS + 5)
            device.save(update_fields=['last_seen_at'])
            active_offline = Alert.objects.filter(
                device=device, alert_type='DEVICE_OFFLINE', status__in=['OPEN', 'ACKNOWLEDGED']
            ).first()
            if not active_offline:
                Alert.objects.create(
                    device=device,
                    alert_type='DEVICE_OFFLINE',
                    severity='WARNING',
                    status='OPEN',
                    message=f"Tủ mạng {device.id} báo mất kết nối (LWT / OFFLINE)."
                )
        else:
            update_fields = ['last_seen_at']
            device.last_seen_at = now
            if 'fan_output' in data and data['fan_output'] is not None:
                device.latest_fan_status = 'ON' if bool(data['fan_output']) else 'OFF'
                update_fields.append('latest_fan_status')
            if 'mode' in data and data['mode']:
                device.latest_mode = str(data['mode']).upper()
                update_fields.append('latest_mode')
            device.save(update_fields=update_fields)
            Alert.objects.filter(
                device=device, alert_type='DEVICE_OFFLINE', status__in=['OPEN', 'ACKNOWLEDGED']
            ).update(status='RESOLVED', resolved_at=now)

    def _publish_outbox_message(self, client, msg: OutboxMessage) -> bool:
        """Xuất bản OutboxMessage sang topic V1.4 (và topic tương thích cũ) kèm kiểm tra rc."""
        payload_str = json.dumps(msg.payload, ensure_ascii=False)
        info = client.publish(msg.topic, payload_str, qos=msg.qos)
        rc = getattr(info, 'rc', mqtt.MQTT_ERR_SUCCESS)
        if rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish trả về mã lỗi rc={rc}")

        legacy_topic = f"cabinet/{msg.command.device_id}/command"
        if msg.topic != legacy_topic:
            client.publish(legacy_topic, payload_str, qos=msg.qos)
        return True

    def sweep_pending_outbox(self, client):
        """
        Quét Outbox:
        1. Gửi các bản tin PENDING chưa xuất bản được lúc gọi API.
        2. Retry ở mốc 3s và 6s (tối đa 2 lần, giữ nguyên correlation_id) cho lệnh SENT chưa có ACK.
        """
        now = timezone.now()
        pending_messages = OutboxMessage.objects.filter(status='PENDING').select_related('command')[:10]
        for msg in pending_messages:
            try:
                self._publish_outbox_message(client, msg)
                msg.status = 'PUBLISHED'
                msg.published_at = now
                msg.save(update_fields=['status', 'published_at'])

                msg.command.status = 'SENT'
                msg.command.sent_at = now
                msg.command.save(update_fields=['status', 'sent_at'])
            except Exception as e:
                msg.retry_count += 1
                msg.last_error = str(e)
                msg.save(update_fields=['retry_count', 'last_error'])

        retry_messages = OutboxMessage.objects.filter(
            status='PUBLISHED',
            command__status='SENT',
            retry_count__lt=2,
        ).select_related('command')[:10]
        for msg in retry_messages:
            sent_at = msg.command.sent_at
            if not sent_at:
                continue
            elapsed = (now - sent_at).total_seconds()
            required_elapsed = 3 if msg.retry_count == 0 else 6
            if required_elapsed <= elapsed < 15:
                try:
                    self._publish_outbox_message(client, msg)
                    msg.retry_count += 1
                    msg.published_at = now
                    msg.save(update_fields=['retry_count', 'published_at'])
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

    def check_offline_devices(self):
        """Kiểm tra và kích hoạt cảnh báo DEVICE_OFFLINE nếu tủ không gửi viễn trắc quá ngưỡng cấu hình"""
        offline_threshold = timezone.now() - timedelta(seconds=DEVICE_OFFLINE_THRESHOLD_SECONDS)
        offline_devices = Device.objects.filter(status='ACTIVE', last_seen_at__lt=offline_threshold)
        for dev in offline_devices:
            active_offline = Alert.objects.filter(
                device=dev, alert_type='DEVICE_OFFLINE', status__in=['OPEN', 'ACKNOWLEDGED']
            ).first()
            if not active_offline:
                Alert.objects.create(
                    device=dev,
                    alert_type='DEVICE_OFFLINE',
                    severity='WARNING',
                    status='OPEN',
                    message=f"Tủ mạng {dev.id} đã mất kết nối viễn trắc quá {DEVICE_OFFLINE_THRESHOLD_SECONDS} giây."
                )

        # Tự động giải tỏa cảnh báo DEVICE_OFFLINE khi thiết bị gửi dữ liệu trở lại
        online_devices = Device.objects.filter(status='ACTIVE', last_seen_at__gte=offline_threshold)
        for dev in online_devices:
            Alert.objects.filter(
                device=dev, alert_type='DEVICE_OFFLINE', status__in=['OPEN', 'ACKNOWLEDGED']
            ).update(
                status='RESOLVED',
                resolved_at=timezone.now()
            )
