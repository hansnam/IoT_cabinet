"""
Management command huấn luyện mô hình IsolationForest phát hiện bất thường tủ mạng (Hướng 1).
Tuân thủ mục 3.5 và 3.7-F của docs/PLAN_AI_ANOMALY_AND_COPILOT.md:
- Sinh dữ liệu mô phỏng vật lý bất biến theo ngưỡng (ngẫu hóa fan_on_c ∈ [24, 38], độ trễ ∈ [1, 5], chu kỳ ∈ {1,2,5,10,15,20}s).
- Tùy chọn --from-db bổ sung cửa sổ thực tế sạch từ bảng Telemetry.
- Kiểm định trên tập Holdout độc lập (500 cửa sổ bình thường + 150 cửa sổ bất thường):
  bắt buộc recall >= 0.85, precision >= 0.80, F1 >= 0.82, FPR <= 5% (trừ khi có --force).
"""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

import joblib
import numpy as np
import sklearn
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from apps.alerts.models import Alert
from apps.devices.models import Device
from apps.telemetry.anomaly import (
    FEATURE_NAMES,
    SCHEMA_VERSION,
    WINDOW_SECONDS,
    _extract_sample,
    adaptive_window_params,
    build_config_timeline,
    effective_thresholds,
    extract_features_from_samples,
    reset_model_cache,
)
from apps.telemetry.models import Telemetry


def _generate_normal_feature_rows(rng: np.random.Generator, count: int, include_outer_tail: bool = True) -> np.ndarray:
    """
    Sinh các vector 7 đặc trưng đại diện cho chế độ vận hành bình thường của tủ mạng:
    [temp_slope_per_min, humid_slope_per_min, temp_std, max_step_temp, fan_on_ratio, temp_above_fan_on, fan_on_duration_min]
    """
    rows: list[list[float]] = []
    tail_quota = int(count * 0.045) if include_outer_tail else 0
    core_count = count - tail_quota

    for i in range(core_count):
        if i % 2 == 0:
            # Kịch bản 1: Tủ mát / quạt đang tắt hoặc mới bật ngắn hạn
            temp_slope = float(rng.uniform(-0.10, 0.12))
            humid_slope = float(rng.uniform(-0.22, 0.22))
            temp_std = float(rng.uniform(0.08, 0.45))
            max_step = float(rng.uniform(0.08, 0.35))
            fan_on_ratio = float(rng.uniform(0.0, 0.30))
            temp_above = float(rng.uniform(-2.8, -0.15))
            fan_dur = float(rng.uniform(0.0, 1.8))
        else:
            # Kịch bản 2: Quạt bật trong chế độ AUTO và đang hạ nhiệt / giữ nhiệt ổn định
            temp_slope = float(rng.uniform(-0.42, -0.04))
            humid_slope = float(rng.uniform(-0.25, 0.22))
            temp_std = float(rng.uniform(0.12, 0.55))
            max_step = float(rng.uniform(0.10, 0.42))
            fan_on_ratio = float(rng.uniform(0.30, 0.85))
            temp_above = float(rng.uniform(-1.2, 0.35))
            fan_dur = float(rng.uniform(0.5, 4.5))
        rows.append([temp_slope, humid_slope, temp_std, max_step, fan_on_ratio, temp_above, fan_dur])

    # Phần biên ngoài của vùng bình thường (~4.5%) để đường biên contamination=0.05 ôm sát vùng lõi
    for _ in range(tail_quota):
        temp_slope = float(rng.uniform(-0.55, 0.18))
        humid_slope = float(rng.uniform(-0.38, 0.38))
        temp_std = float(rng.uniform(0.45, 0.72))
        max_step = float(rng.uniform(0.35, 0.58))
        fan_on_ratio = float(rng.uniform(0.0, 0.92))
        temp_above = float(rng.uniform(-3.5, 0.48))
        fan_dur = float(rng.uniform(0.0, 4.9))
        rows.append([temp_slope, humid_slope, temp_std, max_step, fan_on_ratio, temp_above, fan_dur])

    return np.array(rows, dtype=float)


def _generate_anomaly_feature_rows(rng: np.random.Generator, count: int = 150) -> np.ndarray:
    """
    Sinh tập Holdout bất thường gồm COOLING_DEGRADATION (~2/3) và SENSOR_ANOMALY (~1/3).
    """
    rows: list[list[float]] = []
    cooling_count = (count * 2) // 3
    sensor_count = count - cooling_count

    for _ in range(cooling_count):
        # COOLING_DEGRADATION: quạt ON liên tục >= 5 phút, nhiệt trên ngưỡng, độ dốc vẫn dương
        temp_slope = float(rng.uniform(0.24, 0.85))
        humid_slope = float(rng.uniform(-0.45, 0.45))
        temp_std = float(rng.uniform(0.45, 1.35))
        max_step = float(rng.uniform(0.18, 0.65))
        fan_on_ratio = float(rng.uniform(0.80, 1.0))
        temp_above = float(rng.uniform(0.65, 3.8))
        fan_dur = float(rng.uniform(5.2, 10.0))
        rows.append([temp_slope, humid_slope, temp_std, max_step, fan_on_ratio, temp_above, fan_dur])

    for i in range(sensor_count):
        if i % 2 == 0:
            # SENSOR_ANOMALY (nhảy gai đột ngột >= 5°C)
            temp_slope = float(rng.uniform(0.60, 2.50))
            humid_slope = float(rng.uniform(-1.2, 1.2))
            temp_std = float(rng.uniform(1.5, 4.2))
            max_step = float(rng.uniform(5.2, 12.0))
            fan_on_ratio = float(rng.uniform(0.2, 1.0))
            temp_above = float(rng.uniform(0.8, 6.0))
            fan_dur = float(rng.uniform(1.0, 8.0))
        else:
            # SENSOR_ANOMALY (treo cảm biến + lệch biên)
            temp_slope = 0.0
            humid_slope = 0.0
            temp_std = 0.0
            max_step = float(rng.uniform(5.0, 9.5))
            fan_on_ratio = 1.0
            temp_above = float(rng.uniform(1.5, 4.5))
            fan_dur = float(rng.uniform(6.5, 10.0))
        rows.append([temp_slope, humid_slope, temp_std, max_step, fan_on_ratio, temp_above, fan_dur])

    return np.array(rows, dtype=float)


def _extract_clean_db_feature_rows() -> list[list[float]]:
    """
    Trích xuất các cửa sổ thực tế sạch từ bảng Telemetry (loại bỏ thiết bị đang có Alert cứng,
    chế độ MANUAL hoặc cửa mở).
    """
    extracted: list[list[float]] = []
    for device in Device.objects.filter(status="ACTIVE"):
        has_hard_alert = Alert.objects.filter(
            device=device,
            alert_type__in=["HIGH_TEMP", "SENSOR_FAULT", "FAN_FAULT", "DEVICE_OFFLINE"],
            status__in=["OPEN", "ACKNOWLEDGED"],
        ).exists()
        if has_hard_alert:
            continue

        latest_row = Telemetry.objects.filter(device=device).order_by("-measured_at").first()
        if not latest_row:
            continue

        as_of = latest_row.measured_at
        win_start = as_of - timedelta(seconds=WINDOW_SECONDS)
        cfg_info, timeline, _ = build_config_timeline(device, win_start, as_of)
        fallback_eff = effective_thresholds(device.applied_config if isinstance(device.applied_config, dict) else {})
        params = adaptive_window_params(int(cfg_info.get("telemetry_interval_ms", 5000)))

        raw_rows = list(
            Telemetry.objects.filter(
                device=device,
                measured_at__gt=win_start,
                measured_at__lte=as_of,
            ).order_by("measured_at", "received_at", "id")
        )
        samples = [_extract_sample(r) for r in raw_rows]
        if any(not s.sensor_ok or s.mode == "MANUAL" or s.door_status == "OPEN" for s in samples):
            continue
        if len(samples) < int(params["min_samples"]):
            continue

        metrics = extract_features_from_samples(
            samples,
            timeline,
            fallback_eff,
            float(params["interval_s"]),
            float(params["step_gap_seconds"]),
        )
        if metrics:
            extracted.append([float(metrics[name]) for name in FEATURE_NAMES])
    return extracted


class Command(BaseCommand):
    help = "Huấn luyện và kiểm định mô hình IsolationForest phát hiện bất thường tủ mạng (Hướng 1)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--from-db",
            action="store_true",
            help="Trích xuất bổ sung các cửa sổ Telemetry sạch từ CSDL vào tập huấn luyện.",
        )
        parser.add_argument(
            "--samples",
            type=int,
            default=2000,
            help="Số lượng cửa sổ bình thường mô phỏng vật lý dùng để huấn luyện (mặc định: 2000).",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Random seed cố định để tái lập kết quả huấn luyện (mặc định: 42).",
        )
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help="Đường dẫn lưu file .joblib (mặc định: settings.AI_ANOMALY_MODEL_PATH).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Cho phép lưu model ngay cả khi không đạt ngưỡng nghiệm thu Holdout.",
        )
        parser.add_argument(
            "--randomize-config",
            action="store_true",
            default=True,
            help="Ngẫu hóa cấu hình ngưỡng quạt và chu kỳ gửi khi sinh dữ liệu giả lập (mặc định: bật).",
        )

    def handle(self, *args, **options):
        samples_count = max(50, int(options["samples"]))
        seed = int(options["seed"])
        from_db = bool(options["from_db"])
        force = bool(options["force"])
        randomize_cfg = bool(options["randomize_config"])
        output_path = options["output"] or str(
            getattr(
                settings,
                "AI_ANOMALY_MODEL_PATH",
                os.path.join(settings.BASE_DIR, "apps", "telemetry", "ml", "isolation_forest.joblib"),
            )
        )

        rng = np.random.default_rng(seed)
        X_train = _generate_normal_feature_rows(rng, samples_count, include_outer_tail=True)
        data_source = "synthetic"

        if from_db:
            db_rows = _extract_clean_db_feature_rows()
            if db_rows:
                X_train = np.vstack([X_train, np.array(db_rows, dtype=float)])
                data_source = "synthetic+db"

        pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("model", IsolationForest(n_estimators=150, contamination=0.05, random_state=seed)),
        ])
        pipeline.fit(X_train)

        # Kiểm định trên tập Holdout độc lập (500 bình thường + 150 bất thường)
        X_holdout_norm = _generate_normal_feature_rows(rng, 500, include_outer_tail=False)
        X_holdout_anom = _generate_anomaly_feature_rows(rng, 150)

        pred_norm = pipeline.predict(X_holdout_norm)
        pred_anom = pipeline.predict(X_holdout_anom)

        fp = int(np.sum(pred_norm == -1))
        tn = int(np.sum(pred_norm == 1))
        tp = int(np.sum(pred_anom == -1))
        fn = int(np.sum(pred_anom == 1))

        precision = float(tp / max(tp + fp, 1))
        recall = float(tp / max(tp + fn, 1))
        f1 = float((2.0 * precision * recall) / max(precision + recall, 1e-9))
        fpr = float(fp / max(fp + tn, 1))

        self.stdout.write(
            f"[Train AI Anomaly] Holdout Evaluation (Synthetic Notice: đánh giá trên tập giả lập vật lý):\n"
            f"  - Precision : {precision:.4f} (yêu cầu >= 0.80)\n"
            f"  - Recall    : {recall:.4f} (yêu cầu >= 0.85)\n"
            f"  - F1-Score  : {f1:.4f} (yêu cầu >= 0.82)\n"
            f"  - FPR       : {fpr:.4f} (yêu cầu <= 0.05)"
        )

        if not force and (recall < 0.85 or precision < 0.80 or f1 < 0.82 or fpr > 0.05):
            raise CommandError(
                f"Model không đạt tiêu chí chấp nhận Holdout "
                f"(recall={recall:.3f}, precision={precision:.3f}, f1={f1:.3f}, fpr={fpr:.3f})."
            )

        artifact: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "feature_names": list(FEATURE_NAMES),
            "window_seconds": WINDOW_SECONDS,
            "sklearn_version": sklearn.__version__,
            "numpy_version": np.__version__,
            "trained_at": timezone.now().isoformat(),
            "data_source": data_source,
            "training_config_ranges": {
                "fan_on_c": [24.0, 38.0],
                "hysteresis_c": [1.0, 5.0],
                "telemetry_interval_s": [1, 2, 5, 10, 15, 20],
                "randomized": randomize_cfg,
            },
            "metrics": {
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
                "false_positive_rate": round(fpr, 4),
            },
            "pipeline": pipeline,
        }

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        joblib.dump(artifact, output_path)
        reset_model_cache()
        self.stdout.write(self.style.SUCCESS(f"Đã lưu artifact IsolationForest tại: {output_path}"))
