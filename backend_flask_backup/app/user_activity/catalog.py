from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID
import json

from backend.app.user_activity.messages import ActivityMessage

# Check constraints from user_activity_logs schema
ALLOWED_ACTIONS = frozenset({
    "VIEW", "CREATE", "UPDATE", "DELETE",
    "LOGIN", "LOGOUT", "EXPORT", "CONTROL",
    "ACKNOWLEDGE", "RESOLVE", "PROVISION", "REVOKE"
})

ALLOWED_OUTCOMES = frozenset({"SUCCESS", "ACCEPTED", "DENIED", "FAILED"})
ALLOWED_ACTOR_TYPES = frozenset({"USER", "SYSTEM"})

# Forbidden keys in metadata (never allow raw tokens, passwords, cookies, DSNs)
FORBIDDEN_METADATA_KEYS = frozenset({
    "password", "password_hash", "token", "access_token", "refresh_token",
    "secret", "secret_key", "jwt_secret_key", "authorization", "cookie",
    "credential", "mqtt_password", "dsn", "database_url", "uri",
    "raw_request", "raw_body", "body"
})

# DSN pattern fragments to detect and reject
DSN_MARKERS = ("postgresql://", "postgres://", "postgresql+", "mysql://", "sqlite://")

# Forbidden read / poll function keys (Specification: không ghi read/poll/log-view)
FORBIDDEN_FUNCTION_SUFFIXES = (".read", ".get", ".list", ".poll", ".view")

MAX_METADATA_BYTES = 16384  # 16 KB max bounded metadata


@dataclass(frozen=True)
class ActivitySpec:
    function_key: str
    allowed_actions: frozenset[str]
    allowed_outcomes: frozenset[str]
    summary_template: str
    required_summary_params: frozenset[str]
    allowed_metadata_keys: frozenset[str]
    is_async: bool = False


# Catalog các hoạt động nghiệp vụ cốt lõi P0
ACTIVITY_CATALOG: dict[str, ActivitySpec] = {
    # 1. Xác thực & Tài khoản
    "auth.login": ActivitySpec(
        function_key="auth.login",
        allowed_actions=frozenset({"LOGIN"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Đăng nhập hệ thống: {username} [{outcome}]",
        required_summary_params=frozenset({"username"}),
        allowed_metadata_keys=frozenset({"username", "ip_address", "reason", "failure_count"}),
    ),
    "auth.logout": ActivitySpec(
        function_key="auth.logout",
        allowed_actions=frozenset({"LOGOUT"}),
        allowed_outcomes=frozenset({"SUCCESS"}),
        summary_template="Đăng xuất khỏi hệ thống",
        required_summary_params=frozenset(),
        allowed_metadata_keys=frozenset({"session_id", "reason"}),
    ),
    "auth.password_change": ActivitySpec(
        function_key="auth.password_change",
        allowed_actions=frozenset({"UPDATE"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Đổi mật khẩu tài khoản cá nhân [{outcome}]",
        required_summary_params=frozenset(),
        allowed_metadata_keys=frozenset({"reason"}),
    ),

    # 2. Quản lý người dùng
    "user.create": ActivitySpec(
        function_key="user.create",
        allowed_actions=frozenset({"CREATE"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Tạo tài khoản người dùng: {target_username} (vai trò {role_code})",
        required_summary_params=frozenset({"target_username", "role_code"}),
        allowed_metadata_keys=frozenset({"target_user_id", "target_username", "role_code", "reason"}),
    ),
    "user.update": ActivitySpec(
        function_key="user.update",
        allowed_actions=frozenset({"UPDATE"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Cập nhật thông tin người dùng: {target_username}",
        required_summary_params=frozenset({"target_username"}),
        allowed_metadata_keys=frozenset({"target_user_id", "target_username", "fields", "reason"}),
    ),
    "user.lock": ActivitySpec(
        function_key="user.lock",
        allowed_actions=frozenset({"UPDATE"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Khóa tài khoản người dùng: {target_username}",
        required_summary_params=frozenset({"target_username"}),
        allowed_metadata_keys=frozenset({"target_user_id", "target_username", "reason"}),
    ),
    "user.unlock": ActivitySpec(
        function_key="user.unlock",
        allowed_actions=frozenset({"UPDATE"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Mở khóa tài khoản người dùng: {target_username}",
        required_summary_params=frozenset({"target_username"}),
        allowed_metadata_keys=frozenset({"target_user_id", "target_username", "reason"}),
    ),
    "user.role_assign": ActivitySpec(
        function_key="user.role_assign",
        allowed_actions=frozenset({"UPDATE"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Gán vai trò {role_code} cho người dùng: {target_username}",
        required_summary_params=frozenset({"target_username", "role_code"}),
        allowed_metadata_keys=frozenset({"target_user_id", "target_username", "role_code", "old_role_code", "reason"}),
    ),
    "user.password_reset": ActivitySpec(
        function_key="user.password_reset",
        allowed_actions=frozenset({"UPDATE"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Đặt lại mật khẩu cho người dùng: {target_username}",
        required_summary_params=frozenset({"target_username"}),
        allowed_metadata_keys=frozenset({"target_user_id", "target_username", "reason"}),
    ),

    # 3. Quản lý phân quyền thiết bị
    "device.access.grant": ActivitySpec(
        function_key="device.access.grant",
        allowed_actions=frozenset({"CREATE"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Cấp quyền truy cập {access_level} thiết bị {device_id} cho {target_username}",
        required_summary_params=frozenset({"device_id", "target_username", "access_level"}),
        allowed_metadata_keys=frozenset({"device_id", "target_user_id", "target_username", "access_level", "reason"}),
    ),
    "device.access.revoke": ActivitySpec(
        function_key="device.access.revoke",
        allowed_actions=frozenset({"REVOKE"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Thu hồi quyền truy cập thiết bị {device_id} của {target_username}",
        required_summary_params=frozenset({"device_id", "target_username"}),
        allowed_metadata_keys=frozenset({"device_id", "target_user_id", "target_username", "reason"}),
    ),

    # 4. Quản lý vòng đời & Thiết bị
    "device.register": ActivitySpec(
        function_key="device.register",
        allowed_actions=frozenset({"CREATE"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Đăng ký thiết bị mới: {device_id}",
        required_summary_params=frozenset({"device_id"}),
        allowed_metadata_keys=frozenset({"device_id", "location_id", "device_type", "reason"}),
    ),
    "device.provision": ActivitySpec(
        function_key="device.provision",
        allowed_actions=frozenset({"PROVISION"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Cấp thông tin xác thực MQTT cho thiết bị: {device_id}",
        required_summary_params=frozenset({"device_id"}),
        allowed_metadata_keys=frozenset({"device_id", "credential_version", "operation_id", "reason"}),
    ),
    "device.credential_rotate": ActivitySpec(
        function_key="device.credential_rotate",
        allowed_actions=frozenset({"UPDATE"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Xoay vòng thông tin xác thực cho thiết bị: {device_id}",
        required_summary_params=frozenset({"device_id"}),
        allowed_metadata_keys=frozenset({"device_id", "credential_version", "operation_id", "reason"}),
    ),
    "device.lock": ActivitySpec(
        function_key="device.lock",
        allowed_actions=frozenset({"UPDATE"}),
        allowed_outcomes=frozenset({"SUCCESS", "ACCEPTED", "DENIED", "FAILED"}),
        summary_template="Khóa hoạt động thiết bị: {device_id}",
        required_summary_params=frozenset({"device_id"}),
        allowed_metadata_keys=frozenset({"device_id", "operation_id", "reason"}),
    ),
    "device.unlock": ActivitySpec(
        function_key="device.unlock",
        allowed_actions=frozenset({"UPDATE"}),
        allowed_outcomes=frozenset({"SUCCESS", "ACCEPTED", "DENIED", "FAILED"}),
        summary_template="Mở khóa hoạt động thiết bị: {device_id}",
        required_summary_params=frozenset({"device_id"}),
        allowed_metadata_keys=frozenset({"device_id", "operation_id", "reason"}),
    ),
    "device.decommission": ActivitySpec(
        function_key="device.decommission",
        allowed_actions=frozenset({"UPDATE"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Ngừng hoạt động thiết bị: {device_id}",
        required_summary_params=frozenset({"device_id"}),
        allowed_metadata_keys=frozenset({"device_id", "reason"}),
    ),

    # 5. Điều khiển & Cấu hình thiết bị (Async 202 -> ACCEPTED)
    "device.control": ActivitySpec(
        function_key="device.control",
        allowed_actions=frozenset({"CONTROL"}),
        allowed_outcomes=frozenset({"ACCEPTED", "DENIED", "FAILED"}),
        summary_template="Tiếp nhận lệnh điều khiển quạt {command_type} cho thiết bị {device_id}",
        required_summary_params=frozenset({"device_id", "command_type"}),
        allowed_metadata_keys=frozenset({"device_id", "command_id", "command_type", "fan_state", "mode", "operation_id", "reason"}),
        is_async=True,
    ),
    "device.configure": ActivitySpec(
        function_key="device.configure",
        allowed_actions=frozenset({"UPDATE"}),
        allowed_outcomes=frozenset({"ACCEPTED", "DENIED", "FAILED"}),
        summary_template="Tiếp nhận cấu hình ngưỡng cho thiết bị {device_id} (version {config_version})",
        required_summary_params=frozenset({"device_id", "config_version"}),
        allowed_metadata_keys=frozenset({"device_id", "config_version", "command_id", "operation_id", "reason"}),
        is_async=True,
    ),

    # 6. Xử lý sự cố cảnh báo
    "alert.acknowledge": ActivitySpec(
        function_key="alert.acknowledge",
        allowed_actions=frozenset({"ACKNOWLEDGE"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Tiếp nhận xử lý cảnh báo {alert_id} trên thiết bị {device_id}",
        required_summary_params=frozenset({"alert_id", "device_id"}),
        allowed_metadata_keys=frozenset({"alert_id", "device_id", "reason"}),
    ),
    "alert.resolve": ActivitySpec(
        function_key="alert.resolve",
        allowed_actions=frozenset({"RESOLVE"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Đóng sự cố cảnh báo {alert_id} trên thiết bị {device_id}",
        required_summary_params=frozenset({"alert_id", "device_id"}),
        allowed_metadata_keys=frozenset({"alert_id", "device_id", "reason"}),
    ),

    # 7. Xuất dữ liệu
    "telemetry.export": ActivitySpec(
        function_key="telemetry.export",
        allowed_actions=frozenset({"EXPORT"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Xuất dữ liệu lịch sử telemetry thiết bị {device_id} ra CSV",
        required_summary_params=frozenset({"device_id"}),
        allowed_metadata_keys=frozenset({"device_id", "row_count", "time_range", "reason"}),
    ),

    # 8. Thu hồi phiên làm việc (hoạt động nội bộ hệ thống khi logout hoặc admin thu hồi)
    "session.revoke": ActivitySpec(
        function_key="session.revoke",
        allowed_actions=frozenset({"REVOKE"}),
        allowed_outcomes=frozenset({"SUCCESS", "DENIED", "FAILED"}),
        summary_template="Thu hồi phiên đăng nhập: {target_session_id}",
        required_summary_params=frozenset({"target_session_id"}),
        allowed_metadata_keys=frozenset({"target_session_id", "target_user_id", "reason"}),
    ),
}


def get_activity_spec(function_key: str) -> ActivitySpec:
    """Tra cứu ActivitySpec từ catalog. Chặn hoàn toàn các tác vụ xem/poll thường xuyên."""
    if not isinstance(function_key, str) or not function_key.strip():
        raise ValueError(ActivityMessage.LOG_FIELD_REQUIRED.format(field="function_key"))

    fk = function_key.strip().lower()

    # Chặn các tác vụ đọc/poll (không ghi read/poll/log-view theo Đặc tả V1.3)
    if any(fk.endswith(suffix) for suffix in FORBIDDEN_FUNCTION_SUFFIXES):
        raise ValueError(ActivityMessage.LOG_UNKNOWN_FUNCTION_KEY.format(function_key=function_key))

    spec = ACTIVITY_CATALOG.get(fk)
    if spec is None:
        raise ValueError(ActivityMessage.LOG_UNKNOWN_FUNCTION_KEY.format(function_key=function_key))
    return spec


def _check_string_for_secrets(val: str, field_name: str) -> None:
    val_lower = val.lower()
    if any(marker in val_lower for marker in DSN_MARKERS):
        raise ValueError(ActivityMessage.LOG_SENSITIVE_DATA_DETECTED.format(key=field_name))
    if "bearer " in val_lower:
        raise ValueError(ActivityMessage.LOG_SENSITIVE_DATA_DETECTED.format(key=field_name))


def render_summary(spec: ActivitySpec, params: dict | None, outcome: str) -> str:
    """Sinh chuỗi mô tả hoạt động bắt buộc từ mẫu template của server.
    - Không cho phép params ghi đè outcome thật.
    - Kiểm tra tham số bắt buộc theo spec.required_summary_params.
    - Kiểm tra giá trị tham số không chứa DSN hay bí mật.
    """
    safe_params = dict(params) if isinstance(params, dict) else {}

    # Kiểm tra thiếu tham số bắt buộc của template
    for req_param in spec.required_summary_params:
        if req_param not in safe_params or safe_params[req_param] is None or str(safe_params[req_param]).strip() == "":
            raise ValueError(
                ActivityMessage.LOG_SUMMARY_PARAM_MISSING.format(
                    param=req_param, function_key=spec.function_key
                )
            )

    # Sanitize các tham số summary
    formatted_dict: dict[str, str] = {}
    for k, v in safe_params.items():
        if isinstance(v, (str, int, float, bool, UUID)):
            s_val = str(v).strip()
            if len(s_val) > 128:
                raise ValueError(ActivityMessage.LOG_INVALID_FIELD_TYPE.format(field=f"summary_param.{k}", expected="string <= 128 chars"))
            _check_string_for_secrets(s_val, f"summary_param.{k}")
            formatted_dict[str(k)] = s_val
        else:
            raise ValueError(ActivityMessage.LOG_INVALID_FIELD_TYPE.format(field=f"summary_param.{k}", expected="scalar"))

    # Luôn gán outcome thật từ tham số đã được validate, không để caller override
    formatted_dict["outcome"] = outcome

    try:
        return spec.summary_template.format(**formatted_dict)
    except KeyError as exc:
        raise ValueError(
            ActivityMessage.LOG_SUMMARY_PARAM_MISSING.format(
                param=str(exc), function_key=spec.function_key
            )
        ) from exc


def validate_and_sanitize_metadata(spec: ActivitySpec, raw_metadata: dict | None) -> dict:
    """Kiểm tra và chuẩn hóa metadata:
    - Bắt buộc kiểm tra và từ chối các khóa chứa bí mật (password, token, credential, DSN).
    - Allowlist nghiêm ngặt theo spec.allowed_metadata_keys.
    - Cấm cấu trúc lồng nhau tùy ý (chỉ cho phép scalar phẳng hoặc list tên cột 'fields').
    - Giới hạn kích thước payload (<= 16 KB).
    """
    if raw_metadata is None:
        return {}
    if not isinstance(raw_metadata, dict):
        raise ValueError(ActivityMessage.LOG_INVALID_FIELD_TYPE.format(field="metadata", expected="dict"))

    clean: dict[str, Any] = {}
    for raw_key, value in raw_metadata.items():
        k = str(raw_key).strip()
        k_lower = k.lower()

        # 1. Chặn khóa bí mật tuyệt đối
        if k_lower in FORBIDDEN_METADATA_KEYS:
            raise ValueError(ActivityMessage.LOG_SENSITIVE_DATA_DETECTED.format(key=k))

        # 2. Allowlist khóa theo spec
        if k not in spec.allowed_metadata_keys:
            raise ValueError(ActivityMessage.LOG_UNALLOWED_KEY.format(key=k, target=spec.function_key))

        # 3. Chặn cấu trúc lồng nhau (nested dict)
        if isinstance(value, dict):
            raise ValueError(ActivityMessage.LOG_INVALID_FIELD_TYPE.format(field=k, expected="scalar (nested dict forbidden)"))

        if isinstance(value, (list, tuple)):
            if k != "fields":
                raise ValueError(ActivityMessage.LOG_INVALID_FIELD_TYPE.format(field=k, expected="scalar (nested list forbidden)"))
            if len(value) > 20:
                raise ValueError(ActivityMessage.LOG_INVALID_FIELD_TYPE.format(field=k, expected="list with <= 20 items"))
            clean_list: list[str] = []
            for item in value:
                if not isinstance(item, str) or len(item) > 64:
                    raise ValueError(ActivityMessage.LOG_INVALID_FIELD_TYPE.format(field=k, expected="list of strings (<= 64 chars)"))
                _check_string_for_secrets(item, k)
                clean_list.append(item)
            clean[k] = clean_list
            continue

        if isinstance(value, str):
            if len(value) > 512:
                raise ValueError(ActivityMessage.LOG_INVALID_FIELD_TYPE.format(field=k, expected="string <= 512 chars"))
            _check_string_for_secrets(value, k)
            clean[k] = value
        elif isinstance(value, UUID):
            clean[k] = str(value)
        elif isinstance(value, (int, float, bool)):
            clean[k] = value
        elif value is None:
            clean[k] = None
        else:
            raise ValueError(ActivityMessage.LOG_INVALID_FIELD_TYPE.format(field=k, expected="string, number, boolean, or UUID"))

    # 4. Kiểm tra JSON serialization thực thụ không dùng default=str
    try:
        serialized = json.dumps(clean, ensure_ascii=False)
    except Exception as exc:
        raise ValueError(ActivityMessage.LOG_PAYLOAD_SERIALIZATION_FAILED.format(field="metadata")) from exc

    if len(serialized.encode("utf-8")) > MAX_METADATA_BYTES:
        raise ValueError(ActivityMessage.LOG_PAYLOAD_SERIALIZATION_FAILED.format(field="metadata (exceeds 16KB)"))

    return clean