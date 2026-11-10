"""
Device Policies Module
Thực thi kiểm soát phạm vi thiết bị (Scope / Multi-tenancy / IDOR Prevention):
- visible_devices(user): Lọc queryset Device trả về theo quyền của người dùng.
- require_device_permission(user, device, action): Kiểm tra quyền cụ thể (VIEW / OPERATE).
"""
from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.utils import timezone
from apps.accounts.policies.roles import has_role, ROLE_ADMIN
from apps.devices.models import Device, DeviceAccess


def visible_devices(user) -> QuerySet[Device]:
    """
    Trả về QuerySet các thiết bị mà user hiện tại được phép nhìn thấy:
    - ADMIN hoặc is_superuser: Nhìn thấy toàn bộ thiết bị.
    - OPERATOR / VIEWER: Chỉ nhìn thấy các thiết bị đã được cấp DeviceAccess còn hiệu lực.
    - Anonymous / Unauthenticated: Trả về rỗng (hoặc chỉ demo dev nếu cấu hình).
    """
    if not user or not user.is_authenticated:
        return Device.objects.none()

    if user.is_superuser or has_role(user, ROLE_ADMIN):
        return Device.objects.all()

    now = timezone.now()
    # Lọc các thiết bị có DeviceAccess còn hiệu lực
    allowed_device_ids = DeviceAccess.objects.filter(
        user=user
    ).exclude(
        expires_at__isnull=False,
        expires_at__lt=now
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
    if not user or not user.is_authenticated:
        raise PermissionDenied("Yêu cầu đăng nhập để truy cập thiết bị.")

    if user.is_superuser or has_role(user, ROLE_ADMIN):
        return True

    now = timezone.now()
    access = DeviceAccess.objects.filter(
        user=user,
        device=device
    ).exclude(
        expires_at__isnull=False,
        expires_at__lt=now
    ).first()

    if not access:
        raise PermissionDenied(f"Bạn không có quyền truy cập tủ mạng '{device.id}'.")

    if action == 'VIEW':
        return True

    if action == 'OPERATE':
        if access.role == 'OPERATE':
            return True
        raise PermissionDenied(f"Tài khoản của bạn chỉ có quyền XEM (VIEW), không được phép ĐIỀU KHIỂN tủ '{device.id}'.")

    raise PermissionDenied(f"Hành động '{action}' không được hỗ trợ trên thiết bị.")
