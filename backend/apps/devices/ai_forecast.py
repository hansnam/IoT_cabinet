"""
Mô-đun Dự báo định lượng trung thực (Honest Forecast v2 - Pha 7).
Cung cấp:
  1. Gating chặt chẽ: trả về `available=False` kèm `reason` tường minh khi tủ OFFLINE,
     dữ liệu cũ (STALE_DATA), chế độ MANUAL (MANUAL_MODE), mới đổi cấu hình (RECENT_CONFIG_CHANGE),
     chưa đủ mẫu (TOO_FEW_SAMPLES) hoặc tủ không ở trạng thái ACTIVE (DEVICE_NOT_ACTIVE).
  2. Ngoại suy tuyến tính ngắn hạn (+5m, +15m, +30m — loại bỏ mốc +60m sai số lớn).
  3. Độ lệch chuẩn phần dư (residual_std_c) và khoảng tin cậy 95% (confidence_intervals).
  4. Giới hạn trần vật lý (physical ceiling = min(temp_alert_c + 15, 75.0)) kèm cờ `capped=True`.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Any, Literal

from django.utils import timezone

from apps.devices.models import DEVICE_OFFLINE_THRESHOLD_SECONDS
from apps.telemetry.anomaly import SETTLE_SECONDS, WINDOW_SECONDS, effective_thresholds

MIN_WINDOW_SAMPLES = 12
WINDOW_MINUTES = WINDOW_SECONDS // 60

ForecastBlockReason = Literal[
    "DEVICE_NOT_ACTIVE",
    "DEVICE_OFFLINE",
    "STALE_DATA",
    "MANUAL_MODE",
    "RECENT_CONFIG_CHANGE",
    "TOO_FEW_SAMPLES",
]

REASON_DESCRIPTIONS_VI: dict[str, str] = {
    "DEVICE_NOT_ACTIVE": "Không thể dự báo: Tủ mạng chưa ở trạng thái hoạt động (ACTIVE).",
    "DEVICE_OFFLINE": "Không thể dự báo: Tủ mạng đang mất kết nối (OFFLINE), dữ liệu viễn trắc không còn cập nhật thời gian thực.",
    "STALE_DATA": "Không thể dự báo: Mẫu đo gần nhất đã quá cũ so với chu kỳ gửi telemetry của thiết bị.",
    "MANUAL_MODE": "Không thể dự báo tự động: Tủ đang ở chế độ điều khiển Thủ công (MANUAL), quỹ đạo nhiệt phụ thuộc thao tác cưỡng bức của người vận hành.",
    "RECENT_CONFIG_CHANGE": "Tạm hoãn dự báo (Settle Rule): Tủ vừa thay đổi cấu hình hoặc chuyển chế độ gần đây, nhiệt độ đang trong giai đoạn quá độ.",
    "TOO_FEW_SAMPLES": "Chưa đủ dữ liệu để dự báo: Cần tối thiểu 12 mẫu đo hợp lệ trong cửa sổ 10 phút gần nhất.",
}


def get_device_thresholds(device: Any) -> dict[str, Any]:
    """Lấy cấu hình ngưỡng hiệu lực từ Device (applied_config / desired_config) hoặc dict."""
    if isinstance(device, dict):
        return effective_thresholds(device)
    cfg = getattr(device, "applied_config", None) or getattr(device, "desired_config", None) or {}
    return effective_thresholds(cfg)


def _is_device_online_at(device: Any, ref_time: datetime) -> bool:
    last_seen = getattr(device, "last_seen_at", None)
    if not last_seen:
        return False
    return (ref_time - last_seen).total_seconds() < DEVICE_OFFLINE_THRESHOLD_SECONDS


def _check_recent_settle_condition(
    device: Any,
    ref_time: datetime,
    anomaly_eval: dict[str, Any] | None,
) -> bool:
    """Kiểm tra xem tủ có đang trong thời gian Settle Rule sau khi đổi cấu hình / chuyển chế độ không."""
    if isinstance(anomaly_eval, dict):
        status = anomaly_eval.get("status")
        reason = str(anomaly_eval.get("reason") or "")
        if status == "NOT_APPLICABLE" and any(
            kw in reason.lower() for kw in ("cấu hình", "settle", "chuyển chế độ", "manual")
        ):
            return True

    settle_cutoff = ref_time - timedelta(seconds=SETTLE_SECONDS)
    try:
        if device.config_versions.filter(created_at__gte=settle_cutoff, created_at__lte=ref_time).exists():
            return True
        if device.config_versions.filter(applied_at__gte=settle_cutoff, applied_at__lte=ref_time).exists():
            return True
    except Exception:
        pass
    return False


def _fit_linear_with_residuals(
    points: list[tuple[datetime, float]],
) -> tuple[float, float, float, float]:
    """
    Hồi quy tuyến tính y = intercept + slope * t_min trên danh sách (measured_at, value).
    Trả về (slope_per_min, residual_std, span_minutes, fitted_end_value).
    """
    if len(points) < 2:
        last_val = float(points[-1][1]) if points else 0.0
        return 0.0, 0.05, 0.0, last_val

    t0 = points[0][0]
    xs = [(ts - t0).total_seconds() / 60.0 for ts, _ in points]
    ys = [float(val) for _, val in points]
    span_min = xs[-1] - xs[0]
    if span_min <= 1e-6:
        return 0.0, 0.05, 0.0, ys[-1]

    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 1e-9:
        return 0.0, 0.05, span_min, mean_y

    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    fitted_end = intercept + slope * xs[-1]

    if n > 2:
        sse = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
        residual_std = math.sqrt(sse / (n - 2))
    else:
        residual_std = 0.05

    return float(slope), float(max(residual_std, 0.02)), float(span_min), float(fitted_end)


def _clamp_physical_temp(val: float, ceiling: float, floor: float = -20.0) -> tuple[float, bool]:
    if val > ceiling:
        return round(ceiling, 2), True
    if val < floor:
        return round(floor, 2), True
    return round(val, 2), False


def compute_forecast_v2(
    device: Any,
    tel_qs: Any | None = None,
    anomaly_eval: dict[str, Any] | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """
    Tính toán dự báo nhiệt độ & độ ẩm ngắn hạn (+5m, +15m, +30m) có kiểm soát điều kiện hợp lệ (Gating).
    """
    now = as_of or timezone.now()
    thresholds = get_device_thresholds(device)
    fan_on_c = float(thresholds["fan_on_c"])
    fan_off_c = float(thresholds["fan_off_c"])
    temp_alert_c = float(thresholds["temp_alert_c"])
    humidity_alert_pct = float(thresholds["humidity_alert_pct"])
    interval_ms = int(thresholds.get("telemetry_interval_ms") or 5000)
    interval_s = max(1.0, interval_ms / 1000.0)
    physical_ceiling_c = min(round(temp_alert_c + 15.0, 2), 75.0)

    def _blocked(
        reason: ForecastBlockReason,
        current_temp: float | None = None,
        current_humid: float | None = None,
    ) -> dict[str, Any]:
        return {
            "available": False,
            "reason": reason,
            "basis_samples": 0,
            "sample_count_used": 0,
            "current_temperature_c": round(current_temp, 2) if current_temp is not None else None,
            "current_temp_c": round(current_temp, 2) if current_temp is not None else None,
            "current_humidity_pct": round(current_humid, 2) if current_humid is not None else None,
            "temp_slope_per_min": None,
            "temp_slope_c_per_min": None,
            "humid_slope_per_min": None,
            "residual_std_c": None,
            "projected_temp_5m_c": None,
            "projected_temp_15m_c": None,
            "projected_temp_30m_c": None,
            "projected_humidity_15m_pct": None,
            "projected_humidity_30m_pct": None,
            "confidence_intervals": None,
            "capped": False,
            "physical_ceiling_c": physical_ceiling_c,
            "estimated_minutes_to_fan_on": None,
            "estimated_minutes_to_temp_alert": None,
            "estimated_minutes_to_humidity_alert": None,
            "predictive_assessment": REASON_DESCRIPTIONS_VI[reason],
        }

    # 1. Kiểm tra trạng thái vòng đời thiết bị
    if getattr(device, "status", "ACTIVE") != "ACTIVE":
        return _blocked("DEVICE_NOT_ACTIVE")

    # 2. Kiểm tra kết nối thời gian thực
    if not _is_device_online_at(device, now):
        return _blocked("DEVICE_OFFLINE")

    # 3. Truy vấn chuỗi mẫu gần nhất
    if tel_qs is None:
        window_start = now - timedelta(seconds=WINDOW_SECONDS * 2)
        recent_rows = list(
            device.telemetries.filter(measured_at__gte=window_start, measured_at__lte=now)
            .order_by("-measured_at")
            .values("measured_at", "temperature", "humidity", "fan_status", "mode")[:60]
        )
    else:
        recent_rows = list(
            tel_qs.order_by("-measured_at").values(
                "measured_at", "temperature", "humidity", "fan_status", "mode"
            )[:60]
        )

    if not recent_rows:
        return _blocked("TOO_FEW_SAMPLES")

    latest = recent_rows[0]
    latest_ts = latest.get("measured_at")
    latest_temp = float(latest["temperature"]) if latest.get("temperature") is not None else None
    latest_humid = float(latest["humidity"]) if latest.get("humidity") is not None else None
    latest_mode = str(latest.get("mode") or getattr(device, "latest_mode", "AUTO") or "AUTO").upper()
    latest_fan_status = str(latest.get("fan_status") or getattr(device, "latest_fan_status", "OFF") or "OFF").upper()

    # 4. Kiểm tra độ tươi của mẫu cuối cùng (STALE_DATA)
    max_stale_seconds = max(60.0, 3.0 * interval_s)
    if isinstance(latest_ts, datetime):
        age_seconds = (now - latest_ts).total_seconds()
        if age_seconds > max_stale_seconds:
            return _blocked("STALE_DATA", latest_temp, latest_humid)

    # 5. Kiểm tra chế độ MANUAL
    if latest_mode == "MANUAL" or str(getattr(device, "latest_mode", "AUTO")).upper() == "MANUAL":
        return _blocked("MANUAL_MODE", latest_temp, latest_humid)

    # 6. Kiểm tra Settle Rule (vừa đổi cấu hình trong SETTLE_SECONDS qua)
    if _check_recent_settle_condition(device, now, anomaly_eval):
        return _blocked("RECENT_CONFIG_CHANGE", latest_temp, latest_humid)

    # 7. Lọc các mẫu hợp lệ trong cửa sổ 10 phút tính từ mẫu mới nhất
    ref_latest_ts = latest_ts if isinstance(latest_ts, datetime) else now
    window_cutoff = ref_latest_ts - timedelta(seconds=WINDOW_SECONDS)
    chronological = list(reversed(recent_rows))
    temp_points: list[tuple[datetime, float]] = [
        (r["measured_at"], float(r["temperature"]))
        for r in chronological
        if r.get("measured_at") is not None
        and r["measured_at"] >= window_cutoff
        and r.get("temperature") is not None
        and -40.0 <= float(r["temperature"]) <= 85.0
    ]
    humid_points: list[tuple[datetime, float]] = [
        (r["measured_at"], float(r["humidity"]))
        for r in chronological
        if r.get("measured_at") is not None
        and r["measured_at"] >= window_cutoff
        and r.get("humidity") is not None
        and 0.0 <= float(r["humidity"]) <= 100.0
    ]

    if len(temp_points) < MIN_WINDOW_SAMPLES or latest_temp is None:
        return _blocked("TOO_FEW_SAMPLES", latest_temp, latest_humid)

    temp_slope, residual_std_c, span_min, fitted_end_temp = _fit_linear_with_residuals(temp_points)
    humid_slope, _, _, fitted_end_humid = (
        _fit_linear_with_residuals(humid_points)
        if len(humid_points) >= 2
        else (0.0, 0.1, 0.0, latest_humid or 0.0)
    )

    if span_min < 0.8:
        return _blocked("TOO_FEW_SAMPLES", latest_temp, latest_humid)

    # 8. Tính toán giá trị dự báo tại +5m, +15m, +30m từ điểm cuối đường hồi quy OLS
    # cùng khoảng tin cậy 95% (Student-t df=18 -> t_0.975 = 2.101) và trần vật lý
    n_pts = len(temp_points)
    capped_any = False
    proj_temps: dict[str, float] = {}
    ci_map: dict[str, list[float]] = {}

    for h in (5, 15, 30):
        raw_pred = fitted_end_temp + temp_slope * float(h)
        clamped_pred, is_capped = _clamp_physical_temp(raw_pred, physical_ceiling_c)
        if is_capped:
            capped_any = True
        proj_temps[f"{h}m"] = clamped_pred

        extrap_factor = (
            1.0
            + (1.0 / float(n_pts))
            + (float(h) / span_min)
            + (12.0 * ((0.5 * span_min + float(h)) ** 2)) / (float(n_pts) * (span_min ** 2))
        )
        half_width = 2.101 * residual_std_c * math.sqrt(max(1.0, extrap_factor))
        low_c, _ = _clamp_physical_temp(raw_pred - half_width, physical_ceiling_c)
        high_c, high_capped = _clamp_physical_temp(raw_pred + half_width, physical_ceiling_c)
        if high_capped:
            capped_any = True
        ci_map[f"{h}m"] = [low_c, high_c]

    proj_humid_15 = (
        round(min(100.0, max(0.0, fitted_end_humid + humid_slope * 15.0)), 1)
        if latest_humid is not None
        else None
    )
    proj_humid_30 = (
        round(min(100.0, max(0.0, fitted_end_humid + humid_slope * 30.0)), 1)
        if latest_humid is not None
        else None
    )

    # 9. Ước tính thời gian chạm ngưỡng bật quạt & ngưỡng cảnh báo nhiệt độ (trong tầm nhìn 60 phút)
    mins_to_fan_on: float | None = None
    mins_to_temp_alert: float | None = None
    if latest_temp >= fan_on_c:
        mins_to_fan_on = 0.0
    elif temp_slope > 0.01:
        est = (fan_on_c - latest_temp) / temp_slope
        if 0.0 < est <= 60.0:
            mins_to_fan_on = round(est, 1)

    if latest_temp >= temp_alert_c:
        mins_to_temp_alert = 0.0
    elif temp_slope > 0.01:
        est = (temp_alert_c - latest_temp) / temp_slope
        if 0.0 < est <= 60.0:
            mins_to_temp_alert = round(est, 1)

    mins_to_humid_alert: float | None = None
    if latest_humid is not None:
        if latest_humid >= humidity_alert_pct:
            mins_to_humid_alert = 0.0
        elif humid_slope > 0.02:
            est_h = (humidity_alert_pct - latest_humid) / humid_slope
            if 0.0 < est_h <= 60.0:
                mins_to_humid_alert = round(est_h, 1)

    # 10. Tổng hợp nhận định định lượng kèm khoảng tin cậy
    ci_15 = ci_map["15m"]
    ci_30 = ci_map["30m"]
    cap_note = f" (đã giới hạn ở trần vật lý {physical_ceiling_c}°C)" if capped_any else ""
    if mins_to_temp_alert == 0.0:
        assessment = (
            f"Nhiệt độ hiện tại ({latest_temp:.2f}°C) ĐÃ VƯỢT ngưỡng cảnh báo ({temp_alert_c}°C). "
            f"Dự báo +15 phút: {proj_temps['15m']:.2f}°C (95% CI: [{ci_15[0]:.2f}, {ci_15[1]:.2f}]°C), "
            f"+30 phút: {proj_temps['30m']:.2f}°C (95% CI: [{ci_30[0]:.2f}, {ci_30[1]:.2f}]°C){cap_note}."
        )
    elif mins_to_temp_alert is not None:
        assessment = (
            f"Nhiệt độ đang tăng ({temp_slope:+.3f}°C/phút, σ={residual_std_c:.2f}°C). "
            f"Dự báo +15 phút: {proj_temps['15m']:.2f}°C ([{ci_15[0]:.2f}, {ci_15[1]:.2f}]°C), "
            f"+30 phút: {proj_temps['30m']:.2f}°C ([{ci_30[0]:.2f}, {ci_30[1]:.2f}]°C){cap_note}; "
            f"ước tính chạm ngưỡng cảnh báo {temp_alert_c}°C sau ~{mins_to_temp_alert} phút nếu tốc độ tăng nhiệt không đổi."
        )
    elif temp_slope > 0.02:
        assessment = (
            f"Nhiệt độ tăng nhẹ ({temp_slope:+.3f}°C/phút, σ={residual_std_c:.2f}°C), "
            f"dự báo đạt {proj_temps['15m']:.2f}°C ([{ci_15[0]:.2f}, {ci_15[1]:.2f}]°C) sau 15 phút "
            f"và {proj_temps['30m']:.2f}°C ([{ci_30[0]:.2f}, {ci_30[1]:.2f}]°C) sau 30 phút (chưa chạm ngưỡng {temp_alert_c}°C)."
        )
    elif temp_slope < -0.02:
        assessment = (
            f"Nhiệt độ đang giảm ({temp_slope:+.3f}°C/phút, σ={residual_std_c:.2f}°C), "
            f"dự báo hạ xuống {proj_temps['15m']:.2f}°C sau 15 phút và {proj_temps['30m']:.2f}°C sau 30 phút."
        )
    else:
        assessment = (
            f"Nhiệt độ ổn định quanh mức {latest_temp:.2f}°C (biến thiên {temp_slope:+.3f}°C/phút, σ={residual_std_c:.2f}°C); "
            f"dự báo 15–30 phút tới duy trì trong khoảng [{ci_15[0]:.2f}, {ci_30[1]:.2f}]°C."
        )

    return {
        "available": True,
        "reason": None,
        "basis_samples": len(temp_points),
        "sample_count_used": len(temp_points),
        "window_span_minutes": round(span_min, 2),
        "current_temperature_c": round(latest_temp, 2),
        "current_temp_c": round(latest_temp, 2),
        "current_humidity_pct": round(latest_humid, 2) if latest_humid is not None else None,
        "latest_fan_state": latest_fan_status == "ON",
        "latest_fan_status": latest_fan_status,
        "latest_mode": latest_mode,
        "temp_slope_per_min": round(temp_slope, 4),
        "temp_slope_c_per_min": round(temp_slope, 4),
        "humid_slope_per_min": round(humid_slope, 4),
        "residual_std_c": round(residual_std_c, 3),
        "projected_temp_5m_c": proj_temps["5m"],
        "projected_temp_15m_c": proj_temps["15m"],
        "projected_temp_30m_c": proj_temps["30m"],
        "projected_humidity_15m_pct": proj_humid_15,
        "projected_humidity_30m_pct": proj_humid_30,
        "confidence_intervals": ci_map,
        "capped": capped_any,
        "physical_ceiling_c": physical_ceiling_c,
        "thresholds": {
            "fan_on_c": fan_on_c,
            "fan_off_c": fan_off_c,
            "temp_alert_c": temp_alert_c,
            "humidity_alert_pct": humidity_alert_pct,
        },
        "estimated_minutes_to_fan_on": mins_to_fan_on,
        "estimated_minutes_to_temp_alert": mins_to_temp_alert,
        "estimated_minutes_to_humidity_alert": mins_to_humid_alert,
        "predictive_assessment": assessment,
    }
