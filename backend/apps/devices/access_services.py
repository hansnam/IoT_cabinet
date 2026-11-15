"""Cấp/thu hồi DeviceAccess có sẵn; không tạo thêm bảng phân quyền."""
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.policies.roles import ROLE_ADMIN, get_user_role
from apps.audit.services import AuditService
from apps.devices.models import DeviceAccess


def require_access_admin(actor):
    """Đọc lại actor để không dùng role/trạng thái cũ khi quản trị quyền."""
    current = User.objects.get(pk=actor.pk)
    if not current.is_active or current.must_change_password or get_user_role(current) != ROLE_ADMIN:
        raise PermissionDenied('Chỉ ADMIN đang hoạt động được quản lý quyền thiết bị.')
    return current


@transaction.atomic
def grant_device_access(request, user, device, role, expires_at=None):
    """Ghi đè grant theo cặp user–device và ghi audit trong cùng transaction."""
    actor = require_access_admin(request.user)
    if role not in ('VIEW', 'OPERATE'):
        raise ValidationError('Mức quyền phải là VIEW hoặc OPERATE.')
    if expires_at and expires_at <= timezone.now():
        raise ValidationError('Thời điểm hết hạn phải ở tương lai.')
    grant, _ = DeviceAccess.objects.update_or_create(
        user=user, device=device,
        defaults={'role': role, 'expires_at': expires_at, 'granted_by': actor},
    )
    AuditService.record_event(
        actor_user=actor, function_key='DEVICE_ACCESS', action='GRANT', outcome='SUCCESS',
        summary='Cấp quyền truy cập thiết bị', request_id=getattr(request, 'request_id', None) or uuid.uuid4(),
        target_type='DEVICE', target_id=device.pk, device_id=device.pk,
        metadata={'target_user_id': str(user.pk), 'role': role},
    )
    return grant


@transaction.atomic
def revoke_device_access(request, grant):
    """Xóa grant để API/worker từ chối ở lần kiểm tra tiếp theo; audit giữ dấu vết."""
    actor = require_access_admin(request.user)
    AuditService.record_event(
        actor_user=actor, function_key='DEVICE_ACCESS', action='REVOKE', outcome='SUCCESS',
        summary='Thu hồi quyền truy cập thiết bị', request_id=getattr(request, 'request_id', None) or uuid.uuid4(),
        target_type='DEVICE', target_id=grant.device_id, device_id=grant.device_id,
        metadata={'target_user_id': str(grant.user_id)},
    )
    grant.delete()
