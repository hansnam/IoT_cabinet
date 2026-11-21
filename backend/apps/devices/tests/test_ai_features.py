"""
Bộ kiểm thử tự động toàn diện cho Hướng 1 (AI Anomaly Detection) & Hướng 4 (AI Incident Copilot).
Bao phủ toàn bộ kịch bản nghiệm thu tại mục 3.7-I và mục 6.3 của docs/PLAN_AI_ANOMALY_AND_COPILOT.md.
Chạy hoàn toàn offline 100%, không cần kết nối mạng hay GEMINI_API_KEY thật.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import joblib
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import AuthSession
from apps.accounts.policies.roles import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, assign_role
from apps.alerts.models import Alert
from apps.audit.models import AuditLog
from apps.commands.models import Command
from apps.devices.ai_copilot import build_device_context
from apps.devices.management.commands.run_mqtt_worker import Command as MqttWorkerCommand
from apps.devices.models import Device, DeviceAccess, DeviceConfig
from apps.devices.views import build_esp32_config_json_and_hash
from apps.telemetry.anomaly import (
    FEATURE_NAMES,
    SCHEMA_VERSION,
    WINDOW_SECONDS,
    effective_thresholds,
    evaluate_and_sync_device_anomaly_alerts,
    evaluate_device_anomaly,
    reset_model_cache,
)
from apps.telemetry.models import Telemetry

User = get_user_model()


class AIAnomalyAndCopilotTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_dir = tempfile.mkdtemp(prefix="iot_ai_test_ml_")
        cls._model_path = os.path.join(cls._temp_dir, "isolation_forest.joblib")
        call_command(
            "train_anomaly_model",
            samples=300,
            seed=42,
            output=cls._model_path,
        )

    @classmethod
    def tearDownClass(cls):
        reset_model_cache()
        shutil.rmtree(cls._temp_dir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        reset_model_cache()
        self.settings_override = override_settings(
            AI_ANOMALY_ENABLED=True,
            AI_ANOMALY_MODEL_PATH=self._model_path,
            AI_COPILOT_ENABLED=True,
            AI_COPILOT_PROVIDER="fallback",
            GEMINI_API_KEY="",
            AI_LLM_MODEL="gemini-4-hydrogen",
        )
        self.settings_override.enable()

        self.admin = User.objects.create_user(
            username="ai_admin",
            full_name="AI Admin",
            password="AdminPassword123!",
            status="ACTIVE",
        )
        assign_role(self.admin, ROLE_ADMIN)

        self.operator = User.objects.create_user(
            username="ai_operator",
            full_name="AI Operator",
            password="OperatorPassword123!",
            status="ACTIVE",
        )
        assign_role(self.operator, ROLE_OPERATOR)

        self.viewer = User.objects.create_user(
            username="ai_viewer",
            full_name="AI Viewer",
            password="ViewerPassword123!",
            status="ACTIVE",
        )
        assign_role(self.viewer, ROLE_VIEWER)

        self.now = timezone.now()
        self.cab1 = Device.objects.create(
            id="CAB-AI-01",
            name="Tủ AI 01",
            status="ACTIVE",
            ip_address="192.168.1.101",
            mac_address="AA:BB:CC:DD:EE:01",
            desired_config={"fan_on_c": 30.0, "fan_off_c": 28.0, "temp_alert_c": 35.0},
            applied_config={"fan_on_c": 30.0, "fan_off_c": 28.0, "temp_alert_c": 35.0},
            latest_temperature=29.0,
            latest_humidity=55.0,
            latest_fan_status="OFF",
            latest_door_status="CLOSED",
            latest_mode="AUTO",
            last_seen_at=self.now,
        )
        self.cab2 = Device.objects.create(
            id="CAB-AI-02",
            name="Tủ AI 02 (Ngoài Scope)",
            status="ACTIVE",
            applied_config={"fan_on_c": 30.0, "fan_off_c": 28.0, "temp_alert_c": 35.0},
            last_seen_at=self.now,
        )

        DeviceAccess.objects.create(user=self.operator, device=self.cab1, role="OPERATE")
        DeviceAccess.objects.create(user=self.viewer, device=self.cab1, role="VIEW")

    def tearDown(self):
        self.settings_override.disable()
        reset_model_cache()
        cache.clear()

    def _authenticated_client(self, user) -> APIClient:
        client = APIClient()
        client.force_login(user)
        session = client.session
        reg = AuthSession.objects.create(
            user=user,
            session_key=session.session_key,
            expires_at=timezone.now() + timedelta(minutes=15),
        )
        session["auth_session_id"] = str(reg.pk)
        session.save()
        return client

    def _seed_series(
        self,
        device: Device,
        *,
        count: int,
        interval_s: int = 5,
        end_at=None,
        temp_fn=None,
        hum_fn=None,
        fan_status: str = "OFF",
        mode: str = "AUTO",
        door_status: str = "CLOSED",
        sensor_ok: bool = True,
        prefix: str = "seed",
    ):
        end_ts = end_at or self.now
        start_ts = end_ts - timedelta(seconds=(count - 1) * interval_s)
        rows = []
        for i in range(count):
            ts = start_ts + timedelta(seconds=i * interval_s)
            elapsed_min = (i * interval_s) / 60.0
            t_val = temp_fn(i, elapsed_min) if temp_fn else 27.5
            h_val = hum_fn(i, elapsed_min) if hum_fn else 55.0
            rows.append(
                Telemetry(
                    device=device,
                    msg_id=f"{prefix}-{device.id}-{i}-{int(ts.timestamp())}",
                    seq=i + 1,
                    temperature=t_val,
                    humidity=h_val,
                    fan_status=fan_status,
                    door_status=door_status,
                    mode=mode,
                    uptime=1000 + i * interval_s,
                    measured_at=ts,
                    raw_data={"sensor_ok": sensor_ok, "secret_raw_token": "DO_NOT_LEAK_RAW_DATA"},
                )
            )
        Telemetry.objects.bulk_create(rows)
        device.last_seen_at = end_ts
        device.latest_temperature = rows[-1].temperature
        device.latest_humidity = rows[-1].humidity
        device.latest_fan_status = fan_status
        device.latest_door_status = door_status
        device.latest_mode = mode
        device.save()

    # =========================================================================
    # PHẦN 1: KIỂM THỬ HƯỚNG 1 (ANOMALY DETECTION & PREDICTIVE MAINTENANCE)
    # =========================================================================

    def test_insufficient_data_when_below_min_samples(self):
        """< MIN_SAMPLES mẫu hợp lệ -> trả về INSUFFICIENT_DATA (TOO_FEW_SAMPLES), không tạo Alert."""
        self._seed_series(self.cab1, count=10, interval_s=5, temp_fn=lambda i, m: 28.0 + 0.01 * i)
        res = evaluate_device_anomaly(self.cab1, as_of=self.now)
        self.assertEqual(res["status"], "INSUFFICIENT_DATA")
        self.assertEqual(res["insufficient_reason"], "TOO_FEW_SAMPLES")
        self.assertEqual(res["anomaly_type"], "NONE")

    def test_cooling_degradation_hysteresis_and_auto_resolve(self):
        """
        Quạt ON >= 5 phút trên ngưỡng fan_on_c nhưng nhiệt vẫn tăng >= 0.2°C/phút -> COOLING_DEGRADATION.
        Worker tạo Alert sau 2 cửa sổ lệch 30s và tự động RESOLVED khi nhiệt giảm ổn định trở lại.
        """
        # 120 mẫu (10 phút @ 5s): cả cửa sổ now và now-30s đều có quạt ON >= 5 phút, nhiệt tăng 0.35°C/phút
        self._seed_series(
            self.cab1,
            count=120,
            interval_s=5,
            fan_status="ON",
            temp_fn=lambda i, m: round(30.6 + 0.35 * m, 3),
            hum_fn=lambda i, m: round(54.0 + 0.05 * m, 2),
        )
        res = evaluate_and_sync_device_anomaly_alerts(self.cab1, as_of=self.now)
        self.assertEqual(res["status"], "WARNING")
        self.assertEqual(res["anomaly_type"], "COOLING_DEGRADATION")
        self.assertTrue(res["ml_model_loaded"])
        self.assertTrue(
            Alert.objects.filter(
                device=self.cab1,
                alert_type="COOLING_DEGRADATION",
                status="OPEN",
            ).exists()
        )

        # Khi hệ thống làm mát hoạt động tốt trở lại (nhiệt hạ xuống dưới fan_off_c = 28.0°C)
        Telemetry.objects.filter(device=self.cab1).delete()
        self._seed_series(
            self.cab1,
            count=120,
            interval_s=5,
            fan_status="OFF",
            temp_fn=lambda i, m: round(27.5 - 0.10 * m, 3),
            hum_fn=lambda i, m: round(52.0 + 0.02 * m, 2),
            prefix="resolved",
        )
        res_clean = evaluate_and_sync_device_anomaly_alerts(self.cab1, as_of=self.now)
        self.assertEqual(res_clean["status"], "NORMAL")
        self.assertFalse(
            Alert.objects.filter(
                device=self.cab1,
                alert_type="COOLING_DEGRADATION",
                status__in=["OPEN", "ACKNOWLEDGED"],
            ).exists()
        )

    def test_fan_recently_turned_on_under_5_minutes_does_not_false_alarm(self):
        """Quạt vừa bật < 5 phút (ví dụ 3 phút cuối) -> KHÔNG báo COOLING_DEGRADATION."""
        end_ts = self.now
        start_ts = end_ts - timedelta(seconds=99 * 5)
        rows = []
        for i in range(100):
            ts = start_ts + timedelta(seconds=i * 5)
            # 64 mẫu đầu quạt OFF, 36 mẫu cuối (3 phút) quạt mới ON
            is_on = i >= 64
            temp = 29.5 + 0.02 * i if not is_on else 30.7 + 0.03 * (i - 64)
            rows.append(
                Telemetry(
                    device=self.cab1,
                    msg_id=f"short-fan-{i}",
                    seq=i + 1,
                    temperature=round(temp, 2),
                    humidity=55.0 + 0.01 * i,
                    fan_status="ON" if is_on else "OFF",
                    door_status="CLOSED",
                    mode="AUTO",
                    measured_at=ts,
                    raw_data={"sensor_ok": True},
                )
            )
        Telemetry.objects.bulk_create(rows)
        res = evaluate_device_anomaly(self.cab1, as_of=self.now)
        self.assertEqual(res["status"], "NORMAL")
        self.assertLess(res["metrics"]["fan_on_duration_min"], 5.0)

    def test_sensor_anomaly_spike_and_flatline(self):
        """Nhảy gai >= 5°C trong <= 15s hoặc flatline >= FLATLINE_MIN_SAMPLES -> SENSOR_ANOMALY."""
        # 1. Kịch bản nhảy gai đột ngột +6.5°C ở mẫu cuối
        self._seed_series(
            self.cab1,
            count=40,
            interval_s=5,
            temp_fn=lambda i, m: 28.0 if i < 39 else 34.5,
            hum_fn=lambda i, m: 55.0 + 0.02 * i,
            prefix="spike",
        )
        res_spike = evaluate_device_anomaly(self.cab1, as_of=self.now)
        self.assertEqual(res_spike["status"], "WARNING")
        self.assertEqual(res_spike["anomaly_type"], "SENSOR_ANOMALY")
        self.assertGreaterEqual(res_spike["metrics"]["max_step_temp"], 6.0)

        # 2. Kịch bản cảm biến treo giá trị (temp_std == 0 và humid_std == 0 qua >= 80 mẫu)
        Telemetry.objects.filter(device=self.cab1).delete()
        self._seed_series(
            self.cab1,
            count=85,
            interval_s=5,
            temp_fn=lambda i, m: 28.4,
            hum_fn=lambda i, m: 56.2,
            prefix="flatline",
        )
        res_flat = evaluate_device_anomaly(self.cab1, as_of=self.now)
        self.assertEqual(res_flat["status"], "WARNING")
        self.assertEqual(res_flat["anomaly_type"], "SENSOR_ANOMALY")
        self.assertEqual(res_flat["metrics"]["temp_std"], 0.0)

    def test_missing_or_mismatched_model_file_falls_back_to_rules(self):
        """Không có file .joblib hoặc sai phiên bản -> ml_model_loaded=False và vẫn chạy bằng luật."""
        self._seed_series(
            self.cab1,
            count=110,
            interval_s=5,
            fan_status="ON",
            temp_fn=lambda i, m: round(30.8 + 0.35 * m, 3),
            hum_fn=lambda i, m: round(54.0 + 0.05 * m, 2),
        )
        with override_settings(AI_ANOMALY_MODEL_PATH=os.path.join(self._temp_dir, "nonexistent.joblib")):
            reset_model_cache()
            res_missing = evaluate_device_anomaly(self.cab1, as_of=self.now)
            self.assertFalse(res_missing["ml_model_loaded"])
            self.assertEqual(res_missing["ml_unavailable_reason"], "MODEL_FILE_MISSING")
            self.assertEqual(res_missing["status"], "WARNING")
            self.assertEqual(res_missing["anomaly_type"], "COOLING_DEGRADATION")

        bad_ver_path = os.path.join(self._temp_dir, "bad_version.joblib")
        joblib.dump(
            {
                "schema_version": SCHEMA_VERSION + 99,
                "feature_names": FEATURE_NAMES,
                "window_seconds": WINDOW_SECONDS,
                "pipeline": {},
            },
            bad_ver_path,
        )
        with override_settings(AI_ANOMALY_MODEL_PATH=bad_ver_path):
            reset_model_cache()
            res_ver = evaluate_device_anomaly(self.cab1, as_of=self.now)
            self.assertFalse(res_ver["ml_model_loaded"])
            self.assertEqual(res_ver["ml_unavailable_reason"], "VERSION_MISMATCH")

    def test_worker_process_telemetry_isolates_ai_exceptions(self):
        """Lỗi bên trong module AI không bao giờ làm rơi process_telemetry() của MQTT Worker."""
        worker = MqttWorkerCommand()
        payload = {
            "msg_id": "worker-ai-isolation-001",
            "seq": 10,
            "temperature_c": 29.4,
            "humidity_pct": 58.0,
            "fan_output": False,
            "mode": "AUTO",
            "sensor_ok": True,
        }
        with patch(
            "apps.telemetry.anomaly.evaluate_and_sync_device_anomaly_alerts",
            side_effect=RuntimeError("Simulated AI crash"),
        ):
            worker.process_telemetry(self.cab1.id, payload)

        self.assertTrue(
            Telemetry.objects.filter(device=self.cab1, msg_id="worker-ai-isolation-001").exists()
        )

    # =========================================================================
    # PHẦN 2: KIỂM THỬ MỤC 3.7 (11 BÀI TEST NGƯỠNG ĐỘNG & ĐIỀU KHIỂN THỦ CÔNG)
    # =========================================================================

    def test_37_1_threshold_invariance_across_fan_on_levels(self):
        """Test 3.7-1: Cùng kịch bản suy giảm làm mát với fan_on_c ∈ {26, 30, 36} đều cho kết luận như nhau."""
        for fan_on in (26.0, 30.0, 36.0):
            Telemetry.objects.filter(device=self.cab1).delete()
            self.cab1.applied_config = {
                "fan_on_c": fan_on,
                "fan_off_c": fan_on - 2.0,
                "temp_alert_c": fan_on + 6.0,
            }
            self.cab1.save()
            self._seed_series(
                self.cab1,
                count=110,
                interval_s=5,
                fan_status="ON",
                temp_fn=lambda i, m, base=fan_on: round(base + 0.7 + 0.32 * m, 3),
                hum_fn=lambda i, m: round(53.0 + 0.04 * m, 2),
                prefix=f"inv-{int(fan_on)}",
            )
            res = evaluate_device_anomaly(self.cab1, as_of=self.now)
            self.assertEqual(res["status"], "WARNING", f"Failed at fan_on_c={fan_on}")
            self.assertEqual(res["anomaly_type"], "COOLING_DEGRADATION")

    def test_37_2_and_3_config_change_settle_vs_temp_alert_only(self):
        """
        Test 3.7-2 & 3: Đổi fan_on_c giữa cửa sổ -> INSUFFICIENT_DATA (RECENT_CONFIG_CHANGE) trong 120s;
        Đổi riêng temp_alert_c -> không cắt đoạn, giữ nguyên kết luận.
        """
        self._seed_series(
            self.cab1,
            count=110,
            interval_s=5,
            fan_status="ON",
            temp_fn=lambda i, m: round(30.7 + 0.32 * m, 3),
            hum_fn=lambda i, m: round(54.0 + 0.04 * m, 2),
        )
        # Bản cấu hình gốc trước cửa sổ
        v1 = DeviceConfig.objects.create(
            device=self.cab1,
            version=1,
            config_data={"fan_on_c": 30.0, "fan_off_c": 28.0, "temp_alert_c": 35.0},
            applied=True,
        )
        DeviceConfig.objects.filter(pk=v1.pk).update(applied_at=self.now - timedelta(minutes=30))

        # (a) Đổi riêng temp_alert_c cách đây 30 giây -> KHÔNG cắt đoạn
        v2 = DeviceConfig.objects.create(
            device=self.cab1,
            version=2,
            config_data={"fan_on_c": 30.0, "fan_off_c": 28.0, "temp_alert_c": 38.0},
            applied=True,
        )
        DeviceConfig.objects.filter(pk=v2.pk).update(applied_at=self.now - timedelta(seconds=30))
        res_alert_only = evaluate_device_anomaly(self.cab1, as_of=self.now)
        self.assertEqual(res_alert_only["status"], "WARNING")
        self.assertEqual(res_alert_only["anomaly_type"], "COOLING_DEGRADATION")

        # (b) Đổi fan_on_c từ 30.0 lên 33.0 cách đây 45 giây (< SETTLE_SECONDS=120s) -> RECENT_CONFIG_CHANGE
        v3 = DeviceConfig.objects.create(
            device=self.cab1,
            version=3,
            config_data={"fan_on_c": 33.0, "fan_off_c": 31.0, "temp_alert_c": 38.0},
            applied=True,
        )
        DeviceConfig.objects.filter(pk=v3.pk).update(applied_at=self.now - timedelta(seconds=45))
        res_fan_change = evaluate_device_anomaly(self.cab1, as_of=self.now)
        self.assertEqual(res_fan_change["status"], "INSUFFICIENT_DATA")
        self.assertEqual(res_fan_change["insufficient_reason"], "RECENT_CONFIG_CHANGE")

    def test_37_4_desired_vs_applied_config_pending(self):
        """Test 3.7-4: desired_config != applied_config -> AI chỉ dùng applied_config và trả config_pending=True."""
        self.cab1.applied_config = {"fan_on_c": 30.0, "fan_off_c": 28.0, "temp_alert_c": 35.0}
        self.cab1.desired_config = {"fan_on_c": 34.0, "fan_off_c": 32.0, "temp_alert_c": 39.0}
        self.cab1.save()

        res = evaluate_device_anomaly(self.cab1, as_of=self.now)
        self.assertTrue(res["config_pending"])
        self.assertEqual(res["config_in_effect"]["fan_on_c"], 30.0)

    def test_37_5_manual_fan_off_and_post_ttl_settle(self):
        """
        Test 3.7-5: SET_FAN OFF thủ công -> NOT_APPLICABLE (MANUAL_ACTIVE);
        sau khi hết TTL về AUTO trong 120s -> INSUFFICIENT_DATA (RECENT_MANUAL_CONTROL), không báo lỗi quạt.
        """
        self._seed_series(
            self.cab1,
            count=40,
            interval_s=5,
            fan_status="OFF",
            mode="MANUAL",
            temp_fn=lambda i, m: round(31.0 + 0.4 * m, 2),
        )
        res_manual = evaluate_device_anomaly(self.cab1, as_of=self.now)
        self.assertEqual(res_manual["status"], "NOT_APPLICABLE")
        self.assertEqual(res_manual["not_applicable_reason"], "MANUAL_ACTIVE")

        # Thêm 10 mẫu AUTO (50 giây sau khi hết MANUAL < SETTLE_SECONDS 120s)
        after_ts = self.now + timedelta(seconds=50)
        self._seed_series(
            self.cab1,
            count=10,
            interval_s=5,
            end_at=after_ts,
            fan_status="ON",
            mode="AUTO",
            temp_fn=lambda i, m: round(32.5 + 0.3 * m, 2),
            prefix="post-manual",
        )
        res_settle = evaluate_device_anomaly(self.cab1, as_of=after_ts)
        self.assertEqual(res_settle["status"], "INSUFFICIENT_DATA")
        self.assertEqual(res_settle["insufficient_reason"], "RECENT_MANUAL_CONTROL")

    def test_37_6_stale_ai_alert_auto_resolves_after_30_minutes(self):
        """Test 3.7-6: Alert AI đang mở + thiết bị chuyển MAINTENANCE: < 30 phút giữ nguyên, > 30 phút tự RESOLVED."""
        alert = Alert.objects.create(
            device=self.cab1,
            alert_type="COOLING_DEGRADATION",
            severity="WARNING",
            status="OPEN",
            message="Cảnh báo suy giảm làm mát test.",
        )
        self.cab1.status = "MAINTENANCE"
        self.cab1.save(update_fields=["status", "updated_at"])

        # Sau 15 phút (< 30 phút) -> vẫn giữ OPEN
        evaluate_and_sync_device_anomaly_alerts(self.cab1, as_of=self.now + timedelta(minutes=15))
        alert.refresh_from_db()
        self.assertEqual(alert.status, "OPEN")

        # Sau 31 phút (> 30 phút) -> tự động RESOLVED
        evaluate_and_sync_device_anomaly_alerts(self.cab1, as_of=self.now + timedelta(minutes=31))
        alert.refresh_from_db()
        self.assertEqual(alert.status, "RESOLVED")
        self.assertIsNotNone(alert.resolved_at)

    def test_37_7_and_8_effective_thresholds_parity_and_empty_config(self):
        """Test 3.7-7 & 8: effective_thresholds khớp build_esp32_config_json_and_hash trên >= 6 bộ cấu hình."""
        samples = [
            {},
            {"max_temp": 34.0, "max_hum": 75.0},
            {"temp_alert_c": 37.0, "fan_on_c": 32.0, "fan_off_c": 30.0},
            {"fan_on_c": 29.0, "fan_off_c": 31.0},  # fan_off >= fan_on
            {"temp_alert_c": 28.0},  # max_temp - 2 < 30
            {"fan_on_c": 26.5, "fan_off_c": 24.0, "telemetry_interval_ms": 10000, "manual_timeout_s": 90},
        ]
        for cfg in samples:
            eff = effective_thresholds(cfg)
            cfg_json, _ = build_esp32_config_json_and_hash(cfg)
            esp_cfg = json.loads(cfg_json)
            self.assertEqual(eff["fan_on_c"], esp_cfg["fan_on_c"])
            self.assertEqual(eff["fan_off_c"], esp_cfg["fan_off_c"])
            self.assertEqual(eff["temp_alert_c"], esp_cfg["temp_alert_c"])
            self.assertEqual(eff["humidity_alert_pct"], esp_cfg["humidity_alert_pct"])

        # applied_config rỗng không gây lỗi
        self.cab1.applied_config = {}
        self.cab1.save()
        res = evaluate_device_anomaly(self.cab1, as_of=self.now)
        self.assertEqual(res["config_in_effect"]["fan_on_c"], 30.0)

    def test_37_9_mid_window_sensor_fault_cuts_segment(self):
        """Test 3.7-9: Mẫu có temperature=None (firmware FAULT) ở giữa cửa sổ -> cắt đoạn đúng (RECENT_FAULT)."""
        self._seed_series(
            self.cab1,
            count=80,
            interval_s=5,
            end_at=self.now - timedelta(seconds=60),
            fan_status="ON",
            temp_fn=lambda i, m: round(31.0 + 0.35 * m, 2),
            prefix="before-fault",
        )
        # Chèn 1 mẫu lỗi cảm biến (temperature=None, sensor_ok=False) cách đây 55 giây
        Telemetry.objects.create(
            device=self.cab1,
            msg_id="mid-fault-sample",
            seq=999,
            temperature=None,
            humidity=None,
            fan_status="ON",
            door_status="CLOSED",
            mode="AUTO",
            measured_at=self.now - timedelta(seconds=55),
            raw_data={"sensor_ok": False},
        )
        # 10 mẫu phục hồi sau lỗi (< SETTLE_SECONDS 120s)
        self._seed_series(
            self.cab1,
            count=10,
            interval_s=5,
            end_at=self.now,
            fan_status="ON",
            temp_fn=lambda i, m: round(32.5 + 0.3 * m, 2),
            prefix="after-fault",
        )
        res = evaluate_device_anomaly(self.cab1, as_of=self.now)
        self.assertEqual(res["status"], "INSUFFICIENT_DATA")
        self.assertEqual(res["insufficient_reason"], "RECENT_FAULT")

    def test_37_10_adaptive_telemetry_intervals(self):
        """Test 3.7-10: telemetry_interval_ms ∈ {1000, 5000, 15000} cho cùng kết luận; 30000 -> TELEMETRY_INTERVAL_TOO_LONG."""
        for interval_ms in (1000, 5000, 15000):
            Telemetry.objects.filter(device=self.cab1).delete()
            interval_s = interval_ms // 1000
            count = int(540 // interval_s)  # 9 phút quạt ON liên tục
            self.cab1.applied_config = {
                "fan_on_c": 30.0,
                "fan_off_c": 28.0,
                "temp_alert_c": 35.0,
                "telemetry_interval_ms": interval_ms,
            }
            self.cab1.save()
            self._seed_series(
                self.cab1,
                count=count,
                interval_s=interval_s,
                fan_status="ON",
                temp_fn=lambda i, m: round(30.8 + 0.34 * m, 3),
                hum_fn=lambda i, m: round(54.0 + 0.04 * m, 2),
                prefix=f"int-{interval_ms}",
            )
            res = evaluate_device_anomaly(self.cab1, as_of=self.now)
            self.assertEqual(res["status"], "WARNING", f"Mismatch at interval_ms={interval_ms}")
            self.assertEqual(res["anomaly_type"], "COOLING_DEGRADATION")

        # 30000 ms (> 20000 ms) -> NOT_APPLICABLE (TELEMETRY_INTERVAL_TOO_LONG)
        Telemetry.objects.filter(device=self.cab1).delete()
        self.cab1.applied_config = {"fan_on_c": 30.0, "telemetry_interval_ms": 30000}
        self.cab1.save()
        self._seed_series(
            self.cab1,
            count=20,
            interval_s=30,
            fan_status="ON",
            temp_fn=lambda i, m: round(31.0 + 0.3 * m, 2),
            prefix="int-30000",
        )
        res_long = evaluate_device_anomaly(self.cab1, as_of=self.now)
        self.assertEqual(res_long["status"], "NOT_APPLICABLE")
        self.assertEqual(res_long["not_applicable_reason"], "TELEMETRY_INTERVAL_TOO_LONG")

    def test_37_11_unsupported_profile_defensive_branch(self):
        """Test 3.7-11: Giả lập profile='SENSOR' -> bỏ qua COOLING_DEGRADATION (UNSUPPORTED_PROFILE), vẫn bắt SENSOR_ANOMALY."""
        self.cab1.applied_config = {"profile": "SENSOR", "fan_on_c": 30.0, "fan_off_c": 28.0}
        self.cab1.save()

        self._seed_series(
            self.cab1,
            count=100,
            interval_s=5,
            fan_status="ON",
            temp_fn=lambda i, m: round(31.0 + 0.35 * m, 2),
            hum_fn=lambda i, m: round(54.0 + 0.04 * m, 2),
            prefix="sensor-prof-cool",
        )
        res_prof = evaluate_device_anomaly(self.cab1, as_of=self.now)
        self.assertEqual(res_prof["status"], "NOT_APPLICABLE")
        self.assertEqual(res_prof["not_applicable_reason"], "UNSUPPORTED_PROFILE")

        # Nhưng nếu có nhảy gai cảm biến >= 5°C thì vẫn phát hiện SENSOR_ANOMALY
        Telemetry.objects.filter(device=self.cab1).delete()
        self._seed_series(
            self.cab1,
            count=40,
            interval_s=5,
            temp_fn=lambda i, m: 27.0 if i < 39 else 33.5,
            hum_fn=lambda i, m: 55.0 + 0.02 * i,
            prefix="sensor-prof-spike",
        )
        res_spike = evaluate_device_anomaly(self.cab1, as_of=self.now)
        self.assertEqual(res_spike["status"], "WARNING")
        self.assertEqual(res_spike["anomaly_type"], "SENSOR_ANOMALY")

    # =========================================================================
    # PHẦN 3: KIỂM THỬ HƯỚNG 4 (AI INCIDENT COPILOT, DATA EGRESS, RATE LIMIT, AUDIT)
    # =========================================================================

    def test_copilot_and_anomaly_rbac_scope_and_must_change_password(self):
        """
        VIEWER / OPERATOR có DeviceAccess truy vấn thành công;
        truy cập tủ ngoài scope (cab2) bị chặn 404 (chống IDOR);
        tài khoản must_change_password=True bị chặn 403.
        """
        viewer_client = self._authenticated_client(self.viewer)
        op_client = self._authenticated_client(self.operator)

        res_anom = viewer_client.get(f"/api/v1/devices/{self.cab1.id}/anomaly-status/")
        self.assertEqual(res_anom.status_code, status.HTTP_200_OK)

        res_cop = viewer_client.post(
            f"/api/v1/devices/{self.cab1.id}/ai-copilot/",
            {"question": "Tóm tắt tình trạng tủ 24h qua", "hours": 24},
            format="json",
        )
        self.assertEqual(res_cop.status_code, status.HTTP_200_OK)
        self.assertEqual(res_cop.data["device_id"], self.cab1.id)

        # Ngoài scope (cab2) -> 404
        res_idor_get = op_client.get(f"/api/v1/devices/{self.cab2.id}/anomaly-status/")
        self.assertEqual(res_idor_get.status_code, status.HTTP_404_NOT_FOUND)
        res_idor_post = op_client.post(
            f"/api/v1/devices/{self.cab2.id}/ai-copilot/",
            {"question": "Thử truy cập chéo tủ"},
            format="json",
        )
        self.assertEqual(res_idor_post.status_code, status.HTTP_404_NOT_FOUND)

        # User must_change_password=True -> bị chặn 403
        must_change_user = User.objects.create_user(
            username="must_change_ai",
            full_name="Must Change User",
            password="TempPassword123!",
            status="ACTIVE",
            must_change_password=True,
        )
        assign_role(must_change_user, ROLE_VIEWER)
        DeviceAccess.objects.create(user=must_change_user, device=self.cab1, role="VIEW")
        mc_client = self._authenticated_client(must_change_user)
        res_mc = mc_client.post(
            f"/api/v1/devices/{self.cab1.id}/ai-copilot/",
            {"question": "Tóm tắt tủ"},
            format="json",
        )
        self.assertEqual(res_mc.status_code, status.HTTP_403_FORBIDDEN)

    def test_copilot_input_validation_errors(self):
        """question rỗng / > 500 ký tự / hours ngoài 1..72 -> trả 400 VALIDATION_ERROR và không ghi AuditLog."""
        client = self._authenticated_client(self.operator)

        for bad_payload in (
            {"question": "   ", "hours": 24},
            {"question": "A" * 501, "hours": 24},
            {"question": "Câu hỏi hợp lệ", "hours": 0},
            {"question": "Câu hỏi hợp lệ", "hours": 73},
        ):
            res = client.post(
                f"/api/v1/devices/{self.cab1.id}/ai-copilot/",
                bad_payload,
                format="json",
            )
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        self.assertFalse(AuditLog.objects.filter(function_key="AI_COPILOT").exists())

    @patch("apps.devices.ai_copilot.genai.Client")
    def test_copilot_gemini_interactions_and_data_egress_policy(self, mock_client_cls):
        """
        Xác minh gọi Gemini Interactions API với model='gemini-4-hydrogen', store=False, response_format JSON schema,
        không truyền temperature/top_p/top_k; ngữ cảnh gửi đi không chứa Alert.message, raw_data, IP, MAC, PII,
        và Command.error_message chứa secret đã bị thay bằng [REDACTED].
        """
        self._seed_series(self.cab1, count=25, interval_s=5)
        Alert.objects.create(
            device=self.cab1,
            alert_type="HIGH_HUMIDITY",
            severity="WARNING",
            status="OPEN",
            message="SECRET_ALERT_MESSAGE_SHOULD_NOT_BE_SENT",
            threshold=80.0,
            current_value=85.0,
            acknowledged_by=self.admin,
        )
        Command.objects.create(
            device=self.cab1,
            command_type="SET_FAN",
            status="FAILED",
            payload={"_auth_session_id": "secret-session-uuid"},
            error_message="Broker rejected login password=SuperSecretMqttPass123! token=abc999",
            actor=self.operator,
        )

        # Kiểm tra trực tiếp build_device_context
        ctx = build_device_context(self.cab1, hours=24)
        ctx_str = json.dumps(ctx, ensure_ascii=False)
        self.assertNotIn("SECRET_ALERT_MESSAGE_SHOULD_NOT_BE_SENT", ctx_str)
        self.assertNotIn("DO_NOT_LEAK_RAW_DATA", ctx_str)
        self.assertNotIn("192.168.1.101", ctx_str)
        self.assertNotIn("AA:BB:CC:DD:EE:01", ctx_str)
        self.assertNotIn("secret-session-uuid", ctx_str)
        self.assertNotIn("SuperSecretMqttPass123!", ctx_str)
        self.assertNotIn("abc999", ctx_str)
        self.assertIn("[REDACTED]", ctx_str)

        mock_instance = MagicMock()
        mock_client_cls.return_value = mock_instance
        mock_instance.interactions.create.return_value = SimpleNamespace(
            output_text=json.dumps(
                {
                    "summary": "Tủ hoạt động ổn định, có 1 cảnh báo độ ẩm cao.",
                    "possible_causes": ["Độ ẩm môi trường tăng cao."],
                    "recommended_actions": ["Kiểm tra gioăng cửa tủ mạng."],
                    "severity_assessment": "ATTENTION",
                },
                ensure_ascii=False,
            )
        )

        client = self._authenticated_client(self.operator)
        with override_settings(AI_COPILOT_PROVIDER="gemini", GEMINI_API_KEY="test-fake-gemini-key"):
            res = client.post(
                f"/api/v1/devices/{self.cab1.id}/ai-copilot/",
                {"question": "Phân tích sự cố tủ này?", "hours": 24},
                format="json",
            )

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["provider_used"], "gemini")
        self.assertEqual(res.data["model"], "gemini-4-hydrogen")
        self.assertIsNone(res.data["fallback_reason"])
        self.assertEqual(res.data["answer"]["severity_assessment"], "ATTENTION")

        mock_instance.interactions.create.assert_called_once()
        call_kwargs = mock_instance.interactions.create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gemini-4-hydrogen")
        self.assertIs(call_kwargs["store"], False)
        self.assertIn("properties", call_kwargs["response_format"])
        self.assertNotIn("temperature", call_kwargs.get("generation_config", {}))
        self.assertNotIn("top_p", call_kwargs.get("generation_config", {}))
        self.assertNotIn("top_k", call_kwargs.get("generation_config", {}))
        self.assertIn("<DU_LIEU_THIET_BI>", call_kwargs["input"])
        self.assertIn("<CAU_HOI_NGUOI_DUNG>", call_kwargs["input"])

        # Kiểm tra AuditLog hợp đồng chuẩn
        audit = AuditLog.objects.filter(function_key="AI_COPILOT", action="COPILOT_QUERY").first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.outcome, "SUCCESS")
        self.assertIsNone(audit.error_code)
        self.assertEqual(audit.metadata, {"device_id": self.cab1.id})
        self.assertNotIn("Phân tích sự cố tủ này?", audit.summary)

    @patch("apps.devices.ai_copilot.genai.Client")
    def test_copilot_fallback_on_timeout_invalid_output_and_audit_resilience(self, mock_client_cls):
        """
        Khi Gemini timeout hoặc trả output vượt giới hạn độ dài -> fallback về _build_rule_based_summary (HTTP 200),
        ghi đúng error_code vào AuditLog; và nếu AuditService lỗi cũng không làm hỏng response.
        """
        mock_instance = MagicMock()
        mock_client_cls.return_value = mock_instance
        mock_instance.interactions.create.side_effect = TimeoutError("Gemini request timed out")

        client = self._authenticated_client(self.operator)
        with override_settings(AI_COPILOT_PROVIDER="gemini", GEMINI_API_KEY="test-fake-gemini-key"):
            res_timeout = client.post(
                f"/api/v1/devices/{self.cab1.id}/ai-copilot/",
                {"question": "Tóm tắt sự cố khi mạng chậm"},
                format="json",
            )

        self.assertEqual(res_timeout.status_code, status.HTTP_200_OK)
        self.assertEqual(res_timeout.data["provider_used"], "local_fallback")
        self.assertEqual(res_timeout.data["fallback_reason"], "TIMEOUT")
        audit_to = AuditLog.objects.filter(function_key="AI_COPILOT").order_by("-occurred_at").first()
        self.assertEqual(audit_to.error_code, "AI_FALLBACK_TIMEOUT")

        # Output vượt giới hạn summary > 600 ký tự -> INVALID_RESPONSE fallback
        mock_instance.interactions.create.side_effect = None
        mock_instance.interactions.create.return_value = SimpleNamespace(
            output_text=json.dumps(
                {
                    "summary": "X" * 650,
                    "possible_causes": ["Nguyên nhân 1"],
                    "recommended_actions": ["Hành động 1"],
                    "severity_assessment": "NORMAL",
                }
            )
        )
        with override_settings(AI_COPILOT_PROVIDER="gemini", GEMINI_API_KEY="test-fake-gemini-key"):
            res_invalid = client.post(
                f"/api/v1/devices/{self.cab1.id}/ai-copilot/",
                {"question": "Kiểm tra giới hạn đầu ra"},
                format="json",
            )
        self.assertEqual(res_invalid.status_code, status.HTTP_200_OK)
        self.assertEqual(res_invalid.data["fallback_reason"], "INVALID_RESPONSE")

        # Lỗi ghi AuditLog không làm hỏng response 200 OK
        with patch("apps.devices.views.AuditService.record_event", side_effect=RuntimeError("DB Audit error")):
            res_no_crash = client.post(
                f"/api/v1/devices/{self.cab1.id}/ai-copilot/",
                {"question": "Kiểm tra cô lập lỗi audit"},
                format="json",
            )
            self.assertEqual(res_no_crash.status_code, status.HTTP_200_OK)

    def test_copilot_rate_limiting_10_per_minute(self):
        """Vượt quá 10 requests/phút trên /ai-copilot/ -> trả HTTP 429 RATE_LIMITED."""
        cache.clear()
        client = self._authenticated_client(self.operator)
        url = f"/api/v1/devices/{self.cab1.id}/ai-copilot/"

        for i in range(10):
            res = client.post(url, {"question": f"Câu hỏi thứ {i + 1}"}, format="json")
            self.assertEqual(res.status_code, status.HTTP_200_OK)

        res_11 = client.post(url, {"question": "Câu hỏi thứ 11 vượt ngưỡng"}, format="json")
        self.assertEqual(res_11.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(res_11.json()["error"]["code"], "RATE_LIMITED")
