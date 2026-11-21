"""
Bộ khung trả lời dự phòng đa kịch bản (Multi-Variant Deterministic Fallback Templates - Pha 6).
Sinh câu trả lời tiếng Việt tự nhiên, khác biệt rõ rệt theo từng `answer_type` (10 loại),
tôn trọng cơ chế khóa dự báo (Forecast Gating) và tuyệt đối không nhồi số liệu nhiệt độ/cảnh báo
vào các câu hỏi SMALLTALK, OUT_OF_SCOPE hoặc EXPLAIN.
"""
from __future__ import annotations

import hashlib
from typing import Any

from apps.devices.ai_glossary import lookup_glossary_terms
from apps.devices.ai_intent_router import AnswerType, RouteDecision

MAX_SUMMARY_CHARS = 600
MAX_CAUSES_ITEMS = 5
MAX_ACTIONS_ITEMS = 4
MAX_ITEM_CHARS = 220


def _pick_variant(key: str, variants: list[str]) -> str:
    if not variants:
        return ""
    digest = hashlib.sha256((key or "default").encode("utf-8")).digest()
    idx = digest[0] % len(variants)
    return variants[idx]


def _clamp(text: str, max_len: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1].rstrip() + "…"


def _compute_severity(context: dict[str, Any]) -> str:
    alerts = context.get("recent_alerts") or []
    anomaly = context.get("anomaly_evaluation") or {}
    commands = context.get("recent_commands") or []
    forecast = context.get("real_time_forecast") or {}

    active_alerts = [a for a in alerts if a.get("status") in ("OPEN", "ACKNOWLEDGED")]
    failed_cmds = [c for c in commands if c.get("status") in ("TIMEOUT", "FAILED")]
    ai_status = anomaly.get("status", "NOT_APPLICABLE")

    if (
        any(a.get("severity") == "CRITICAL" for a in active_alerts)
        or ai_status == "CRITICAL"
        or (forecast.get("available") and forecast.get("estimated_minutes_to_temp_alert") == 0.0)
    ):
        return "CRITICAL"
    if (
        active_alerts
        or ai_status == "WARNING"
        or failed_cmds
        or (
            forecast.get("available")
            and forecast.get("estimated_minutes_to_temp_alert") is not None
            and float(forecast["estimated_minutes_to_temp_alert"]) <= 30.0
        )
    ):
        return "ATTENTION"
    return "NORMAL"


def _default_follow_ups(answer_type: AnswerType, context: dict[str, Any]) -> list[str]:
    mapping: dict[AnswerType, list[str]] = {
        "SMALLTALK": [
            "Tóm tắt tình trạng hiện tại của tủ",
            "Dự báo nhiệt độ 15–30 phút tới",
            "Giải thích cơ chế Hysteresis là gì?",
        ],
        "OUT_OF_SCOPE": [
            "Nhiệt độ và độ ẩm hiện tại của tủ là bao nhiêu?",
            "Phân tích hiệu suất làm mát của quạt",
            "Kiểm tra các lệnh điều khiển gần đây",
        ],
        "EXPLAIN": [
            "Giải thích cảnh báo COOLING_DEGRADATION là gì?",
            "Tại sao cần hai ngưỡng fan_on và fan_off?",
            "Tóm tắt tình trạng hiện tại của tủ",
        ],
        "STATUS": [
            "Dự báo xu hướng nhiệt độ 30 phút tới",
            "Phân tích nguyên nhân các cảnh báo đang mở",
            "Nếu hạ ngưỡng bật quạt xuống 28°C thì sao?",
        ],
        "FORECAST": [
            "Nếu hạ ngưỡng bật quạt xuống 28°C thì sao?",
            "Đánh giá hiệu suất làm mát của quạt",
            "Tôi nên làm gì để giảm nhiệt độ tủ?",
        ],
        "DIAGNOSE": [
            "Khuyến nghị các bước xử lý sự cố này",
            "Dự báo nhiệt độ tủ trong 15–30 phút tới",
            "Giải thích các lệnh TIMEOUT / FAILED gần đây",
        ],
        "WHAT_IF": [
            "Dự báo nhiệt độ 15–30 phút tới với cấu hình hiện tại",
            "Giải thích cơ chế trễ nhiệt Hysteresis là gì?",
            "Tóm tắt tình trạng hiện tại của tủ",
        ],
        "COMMANDS": [
            "Tóm tắt tình trạng kết nối và cảnh báo của tủ",
            "Tôi nên làm gì khi lệnh MQTT bị TIMEOUT?",
            "Đánh giá hiệu suất làm mát của quạt",
        ],
        "COMPARE": [
            "Dự báo xu hướng nhiệt độ 30 phút tới",
            "Phân tích hiệu suất làm mát của quạt",
            "Nếu hạ ngưỡng bật quạt xuống 28°C thì sao?",
        ],
        "ADVICE": [
            "Phân tích nguyên nhân gốc rễ của sự cố",
            "Nếu hạ ngưỡng bật quạt xuống 28°C thì sao?",
            "Kiểm tra lịch sử lệnh điều khiển gần đây",
        ],
    }
    return mapping.get(answer_type, mapping["STATUS"])[:3]


def build_adaptive_fallback_response(
    context: dict[str, Any],
    question: str,
    route: RouteDecision,
) -> dict[str, Any]:
    """
    Sinh phản hồi thích ứng theo `route.answer_type` ở Chế độ cơ bản (không dùng LLM).
    Đảm bảo tương thích ngược với cả `details` và `possible_causes`.
    """
    ans_type: AnswerType = route.answer_type
    norm_q = route.normalized_question or question

    # 1. SMALLTALK: Chỉ chào hỏi và giới thiệu ngắn gọn khả năng trợ lý, 0 số liệu tủ
    if ans_type == "SMALLTALK":
        if any(k in norm_q for k in ("cam on", "thanks", "thank")):
            variants = [
                "Rất vui vì đã hỗ trợ được bạn! Nếu cần tra cứu trạng thái tủ mạng, dự báo nhiệt độ hoặc giả lập đổi ngưỡng quạt, bạn cứ nhắn bất cứ lúc nào.",
                "Không có gì ạ! Tôi luôn sẵn sàng hỗ trợ bạn giám sát thông số, chẩn đoán cảnh báo hoặc giải thích thuật ngữ vận hành tủ mạng.",
            ]
        elif any(k in norm_q for k in ("tam biet", "bye", "hen gap lai")):
            variants = [
                "Tạm biệt bạn! Hệ thống giám sát và phát hiện bất thường AI Hướng 1 vẫn tiếp tục theo dõi tủ mạng tự động 24/7.",
                "Chào tạm biệt! Hẹn gặp lại bạn trong các phiên kiểm tra vận hành tủ mạng tiếp theo.",
            ]
        elif any(k in norm_q for k in ("la ai", "ten gi", "lam duoc gi", "giup duoc gi", "chuc nang")):
            variants = [
                "Tôi là Trợ lý AI vận hành tủ mạng IoT (chế độ chỉ đọc). Tôi có thể giúp bạn: (1) Xem nhanh nhiệt độ, độ ẩm, trạng thái quạt; (2) Chẩn đoán suy giảm làm mát & lỗi cảm biến; (3) Dự báo nhiệt độ 5–30 phút tới; (4) Giả lập What-If khi đổi ngưỡng fan_on/fan_off; và (5) Giải thích thuật ngữ kỹ thuật.",
                "Chào bạn! Tôi là Trợ lý AI chuyên trách phân tích kỹ thuật tủ mạng ESP32. Bạn có thể hỏi tôi về tình trạng hiện tại, nguyên nhân cảnh báo, lịch sử lệnh MQTT TIMEOUT/FAILED, hoặc thử kịch bản hạ ngưỡng bật quạt.",
            ]
        else:
            variants = [
                "Xin chào! Tôi là Trợ lý AI hỗ trợ giám sát và vận hành tủ mạng. Bạn muốn kiểm tra thông số hiện tại, dự báo nhiệt độ 15–30 phút tới, hay phân tích nguyên nhân cảnh báo?",
                "Chào bạn! Tôi có thể hỗ trợ tra cứu nhanh tình trạng tủ mạng, đánh giá hiệu suất quạt tản nhiệt, giả lập thay đổi ngưỡng cấu hình hoặc giải thích các chỉ số AI. Bạn cần kiểm tra nội dung nào?",
                "Xin chào người vận hành! Hãy chọn một gợi ý bên dưới hoặc đặt câu hỏi về nhiệt độ, độ ẩm, quạt, lệnh điều khiển hay thuật ngữ kỹ thuật của tủ mạng.",
            ]
        summary = _clamp(_pick_variant(norm_q, variants), MAX_SUMMARY_CHARS)
        return {
            "answer_type": "SMALLTALK",
            "summary": summary,
            "details": [],
            "possible_causes": [],
            "recommended_actions": [],
            "severity_assessment": None,
            "follow_up_suggestions": _default_follow_ups("SMALLTALK", context),
            "data_used": [],
        }

    # 2. OUT_OF_SCOPE: Từ chối lịch sự câu hỏi ngoài phạm vi IoT hoặc chuỗi gõ nhầm, 0 số liệu tủ
    if ans_type == "OUT_OF_SCOPE":
        if route.matched_rule == "gibberish_input":
            variants = [
                "Tôi chưa hiểu rõ nội dung bạn vừa nhập. Bạn vui lòng nhập câu hỏi rõ ràng bằng tiếng Việt hoặc chọn một gợi ý bên dưới (vd: trạng thái tủ, dự báo nhiệt độ, chẩn đoán quạt, giả lập ngưỡng).",
                "Nội dung bạn nhập có vẻ bị gõ nhầm hoặc chưa rõ ý. Hãy thử hỏi tôi về nhiệt độ, độ ẩm, cảnh báo, lệnh MQTT hoặc giải thích thuật ngữ của tủ mạng nhé.",
            ]
        else:
            variants = [
                "Câu hỏi này nằm ngoài phạm vi chuyên môn của Trợ lý vận hành tủ mạng IoT. Tôi chỉ hỗ trợ phân tích nhiệt độ, độ ẩm, quạt thông gió, cảnh báo bất thường, lệnh MQTT và cấu hình ngưỡng của tủ mạng.",
                "Xin lỗi, tôi chỉ được thiết kế để hỗ trợ kỹ thuật cho hệ thống giám sát và điều khiển thông gió tủ mạng. Bạn có thể hỏi tôi về trạng thái thiết bị, dự báo nhiệt độ hoặc chẩn đoán sự cố làm mát.",
            ]
        summary = _clamp(_pick_variant(norm_q, variants), MAX_SUMMARY_CHARS)
        return {
            "answer_type": "OUT_OF_SCOPE",
            "summary": summary,
            "details": [],
            "possible_causes": [],
            "recommended_actions": [],
            "severity_assessment": None,
            "follow_up_suggestions": _default_follow_ups("OUT_OF_SCOPE", context),
            "data_used": [],
        }

    # 3. EXPLAIN: Giải thích khái niệm kỹ thuật từ từ điển tĩnh, không trộn số liệu tủ
    if ans_type == "EXPLAIN":
        terms = lookup_glossary_terms(question, limit=3)
        primary = terms[0]
        summary = _clamp(f"{primary['title']}: {primary['definition']}", MAX_SUMMARY_CHARS)
        details = [
            _clamp(f"{item['title']}: {item['definition']}", MAX_ITEM_CHARS)
            for item in terms[1:]
        ]
        return {
            "answer_type": "EXPLAIN",
            "summary": summary,
            "details": details,
            "possible_causes": list(details),
            "recommended_actions": [],
            "severity_assessment": None,
            "follow_up_suggestions": _default_follow_ups("EXPLAIN", context),
            "data_used": ["glossary"],
        }

    # Các câu hỏi nghiệp vụ tủ mạng bên dưới sử dụng ngữ cảnh thiết bị
    dev = context.get("device") or {}
    tel = context.get("telemetry_summary") or {}
    alerts = context.get("recent_alerts") or []
    commands = context.get("recent_commands") or []
    configs = context.get("config_changes") or []
    anomaly = context.get("anomaly_evaluation") or {}
    forecast = context.get("real_time_forecast") or {}
    simulation = context.get("what_if_simulation") or {}
    peer_stats = context.get("peer_comparison") or {}

    serial = dev.get("id") or dev.get("serial") or "Tủ mạng"
    conn = dev.get("connection_status") or "OFFLINE"
    status = dev.get("status") or "ACTIVE"
    hours = tel.get("window_hours", 24)
    sample_count = int(tel.get("sample_count") or 0)
    cur_temp = forecast.get("current_temperature_c")
    if cur_temp is None:
        cur_temp = tel.get("temp_avg")
    cur_humid = forecast.get("current_humidity_pct")
    if cur_humid is None:
        cur_humid = tel.get("humid_avg")

    active_alerts = [a for a in alerts if a.get("status") in ("OPEN", "ACKNOWLEDGED")]
    failed_cmds = [c for c in commands if c.get("status") in ("TIMEOUT", "FAILED")]
    severity = _compute_severity(context)

    # 4. WHAT_IF: Giả lập thay đổi ngưỡng cấu hình
    if ans_type == "WHAT_IF":
        sim_summary = simulation.get("summary") or "Không đủ thông số để mô phỏng kịch bản thay đổi ngưỡng."
        details: list[str] = []
        cur_th = simulation.get("current_thresholds") or {}
        prop_th = simulation.get("proposed_thresholds") or {}
        if cur_th and prop_th:
            details.append(
                _clamp(
                    f"Ngưỡng hiện tại: fan_on={cur_th.get('fan_on_c')}°C, fan_off={cur_th.get('fan_off_c')}°C "
                    f"(cảnh báo quá nhiệt temp_alert={cur_th.get('temp_alert_c')}°C).",
                    MAX_ITEM_CHARS,
                )
            )
            details.append(
                _clamp(
                    f"Ngưỡng giả lập: fan_on={prop_th.get('fan_on_c')}°C, fan_off={prop_th.get('fan_off_c')}°C "
                    f"(biên độ trễ nhiệt {simulation.get('hysteresis_band_c', '--')}°C).",
                    MAX_ITEM_CHARS,
                )
            )
        for w in simulation.get("warnings") or []:
            if len(details) < MAX_CAUSES_ITEMS:
                details.append(_clamp(str(w), MAX_ITEM_CHARS))
        for a in simulation.get("assumptions") or []:
            if len(details) < MAX_CAUSES_ITEMS:
                details.append(_clamp(f"Giả định: {a}", MAX_ITEM_CHARS))

        actions = [
            _clamp(
                "Nếu đồng ý với kịch bản mô phỏng, mở tab 'Cấu hình ngưỡng' để cập nhật fan_on_c / fan_off_c (Trợ lý AI không tự động ghi cấu hình).",
                MAX_ITEM_CHARS,
            ),
            _clamp(
                "Đảm bảo fan_off_c luôn thấp hơn fan_on_c tối thiểu 1.5°C–2.0°C để tránh rơ-le quạt đóng cắt liên tục.",
                MAX_ITEM_CHARS,
            ),
        ]
        sim_sev = "CRITICAL" if simulation.get("risk_level") == "CRITICAL" else (
            "ATTENTION" if not simulation.get("valid", True) or simulation.get("risk_level") == "HIGH" else "NORMAL"
        )
        return {
            "answer_type": "WHAT_IF",
            "summary": _clamp(sim_summary, MAX_SUMMARY_CHARS),
            "details": details[:MAX_CAUSES_ITEMS],
            "possible_causes": details[:MAX_CAUSES_ITEMS],
            "recommended_actions": actions[:MAX_ACTIONS_ITEMS],
            "severity_assessment": sim_sev,
            "follow_up_suggestions": _default_follow_ups("WHAT_IF", context),
            "data_used": ["telemetry_summary", "config_thresholds", "what_if_simulation"],
        }

    # 5. FORECAST: Dự báo xu hướng ngắn hạn (+5m, +15m, +30m) có Gating
    if ans_type == "FORECAST":
        if not forecast.get("available"):
            reason_code = forecast.get("reason") or "UNAVAILABLE"
            reason_text = forecast.get("predictive_assessment") or "Không đủ điều kiện ngoại suy dự báo."
            summary = _clamp(f"Tạm khóa dự báo định lượng ({reason_code}): {reason_text}", MAX_SUMMARY_CHARS)
            details = [
                _clamp(f"Mã lý do khóa dự báo: {reason_code}.", MAX_ITEM_CHARS),
                _clamp(
                    f"Trạng thái thiết bị: vòng đời {status}, kết nối {conn}, số mẫu {hours}h qua: {sample_count}.",
                    MAX_ITEM_CHARS,
                ),
            ]
            actions = [
                _clamp(
                    "Đảm bảo tủ đang ONLINE, ở chế độ AUTO và đã tích lũy tối thiểu 12 mẫu đo liên tục trong 10 phút trước khi yêu cầu dự báo.",
                    MAX_ITEM_CHARS,
                )
            ]
            return {
                "answer_type": "FORECAST",
                "summary": summary,
                "details": details,
                "possible_causes": list(details),
                "recommended_actions": actions,
                "severity_assessment": severity,
                "follow_up_suggestions": _default_follow_ups("FORECAST", context),
                "data_used": ["device", "real_time_forecast"],
            }

        ci = forecast.get("confidence_intervals") or {}
        ci5 = ci.get("5m") or [forecast.get("projected_temp_5m_c"), forecast.get("projected_temp_5m_c")]
        ci15 = ci.get("15m") or [forecast.get("projected_temp_15m_c"), forecast.get("projected_temp_15m_c")]
        ci30 = ci.get("30m") or [forecast.get("projected_temp_30m_c"), forecast.get("projected_temp_30m_c")]
        slope = forecast.get("temp_slope_per_min", 0.0)
        std_c = forecast.get("residual_std_c", 0.0)
        summary = _clamp(
            f"Dự báo quỹ đạo nhiệt ngắn hạn dựa trên {forecast.get('basis_samples')} mẫu gần nhất: "
            f"{forecast.get('predictive_assessment')}",
            MAX_SUMMARY_CHARS,
        )
        details = [
            _clamp(
                f"Mốc +5 phút: {forecast.get('projected_temp_5m_c')}°C (khoảng tin cậy 95%: [{ci5[0]}, {ci5[1]}]°C).",
                MAX_ITEM_CHARS,
            ),
            _clamp(
                f"Mốc +15 phút: {forecast.get('projected_temp_15m_c')}°C (khoảng tin cậy 95%: [{ci15[0]}, {ci15[1]}]°C); độ ẩm dự kiến {forecast.get('projected_humidity_15m_pct')} %.",
                MAX_ITEM_CHARS,
            ),
            _clamp(
                f"Mốc +30 phút: {forecast.get('projected_temp_30m_c')}°C (khoảng tin cậy 95%: [{ci30[0]}, {ci30[1]}]°C); tốc độ biến thiên {slope:+.4f}°C/phút, sai số chuẩn phần dư σ={std_c}°C.",
                MAX_ITEM_CHARS,
            ),
        ]
        if forecast.get("capped"):
            details.append(
                _clamp(
                    f"Lưu ý: Giá trị ngoại suy đã được giới hạn ở trần vật lý {forecast.get('physical_ceiling_c')}°C.",
                    MAX_ITEM_CHARS,
                )
            )
        actions = []
        if forecast.get("estimated_minutes_to_temp_alert") is not None:
            actions.append(
                _clamp(
                    "Theo dõi sát tải nhiệt và kiểm tra lưu lượng gió quạt vì quỹ đạo hiện tại có nguy cơ chạm ngưỡng cảnh báo quá nhiệt.",
                    MAX_ITEM_CHARS,
                )
            )
        else:
            actions.append(
                _clamp(
                    "Duy trì chế độ AUTO và tiếp tục giám sát biểu đồ nhiệt độ theo thời gian thực.",
                    MAX_ITEM_CHARS,
                )
            )
        return {
            "answer_type": "FORECAST",
            "summary": summary,
            "details": details[:MAX_CAUSES_ITEMS],
            "possible_causes": details[:MAX_CAUSES_ITEMS],
            "recommended_actions": actions[:MAX_ACTIONS_ITEMS],
            "severity_assessment": severity,
            "follow_up_suggestions": _default_follow_ups("FORECAST", context),
            "data_used": ["telemetry_summary", "real_time_forecast", "config_thresholds"],
        }

    # 6. COMMANDS: Phân tích lịch sử lệnh điều khiển & cấu hình MQTT
    if ans_type == "COMMANDS":
        total_cmds = len(commands)
        if failed_cmds:
            summary = _clamp(
                f"Nhật ký truyền lệnh của {serial} trong {hours}h qua ghi nhận {len(failed_cmds)}/{total_cmds} lệnh gặp sự cố "
                f"(TIMEOUT hoặc FAILED). Nguyên nhân phổ biến là thiết bị mất kết nối MQTT hoặc không phản hồi gói tin ACK đúng hạn.",
                MAX_SUMMARY_CHARS,
            )
        elif total_cmds > 0:
            summary = _clamp(
                f"Tất cả {total_cmds} lệnh điều khiển và cấu hình gần nhất của {serial} trong {hours}h qua đều thực thi thành công (ACKED), không có lệnh TIMEOUT hoặc FAILED.",
                MAX_SUMMARY_CHARS,
            )
        else:
            summary = _clamp(
                f"Không ghi nhận lệnh điều khiển hoặc cấu hình nào được gửi tới {serial} trong {hours}h qua.",
                MAX_SUMMARY_CHARS,
            )

        details = []
        for cmd in failed_cmds[:3]:
            cmd_name = cmd.get("command_type") or cmd.get("command")
            err = f" - chi tiết: {cmd['error_message']}" if cmd.get("error_message") else ""
            details.append(
                _clamp(
                    f"Lệnh {cmd_name} ở trạng thái {cmd.get('status')}{err}.",
                    MAX_ITEM_CHARS,
                )
            )
        for cmd in [c for c in commands if c.get("status") not in ("TIMEOUT", "FAILED")][:2]:
            if len(details) < MAX_CAUSES_ITEMS:
                cmd_name = cmd.get("command_type") or cmd.get("command")
                details.append(
                    _clamp(
                        f"Lệnh {cmd_name} trạng thái {cmd.get('status')} lúc {cmd.get('created_at')}.",
                        MAX_ITEM_CHARS,
                    )
                )
        if not details:
            details.append(_clamp(f"Trạng thái kết nối hiện tại của thiết bị: {conn}.", MAX_ITEM_CHARS))

        actions = []
        if failed_cmds or conn == "OFFLINE":
            actions.append(
                _clamp(
                    "Kiểm tra kết nối Wi-Fi/Ethernet của ESP32 với MQTT Broker và nguồn cấp 5V/12V trước khi phát lại lệnh.",
                    MAX_ITEM_CHARS,
                )
            )
            actions.append(
                _clamp(
                    "Đối chiếu topic subscribe command và cơ chế gửi ACK JSON trên firmware ESP32.",
                    MAX_ITEM_CHARS,
                )
            )
        else:
            actions.append(
                _clamp("Kênh truyền lệnh MQTT đang hoạt động ổn định, không cần can thiệp.", MAX_ITEM_CHARS)
            )
        return {
            "answer_type": "COMMANDS",
            "summary": summary,
            "details": details[:MAX_CAUSES_ITEMS],
            "possible_causes": details[:MAX_CAUSES_ITEMS],
            "recommended_actions": actions[:MAX_ACTIONS_ITEMS],
            "severity_assessment": "ATTENTION" if failed_cmds else "NORMAL",
            "follow_up_suggestions": _default_follow_ups("COMMANDS", context),
            "data_used": ["device", "recent_commands"],
        }

    # 7. COMPARE: Đối chiếu lịch sử 24h & mặt bằng chung các tủ khác
    if ans_type == "COMPARE":
        peer_avg = peer_stats.get("fleet_avg_temp_c")
        peer_count = peer_stats.get("online_device_count", 0)
        t_avg = tel.get("temp_avg")
        t_min = tel.get("temp_min")
        t_max = tel.get("temp_max")
        diff_str = ""
        if t_avg is not None and peer_avg is not None:
            delta = round(float(t_avg) - float(peer_avg), 2)
            cmp_word = "cao hơn" if delta > 0 else ("thấp hơn" if delta < 0 else "bằng")
            diff_str = f" So với nhiệt độ trung bình toàn hệ thống ({peer_avg}°C trên {peer_count} tủ), tủ này đang {cmp_word} {abs(delta):.2f}°C."

        summary = _clamp(
            f"Đối chiếu thông số {serial} trong {hours}h qua ({sample_count} mẫu): nhiệt độ trung bình {t_avg if t_avg is not None else '--'}°C "
            f"(dao động từ {t_min if t_min is not None else '--'}°C đến {t_max if t_max is not None else '--'}°C), "
            f"tỷ lệ bật quạt {int(round(float(tel.get('fan_on_ratio') or 0) * 100))}%.{diff_str}",
            MAX_SUMMARY_CHARS,
        )
        details = [
            _clamp(
                f"Biên độ nhiệt 24h của tủ: thấp nhất {t_min}°C, trung bình {t_avg}°C, cao nhất {t_max}°C.",
                MAX_ITEM_CHARS,
            ),
            _clamp(
                f"Biên độ độ ẩm 24h của tủ: thấp nhất {tel.get('humid_min')}%, trung bình {tel.get('humid_avg')}%, cao nhất {tel.get('humid_max')}%.",
                MAX_ITEM_CHARS,
            ),
        ]
        if peer_stats:
            details.append(
                _clamp(
                    f"Thống kê toàn hệ thống: {peer_count} tủ đang hoạt động, nhiệt độ trung bình {peer_avg}°C, tủ nóng nhất đạt {peer_stats.get('fleet_max_temp_c')}°C.",
                    MAX_ITEM_CHARS,
                )
            )
        actions = [
            _clamp(
                "Nếu nhiệt độ trung bình của tủ cao hơn đáng kể (> 3°C) so với các tủ cùng phòng máy, cần kiểm tra mật độ thiết bị mạng và hướng gió điều hòa.",
                MAX_ITEM_CHARS,
            )
        ]
        return {
            "answer_type": "COMPARE",
            "summary": summary,
            "details": details[:MAX_CAUSES_ITEMS],
            "possible_causes": details[:MAX_CAUSES_ITEMS],
            "recommended_actions": actions[:MAX_ACTIONS_ITEMS],
            "severity_assessment": severity,
            "follow_up_suggestions": _default_follow_ups("COMPARE", context),
            "data_used": ["telemetry_summary", "peer_comparison", "recent_alerts"],
        }

    # 8. ADVICE: Khuyến nghị các bước xử lý & quy trình bảo trì vận hành
    if ans_type == "ADVICE":
        ai_type = anomaly.get("anomaly_type") or anomaly.get("alert_type")
        actions = []
        details = []
        if conn == "OFFLINE":
            actions.append(_clamp("Bước 1: Kiểm tra nguồn cấp điện, cáp mạng/Wi-Fi của node ESP32 và trạng thái MQTT Broker.", MAX_ITEM_CHARS))
        if ai_type == "COOLING_DEGRADATION" or any(a.get("alert_type") == "COOLING_DEGRADATION" for a in active_alerts):
            actions.append(_clamp("Bước 1: Kiểm tra trực tiếp quạt tản nhiệt, vệ sinh lưới lọc bụi và khe thoát gió của tủ mạng.", MAX_ITEM_CHARS))
            actions.append(_clamp("Bước 2: Kiểm tra nhiệt độ phòng máy (ambient) và mật độ tỏa nhiệt của switch/router bên trong tủ.", MAX_ITEM_CHARS))
        if ai_type == "SENSOR_ANOMALY" or any(a.get("alert_type") == "SENSOR_ANOMALY" for a in active_alerts):
            actions.append(_clamp("Bước 1: Kiểm tra dây tín hiệu, nguồn 3.3V và điện trở kéo lên (pull-up) của cảm biến DHT22/SHT3x.", MAX_ITEM_CHARS))
        if active_alerts:
            actions.append(_clamp(f"Xác nhận (ACK) và xử lý {len(active_alerts)} cảnh báo đang mở trên trang Cảnh báo sau khi kiểm tra hiện trường.", MAX_ITEM_CHARS))
        if failed_cmds:
            actions.append(_clamp("Kiểm tra độ trễ mạng MQTT trước khi gửi lại các lệnh điều khiển bị TIMEOUT/FAILED.", MAX_ITEM_CHARS))
        if not actions:
            actions.append(_clamp("Duy trì chế độ điều khiển Tự động (AUTO) và lịch vệ sinh định kỳ lưới lọc gió mỗi tháng.", MAX_ITEM_CHARS))
            actions.append(_clamp("Theo dõi chỉ số ml_score và độ dốc nhiệt temp_slope_per_min để phát hiện sớm suy giảm làm mát.", MAX_ITEM_CHARS))

        details.append(
            _clamp(
                f"Mức độ ưu tiên xử lý hiện tại: {severity} (kết nối {conn}, {len(active_alerts)} cảnh báo mở, {len(failed_cmds)} lệnh lỗi).",
                MAX_ITEM_CHARS,
            )
        )
        if anomaly.get("reason"):
            details.append(_clamp(f"Nhận định từ AI Hướng 1: {anomaly.get('reason')}", MAX_ITEM_CHARS))

        summary = _clamp(
            f"Quy trình khuyến nghị vận hành cho {serial} (mức độ {severity}): ưu tiên thực hiện {len(actions[:MAX_ACTIONS_ITEMS])} bước kiểm tra và bảo trì bên dưới. "
            "Lưu ý: Trợ lý AI chỉ đưa ra hướng dẫn kỹ thuật, không tự động phát lệnh xuống phần cứng.",
            MAX_SUMMARY_CHARS,
        )
        return {
            "answer_type": "ADVICE",
            "summary": summary,
            "details": details[:MAX_CAUSES_ITEMS],
            "possible_causes": details[:MAX_CAUSES_ITEMS],
            "recommended_actions": actions[:MAX_ACTIONS_ITEMS],
            "severity_assessment": severity,
            "follow_up_suggestions": _default_follow_ups("ADVICE", context),
            "data_used": ["device", "recent_alerts", "anomaly_evaluation", "recent_commands"],
        }

    # 9. STATUS: Trạng thái hiện tại / tóm tắt nhanh
    if ans_type == "STATUS":
        fan_ratio_pct = int(round(float(tel.get("fan_on_ratio") or 0.0) * 100))
        fan_state_str = "ĐANG BẬT" if forecast.get("latest_fan_state") else "ĐANG TẮT"
        mode_str = forecast.get("latest_mode") or "AUTO"
        summary = _clamp(
            f"Trạng thái hiện tại của {serial} ({status}, {conn}): nhiệt độ {cur_temp if cur_temp is not None else '--'}°C, "
            f"độ ẩm {cur_humid if cur_humid is not None else '--'}%, quạt {fan_state_str} (chế độ {mode_str}). "
            f"Trong {hours}h qua ghi nhận {sample_count} mẫu đo (tỷ lệ quạt chạy {fan_ratio_pct}%) và {len(active_alerts)} cảnh báo đang mở.",
            MAX_SUMMARY_CHARS,
        )
        details = [
            _clamp(
                f"Thống kê {hours}h: nhiệt độ trung bình {tel.get('temp_avg')}°C (min {tel.get('temp_min')}°C, max {tel.get('temp_max')}°C); độ ẩm trung bình {tel.get('humid_avg')}%.",
                MAX_ITEM_CHARS,
            ),
            _clamp(
                f"Đánh giá AI Hướng 1 (IsolationForest): trạng thái {anomaly.get('status', 'NOT_APPLICABLE')} — {anomaly.get('reason', 'Không có ghi chú')}.",
                MAX_ITEM_CHARS,
            ),
        ]
        for a in active_alerts[:2]:
            details.append(
                _clamp(
                    f"Cảnh báo đang mở: {a.get('alert_type')} ({a.get('severity')}) — giá trị {a.get('current_value')}, ngưỡng {a.get('threshold')}.",
                    MAX_ITEM_CHARS,
                )
            )
        if configs:
            c0 = configs[0]
            details.append(
                _clamp(
                    f"Cấu hình hiện hành v{c0.get('version')}: fan_on={c0.get('fan_on_c')}°C, fan_off={c0.get('fan_off_c')}°C, temp_alert={c0.get('temp_alert_c')}°C.",
                    MAX_ITEM_CHARS,
                )
            )
        actions = []
        if active_alerts:
            actions.append(_clamp("Kiểm tra và xử lý các cảnh báo đang ở trạng thái OPEN/ACKNOWLEDGED.", MAX_ITEM_CHARS))
        else:
            actions.append(_clamp("Hệ thống đang vận hành trong ngưỡng an toàn, tiếp tục duy trì giám sát tự động.", MAX_ITEM_CHARS))
        return {
            "answer_type": "STATUS",
            "summary": summary,
            "details": details[:MAX_CAUSES_ITEMS],
            "possible_causes": details[:MAX_CAUSES_ITEMS],
            "recommended_actions": actions[:MAX_ACTIONS_ITEMS],
            "severity_assessment": severity,
            "follow_up_suggestions": _default_follow_ups("STATUS", context),
            "data_used": ["device", "telemetry_summary", "recent_alerts", "anomaly_evaluation"],
        }

    # 10. DIAGNOSE: Chẩn đoán nguyên nhân sự cố / hiệu suất làm mát / lỗi cảm biến
    ai_status = anomaly.get("status", "NOT_APPLICABLE")
    ai_reason = anomaly.get("reason") or "Chưa phát hiện dấu hiệu bất thường."
    ai_type = anomaly.get("anomaly_type") or anomaly.get("alert_type")
    causes: list[str] = []
    if conn == "OFFLINE":
        causes.append(
            _clamp("Thiết bị đang mất kết nối (OFFLINE); có thể do mất nguồn, gián đoạn Wi-Fi hoặc mất kết nối tới MQTT Broker.", MAX_ITEM_CHARS)
        )
    if ai_status in ("WARNING", "CRITICAL") and anomaly.get("reason"):
        causes.append(_clamp(f"Phát hiện từ IsolationForest + Luật vật lý: {anomaly['reason']}", MAX_ITEM_CHARS))
    for alert in active_alerts[:3]:
        causes.append(
            _clamp(
                f"Cảnh báo {alert.get('alert_type')} ({alert.get('severity')}) đang {alert.get('status')} (giá trị: {alert.get('current_value')}, ngưỡng: {alert.get('threshold')}).",
                MAX_ITEM_CHARS,
            )
        )
    for cmd in failed_cmds[:2]:
        cmd_name = cmd.get("command_type") or cmd.get("command")
        err = f" ({cmd['error_message']})" if cmd.get("error_message") else ""
        causes.append(
            _clamp(f"Lệnh {cmd_name} gần đây bị {cmd.get('status')}{err}.", MAX_ITEM_CHARS)
        )
    if not causes:
        causes.append(_clamp(f"Kết quả đánh giá bất thường AI Hướng 1: {ai_status} — {ai_reason}", MAX_ITEM_CHARS))

    actions = []
    if conn == "OFFLINE":
        actions.append(_clamp("Kiểm tra nguồn cấp thiết bị, kết nối mạng của ESP32 và trạng thái MQTT Broker.", MAX_ITEM_CHARS))
    if ai_type == "COOLING_DEGRADATION" or any(
        a.get("alert_type") == "COOLING_DEGRADATION" for a in active_alerts
    ):
        actions.append(_clamp("Kiểm tra lưu lượng gió quạt tản nhiệt, vệ sinh lưới lọc bụi và khe thoát nhiệt của tủ mạng.", MAX_ITEM_CHARS))
    if ai_type == "SENSOR_ANOMALY" or any(
        a.get("alert_type") == "SENSOR_ANOMALY" for a in active_alerts
    ):
        actions.append(_clamp("Kiểm tra dây kết nối cảm biến DHT22/SHT3x, nguồn cấp cảm biến và nhiễu điện từ trong tủ.", MAX_ITEM_CHARS))
    if failed_cmds:
        actions.append(_clamp("Kiểm tra độ ổn định kết nối MQTT và phản hồi ACK trước khi gửi lại lệnh điều khiển.", MAX_ITEM_CHARS))
    if active_alerts:
        actions.append(_clamp("Xem xét và xử lý các cảnh báo đang ở trạng thái OPEN/ACKNOWLEDGED trên trang Cảnh báo.", MAX_ITEM_CHARS))
    if not actions:
        actions.append(_clamp("Tiếp tục duy trì chế độ vận hành tự động (AUTO) và theo dõi định kỳ.", MAX_ITEM_CHARS))

    summary = _clamp(
        f"Chẩn đoán kỹ thuật cho {serial} ({conn}, mức độ {severity}): trạng thái AI Hướng 1 là {ai_status} ({ai_reason}) "
        f"Hiện có {len(active_alerts)} cảnh báo đang mở và {len(failed_cmds)} lệnh lỗi/timeout trong {hours}h qua.",
        MAX_SUMMARY_CHARS,
    )
    return {
        "answer_type": "DIAGNOSE",
        "summary": summary,
        "details": causes[:MAX_CAUSES_ITEMS],
        "possible_causes": causes[:MAX_CAUSES_ITEMS],
        "recommended_actions": actions[:MAX_ACTIONS_ITEMS],
        "severity_assessment": severity,
        "follow_up_suggestions": _default_follow_ups("DIAGNOSE", context),
        "data_used": ["device", "telemetry_summary", "recent_alerts", "anomaly_evaluation", "recent_commands"],
    }
