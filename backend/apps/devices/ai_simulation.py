"""
Mô-đun Giả lập thay đổi ngưỡng cấu hình (What-If Threshold Simulation - Pha 7).
Ước tính tác động của việc thay đổi `fan_on_c` / `fan_off_c` dựa trên nhiệt độ hiện tại,
độ dốc nhiệt (`temp_slope_per_min`) và ngưỡng cảnh báo (`temp_alert_c`) mà KHÔNG tự động
gửi lệnh cấu hình xuống thiết bị phần cứng.
"""
from __future__ import annotations

from typing import Any

from apps.devices.ai_forecast import get_device_thresholds


def simulate_threshold_change(
    device: Any,
    new_fan_on_c: float | None = None,
    new_fan_off_c: float | None = None,
    forecast: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Mô phỏng việc thay đổi ngưỡng bật/tắt quạt (`fan_on_c`, `fan_off_c`).
    Kiểm tra tính hợp lệ (`-40 <= fan_off_c < fan_on_c <= 80`), tính toán độ lệch thời gian
    kích hoạt quạt, cảnh báo rủi ro chạm `temp_alert_c` và trả về danh sách `assumptions`.
    """
    thresholds = get_device_thresholds(device)
    cur_on = float(thresholds["fan_on_c"])
    cur_off = float(thresholds["fan_off_c"])
    temp_alert = float(thresholds["temp_alert_c"])
    cur_band = max(1.0, round(cur_on - cur_off, 2))

    explicit_both = new_fan_on_c is not None and new_fan_off_c is not None

    if new_fan_on_c is None and new_fan_off_c is None:
        # Mặc định mô phỏng kịch bản hạ ngưỡng bật quạt 2.0°C để làm mát sớm hơn
        sim_on = round(cur_on - 2.0, 2)
        sim_off = round(sim_on - cur_band, 2)
        inferred_note = f"Không nhận thấy con số ngưỡng cụ thể trong câu hỏi; tự động giả lập hạ fan_on_c từ {cur_on}°C xuống {sim_on}°C (và fan_off_c xuống {sim_off}°C)."
    elif new_fan_on_c is not None and new_fan_off_c is None:
        sim_on = round(float(new_fan_on_c), 2)
        # Nếu fan_off hiện tại >= sim_on thì gợi ý dịch fan_off theo độ trễ hiện tại
        if cur_off >= sim_on:
            sim_off = round(sim_on - cur_band, 2)
            inferred_note = (
                f"Do fan_off_c hiện tại ({cur_off}°C) >= mức fan_on_c giả lập ({sim_on}°C), "
                f"mô phỏng tự động điều chỉnh fan_off_c đề xuất xuống {sim_off}°C để giữ độ trễ nhiệt {cur_band}°C."
            )
        else:
            sim_off = cur_off
            inferred_note = None
    elif new_fan_on_c is None and new_fan_off_c is not None:
        sim_off = round(float(new_fan_off_c), 2)
        sim_on = cur_on
        inferred_note = None
    else:
        sim_on = round(float(new_fan_on_c), 2)
        sim_off = round(float(new_fan_off_c), 2)
        inferred_note = None

    warnings: list[str] = []
    if inferred_note:
        warnings.append(inferred_note)

    # 1. Kiểm tra miền giá trị vật lý [-40, 80]
    if not (-40.0 <= sim_on <= 80.0) or not (-40.0 <= sim_off <= 80.0):
        return {
            "valid": False,
            "error_code": "OUT_OF_RANGE",
            "current_thresholds": {"fan_on_c": cur_on, "fan_off_c": cur_off, "temp_alert_c": temp_alert},
            "proposed_thresholds": {"fan_on_c": sim_on, "fan_off_c": sim_off},
            "summary": (
                f"Cấu hình giả lập không hợp lệ: Ngưỡng nhiệt độ (fan_on_c={sim_on}°C, fan_off_c={sim_off}°C) "
                "nằm ngoài dải cho phép [-40°C, 80°C]."
            ),
            "warnings": ["Giá trị ngưỡng vượt ngoài giới hạn vật lý của cảm biến và bộ điều khiển."],
            "assumptions": ["Chỉ kiểm tra mô phỏng toán học, không áp dụng xuống thiết bị."],
        }

    # 2. Kiểm tra điều kiện trễ nhiệt fan_off_c < fan_on_c
    if sim_off >= sim_on:
        return {
            "valid": False,
            "error_code": "INVALID_HYSTERESIS",
            "current_thresholds": {"fan_on_c": cur_on, "fan_off_c": cur_off, "temp_alert_c": temp_alert},
            "proposed_thresholds": {"fan_on_c": sim_on, "fan_off_c": sim_off},
            "summary": (
                f"Cấu hình giả lập vi phạm nguyên tắc Hysteresis: fan_off_c ({sim_off}°C) phải nhỏ hơn nghiêm ngặt "
                f"so với fan_on_c ({sim_on}°C). Gợi ý đặt fan_off_c <= {round(sim_on - 1.5, 2)}°C."
            ),
            "warnings": [
                f"Nếu fan_off_c ({sim_off}°C) >= fan_on_c ({sim_on}°C), bộ điều khiển ESP32 và serializer cấu hình sẽ từ chối lưu."
            ],
            "assumptions": ["Chỉ kiểm tra mô phỏng toán học, không áp dụng xuống thiết bị."],
        }

    hysteresis_band = round(sim_on - sim_off, 2)
    delta_on = round(sim_on - cur_on, 2)
    delta_off = round(sim_off - cur_off, 2)

    if hysteresis_band < 1.0:
        warnings.append(
            f"Biên độ trễ nhiệt (fan_on_c - fan_off_c = {hysteresis_band}°C) quá hẹp (< 1.0°C), "
            "có nguy cơ làm rơ-le quạt đóng/cắt liên tục (chattering) quanh điểm cân bằng."
        )

    # 3. Đánh giá khoảng cách tới ngưỡng cảnh báo quá nhiệt temp_alert_c
    alert_margin = round(temp_alert - sim_on, 2)
    risk_level = "LOW"
    if sim_on >= temp_alert:
        risk_level = "CRITICAL"
        warnings.append(
            f"RỦI RO CAO: Ngưỡng bật quạt mới ({sim_on}°C) >= ngưỡng cảnh báo quá nhiệt ({temp_alert}°C). "
            "Tủ sẽ phát cảnh báo TEMPERATURE_HIGH trước khi quạt tự động bật!"
        )
    elif alert_margin < 1.5:
        risk_level = "HIGH"
        warnings.append(
            f"Khoảng đệm an toàn tới ngưỡng cảnh báo ({temp_alert}°C) chỉ còn {alert_margin}°C (< 1.5°C). "
            "Khi tải nhiệt tăng nhanh, quạt có thể không kịp hãm đà tăng nhiệt trước khi chạm ngưỡng cảnh báo."
        )

    # 4. Ước tính thời gian kích hoạt quạt dựa trên số liệu viễn trắc hiện tại
    cur_temp: float | None = None
    temp_slope: float | None = None
    if isinstance(forecast, dict):
        if forecast.get("current_temperature_c") is not None:
            cur_temp = float(forecast["current_temperature_c"])
        if forecast.get("temp_slope_per_min") is not None:
            temp_slope = float(forecast["temp_slope_per_min"])

    if cur_temp is None:
        try:
            latest_row = device.telemetries.order_by("-measured_at").values("temperature").first()
            if latest_row and latest_row.get("temperature") is not None:
                cur_temp = float(latest_row["temperature"])
            elif getattr(device, "latest_temperature", None) is not None:
                cur_temp = float(device.latest_temperature)
        except Exception:
            cur_temp = None

    immediate_trigger = bool(cur_temp is not None and cur_temp >= sim_on)
    cur_mins_to_on: float | None = None
    sim_mins_to_on: float | None = None
    shift_minutes: float | None = None

    if cur_temp is not None:
        if cur_temp >= cur_on:
            cur_mins_to_on = 0.0
        elif temp_slope is not None and temp_slope > 0.01:
            cur_mins_to_on = round((cur_on - cur_temp) / temp_slope, 1)

        if cur_temp >= sim_on:
            sim_mins_to_on = 0.0
        elif temp_slope is not None and temp_slope > 0.01:
            sim_mins_to_on = round((sim_on - cur_temp) / temp_slope, 1)

        if cur_mins_to_on is not None and sim_mins_to_on is not None:
            shift_minutes = round(sim_mins_to_on - cur_mins_to_on, 1)
        elif temp_slope is not None and temp_slope > 0.01:
            # Ước tính độ dịch thời gian thuần theo delta_on / temp_slope
            shift_minutes = round(delta_on / temp_slope, 1)

    # 5. Tổng hợp nhận định mô phỏng
    parts: list[str] = [
        f"Giả lập đổi ngưỡng từ (fan_on={cur_on}°C, fan_off={cur_off}°C) sang (fan_on={sim_on}°C, fan_off={sim_off}°C, biên trễ {hysteresis_band}°C):"
    ]
    if cur_temp is not None:
        if immediate_trigger and cur_temp < cur_on:
            parts.append(
                f"Với nhiệt độ hiện tại {cur_temp:.2f}°C, quạt sẽ KÍCH HOẠT NGAY LẬP TỨC ở chế độ AUTO (thay vì chờ tới {cur_on}°C)."
            )
        elif immediate_trigger and cur_temp >= cur_on:
            parts.append(
                f"Nhiệt độ hiện tại ({cur_temp:.2f}°C) đang cao hơn cả ngưỡng cũ lẫn ngưỡng mới ({sim_on}°C), quạt vẫn tiếp tục duy trì trạng thái BẬT."
            )
        elif not immediate_trigger and cur_temp >= cur_on:
            parts.append(
                f"Do tăng ngưỡng bật quạt lên {sim_on}°C (cao hơn nhiệt độ hiện tại {cur_temp:.2f}°C), quạt sẽ chưa kích hoạt ở mức nhiệt hiện tại."
            )
        elif shift_minutes is not None:
            direction_str = f"sớm hơn ~{abs(shift_minutes):.1f} phút" if shift_minutes < 0 else f"muộn hơn ~{abs(shift_minutes):.1f} phút"
            parts.append(
                f"Ở tốc độ tăng nhiệt hiện tại ({temp_slope:+.3f}°C/phút), quạt dự kiến kích hoạt {direction_str} (sau ~{sim_mins_to_on} phút)."
            )
        else:
            parts.append(
                f"Nhiệt độ hiện tại ({cur_temp:.2f}°C) thấp hơn ngưỡng bật quạt mới ({sim_on}°C) một khoảng {sim_on - cur_temp:.2f}°C."
            )

    if delta_on < 0:
        parts.append("Hạ ngưỡng bật quạt giúp làm mát chủ động sớm hơn nhưng sẽ làm tăng tần suất và thời gian chạy quạt (duty cycle).")
    elif delta_on > 0:
        parts.append("Tăng ngưỡng bật quạt giúp giảm thời gian quạt chạy nhưng làm nhiệt độ nền của tủ cao hơn và thu hẹp biên an toàn tới ngưỡng cảnh báo.")

    assumptions = [
        "Giả định tủ vận hành ở chế độ Tự động (AUTO) và tốc độ biến thiên nhiệt độ hiện tại không đổi.",
        "Mô phỏng tuyến tính bậc nhất, chưa tính quán tính nhiệt phi tuyến của vỏ tủ và nhiệt độ phòng máy bên ngoài.",
        "Chỉ là kết quả mô phỏng What-If (chỉ đọc) — cấu hình thực tế của thiết bị CHƯA bị thay đổi.",
    ]

    return {
        "valid": True,
        "error_code": None,
        "explicit_both_thresholds": explicit_both,
        "current_thresholds": {
            "fan_on_c": cur_on,
            "fan_off_c": cur_off,
            "temp_alert_c": temp_alert,
        },
        "proposed_thresholds": {
            "fan_on_c": sim_on,
            "fan_off_c": sim_off,
        },
        "fan_on_delta_c": delta_on,
        "fan_off_delta_c": delta_off,
        "hysteresis_band_c": hysteresis_band,
        "margin_to_temp_alert_c": alert_margin,
        "risk_level": risk_level,
        "current_temperature_c": round(cur_temp, 2) if cur_temp is not None else None,
        "temp_slope_per_min": round(temp_slope, 4) if temp_slope is not None else None,
        "immediate_fan_trigger": immediate_trigger,
        "current_minutes_to_fan_on": cur_mins_to_on,
        "simulated_minutes_to_fan_on": sim_mins_to_on,
        "trigger_time_shift_minutes": shift_minutes,
        "summary": " ".join(parts),
        "warnings": warnings,
        "assumptions": assumptions,
    }
