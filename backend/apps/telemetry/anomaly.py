"""
Module Phát hiện bất thường & Bảo trì dự đoán (Hướng 1 - AI Anomaly Detection).
Thiết kế tuân thủ chặt chẽ mục 3.1 - 3.7 của docs/PLAN_AI_ANOMALY_AND_COPILOT.md:
- Cửa sổ thời gian chuẩn WINDOW_SECONDS = 600s (10 phút), thích ứng theo telemetry_interval_ms.
- Vector 7 đặc trưng tương đối theo ngưỡng hiệu lực tại từng mẫu (không phụ thuộc ngưỡng cố định).
- Guard Conditions & Settle Rule khi đổi ngưỡng fan_on_c/fan_off_c, điều khiển MANUAL, lỗi DHT22 (FAULT), mở cửa.
- Kết hợp Explainable Rule-based + IsolationForest (lazy-singleton, thread-safe, kiểm tra phiên bản artifact).
"""
from __future__ import annotations

import logging
import math
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import joblib
import numpy as np
import sklearn
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.alerts.models import Alert
from apps.devices.models import DEVICE_OFFLINE_THRESHOLD_SECONDS, Device, DeviceConfig
from apps.telemetry.models import Telemetry

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
WINDOW_SECONDS = 600
FAN_ON_MIN_MINUTES = 5.0
CONFIG_APPLY_MARGIN_S = 10
SETTLE_SECONDS = 120
DOOR_SETTLE_SECONDS = 60
AI_ALERT_STALE_MINUTES = 30
HYSTERESIS_OFFSET_SECONDS = 30

FEATURE_NAMES: list[str] = [
    "temp_slope_per_min",
    "humid_slope_per_min",
    "temp_std",
    "max_step_temp",
    "fan_on_ratio",
    "temp_above_fan_on",
    "fan_on_duration_min",
]


@dataclass
class _Sample:
    measured_at: datetime
    received_at: datetime
    temperature: float | None
    humidity: float | None
    fan_status: str
    door_status: str
    mode: str
    sensor_ok: bool


_MODEL_LOCK = threading.Lock()
_CACHED_MODEL_PATH: str | None = None
_CACHED_ARTIFACT: dict[str, Any] | None = None
_CACHED_UNAVAILABLE_REASON: str | None = None


def effective_thresholds(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """
    Chuẩn hóa cấu hình hiệu lực khớp 100% với build_esp32_config_json_and_hash
    trong backend/apps/devices/views.py.
    """
    raw = cfg if isinstance(cfg, dict) else {}
    try:
        max_temp = float(raw.get("temp_alert_c", raw.get("max_temp", 35.0)))
    except (TypeError, ValueError):
        max_temp = 35.0
    try:
        max_hum = float(raw.get("humidity_alert_pct", raw.get("max_hum", 80.0)))
    except (TypeError, ValueError):
        max_hum = 80.0
    try:
        fan_on = float(raw.get("fan_on_c", min(max_temp - 2.0, 30.0)))
    except (TypeError, ValueError):
        fan_on = min(max_temp - 2.0, 30.0)
    try:
        fan_off = float(raw.get("fan_off_c", fan_on - 2.0))
    except (TypeError, ValueError):
        fan_off = fan_on - 2.0
    if fan_off >= fan_on:
        fan_off = fan_on - 2.0

    try:
        temp_clear_margin_c = float(raw.get("temp_clear_margin_c", 1.0))
    except (TypeError, ValueError):
        temp_clear_margin_c = 1.0
    try:
        humidity_clear_margin_pct = float(raw.get("humidity_clear_margin_pct", 3.0))
    except (TypeError, ValueError):
        humidity_clear_margin_pct = 3.0
    try:
        telemetry_interval_ms = int(raw.get("telemetry_interval_ms", 5000))
    except (TypeError, ValueError):
        telemetry_interval_ms = 5000
    if telemetry_interval_ms <= 0:
        telemetry_interval_ms = 5000
    try:
        manual_timeout_s = int(raw.get("manual_timeout_s", 60))
    except (TypeError, ValueError):
        manual_timeout_s = 60

    profile_raw = raw.get("profile", "CONTROLLER")
    profile = str(profile_raw if profile_raw is not None else "CONTROLLER").strip() or "CONTROLLER"

    return {
        "profile": profile,
        "fan_binding_id": raw.get("fan_binding_id", None),
        "fan_on_c": fan_on,
        "fan_off_c": fan_off,
        "temp_alert_c": max_temp,
        "humidity_alert_pct": max_hum,
        "temp_clear_margin_c": temp_clear_margin_c,
        "humidity_clear_margin_pct": humidity_clear_margin_pct,
        "telemetry_interval_ms": telemetry_interval_ms,
        "manual_timeout_s": manual_timeout_s,
    }


def adaptive_window_params(telemetry_interval_ms: int = 5000) -> dict[str, float | int]:
    """Tính các ngưỡng cửa sổ thích ứng theo telemetry_interval_ms (Mục 3.7-K)."""
    interval_s = max(0.5, float(telemetry_interval_ms) / 1000.0)
    max_gap_seconds = max(60.0, 4.0 * interval_s)
    step_gap_seconds = max(15.0, 2.0 * interval_s)
    min_samples = max(12, int(math.floor((180.0 / interval_s) * 0.6)))
    flatline_min_samples = max(16, int(math.floor((480.0 / interval_s) * 0.8)))
    return {
        "interval_s": interval_s,
        "max_gap_seconds": max_gap_seconds,
        "step_gap_seconds": step_gap_seconds,
        "min_samples": min_samples,
        "flatline_min_samples": flatline_min_samples,
    }


def _major_minor(version_str: str) -> str:
    parts = str(version_str).split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else str(version_str)


def reset_model_cache() -> None:
    """Xóa cache model trong RAM (phục vụ kiểm thử hoặc nạp lại model mới)."""
    global _CACHED_MODEL_PATH, _CACHED_ARTIFACT, _CACHED_UNAVAILABLE_REASON
    with _MODEL_LOCK:
        _CACHED_MODEL_PATH = None
        _CACHED_ARTIFACT = None
        _CACHED_UNAVAILABLE_REASON = None


def load_model() -> tuple[dict[str, Any] | None, str | None]:
    """
    Nạp artifact IsolationForest theo cơ chế lazy-singleton có khóa thread-safe.
    Chỉ đọc từ đường dẫn settings.AI_ANOMALY_MODEL_PATH.
    Trả về (artifact_dict | None, unavailable_reason | None).
    """
    global _CACHED_MODEL_PATH, _CACHED_ARTIFACT, _CACHED_UNAVAILABLE_REASON
    model_path = str(
        getattr(
            settings,
            "AI_ANOMALY_MODEL_PATH",
            os.path.join(settings.BASE_DIR, "apps", "telemetry", "ml", "isolation_forest.joblib"),
        )
    )

    with _MODEL_LOCK:
        if _CACHED_MODEL_PATH == model_path and (_CACHED_ARTIFACT is not None or _CACHED_UNAVAILABLE_REASON is not None):
            return _CACHED_ARTIFACT, _CACHED_UNAVAILABLE_REASON

        _CACHED_MODEL_PATH = model_path
        _CACHED_ARTIFACT = None
        _CACHED_UNAVAILABLE_REASON = None

        if not model_path or not os.path.isfile(model_path):
            _CACHED_UNAVAILABLE_REASON = "MODEL_FILE_MISSING"
            return None, _CACHED_UNAVAILABLE_REASON

        try:
            artifact = joblib.load(model_path)
        except Exception as exc:
            logger.warning("[AI Anomaly] Không thể đọc file model %s: %s", model_path, exc)
            _CACHED_UNAVAILABLE_REASON = "LOAD_ERROR"
            return None, _CACHED_UNAVAILABLE_REASON

        if not isinstance(artifact, dict) or "pipeline" not in artifact:
            _CACHED_UNAVAILABLE_REASON = "LOAD_ERROR"
            return None, _CACHED_UNAVAILABLE_REASON

        if artifact.get("schema_version") != SCHEMA_VERSION:
            _CACHED_UNAVAILABLE_REASON = "VERSION_MISMATCH"
            return None, _CACHED_UNAVAILABLE_REASON

        if artifact.get("feature_names") != FEATURE_NAMES or artifact.get("window_seconds") != WINDOW_SECONDS:
            _CACHED_UNAVAILABLE_REASON = "FEATURE_MISMATCH"
            return None, _CACHED_UNAVAILABLE_REASON

        saved_sk = artifact.get("sklearn_version")
        saved_np = artifact.get("numpy_version")
        if saved_sk and _major_minor(saved_sk) != _major_minor(sklearn.__version__):
            _CACHED_UNAVAILABLE_REASON = "VERSION_MISMATCH"
            return None, _CACHED_UNAVAILABLE_REASON
        if saved_np and _major_minor(saved_np) != _major_minor(np.__version__):
            _CACHED_UNAVAILABLE_REASON = "VERSION_MISMATCH"
            return None, _CACHED_UNAVAILABLE_REASON

        _CACHED_ARTIFACT = artifact
        return _CACHED_ARTIFACT, None


def _is_finite_number(val: Any) -> bool:
    if val is None or isinstance(val, bool):
        return False
    try:
        return math.isfinite(float(val))
    except (TypeError, ValueError):
        return False


def _extract_sample(row: Telemetry) -> _Sample:
    raw = row.raw_data if isinstance(row.raw_data, dict) else {}
    raw_sensor_ok = raw.get("sensor_ok", True)
    sensor_ok = bool(raw_sensor_ok) is not False

    temp = float(row.temperature) if _is_finite_number(row.temperature) else None
    hum = float(row.humidity) if _is_finite_number(row.humidity) else None
    if temp is None or hum is None or str(row.fan_status).upper() == "FAULT":
        sensor_ok = False

    return _Sample(
        measured_at=row.measured_at,
        received_at=row.received_at or row.measured_at,
        temperature=temp if sensor_ok else None,
        humidity=hum if sensor_ok else None,
        fan_status=str(row.fan_status or "OFF").upper(),
        door_status=str(row.door_status or "CLOSED").upper(),
        mode=str(row.mode or "AUTO").upper(),
        sensor_ok=sensor_ok,
    )


def build_config_timeline(
    device: Device,
    window_start: datetime,
    as_of: datetime,
) -> tuple[dict[str, Any], list[tuple[datetime, dict[str, Any], int]], datetime | None]:
    """
    Dựng đường thời gian cấu hình hiệu lực trong cửa sổ (Mục 3.7-A, B, C):
    - Chỉ sử dụng applied_config và các bản ghi DeviceConfig(applied=True).
    - Trả về:
      1. current_config_info: dict thông tin config_in_effect tại as_of
      2. timeline: danh sách (effective_from_dt, effective_thresholds_dict, version)
      3. last_fan_config_change_at: thời điểm đổi fan_on_c hoặc fan_off_c gần nhất trong cửa sổ (nếu có)
    """
    applied_versions = list(
        DeviceConfig.objects.filter(
            device=device,
            applied=True,
            applied_at__isnull=False,
            applied_at__lte=as_of,
        ).order_by("applied_at", "version")
    )

    device_applied_raw = device.applied_config if isinstance(device.applied_config, dict) and device.applied_config else {}
    default_eff = effective_thresholds({})
    device_eff = effective_thresholds(device_applied_raw) if device_applied_raw else default_eff

    timeline: list[tuple[datetime, dict[str, Any], int]] = []
    last_fan_config_change_at: datetime | None = None

    if not applied_versions:
        current_info = {
            "fan_on_c": device_eff["fan_on_c"],
            "fan_off_c": device_eff["fan_off_c"],
            "temp_alert_c": device_eff["temp_alert_c"],
            "profile": device_eff["profile"],
            "telemetry_interval_ms": device_eff["telemetry_interval_ms"],
            "applied_version": None,
            "applied_at": None,
        }
        return current_info, [(window_start - timedelta(days=3650), device_eff, 0)], None

    # Xác định cấu hình nền trước bản ghi đầu tiên nằm trong cửa sổ
    before_window = [v for v in applied_versions if v.applied_at <= window_start]
    in_window = [v for v in applied_versions if v.applied_at > window_start]

    if before_window:
        prev_v = before_window[-1]
        prev_eff = effective_thresholds(prev_v.config_data)
        timeline.append((prev_v.applied_at - timedelta(seconds=CONFIG_APPLY_MARGIN_S), prev_eff, prev_v.version))
    else:
        # Nếu trước window_start chưa có bản ghi DeviceConfig nào thì cấu hình nền là mặc định
        prev_eff = default_eff
        timeline.append((window_start - timedelta(days=3650), prev_eff, 0))

    for ver in in_window:
        new_eff = effective_thresholds(ver.config_data)
        eff_from = ver.applied_at - timedelta(seconds=CONFIG_APPLY_MARGIN_S)
        timeline.append((eff_from, new_eff, ver.version))
        if (
            abs(new_eff["fan_on_c"] - prev_eff["fan_on_c"]) > 1e-6
            or abs(new_eff["fan_off_c"] - prev_eff["fan_off_c"]) > 1e-6
        ):
            last_fan_config_change_at = ver.applied_at
        prev_eff = new_eff

    latest_ver = applied_versions[-1]
    latest_eff = effective_thresholds(latest_ver.config_data)
    current_info = {
        "fan_on_c": latest_eff["fan_on_c"],
        "fan_off_c": latest_eff["fan_off_c"],
        "temp_alert_c": latest_eff["temp_alert_c"],
        "profile": latest_eff["profile"],
        "telemetry_interval_ms": latest_eff["telemetry_interval_ms"],
        "applied_version": latest_ver.version,
        "applied_at": latest_ver.applied_at.isoformat() if latest_ver.applied_at else None,
    }
    return current_info, timeline, last_fan_config_change_at


def _config_at(
    ts: datetime,
    timeline: list[tuple[datetime, dict[str, Any], int]],
    fallback_eff: dict[str, Any],
) -> dict[str, Any]:
    chosen = fallback_eff
    for eff_from, eff_cfg, _ in timeline:
        if eff_from <= ts:
            chosen = eff_cfg
        else:
            break
    return chosen


def is_config_pending(device: Device) -> bool:
    """Kiểm tra desired_config có đang chờ thiết bị ACK khác với applied_config hay không."""
    desired = device.desired_config if isinstance(device.desired_config, dict) else {}
    if not desired:
        return False
    applied = device.applied_config if isinstance(device.applied_config, dict) else {}
    if not applied:
        return True
    return effective_thresholds(desired) != effective_thresholds(applied)


def extract_features_from_samples(
    samples: list[_Sample],
    timeline: list[tuple[datetime, dict[str, Any], int]],
    fallback_eff: dict[str, Any],
    interval_s: float,
    step_gap_seconds: float,
) -> dict[str, float] | None:
    """
    Trích xuất 7 đặc trưng chuẩn hóa từ chuỗi mẫu ổn định đã sắp tăng dần thời gian.
    Trả về None nếu xảy ra lỗi số học hoặc giá trị không hữu hạn.
    """
    if len(samples) < 2:
        return None

    # Dự phòng nếu hàng loạt mẫu có cùng measured_at (Mục 3.7-J.6)
    first_ts = samples[0].measured_at
    last_ts = samples[-1].measured_at
    use_received = (last_ts - first_ts).total_seconds() < 1e-3

    def _ts(s: _Sample) -> datetime:
        return s.received_at if use_received else s.measured_at

    t0 = _ts(samples[0])
    t_mins = np.array([(_ts(s) - t0).total_seconds() / 60.0 for s in samples], dtype=float)
    temps = np.array([float(s.temperature) for s in samples], dtype=float)
    hums = np.array([float(s.humidity) for s in samples], dtype=float)

    if not (np.all(np.isfinite(t_mins)) and np.all(np.isfinite(temps)) and np.all(np.isfinite(hums))):
        return None

    # Nếu tất cả timestamp trùng nhau, dàn đều theo interval_s dự phòng
    if float(t_mins[-1] - t_mins[0]) < 1e-6:
        t_mins = np.arange(len(samples), dtype=float) * (interval_s / 60.0)

    # Tìm chuỗi quạt ON liên tục tính đến mẫu cuối cùng
    trailing_on_count = 0
    for s in reversed(samples):
        if s.fan_status == "ON":
            trailing_on_count += 1
        else:
            break

    try:
        # 1. temp_slope_per_min: hồi quy tuyến tính trên đoạn quạt ON cuối cùng (nếu >= 2 mẫu), ngược lại trên toàn cửa sổ
        if trailing_on_count >= 2:
            on_t = t_mins[-trailing_on_count:]
            on_temps = temps[-trailing_on_count:]
            if float(on_t[-1] - on_t[0]) >= (interval_s / 60.0) * 0.5:
                temp_slope = float(np.polyfit(on_t, on_temps, 1)[0])
            else:
                temp_slope = 0.0
        else:
            temp_slope = float(np.polyfit(t_mins, temps, 1)[0])

        # 2. humid_slope_per_min trên toàn cửa sổ
        humid_slope = float(np.polyfit(t_mins, hums, 1)[0])

        # 3. temp_std (khử nhiễu dấu phẩy động IEEE-754 khi chuỗi hằng số)
        temp_std = float(np.std(temps))
        if temp_std < 1e-7:
            temp_std = 0.0
        humid_std = float(np.std(hums))
        if humid_std < 1e-7:
            humid_std = 0.0

        # 4. max_step_temp chỉ tính cho các cặp cách nhau <= step_gap_seconds
        max_step = 0.0
        for idx in range(1, len(samples)):
            dt_s = (_ts(samples[idx]) - _ts(samples[idx - 1])).total_seconds()
            if 0.0 <= dt_s <= step_gap_seconds + 1e-6:
                step = abs(float(temps[idx] - temps[idx - 1]))
                if step > max_step:
                    max_step = step

        # 5. fan_on_ratio
        fan_on_total = sum(1 for s in samples if s.fan_status == "ON")
        fan_on_ratio = float(fan_on_total) / float(len(samples))

        # 6. temp_above_fan_on: trung bình (T_i - fan_on_c(t_i)) trên 6 mẫu cuối
        tail_samples = samples[-min(6, len(samples)):]
        diffs = [
            float(s.temperature) - float(_config_at(_ts(s), timeline, fallback_eff)["fan_on_c"])
            for s in tail_samples
        ]
        temp_above_fan_on = float(np.mean(diffs))

        # 7. fan_on_duration_min: độ dài (phút) chuỗi quạt ON liên tục đến mẫu cuối
        if trailing_on_count == 0:
            fan_on_duration_min = 0.0
        else:
            first_on_ts = _ts(samples[-trailing_on_count])
            last_on_ts = _ts(samples[-1])
            span_s = max(0.0, (last_on_ts - first_on_ts).total_seconds()) + interval_s
            fan_on_duration_min = float(span_s / 60.0)
    except (ValueError, FloatingPointError, np.linalg.LinAlgError):
        return None

    metrics = {
        "temp_slope_per_min": round(temp_slope, 4),
        "humid_slope_per_min": round(humid_slope, 4),
        "temp_std": round(temp_std, 4),
        "max_step_temp": round(max_step, 4),
        "fan_on_ratio": round(fan_on_ratio, 4),
        "temp_above_fan_on": round(temp_above_fan_on, 4),
        "fan_on_duration_min": round(fan_on_duration_min, 4),
        "_humid_std": round(humid_std, 4),
    }
    if not all(_is_finite_number(v) for v in metrics.values()):
        return None
    return metrics


def score_feature_vector(metrics: dict[str, float]) -> tuple[float | None, int | None, bool, str | None]:
    """
    Chấm điểm vector 7 đặc trưng bằng IsolationForest nếu model khả dụng.
    Trả về (ml_score, prediction (-1 hoặc 1), ml_model_loaded, ml_unavailable_reason).
    """
    artifact, err_reason = load_model()
    if artifact is None:
        return None, None, False, err_reason

    try:
        vec = np.array([[float(metrics[name]) for name in FEATURE_NAMES]], dtype=float)
        if not np.all(np.isfinite(vec)):
            return None, None, False, "LOAD_ERROR"
        pipeline = artifact["pipeline"]
        pred = int(pipeline.predict(vec)[0])
        raw_score = float(pipeline.decision_function(vec)[0])
        if not math.isfinite(raw_score):
            return None, None, False, "LOAD_ERROR"
        return round(raw_score, 4), pred, True, None
    except Exception as exc:
        logger.warning("[AI Anomaly] Lỗi khi chạy IsolationForest: %s", exc)
        return None, None, False, "LOAD_ERROR"


def evaluate_device_anomaly(device: Device, as_of: datetime | None = None) -> dict[str, Any]:
    """
    Hàm đánh giá bất thường chính cho một tủ mạng tại thời điểm as_of (mặc định: now).
    Hoàn toàn chỉ đọc (stateless, idempotent), không tự tạo hoặc đóng Alert.
    """
    now = as_of or timezone.now()
    window_start = now - timedelta(seconds=WINDOW_SECONDS)

    config_in_effect, timeline, last_fan_cfg_change_at = build_config_timeline(device, window_start, now)
    fallback_eff = effective_thresholds(device.applied_config if isinstance(device.applied_config, dict) else {})
    params = adaptive_window_params(int(config_in_effect.get("telemetry_interval_ms", 5000)))
    interval_s = float(params["interval_s"])
    max_gap_s = float(params["max_gap_seconds"])
    step_gap_s = float(params["step_gap_seconds"])
    min_samples = int(params["min_samples"])
    flatline_min_samples = int(params["flatline_min_samples"])

    artifact, ml_unavail_reason = load_model()
    ml_loaded = artifact is not None
    cfg_pending = is_config_pending(device)

    def _base_response(
        status_code: str,
        summary_msg: str,
        *,
        anomaly_type: str = "NONE",
        not_applicable_reason: str | None = None,
        insufficient_reason: str | None = None,
        sample_count: int = 0,
        ml_score: float | None = None,
        metrics: dict[str, float] | None = None,
        eff_win_start: datetime = window_start,
        auto_resolve_cooling: bool = False,
        auto_resolve_sensor: bool = False,
    ) -> dict[str, Any]:
        clean_metrics = (
            {k: metrics[k] for k in FEATURE_NAMES if k in metrics}
            if metrics
            else {k: 0.0 for k in FEATURE_NAMES}
        )
        return {
            "device_id": device.id,
            "status": status_code,
            "anomaly_type": anomaly_type,
            "not_applicable_reason": not_applicable_reason,
            "insufficient_reason": insufficient_reason,
            "ml_score": ml_score,
            "ml_model_loaded": ml_loaded,
            "ml_unavailable_reason": ml_unavail_reason,
            "sample_count": sample_count,
            "window_seconds": WINDOW_SECONDS,
            "evaluated_at": now.isoformat(),
            "effective_window_start": eff_win_start.isoformat(),
            "config_in_effect": {
                "fan_on_c": config_in_effect["fan_on_c"],
                "fan_off_c": config_in_effect["fan_off_c"],
                "temp_alert_c": config_in_effect["temp_alert_c"],
                "applied_version": config_in_effect["applied_version"],
                "applied_at": config_in_effect["applied_at"],
            },
            "config_pending": cfg_pending,
            "summary": summary_msg,
            "metrics": clean_metrics,
            "_auto_resolve_cooling": auto_resolve_cooling,
            "_auto_resolve_sensor": auto_resolve_sensor,
        }

    # 1. Kiểm tra trạng thái vòng đời thiết bị
    if device.status != "ACTIVE":
        return _base_response(
            "NOT_APPLICABLE",
            f"Tủ mạng đang ở trạng thái '{device.status}', tạm ngưng đánh giá bất thường AI.",
            not_applicable_reason="DEVICE_NOT_ACTIVE",
        )

    # 2. Kiểm tra thiết bị offline (nếu last_seen_at đã có và vượt ngưỡng offline)
    if device.last_seen_at is not None and (now - device.last_seen_at).total_seconds() > DEVICE_OFFLINE_THRESHOLD_SECONDS:
        return _base_response(
            "NOT_APPLICABLE",
            "Tủ mạng đang mất kết nối (OFFLINE), không áp dụng đánh giá bất thường.",
            not_applicable_reason="DEVICE_OFFLINE",
        )

    # 3. Kiểm tra cảnh báo cứng đang hoạt động
    active_hard_alerts = set(
        Alert.objects.filter(
            device=device,
            alert_type__in=["SENSOR_FAULT", "HIGH_TEMP", "DEVICE_OFFLINE"],
            status__in=["OPEN", "ACKNOWLEDGED"],
        ).values_list("alert_type", flat=True)
    )
    if "SENSOR_FAULT" in active_hard_alerts:
        return _base_response(
            "NOT_APPLICABLE",
            "Cảm biến DHT22 đang có cảnh báo lỗi phần cứng (SENSOR_FAULT).",
            not_applicable_reason="SENSOR_FAULT_ACTIVE",
        )
    if "HIGH_TEMP" in active_hard_alerts or "DEVICE_OFFLINE" in active_hard_alerts:
        return _base_response(
            "NOT_APPLICABLE",
            "Thiết bị đang có cảnh báo ngưỡng cứng (HIGH_TEMP / DEVICE_OFFLINE) đang mở.",
            not_applicable_reason="HARD_ALERT_ACTIVE",
        )

    # 4. Truy vấn chuỗi Telemetry trong cửa sổ (as_of - WINDOW_SECONDS, as_of]
    raw_rows = list(
        Telemetry.objects.filter(
            device=device,
            measured_at__gt=window_start,
            measured_at__lte=now,
        ).order_by("measured_at", "received_at", "id")
    )
    all_samples = [_extract_sample(r) for r in raw_rows]

    if not all_samples:
        if device.latest_mode == "MANUAL":
            return _base_response(
                "NOT_APPLICABLE",
                "Tủ mạng đang ở chế độ điều khiển quạt thủ công (MANUAL).",
                not_applicable_reason="MANUAL_ACTIVE",
            )
        if device.latest_door_status == "OPEN":
            return _base_response(
                "NOT_APPLICABLE",
                "Cửa tủ mạng đang mở (DOOR_OPEN), không đánh giá hiệu suất làm mát.",
                not_applicable_reason="DOOR_OPEN",
            )
        return _base_response(
            "INSUFFICIENT_DATA",
            f"Đang thu thập dữ liệu đánh giá AI (cần tối thiểu {min_samples} mẫu hợp lệ).",
            insufficient_reason="TOO_FEW_SAMPLES",
            sample_count=0,
        )

    last_sample = all_samples[-1]

    # 5. Kiểm tra trạng thái hiện thời tại mẫu cuối cùng
    if not last_sample.sensor_ok:
        return _base_response(
            "NOT_APPLICABLE",
            "Mẫu đo mới nhất báo lỗi cảm biến DHT22 (sensor_ok=false / FAULT).",
            not_applicable_reason="SENSOR_FAULT_ACTIVE",
        )
    if last_sample.mode == "MANUAL":
        return _base_response(
            "NOT_APPLICABLE",
            "Tủ mạng đang ở chế độ điều khiển quạt thủ công (MANUAL).",
            not_applicable_reason="MANUAL_ACTIVE",
        )
    if last_sample.door_status == "OPEN":
        return _base_response(
            "NOT_APPLICABLE",
            "Cửa tủ mạng đang mở (DOOR_OPEN), nhiệt độ biến thiên theo môi trường ngoài.",
            not_applicable_reason="DOOR_OPEN",
        )

    # 6. Quy tắc cắt đoạn ổn định (Settle Rule - Mục 3.7-C)
    effective_start = window_start
    dominant_reason = "TOO_FEW_SAMPLES"

    # (a) Đổi cấu hình ngưỡng quạt fan_on_c / fan_off_c trong cửa sổ
    if last_fan_cfg_change_at is not None:
         first_sample_ts = all_samples[0].measured_at
         if last_fan_cfg_change_at >= first_sample_ts:
            cut_at = last_fan_cfg_change_at + timedelta(seconds=SETTLE_SECONDS)
            if cut_at > effective_start:
                effective_start = cut_at
                dominant_reason = "RECENT_CONFIG_CHANGE"

    # (b) Mẫu MANUAL cuối cùng trong cửa sổ
    manual_times = [s.measured_at for s in all_samples if s.mode == "MANUAL"]
    if manual_times:
        cut_at = manual_times[-1] + timedelta(seconds=SETTLE_SECONDS)
        if cut_at >= effective_start:
            effective_start = cut_at
            dominant_reason = "RECENT_MANUAL_CONTROL"

    # (c) Mẫu lỗi cảm biến (FAULT / temperature=None / sensor_ok=False) cuối cùng trong cửa sổ
    fault_times = [s.measured_at for s in all_samples if not s.sensor_ok]
    if fault_times:
        cut_at = fault_times[-1] + timedelta(seconds=SETTLE_SECONDS)
        if cut_at >= effective_start:
            effective_start = cut_at
            dominant_reason = "RECENT_FAULT"

    # (d) Mẫu cửa mở cuối cùng trong cửa sổ
    door_times = [s.measured_at for s in all_samples if s.door_status == "OPEN"]
    if door_times:
        cut_at = door_times[-1] + timedelta(seconds=DOOR_SETTLE_SECONDS)
        if cut_at >= effective_start:
            effective_start = cut_at
            dominant_reason = "RECENT_DOOR_OPEN"

    # Lọc các mẫu hợp lệ sau mốc ổn định
    valid_samples = [
        s
        for s in all_samples
        if s.sensor_ok and s.temperature is not None and s.humidity is not None and s.measured_at >= effective_start
    ]

    # (e) Cắt theo khoảng hở > MAX_GAP_SECONDS (chỉ giữ đoạn liên tục mới nhất)
    if len(valid_samples) >= 2:
        last_gap_idx = 0
        for idx in range(1, len(valid_samples)):
            gap_s = (valid_samples[idx].measured_at - valid_samples[idx - 1].measured_at).total_seconds()
            if gap_s > max_gap_s:
                last_gap_idx = idx
        if last_gap_idx > 0:
            valid_samples = valid_samples[last_gap_idx:]
            effective_start = valid_samples[0].measured_at
            dominant_reason = "RECENT_GAP"

    profile = str(config_in_effect.get("profile", "CONTROLLER")).upper()
    interval_ms = int(config_in_effect.get("telemetry_interval_ms", 5000))

    # Nếu chu kỳ gửi quá dài (> 20 000 ms) và không đủ mẫu cho SENSOR_ANOMALY
    if len(valid_samples) < min_samples:
        if interval_ms > 20000:
            return _base_response(
                "NOT_APPLICABLE",
                f"Chu kỳ gửi dữ liệu ({interval_ms} ms) lớn hơn 20 000 ms, không đủ độ phân giải đánh giá làm mát.",
                not_applicable_reason="TELEMETRY_INTERVAL_TOO_LONG",
                sample_count=len(valid_samples),
                eff_win_start=effective_start,
            )
        reason_messages = {
            "RECENT_CONFIG_CHANGE": "Vừa thay đổi ngưỡng quạt trên thiết bị — AI đang chờ 120 giây để nhiệt độ ổn định.",
            "RECENT_MANUAL_CONTROL": "Vừa kết thúc điều khiển quạt thủ công (MANUAL) — AI đang chờ 120 giây để đánh giá lại.",
            "RECENT_FAULT": "Cảm biến vừa phục hồi sau lỗi đọc — AI đang chờ 120 giây để ổn định.",
            "RECENT_DOOR_OPEN": "Cửa tủ vừa đóng lại — AI đang chờ 60 giây để nhiệt độ trong tủ ổn định.",
            "RECENT_GAP": "Tín hiệu vừa khôi phục sau khoảng gián đoạn — đang thu thập lại đoạn dữ liệu liên tục.",
            "TOO_FEW_SAMPLES": f"Đang thu thập dữ liệu đánh giá AI (hiện có {len(valid_samples)}/{min_samples} mẫu hợp lệ).",
        }
        return _base_response(
            "INSUFFICIENT_DATA",
            reason_messages.get(dominant_reason, reason_messages["TOO_FEW_SAMPLES"]),
            insufficient_reason=dominant_reason,
            sample_count=len(valid_samples),
            eff_win_start=effective_start,
        )

    metrics = extract_features_from_samples(valid_samples, timeline, fallback_eff, interval_s, step_gap_s)
    if metrics is None:
        return _base_response(
            "INSUFFICIENT_DATA",
            "Dữ liệu trong cửa sổ không hợp lệ về mặt số học.",
            insufficient_reason="TOO_FEW_SAMPLES",
            sample_count=len(valid_samples),
            eff_win_start=effective_start,
        )

    ml_score, ml_pred, ml_loaded, ml_unavail_reason = score_feature_vector(metrics)

    # Kiểm tra điều kiện tự giải tỏa (Auto-resolve flags phục vụ Worker)
    last_temp = float(valid_samples[-1].temperature)
    fan_off_c = float(config_in_effect["fan_off_c"])
    trailing_off_count = 0
    for s in reversed(valid_samples):
        if s.fan_status == "OFF":
            trailing_off_count += 1
        else:
            break
    fan_off_duration_min = (
        ((valid_samples[-1].measured_at - valid_samples[-trailing_off_count].measured_at).total_seconds() + interval_s) / 60.0
        if trailing_off_count > 0
        else 0.0
    )
    auto_resolve_cooling = (
        metrics["temp_slope_per_min"] <= -0.05
        or last_temp <= fan_off_c
        or fan_off_duration_min >= 2.0
    )
    auto_resolve_sensor = metrics["temp_std"] > 0.0 and metrics["max_step_temp"] < 2.0

    # 7. Đánh giá SENSOR_ANOMALY (luật cứng, chạy cho cả profile khác CONTROLLER)
    is_spike = metrics["max_step_temp"] >= 5.0
    is_flatline = (
        len(valid_samples) >= flatline_min_samples
        and metrics["temp_std"] == 0.0
        and metrics.get("_humid_std", 1.0) == 0.0
    )
    if is_spike or is_flatline:
        if is_spike:
            summary = (
                f"Phát hiện số đo nhiệt độ nhảy gai đột ngột ({metrics['max_step_temp']:.2f}°C "
                f"giữa 2 mẫu liên tiếp ≤ {int(step_gap_s)}s)."
            )
        else:
            summary = (
                f"Phát hiện số đo nhiệt độ và độ ẩm đứng yên bất thường (temp_std=0, humid_std=0 "
                f"qua {len(valid_samples)} mẫu liên tiếp) — nghi cảm biến treo giá trị."
            )
        return _base_response(
            "WARNING",
            summary,
            anomaly_type="SENSOR_ANOMALY",
            sample_count=len(valid_samples),
            ml_score=ml_score,
            metrics=metrics,
            eff_win_start=effective_start,
            auto_resolve_cooling=auto_resolve_cooling,
            auto_resolve_sensor=False,
        )

    # 8. Lớp phòng thủ profile & telemetry_interval_ms trước khi đánh giá COOLING_DEGRADATION
    if profile != "CONTROLLER":
        return _base_response(
            "NOT_APPLICABLE",
            f"Profile thiết bị '{profile}' không hỗ trợ đánh giá hiệu suất quạt làm mát.",
            not_applicable_reason="UNSUPPORTED_PROFILE",
            sample_count=len(valid_samples),
            ml_score=ml_score,
            metrics=metrics,
            eff_win_start=effective_start,
        )
    if interval_ms > 20000:
        return _base_response(
            "NOT_APPLICABLE",
            f"Chu kỳ gửi dữ liệu ({interval_ms} ms) lớn hơn 20 000 ms, không áp dụng đánh giá suy giảm làm mát.",
            not_applicable_reason="TELEMETRY_INTERVAL_TOO_LONG",
            sample_count=len(valid_samples),
            ml_score=ml_score,
            metrics=metrics,
            eff_win_start=effective_start,
        )

    # 9. Đánh giá COOLING_DEGRADATION (Hybrid: Rule + IsolationForest nếu đã nạp)
    slope_threshold = 0.2 if ml_loaded else 0.3
    rule_cooling_bad = (
        metrics["fan_on_duration_min"] >= FAN_ON_MIN_MINUTES
        and metrics["temp_above_fan_on"] > 0.5
        and metrics["temp_slope_per_min"] >= slope_threshold
    )
    ml_confirms = (ml_pred == -1) if ml_loaded else True

    if rule_cooling_bad and ml_confirms:
        mode_note = "IsolationForest + Luật vật lý" if ml_loaded else "Luật định lượng (chưa nạp model ML)"
        summary = (
            f"Cảnh báo suy giảm làm mát ({mode_note}): quạt đã bật liên tục "
            f"{metrics['fan_on_duration_min']:.1f} phút trên ngưỡng bật quạt "
            f"(+{metrics['temp_above_fan_on']:.2f}°C) nhưng nhiệt độ vẫn tăng "
            f"{metrics['temp_slope_per_min']:+.2f}°C/phút."
        )
        return _base_response(
            "WARNING",
            summary,
            anomaly_type="COOLING_DEGRADATION",
            sample_count=len(valid_samples),
            ml_score=ml_score,
            metrics=metrics,
            eff_win_start=effective_start,
            auto_resolve_cooling=False,
            auto_resolve_sensor=auto_resolve_sensor,
        )

    normal_note = "Hệ thống làm mát và cảm biến hoạt động bình thường."
    if not ml_loaded:
        normal_note += " (Đang đánh giá bằng luật định lượng, chưa nạp model ML)."
    return _base_response(
        "NORMAL",
        normal_note,
        anomaly_type="NONE",
        sample_count=len(valid_samples),
        ml_score=ml_score,
        metrics=metrics,
        eff_win_start=effective_start,
        auto_resolve_cooling=auto_resolve_cooling,
        auto_resolve_sensor=auto_resolve_sensor,
    )


def evaluate_and_sync_device_anomaly_alerts(
    device: Device,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """
    Hàm dành riêng cho Worker (run_mqtt_worker.py):
    - Đánh giá hysteresis dựa trên 2 cửa sổ lệch nhau 30 giây lấy từ CSDL (không lưu counter trong RAM).
    - Mở Alert AI khi cả 2 cửa sổ cùng báo WARNING cùng một anomaly_type.
    - Tự động RESOLVED khi cả 2 cửa sổ đạt điều kiện auto-resolve, hoặc khi Alert AI bị treo
      ở trạng thái NOT_APPLICABLE / INSUFFICIENT_DATA quá AI_ALERT_STALE_MINUTES (30 phút).
    - Dùng select_for_update trên Device để chống tạo trùng Alert khi chạy nhiều worker.
    """
    now = as_of or timezone.now()
    res_now = evaluate_device_anomaly(device, as_of=now)
    res_prev = evaluate_device_anomaly(device, as_of=now - timedelta(seconds=HYSTERESIS_OFFSET_SECONDS))

    with transaction.atomic():
        locked_device = Device.objects.select_for_update().get(pk=device.pk)
        open_ai_alerts = list(
            Alert.objects.filter(
                device=locked_device,
                alert_type__in=["COOLING_DEGRADATION", "SENSOR_ANOMALY"],
                status__in=["OPEN", "ACKNOWLEDGED"],
            )
        )

        # 1. Xử lý khi trạng thái hiện tại là NOT_APPLICABLE hoặc INSUFFICIENT_DATA
        if res_now["status"] in ("NOT_APPLICABLE", "INSUFFICIENT_DATA"):
            stale_cutoff = timedelta(minutes=AI_ALERT_STALE_MINUTES)
            for alert in open_ai_alerts:
                # Xác định thời gian bắt đầu rơi vào trạng thái không kiểm chứng được
                if locked_device.updated_at and (now - locked_device.updated_at).total_seconds() > 5.0:
                    elapsed_stale = now - max(alert.created_at, locked_device.updated_at)
                else:
                    elapsed_stale = now - alert.created_at
                if elapsed_stale >= stale_cutoff:
                    alert.status = "RESOLVED"
                    alert.resolved_at = now
                    alert.save(update_fields=["status", "resolved_at"])
                    logger.info(
                        "[AI Anomaly] Tự đóng Alert AI treo quá %d phút: device=%s, type=%s",
                        AI_ALERT_STALE_MINUTES,
                        locked_device.id,
                        alert.alert_type,
                    )
            return res_now

        # 2. Mở Alert AI mới nếu cả 2 cửa sổ liên tiếp cùng phát hiện 1 loại bất thường
        if (
            res_now["status"] == "WARNING"
            and res_prev["status"] == "WARNING"
            and res_now["anomaly_type"] == res_prev["anomaly_type"]
            and res_now["anomaly_type"] in ("COOLING_DEGRADATION", "SENSOR_ANOMALY")
        ):
            anom_type = res_now["anomaly_type"]
            already_open = any(a.alert_type == anom_type for a in open_ai_alerts)
            if not already_open:
                Alert.objects.create(
                    device=locked_device,
                    alert_type=anom_type,
                    severity="WARNING",
                    status="OPEN",
                    message=str(res_now["summary"])[:255],
                    threshold=float(res_now["config_in_effect"]["fan_on_c"]),
                    current_value=locked_device.latest_temperature,
                )
                logger.info(
                    "[AI Anomaly] Đã tạo Alert %s cho tủ %s",
                    anom_type,
                    locked_device.id,
                )

        # 3. Tự động giải tỏa (Auto-resolve) khi cả 2 cửa sổ liên tiếp đạt điều kiện an toàn
        for alert in open_ai_alerts:
            if alert.alert_type == "COOLING_DEGRADATION":
                should_resolve = (
                    res_now["anomaly_type"] != "COOLING_DEGRADATION"
                    and res_prev["anomaly_type"] != "COOLING_DEGRADATION"
                    and bool(res_now.get("_auto_resolve_cooling"))
                    and bool(res_prev.get("_auto_resolve_cooling"))
                )
            else:
                should_resolve = (
                    res_now["anomaly_type"] != "SENSOR_ANOMALY"
                    and res_prev["anomaly_type"] != "SENSOR_ANOMALY"
                    and bool(res_now.get("_auto_resolve_sensor"))
                    and bool(res_prev.get("_auto_resolve_sensor"))
                )
            if should_resolve:
                alert.status = "RESOLVED"
                alert.resolved_at = now
                alert.save(update_fields=["status", "resolved_at"])
                logger.info(
                    "[AI Anomaly] Tự động giải tỏa Alert %s cho tủ %s",
                    alert.alert_type,
                    locked_device.id,
                )

    return res_now
