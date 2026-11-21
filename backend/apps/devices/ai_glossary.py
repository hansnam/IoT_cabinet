"""
Từ điển thuật ngữ kỹ thuật tĩnh (Static Technical Glossary) cho Trợ lý AI V2 (Adaptive Copilot).
Dùng cho intent EXPLAIN để giải thích khái niệm hệ thống bằng tiếng Việt dễ hiểu (<= 300 ký tự/mục)
mà không trộn lẫn số liệu nhiệt độ/cảnh báo tức thời của tủ mạng.
"""
from __future__ import annotations

import unicodedata
from typing import Any


def _strip_accents(text: str) -> str:
    if not text:
        return ""
    text = text.replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn").lower()


GLOSSARY_ENTRIES: list[dict[str, Any]] = [
    {
        "term": "ml_score",
        "title": "Điểm bất thường học máy (ml_score)",
        "keywords": ["ml_score", "ml score", "diem bat thuong", "diem ml", "decision function"],
        "definition": (
            "ml_score là điểm đầu ra từ hàm decision_function của mô hình IsolationForest trên cửa sổ 10 phút. "
            "Giá trị càng âm (thấp hơn ngưỡng -0.05) thì hành vi nhiệt độ - độ ẩm - quạt càng khác biệt so với trạng thái vận hành bình thường."
        ),
    },
    {
        "term": "IsolationForest",
        "title": "Mô hình IsolationForest (Hướng 1)",
        "keywords": ["isolationforest", "isolation forest", "mo hinh rung co lap", "huong 1", "ai huong 1"],
        "definition": (
            "IsolationForest là thuật toán học máy không giám sát dùng cây quyết định ngẫu nhiên để cô lập điểm dữ liệu dị biệt "
            "từ 7 đặc trưng vật lý (độ dốc nhiệt/ẩm, độ lệch chuẩn, bước nhảy cực đại, tỷ lệ quạt bật và thời gian vượt ngưỡng)."
        ),
    },
    {
        "term": "temp_slope_per_min",
        "title": "Tốc độ biến thiên nhiệt độ (temp_slope_per_min)",
        "keywords": ["temp_slope_per_min", "temp slope", "toc do tang nhiet", "do doc nhiet do", "slope nhiet"],
        "definition": (
            "temp_slope_per_min (°C/phút) là hệ số góc hồi quy tuyến tính của nhiệt độ theo thời gian trong cửa sổ quan sát. "
            "Giá trị dương nghĩa là nhiệt độ đang tăng; giá trị âm nghĩa là tủ đang được làm mát hạ nhiệt."
        ),
    },
    {
        "term": "humid_slope_per_min",
        "title": "Tốc độ biến thiên độ ẩm (humid_slope_per_min)",
        "keywords": ["humid_slope_per_min", "humid slope", "do doc do am", "toc do tang am"],
        "definition": (
            "humid_slope_per_min (%/phút) là tốc độ thay đổi độ ẩm tương đối theo phút tính bằng hồi quy tuyến tính, "
            "giúp phát hiện hơi ẩm xâm nhập hoặc ngưng tụ bất thường trong tủ mạng."
        ),
    },
    {
        "term": "fan_on_duration_min",
        "title": "Thời gian quạt chạy liên tục (fan_on_duration_min)",
        "keywords": ["fan_on_duration_min", "thoi gian quat chay", "quat bat lien tuc"],
        "definition": (
            "fan_on_duration_min (phút) đo khoảng thời gian quạt tản nhiệt đã bật liên tục trong cửa sổ 10 phút. "
            "Nếu quạt đã bật >= 5 phút trên ngưỡng mà nhiệt độ vẫn tăng, hệ thống nghi ngờ suy giảm hiệu suất làm mát."
        ),
    },
    {
        "term": "fan_on_ratio",
        "title": "Tỷ lệ thời gian bật quạt (fan_on_ratio)",
        "keywords": ["fan_on_ratio", "ty le bat quat", "ti le quat chay", "duty cycle"],
        "definition": (
            "fan_on_ratio (từ 0.0 đến 1.0) là tỷ lệ số mẫu đo mà quạt ở trạng thái ON trong khoảng thời gian khảo sát. "
            "Giá trị 1.0 tương ứng quạt phải chạy 100% thời gian."
        ),
    },
    {
        "term": "temp_above_fan_on",
        "title": "Độ vượt ngưỡng bật quạt (temp_above_fan_on)",
        "keywords": ["temp_above_fan_on", "vuot nguong bat quat", "chenh lech nguong quat"],
        "definition": (
            "temp_above_fan_on (°C) là hiệu số giữa nhiệt độ hiện tại và ngưỡng bật quạt (fan_on_c). "
            "Giá trị dương lớn cho thấy nhiệt độ tủ đã vượt xa mốc kích hoạt quạt tự động."
        ),
    },
    {
        "term": "temp_std",
        "title": "Độ lệch chuẩn nhiệt độ (temp_std & max_step_temp)",
        "keywords": ["temp_std", "max_step_temp", "do lech chuan nhiet", "buoc nhay nhiet"],
        "definition": (
            "temp_std đo mức độ dao động nhiệt độ và max_step_temp đo bước nhảy lớn nhất giữa 2 mẫu liên tiếp. "
            "Hai chỉ số này dùng để phát hiện cảm biến nhiễu, lỏng dây hoặc đóng băng số liệu (flatline)."
        ),
    },
    {
        "term": "hysteresis",
        "title": "Cơ chế trễ nhiệt (Hysteresis: fan_on_c / fan_off_c)",
        "keywords": ["hysteresis", "do tre nhiet", "tre nhiet", "fan_on_c", "fan_off_c", "bat tat quat lien tuc"],
        "definition": (
            "Hysteresis là cơ chế dùng 2 ngưỡng tách biệt: bật quạt khi nhiệt độ >= fan_on_c và chỉ tắt quạt khi hạ xuống <= fan_off_c "
            "(với fan_off_c < fan_on_c), giúp ngăn rơ-le và quạt đóng cắt liên tục quanh một điểm nhiệt."
        ),
    },
    {
        "term": "COOLING_DEGRADATION",
        "title": "Cảnh báo suy giảm hiệu suất làm mát (COOLING_DEGRADATION)",
        "keywords": ["cooling_degradation", "cooling degradation", "suy giam lam mat", "suy giam hieu suat lam mat"],
        "definition": (
            "COOLING_DEGRADATION là cảnh báo AI phát sinh khi tủ ở chế độ AUTO, quạt đã bật liên tục >= 5 phút và nhiệt độ vượt fan_on_c "
            "nhưng nhiệt độ vẫn không giảm (slope >= -0.02°C/phút) hoặc điểm ml_score báo bất thường."
        ),
    },
    {
        "term": "SENSOR_ANOMALY",
        "title": "Cảnh báo bất thường cảm biến (SENSOR_ANOMALY)",
        "keywords": ["sensor_anomaly", "sensor anomaly", "bat thuong cam bien", "loi cam bien", "flatline", "nhieu cam bien"],
        "definition": (
            "SENSOR_ANOMALY là cảnh báo AI khi dữ liệu cảm biến DHT22/SHT3x có dấu hiệu phi vật lý: đứng yên hoàn toàn (flatline >= 20 mẫu), "
            "nhảy vọt đột ngột (>= 3.5°C hoặc >= 18% độ ẩm giữa 2 mẫu <= 30 giây), hoặc ra ngoài dải đo hợp lệ."
        ),
    },
    {
        "term": "Settle Rule",
        "title": "Quy tắc thời gian ổn định (Settle Rule)",
        "keywords": ["settle rule", "thoi gian on dinh", "settle", "vua doi cau hinh"],
        "definition": (
            "Settle Rule là quy tắc tạm hoãn đánh giá bất thường và dự báo trong 10 phút ngay sau khi tủ mới đổi ngưỡng cấu hình "
            "hoặc vừa chuyển từ chế độ MANUAL sang AUTO, nhằm tránh cảnh báo giả khi nhiệt độ chưa ổn định."
        ),
    },
    {
        "term": "NOT_APPLICABLE",
        "title": "Trạng thái không áp dụng (NOT_APPLICABLE & INSUFFICIENT_DATA)",
        "keywords": ["not_applicable", "insufficient_data", "khong ap dung", "chua du du lieu"],
        "definition": (
            "NOT_APPLICABLE xuất hiện khi tủ đang OFFLINE, ở chế độ MANUAL hoặc trong thời gian Settle Rule. "
            "INSUFFICIENT_DATA xuất hiện khi tủ chưa tích lũy đủ tối thiểu 12 mẫu đo hợp lệ trong cửa sổ 10 phút."
        ),
    },
    {
        "term": "AUTO_MANUAL",
        "title": "Chế độ vận hành Tự động (AUTO) và Thủ công (MANUAL)",
        "keywords": ["che do auto", "che do manual", "auto va manual", "tu dong va thu cong"],
        "definition": (
            "Ở chế độ AUTO, ESP32 tự đóng/cắt quạt và còi theo ngưỡng nhiệt độ - độ ẩm đã cấu hình (hysteresis). "
            "Ở chế độ MANUAL, người vận hành cưỡng bức trạng thái quạt/còi từ giao diện web qua lệnh MQTT."
        ),
    },
]


def lookup_glossary_terms(question: str, limit: int = 3) -> list[dict[str, str]]:
    """
    Tìm các mục thuật ngữ khớp với câu hỏi của người dùng.
    Nếu không khớp từ khóa cụ thể nào, trả về các thuật ngữ cốt lõi của hệ thống AI.
    """
    norm_q = _strip_accents(question)
    matched: list[dict[str, str]] = []

    for entry in GLOSSARY_ENTRIES:
        for kw in entry["keywords"]:
            if _strip_accents(kw) in norm_q:
                matched.append(
                    {
                        "term": str(entry["term"]),
                        "title": str(entry["title"]),
                        "definition": str(entry["definition"])[:300],
                    }
                )
                break
        if len(matched) >= limit:
            break

    if not matched:
        # Trả về 3 thuật ngữ nền tảng khi người dùng hỏi giải thích chung
        for entry in GLOSSARY_ENTRIES[:limit]:
            matched.append(
                {
                    "term": str(entry["term"]),
                    "title": str(entry["title"]),
                    "definition": str(entry["definition"])[:300],
                }
            )
    return matched


def get_full_glossary_dict() -> dict[str, str]:
    """Trả về dict {term: definition} gọn nhẹ để nạp vào context khi cần."""
    return {str(e["term"]): str(e["definition"])[:300] for e in GLOSSARY_ENTRIES}
