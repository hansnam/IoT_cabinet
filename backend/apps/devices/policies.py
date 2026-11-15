"""
Device Policies Module
Thực thi kiểm soát phạm vi thiết bị (Scope / Multi-tenancy / IDOR Prevention):
- visible_devices(user): Lọc queryset Device trả về theo quyền của người dùng.
- require_device_permission(user, device, action): Kiểm tra quyền cụ thể (VIEW / OPERATE).
"""
from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.utils import timezone
from apps.accounts.policies.roles import get_user_role, ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER
from apps.devices.models import Device, DeviceAccess


def visible_devices(user) -> QuerySet[Device]:
    """
    Trả về QuerySet các thiết bị mà user hiện tại được phép nhìn thấy:
    - ADMIN: Nhìn thấy toàn bộ thiết bị.
    - OPERATOR / VIEWER: Chỉ nhìn thấy các thiết bị đã được cấp DeviceAccess còn hiệu lực.
    - Anonymous / Unauthenticated hoặc vai trò không hợp lệ: Trả về rỗng.
    """
    if not user or not user.is_authenticated or not user.is_active or user.status != 'ACTIVE':
        return Device.objects.none()

    role = get_user_role(user)
    if role == ROLE_ADMIN:
        return Device.objects.all()
    if role not in (ROLE_OPERATOR, ROLE_VIEWER):
        return Device.objects.none()

    now = timezone.now()
    # Lọc các thiết bị có DeviceAccess còn hiệu lực
    allowed_device_ids = DeviceAccess.objects.filter(
        user=user
    ).exclude(
        expires_at__isnull=False,
        expires_at__lte=now
    ).values_list('device_id', flat=True)

    return Device.objects.filter(id__in=allowed_device_ids)


def require_device_permission(user, device: Device, action: str = 'VIEW') -> bool:
    """
    Kiểm tra xem user có quyền thực hiện action trên device hay không:
    - action = 'VIEW': Cho phép nếu là ADMIN hoặc có quyền VIEW/OPERATE.
    - action = 'OPERATE': Cho phép nếu là ADMIN hoặc có quyền OPERATE.

    Trả về:
        bool: True nếu hợp lệ.

    Ngoại lệ:
        PermissionDenied: Nếu không đủ quyền hạn.
    """
    if action not in ('VIEW', 'OPERATE'):
        raise PermissionDenied('Hành động không được hỗ trợ.')
    if not user or not user.is_authenticated or not user.is_active or user.status != 'ACTIVE':
        raise PermissionDenied("Yêu cầu đăng nhập để truy cập thiết bị.")

    role = get_user_role(user)
    if role == ROLE_ADMIN:
        return True
    if role not in (ROLE_OPERATOR, ROLE_VIEWER):
        raise PermissionDenied('Tài khoản chưa có vai trò hợp lệ.')

    now = timezone.now()
    access = DeviceAccess.objects.filter(
        user=user,
        device=device
    ).exclude(
        expires_at__isnull=False,
        expires_at__lte=now
    ).first()

    if not access:
        raise PermissionDenied(f"Bạn không có quyền truy cập tủ mạng '{device.id}'.")

    if action == 'VIEW':
        return True

    if action == 'OPERATE':
        # VIEWER không được điều khiển, kể cả khi grant vô tình ghi OPERATE.
        if role == ROLE_OPERATOR and access.role == 'OPERATE':
            return True
        raise PermissionDenied(f"Tài khoản của bạn chỉ có quyền XEM (VIEW), không được phép ĐIỀU KHIỂN tủ '{device.id}'.")

    raise PermissionDenied(f"Hành động '{action}' không được hỗ trợ trên thiết bị.")
