"""Worker không có HTTP request: kiểm tra lại actor, phiên và grant từ lệnh đã lưu."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.accounts.models import AuthSession
from apps.accounts.services.sessions import verify_auth_session
from apps.devices.models import DeviceConfig
from apps.devices.policies import require_device_permission


def dispatch_decision(command):
    """Trả (được gửi, mã lý do), không tự publish hay commit thay worker."""
    if command.status != 'PENDING':
        return False, 'COMMAND_NOT_PENDING'
    if command.device.status != 'ACTIVE':
        return False, 'DEVICE_NOT_ACTIVE'
    if (timezone.now() - command.created_at).total_seconds() > 60:
        return False, 'COMMAND_EXPIRED'
    # Chỉ đường quản trị nội bộ mới tạo nguồn này; API không nhận _source từ client.
    if command.payload.get('_source') == 'APPLIED_CONFIG_RESYNC':
        config = DeviceConfig.objects.filter(
            device_id=command.device_id, version=command.payload.get('config_version'), applied=True,
        ).first()
        if command.command_type != 'APPLY_CONFIG' or not config or config.config_data != command.payload.get('config'):
            return False, 'CONFIG_MISMATCH'
        return True, None
    if not command.actor_id:
        return False, 'ACTOR_REQUIRED'
    # Chỉ lưu sid UUID trong payload nội bộ, tuyệt đối không lưu cookie/session key.
    sid = command.payload.get('_auth_session_id')
    try:
        session = AuthSession.objects.filter(pk=sid, user_id=command.actor_id).first() if sid else None
    except (ValidationError, ValueError, TypeError):
        session = None
    if not session:
        return False, 'SESSION_INVALID'
    valid, session, reason = verify_auth_session(session.session_key, user=session.user)
    if not valid:
        return False, reason
    if session.user.must_change_password:
        return False, 'MUST_CHANGE_PASSWORD'
    try:
        require_device_permission(session.user, command.device, 'OPERATE')
    except PermissionDenied:
        return False, 'DEVICE_PERMISSION_REVOKED'
    if command.command_type == 'APPLY_CONFIG':
        config = DeviceConfig.objects.filter(
            device_id=command.device_id, version=command.payload.get('config_version'),
        ).first()
        if not config or config.config_data != command.payload.get('config'):
            return False, 'CONFIG_MISMATCH'
    return True, None
