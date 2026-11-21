"""
Bộ phân loại ý định câu hỏi xác định (Deterministic Intent Router - Pha 6).
Phân loại câu hỏi người dùng thành 10 nhóm `answer_type` trong < 1ms,
chuẩn hóa tiếng Việt không dấu / lỗi gõ phổ biến, trích xuất tham số giả lập What-If
và quyết định tập dữ liệu ngữ cảnh cần nạp (`needs`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Any, Literal

AnswerType = Literal[
    "SMALLTALK",
    "OUT_OF_SCOPE",
    "EXPLAIN",
    "STATUS",
    "FORECAST",
    "DIAGNOSE",
    "WHAT_IF",
    "COMMANDS",
    "COMPARE",
    "ADVICE",
]

ALL_ANSWER_TYPES: tuple[AnswerType, ...] = (
    "SMALLTALK",
    "OUT_OF_SCOPE",
    "EXPLAIN",
    "STATUS",
    "FORECAST",
    "DIAGNOSE",
    "WHAT_IF",
    "COMMANDS",
    "COMPARE",
    "ADVICE",
)


@dataclass(frozen=True)
class RouteDecision:
    answer_type: AnswerType
    needs: frozenset[str]
    confidence: float = 1.0
    matched_rule: str = "default"
    extracted_fan_on_c: float | None = None
    extracted_fan_off_c: float | None = None
    normalized_question: str = ""


def strip_accents(text: str) -> str:
    """Chuẩn hóa NFD, bỏ dấu tiếng Việt và chuyển về chữ thường."""
    if not text:
        return ""
    text = text.replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn").lower()


def normalize_text(text: str) -> str:
    """Chuẩn hóa khoảng trắng, chữ thường, bỏ dấu và gom các lỗi gõ chào hỏi phổ biến."""
    raw = strip_accents(text or "")
    # Thay các ký tự đặc biệt trừ dấu +, -, ., =, ?, % bằng khoảng trắng
    cleaned = re.sub(r"[^\w\s\+\-\.\=\?\%\/]", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Chuẩn hóa lỗi gõ "he loo", "hellooo", "hiii", "xinchao"
    cleaned = re.sub(r"\bhe\s+lo+\b", "hello", cleaned)
    cleaned = re.sub(r"\bhel+o+\b", "hello", cleaned)
    cleaned = re.sub(r"\bhi+\b", "hi", cleaned)
    cleaned = re.sub(r"\bxinchao\b", "xin chao", cleaned)
    return cleaned


# Từ khóa thuộc miền tủ mạng / IoT để phân biệt câu hỏi ngắn có thuộc phạm vi hay không
_DOMAIN_KEYWORDS: tuple[str, ...] = (
    "nhiet",
    "do am",
    "am do",
    "quat",
    "tu",
    "cabinet",
    "canh bao",
    "alert",
    "lenh",
    "command",
    "timeout",
    "failed",
    "ack",
    "mqtt",
    "cam bien",
    "sensor",
    "dht",
    "esp32",
    "firmware",
    "auto",
    "manual",
    "fan_on",
    "fan_off",
    "fan on",
    "fan off",
    "nguong",
    "threshold",
    "hysteresis",
    "tre nhiet",
    "isolation",
    "ml_score",
    "ml score",
    "slope",
    "cooling",
    "degradation",
    "anomaly",
    "bat thuong",
    "su co",
    "lam mat",
    "tan nhiet",
    "du bao",
    "du doan",
    "xu huong",
    "coi",
    "buzzer",
    "online",
    "offline",
    "ket noi",
    "telemetry",
    "settle",
    "not_applicable",
    "insufficient_data",
    "cau hinh",
    "config",
    "trang thai",
    "tinh trang",
    "nong",
    "am",
    "gioi han",
)

_DOMAIN_KEYWORD_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in _DOMAIN_KEYWORDS) + r")\b"
)


def _has_domain_keyword(norm_q: str) -> bool:
    return bool(_DOMAIN_KEYWORD_PATTERN.search(norm_q))


def _is_gibberish(norm_q: str) -> bool:
    """
    Phát hiện chuỗi gõ linh tinh / spam bàn phím (vd: 'dhvbdbvdsbi', 'asdfghjkl', 'aaaaaa').
    """
    tokens = re.findall(r"[a-z0-9]+", norm_q)
    if not tokens:
        return True
    for tok in tokens:
        # Ký tự lặp liên tiếp >= 4 lần (vd: 'aaaaa', 'zzzzz')
        if re.search(r"(.)\1{3,}", tok):
            return True
        # Cụm >= 5 phụ âm liên tiếp (rất hiếm trong cả tiếng Việt không dấu lẫn tiếng Anh thông thường)
        if re.search(r"[bcdfghjklmnpqrstvwxz]{5,}", tok):
            return True
        # Từ dài >= 6 ký tự nhưng không có nguyên âm nào
        if len(tok) >= 6 and not re.search(r"[aeiouy]", tok):
            return True
    # Câu chỉ có 1 từ dài >= 7 ký tự không thuộc từ khóa hệ thống hoặc chào hỏi
    if len(tokens) == 1 and len(tokens[0]) >= 7 and not _has_domain_keyword(norm_q):
        return True
    return False


def _extract_what_if_thresholds(raw_question: str, norm_q: str) -> tuple[float | None, float | None]:
    """
    Trích xuất ngưỡng fan_on_c và fan_off_c từ câu hỏi giả lập What-If.
    Hỗ trợ:
      - "fan_on = 28, fan_off = 26"
      - "hạ ngưỡng bật quạt xuống 28°C"
      - "đặt bật quạt 29 độ và tắt quạt 27 độ"
      - "nếu hạ xuống 28 độ thì sao" (mặc định hiểu là fan_on_c nếu chỉ có 1 số)
    """
    fan_on: float | None = None
    fan_off: float | None = None

    # 1. Tìm gán trực tiếp cạnh fan_on / bật quạt
    on_patterns = [
        r"(?:fan[_\s]?on(?:[_\s]?c)?|nguong\s+bat(?:\s+quat)?|bat\s+quat|moc\s+bat)\s*(?:la|=|xuong|len|thanh|o\s+muc|ve|[:\s])\s*(-?\d+(?:[\.,]\d+)?)",
        r"(?:ha|tang|doi|dat|chinh|set)\s+(?:fan[_\s]?on(?:[_\s]?c)?|nguong\s+bat(?:\s+quat)?|bat\s+quat)\s+(?:xuong|len|thanh|ve|=|la)?\s*(-?\d+(?:[\.,]\d+)?)",
    ]
    for pat in on_patterns:
        m = re.search(pat, norm_q)
        if m:
            try:
                fan_on = float(m.group(1).replace(",", "."))
                break
            except ValueError:
                pass

    # 2. Tìm gán trực tiếp cạnh fan_off / tắt quạt
    off_patterns = [
        r"(?:fan[_\s]?off(?:[_\s]?c)?|nguong\s+tat(?:\s+quat)?|tat\s+quat|moc\s+tat)\s*(?:la|=|xuong|len|thanh|o\s+muc|ve|[:\s])\s*(-?\d+(?:[\.,]\d+)?)",
        r"(?:ha|tang|doi|dat|chinh|set)\s+(?:fan[_\s]?off(?:[_\s]?c)?|nguong\s+tat(?:\s+quat)?|tat\s+quat)\s+(?:xuong|len|thanh|ve|=|la)?\s*(-?\d+(?:[\.,]\d+)?)",
    ]
    for pat in off_patterns:
        m = re.search(pat, norm_q)
        if m:
            try:
                fan_off = float(m.group(1).replace(",", "."))
                break
            except ValueError:
                pass

    # 3. Nếu chưa bắt được theo nhãn tường minh, lấy các số nhiệt độ trong câu hỏi What-If
    if fan_on is None and fan_off is None:
        # Bỏ qua các số đi kèm "phut", "gio", "giay", "tu", "cabinet"
        cleaned_for_nums = re.sub(r"\bcabinet[\-\s]?\d+\b", " ", norm_q)
        cleaned_for_nums = re.sub(r"\b\d+\s*(?:phut|gio|giay|h|m|s|mau|lan)\b", " ", cleaned_for_nums)
        nums = [
            float(x.replace(",", "."))
            for x in re.findall(r"(-?\d+(?:[\.,]\d+)?)", cleaned_for_nums)
        ]
        # Lọc các số hợp lý cho ngưỡng nhiệt độ (-40..85)
        plausible = [n for n in nums if -40.0 <= n <= 85.0]
        if len(plausible) >= 2:
            # Nếu câu có nhắc cả bật và tắt theo thứ tự, hoặc truyền 2 ngưỡng
            if "fan_off" in norm_q and "fan_on" in norm_q and norm_q.find("fan_off") < norm_q.find("fan_on"):
                fan_off, fan_on = plausible[0], plausible[1]
            else:
                fan_on, fan_off = plausible[0], plausible[1]
        elif len(plausible) == 1:
            if any(k in norm_q for k in ("fan_off", "fan off", "tat quat", "nguong tat")):
                fan_off = plausible[0]
            else:
                fan_on = plausible[0]

    return fan_on, fan_off


_SMALLTALK_EXACT: frozenset[str] = frozenset(
    {
        "xin chao",
        "chao",
        "chao ban",
        "chao ai",
        "chao tro ly",
        "hello",
        "hi",
        "hey",
        "alo",
        "a lo",
        "good morning",
        "good afternoon",
        "good evening",
        "chao buoi sang",
        "chao buoi chieu",
        "chao buoi toi",
        "cam on",
        "cam on ban",
        "cam on nhieu",
        "thanks",
        "thank you",
        "tks",
        "ok",
        "oke",
        "okay",
        "tam biet",
        "bye",
        "goodbye",
        "hen gap lai",
        "ban la ai",
        "ban ten gi",
        "tro ly la ai",
        "ban lam duoc gi",
        "ban giup duoc gi",
        "ban co the lam gi",
        "huong dan su dung",
        "giup toi voi",
        "help",
        "test",
        "alo 123",
    }
)

_SMALLTALK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:xin\s+chao|chao|hello|hi|hey|alo)(?:\s+(?:ban|tro\s+ly|ai|admin|ad|nha|nhe|a))?[\!\.\?]*$"),
    re.compile(r"^(?:cam\s+on|thanks|thank\s+you)(?:\s+(?:ban|tro\s+ly|ai|nhieu|nhe|nha))?[\!\.\?]*$"),
    re.compile(r"^(?:tam\s+biet|bye|goodbye|hen\s+gap\s+lai)(?:\s+(?:ban|nhe|nha))?[\!\.\?]*$"),
    re.compile(r"^(?:ban|em|tro\s+ly|ai)\s+(?:la\s+ai|ten\s+(?:la\s+)?gi|lam\s+duoc\s+(?:nhung\s+)?gi|co\s+chuc\s+nang\s+gi|giup\s+duoc\s+gi)\??$"),
    re.compile(r"^ban\s+khoe\s+khong\??$"),
)

_OUT_OF_SCOPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Biểu thức toán học thuần túy (vd: "2+2 bằng mấy", "15 * 4 = ?")
    re.compile(r"^\d+\s*[\+\-\*\/x]\s*\d+(?:\s*(?:=|\s+bang)\s*(?:may|bao\s+nhieu|\?)?)?$"),
    re.compile(
        r"\b(?:"
        r"thoi\s+tiet\s+(?:ha\s+noi|sai\s+gon|da\s+nang|hom\s+nay\s+o)|"
        r"gia\s+vang|gia\s+bitcoin|gia\s+xang|ty\s+gia\s+usd|chung\s+khoan|"
        r"viet\s+(?:bai\s+)?tho|sang\s+tac\s+nhac|ke\s+chuyen\s+cuoi|"
        r"bong\s+da|world\s+cup|ngoai\s+hang\s+anh|champion\s+league|"
        r"cong\s+thuc\s+nau\s+an|cach\s+nau\s+pho|cach\s+lam\s+banh|"
        r"code\s+game|lap\s+trinh\s+web\s+ban\s+hang|viet\s+code\s+react|"
        r"thu\s+do\s+cua\s+\w+\s+la\s+gi|tong\s+thong\s+my|"
        r"dich\s+sang\s+tieng\s+(?:anh|phap|nhat|trung)|"
        r"tu\s+vi\s+ung\s+menh|xem\s+boi|xo\s+so\s+mien"
        r")\b"
    ),
)

_EXPLAIN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:"
        r"isolation\s*forest|ml[_\s]score|temp[_\s]slope(?:[_\s]per[_\s]min)?|"
        r"humid[_\s]slope(?:[_\s]per[_\s]min)?|fan[_\s]on[_\s]duration(?:[_\s]min)?|"
        r"fan[_\s]on[_\s]ratio|temp[_\s]above[_\s]fan[_\s]on|temp[_\s]std|max[_\s]step[_\s]temp|"
        r"hysteresis|tre\s+nhiet|do\s+tre\s+nhiet|cooling[_\s]degradation|sensor[_\s]anomaly|"
        r"settle\s+rule|not[_\s]applicable|insufficient[_\s]data"
        r")\b.*\b(?:la\s+gi|nghia\s+la\s+(?:gi|sao)|hoat\s+dong\s+(?:nhu\s+the\s+nao|ra\s+sao)|giai\s+thich|khai\s+niem|co\s+y\s+nghia\s+gi|tinh\s+nhu\s+the\s+nao)\b"
    ),
    re.compile(
        r"\b(?:giai\s+thich|khai\s+niem| dinh\s+nghia|cho\s+hoi\s+ve|the\s+nao\s+la)\b.*\b(?:"
        r"isolation\s*forest|ml[_\s]score|temp[_\s]slope|humid[_\s]slope|fan[_\s]on[_\s]duration|"
        r"fan[_\s]on[_\s]ratio|temp[_\s]std|max[_\s]step|hysteresis|tre\s+nhiet|"
        r"cooling[_\s]degradation|sensor[_\s]anomaly|settle\s+rule|not[_\s]applicable|insufficient[_\s]data|"
        r"che\s+do\s+auto|che\s+do\s+manual|fan[_\s]on\s+va\s+fan[_\s]off"
        r")\b"
    ),
    re.compile(
        r"\b(?:tai\s+sao\s+can|khac\s+nhau\s+giua|phan\s+biet)\b.*\b(?:fan[_\s]on\s+va\s+fan[_\s]off|auto\s+va\s+manual|hai\s+nguong\s+bat\s+tat)\b"
    ),
    re.compile(
        r"^(?:isolation\s*forest|ml[_\s]score|hysteresis|cooling[_\s]degradation|sensor[_\s]anomaly|settle\s+rule|not[_\s]applicable|insufficient[_\s]data|temp[_\s]slope(?:[_\s]per[_\s]min)?|fan[_\s]on[_\s]duration(?:[_\s]min)?)\s+(?:la\s+gi|nghia\s+la\s+sao)\??$"
    ),
)

_WHAT_IF_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Câu hỏi giả định "nếu / giả sử / thử ..." kèm thay đổi ngưỡng
    re.compile(
        r"\b(?:neu|gia\s+su|gia\s+dinh|thu|what\s+if)\b.*\b(?:ha|tang|giam|doi|dat|chinh|thay\s+doi|set|dua)\b.*\b(?:fan[_\s]on|fan[_\s]off|nguong|bat\s+quat|tat\s+quat|nhiet\s+do\s+bat|do\s+c|\d+)"
    ),
    re.compile(
        r"\b(?:ha|tang|giam|doi|dat|chinh|thay\s+doi|set)\b.*\b(?:fan[_\s]on(?:[_\s]c)?|fan[_\s]off(?:[_\s]c)?|nguong\s+bat(?:\s+quat)?|nguong\s+tat(?:\s+quat)?)\b.*\b(?:xuong|len|thanh|ve|=)\s*-?\d+"
    ),
    re.compile(
        r"\b(?:fan[_\s]on(?:[_\s]c)?|fan[_\s]off(?:[_\s]c)?)\s*(?:=|la|thanh|xuong|len)\s*-?\d+(?:[\.,]\d+)?\b.*\b(?:thi\s+sao|co\s+sao\s+khong|anh\s+huong|bao\s+lau|nhu\s+the\s+nao)\b"
    ),
    re.compile(
        r"\b(?:dieu\s+gi\s+xay\s+ra|chuyen\s+gi\s+xay\s+ra|anh\s+huong\s+gi)\s+khi\s+(?:ha|tang|doi|dat|chinh)\s+(?:nguong|fan[_\s]on|fan[_\s]off)"
    ),
)

_COMMANDS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:lenh|command|commands|mqtt\s+ack|ack\s+lenh|lich\s+su\s+lenh|gui\s+lenh|phat\s+lenh)\b.*\b(?:timeout|failed|loi|that\s+bai|khong\s+phan\s+hoi|treo|gan\s+day|tai\s+sao|giai\s+thich)\b"
    ),
    re.compile(
        r"\b(?:giai\s+thich|kiem\s+tra|xem|thong\s+ke|tai\s+sao)\b.*\b(?:cac\s+lenh|lenh\s+dieu\s+khien|lenh\s+timeout|lenh\s+failed|lenh\s+loi|lich\s+su\s+lenh)\b"
    ),
    re.compile(r"\b(?:timeout\s*\/?\s*failed|failed\s*\/?\s*timeout)\b"),
)

_COMPARE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:so\s+sanh|so\s+voi|khac\s+gi\s+so\s+voi|cao\s+hon\s+hay\s+thap\s+hon|nong\s+hon\s+hay\s+mat\s+hon)\b.*\b(?:hom\s+qua|tu\s+khac|cac\s+tu|trung\s+binh|binh\s+thuong|24h\s+truoc|tuan\s+truoc|he\s+thong)\b"
    ),
    re.compile(r"\b(?:tu\s+nay\s+voi\s+cac\s+tu\s+khac|giua\s+cac\s+tu\s+mang|xep\s+hang\s+nhiet\s+do\s+tu)\b"),
)

_FORECAST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:du\s+bao|du\s+doan|uoc\s+tinh|ngoai\s+suy|forecast)\b"
    ),
    re.compile(
        r"\b(?:\d+\s*phut\s*(?:toi|nua|sau)|bao\s+lau\s+nua|khi\s+nao\s+(?:cham|vuot)\s+nguong|sap\s+toi\s+nhiet\s+do|xu\s+huong\s+(?:nhiet\s+do|do\s+am|sap\s+toi|\d+\s*phut))\b"
    ),
)

_ADVICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:nen\s+lam\s+gi|can\s+lam\s+gi|phai\s+lam\s+sao|cach\s+khac\s+phuc|huong\s+dan\s+xu\s+ly|quy\s+trinh\s+xu\s+ly|khuyen\s+nghi\s+van\s+hanh|goi\s+y\s+xu\s+ly|cac\s+buoc\s+kiem\s+tra|bao\s+tri\s+nhu\s+the\s+nao)\b"
    ),
    re.compile(r"^(?:khac\s+phuc\s+the\s+nao|xu\s+ly\s+ra\s+sao|lam\s+sao\s+de\s+ha\s+nhiet)\??$"),
)

_DIAGNOSE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:tai\s+sao|vi\s+sao|nguyen\s+nhan(?:\s+nao)?|ly\s+do|chan\s+doan|phan\s+tich\s+su\s+co|danh\s+gia\s+hieu\s+suat(?:\s+lam\s+mat)?|kiem\s+tra\s+bat\s+thuong)\b"
    ),
    re.compile(
        r"\b(?:quat\s+co\s+(?:bi\s+)?(?:hong|ket|yeu|loi)|cam\s+bien\s+co\s+(?:bi\s+)?(?:loi|sai|nhieu|hong)|suy\s+giam\s+lam\s+mat|tu\s+bi\s+nong|nhiet\s+do\s+tang\s+cao)\b"
    ),
)

_STATUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:nhiet\s+do\s+hien\s+tai|do\s+am\s+hien\s+tai|trang\s+thai\s+(?:tu|hien\s+tai|ket\s+noi|quat)|tinh\s+trang\s+(?:tu|hien\s+tai|24h)|tom\s+tat\s+tinh\s+trang|quat\s+dang\s+bat\s+hay\s+tat|co\s+canh\s+bao\s+nao|tu\s+dang\s+online\s+hay\s+offline|thong\s+so\s+hien\s+tai|bao\s+nhieu\s+do)\b"
    ),
)

NEEDS_BY_ANSWER_TYPE: dict[AnswerType, frozenset[str]] = {
    "SMALLTALK": frozenset(),
    "OUT_OF_SCOPE": frozenset(),
    "EXPLAIN": frozenset({"glossary"}),
    "STATUS": frozenset({"telemetry", "alerts", "anomaly", "config"}),
    "FORECAST": frozenset({"telemetry", "forecast", "config", "anomaly"}),
    "DIAGNOSE": frozenset({"telemetry", "alerts", "anomaly", "forecast", "config"}),
    "WHAT_IF": frozenset({"telemetry", "config", "forecast", "simulation"}),
    "COMMANDS": frozenset({"telemetry", "commands", "config"}),
    "COMPARE": frozenset({"telemetry", "history", "peer_stats", "alerts", "config"}),
    "ADVICE": frozenset({"telemetry", "alerts", "anomaly", "config", "commands"}),
}


def route_question(
    question: str,
    history: list[dict[str, Any]] | None = None,
) -> RouteDecision:
    """
    Phân loại câu hỏi thành 1 trong 10 `AnswerType` theo thứ tự ưu tiên chặt chẽ:
      1. SMALLTALK (chào hỏi / cảm ơn / hỏi danh tính trợ lý và không chứa từ khóa chuyên ngành)
      2. OUT_OF_SCOPE (câu hỏi ngoài phạm vi IoT / toán học / thời tiết / bóng đá...)
      3. WHAT_IF (giả lập đổi ngưỡng fan_on / fan_off — ưu tiên trước DIAGNOSE/ADVICE/CONFIG)
      4. EXPLAIN (giải thích thuật ngữ kỹ thuật)
      5. COMMANDS (lịch sử lệnh TIMEOUT / FAILED / MQTT ACK)
      6. COMPARE (so sánh với hôm qua / với các tủ khác)
      7. FORECAST (dự báo nhiệt độ / độ ẩm 5-30 phút tới)
      8. ADVICE (xin khuyến nghị vận hành / bước xử lý)
      9. DIAGNOSE (phân tích nguyên nhân sự cố / hiệu suất quạt / lỗi cảm biến)
     10. STATUS (trạng thái hiện tại / tóm tắt nhanh)
    """
    norm_q = normalize_text(question)
    if not norm_q:
        return RouteDecision(
            answer_type="SMALLTALK",
            needs=NEEDS_BY_ANSWER_TYPE["SMALLTALK"],
            confidence=1.0,
            matched_rule="empty_question",
            normalized_question=norm_q,
        )

    has_domain = _has_domain_keyword(norm_q)

    # 1. SMALLTALK
    if not has_domain:
        if norm_q in _SMALLTALK_EXACT:
            return RouteDecision(
                answer_type="SMALLTALK",
                needs=NEEDS_BY_ANSWER_TYPE["SMALLTALK"],
                confidence=1.0,
                matched_rule="smalltalk_exact",
                normalized_question=norm_q,
            )
        for pat in _SMALLTALK_PATTERNS:
            if pat.search(norm_q):
                return RouteDecision(
                    answer_type="SMALLTALK",
                    needs=NEEDS_BY_ANSWER_TYPE["SMALLTALK"],
                    confidence=0.98,
                    matched_rule="smalltalk_regex",
                    normalized_question=norm_q,
                )

    # 2. OUT_OF_SCOPE (khi khớp mẫu ngoài phạm vi và không hỏi về tủ mạng)
    for pat in _OUT_OF_SCOPE_PATTERNS:
        if pat.search(norm_q) and not any(k in norm_q for k in ("tu mang", "cabinet", "esp32", "fan_on", "fan_off")):
            return RouteDecision(
                answer_type="OUT_OF_SCOPE",
                needs=NEEDS_BY_ANSWER_TYPE["OUT_OF_SCOPE"],
                confidence=0.98,
                matched_rule="out_of_scope_regex",
                normalized_question=norm_q,
            )

    # 3. WHAT_IF (ưu tiên trước EXPLAIN/DIAGNOSE/FORECAST khi có giả định đổi ngưỡng)
    for pat in _WHAT_IF_PATTERNS:
        if pat.search(norm_q):
            fan_on, fan_off = _extract_what_if_thresholds(question, norm_q)
            return RouteDecision(
                answer_type="WHAT_IF",
                needs=NEEDS_BY_ANSWER_TYPE["WHAT_IF"],
                confidence=0.95,
                matched_rule="what_if_regex",
                extracted_fan_on_c=fan_on,
                extracted_fan_off_c=fan_off,
                normalized_question=norm_q,
            )

    # 4. EXPLAIN (giải thích thuật ngữ kỹ thuật)
    for pat in _EXPLAIN_PATTERNS:
        if pat.search(norm_q):
            return RouteDecision(
                answer_type="EXPLAIN",
                needs=NEEDS_BY_ANSWER_TYPE["EXPLAIN"],
                confidence=0.95,
                matched_rule="explain_regex",
                normalized_question=norm_q,
            )

    # 5. COMMANDS (giải thích lệnh TIMEOUT / FAILED / ACK)
    for pat in _COMMANDS_PATTERNS:
        if pat.search(norm_q):
            return RouteDecision(
                answer_type="COMMANDS",
                needs=NEEDS_BY_ANSWER_TYPE["COMMANDS"],
                confidence=0.95,
                matched_rule="commands_regex",
                normalized_question=norm_q,
            )

    # 6. COMPARE (so sánh với hôm qua / các tủ khác)
    for pat in _COMPARE_PATTERNS:
        if pat.search(norm_q):
            return RouteDecision(
                answer_type="COMPARE",
                needs=NEEDS_BY_ANSWER_TYPE["COMPARE"],
                confidence=0.93,
                matched_rule="compare_regex",
                normalized_question=norm_q,
            )

    # 7. FORECAST (dự báo nhiệt độ / xu hướng tương lai)
    for pat in _FORECAST_PATTERNS:
        if pat.search(norm_q):
            return RouteDecision(
                answer_type="FORECAST",
                needs=NEEDS_BY_ANSWER_TYPE["FORECAST"],
                confidence=0.93,
                matched_rule="forecast_regex",
                normalized_question=norm_q,
            )

    # 8. ADVICE (xin khuyến nghị vận hành / cách khắc phục)
    for pat in _ADVICE_PATTERNS:
        if pat.search(norm_q):
            return RouteDecision(
                answer_type="ADVICE",
                needs=NEEDS_BY_ANSWER_TYPE["ADVICE"],
                confidence=0.92,
                matched_rule="advice_regex",
                normalized_question=norm_q,
            )

    # 9. DIAGNOSE (phân tích sự cố / tại sao nóng / hiệu suất quạt)
    # Lưu ý: Câu "Tóm tắt tình trạng & sự cố 24h qua" là câu hỏi tổng quan -> ưu tiên STATUS nếu bắt đầu bằng "tom tat tinh trang"
    if norm_q.startswith("tom tat tinh trang"):
        return RouteDecision(
            answer_type="STATUS",
            needs=NEEDS_BY_ANSWER_TYPE["STATUS"],
            confidence=0.95,
            matched_rule="status_summary_prefix",
            normalized_question=norm_q,
        )

    for pat in _DIAGNOSE_PATTERNS:
        if pat.search(norm_q):
            return RouteDecision(
                answer_type="DIAGNOSE",
                needs=NEEDS_BY_ANSWER_TYPE["DIAGNOSE"],
                confidence=0.90,
                matched_rule="diagnose_regex",
                normalized_question=norm_q,
            )

    # 10. STATUS (trạng thái hiện tại / thông số tủ)
    for pat in _STATUS_PATTERNS:
        if pat.search(norm_q):
            return RouteDecision(
                answer_type="STATUS",
                needs=NEEDS_BY_ANSWER_TYPE["STATUS"],
                confidence=0.90,
                matched_rule="status_regex",
                normalized_question=norm_q,
            )

    # 11. Xử lý câu hỏi nối tiếp (follow-up) dựa trên history nếu có
    if history and any(
        norm_q.startswith(prefix)
        for prefix in ("con ", "the con ", "vay con ", "chi tiet hon", "tiep theo", "vay thi")
    ):
        if any(k in norm_q for k in ("ha ", "tang ", "fan_on", "fan_off", "nguong")) and re.search(r"\d", norm_q):
            fan_on, fan_off = _extract_what_if_thresholds(question, norm_q)
            return RouteDecision(
                answer_type="WHAT_IF",
                needs=NEEDS_BY_ANSWER_TYPE["WHAT_IF"],
                confidence=0.85,
                matched_rule="followup_what_if",
                extracted_fan_on_c=fan_on,
                extracted_fan_off_c=fan_off,
                normalized_question=norm_q,
            )
        if has_domain:
            return RouteDecision(
                answer_type="STATUS",
                needs=NEEDS_BY_ANSWER_TYPE["STATUS"],
                confidence=0.80,
                matched_rule="followup_domain_status",
                normalized_question=norm_q,
            )

    # 12. Nếu câu không có bất kỳ từ khóa domain nào -> kiểm tra chuỗi vô nghĩa / chào hỏi ngắn / ngoài phạm vi
    if not has_domain:
        if _is_gibberish(norm_q):
            return RouteDecision(
                answer_type="OUT_OF_SCOPE",
                needs=NEEDS_BY_ANSWER_TYPE["OUT_OF_SCOPE"],
                confidence=0.95,
                matched_rule="gibberish_input",
                normalized_question=norm_q,
            )
        words = norm_q.split()
        if len(words) <= 3 and any(w in ("chao", "hello", "hi", "alo", "hey", "ok", "thanks") for w in words):
            return RouteDecision(
                answer_type="SMALLTALK",
                needs=NEEDS_BY_ANSWER_TYPE["SMALLTALK"],
                confidence=0.85,
                matched_rule="short_greeting_fallback",
                normalized_question=norm_q,
            )
        return RouteDecision(
            answer_type="OUT_OF_SCOPE",
            needs=NEEDS_BY_ANSWER_TYPE["OUT_OF_SCOPE"],
            confidence=0.90,
            matched_rule="non_domain_out_of_scope",
            normalized_question=norm_q,
        )

    # Mặc định cho câu hỏi kỹ thuật thuộc domain tủ mạng
    default_type: AnswerType = "STATUS" if "hien tai" in norm_q or "bao nhieu" in norm_q else "DIAGNOSE"
    return RouteDecision(
        answer_type=default_type,
        needs=NEEDS_BY_ANSWER_TYPE[default_type],
        confidence=0.75,
        matched_rule=f"domain_default_{default_type.lower()}",
        normalized_question=norm_q,
    )
