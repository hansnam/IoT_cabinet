"""Nghiệp vụ quản trị user; HTTP/Admin/command có actor phải gọi cùng service."""
import uuid

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.policies.roles import (
    ALL_ROLES,
    ROLE_ADMIN,
    assign_role,
    get_user_role,
    require_permission,
)
from apps.accounts.security.rate_limit import get_client_ip
from apps.accounts.services.sessions import (
    revoke_all_user_sessions,
    verify_auth_session,
)
from apps.audit.services import AuditService

ADMIN_LOCK_ID = 7062026
EDITABLE = {"username", "full_name", "email", "role", "status", "temporary_password"}


def manage_user(request, target_id=None, **changes):
    """Khóa chung → actor/target → kiểm policy → mutation/revoke/audit cùng transaction."""
    if target_id is not None:
        assert User._meta.pk is not None
        target_id = User._meta.pk.to_python(target_id)

    if set(changes) - EDITABLE:
        raise ValidationError("Có trường quản trị không được phép.")
    
    with transaction.atomic():
        # Mọi mutation quản trị phải lấy khóa này TRƯỚC khóa từng User.
        if connection.vendor != "postgresql":
            raise RuntimeError("Quản trị user yêu cầu PostgreSQL để bảo vệ last-admin.")
        
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [ADMIN_LOCK_ID])

        actor_id = request.user.pk
        ids = {actor_id} | ({target_id} if target_id else set())
        locked = {u.pk: u for u in User.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
        actor = locked.get(actor_id)

        if not actor or not actor.is_active or get_user_role(actor) != ROLE_ADMIN or actor.must_change_password:
            raise PermissionDenied("Cần tài khoản ADMIN đang hoạt động và đã đổi mật khẩu.")
        
        valid, session, _ = verify_auth_session(request.session.session_key, user=actor)
        if not valid or session is None:
            raise PermissionDenied("Phiên quản trị không còn hiệu lực.")
        require_permission(actor, "USER_UPDATE" if target_id else "USER_CREATE")

        target = locked.get(target_id) if target_id else User(created_by=actor)
        if target is None:
            raise ValidationError("Không tìm thấy người dùng.")
        
        old_role = get_user_role(target) if target_id else None
        old_status = target.status

        role = changes.get("role", old_role)
        status = changes.get("status", old_status)
        if role not in ALL_ROLES or status not in User.Status.values:
            raise ValidationError("Role hoặc trạng thái không hợp lệ.")
        
        if status == User.Status.ARCHIVED:
            require_permission(actor, "USER_DELETE")

        losing_admin = target_id and old_role == ROLE_ADMIN and old_status == User.Status.ACTIVE and (
            role != ROLE_ADMIN or status != User.Status.ACTIVE
        )

        if losing_admin:
            others = User.objects.filter(status=User.Status.ACTIVE, is_active=True, groups__name=ROLE_ADMIN).exclude(pk=target.pk)
            if not any(get_user_role(u) == ROLE_ADMIN for u in others):
                raise ValidationError("Không được khóa, archive hoặc hạ quyền ADMIN hoạt động cuối cùng.")
        
        if target_id and "username" in changes and changes["username"].strip().lower() != target.username:
            raise ValidationError("Không đổi username của tài khoản đã tạo.")
        
        for field in ("username", "full_name", "email"):
            if field in changes:
                setattr(target, field, changes[field])

        target.username = target.username.strip().lower()
        target.full_name = target.full_name.strip()
        password = changes.get("temporary_password", "")
        if not target_id and not password:
            raise ValidationError("Cần mật khẩu tạm khi tạo tài khoản.")
        
        target.status = status
        target.is_active = status == User.Status.ACTIVE
        target.is_staff = role == ROLE_ADMIN and target.is_active
        target.is_superuser = False
        target.full_clean(exclude=["password"])
        
        if password:
            validate_password(password, user=target)
            target.set_password(password)
            target.must_change_password = True
            target.password_changed_at = timezone.now()

        target.save()
        assign_role(target, role)
        target.is_staff = role == ROLE_ADMIN and target.is_active
        target.save(update_fields=["is_staff"])

        if target_id and (password or role != old_role or status != old_status):
            revoke_all_user_sessions(target, reason="ADMIN_USER_CHANGED")

        fields = sorted(set(changes) - {"temporary_password"})
        if password:
            fields.append("password")

        AuditService.record_event(
            actor_user=actor, actor_session_id=session.id,
            function_key="users.manage", action="USER_CREATE" if not target_id else "USER_UPDATE",
            outcome="SUCCESS", target_type="USER", target_id=str(target.pk),
            target_name_snapshot=target.username, summary="Quản trị cập nhật tài khoản người dùng",
            request_id=getattr(request, "request_id", None) or uuid.uuid4(),
            ip_address=get_client_ip(request), metadata={"fields": fields},
        )
        return target


def change_status(request, user_id, status):
    return manage_user(request, target_id=user_id, status=status)


def change_role(request, user_id, role):
    return manage_user(request, target_id=user_id, role=role)


def reset_password(request, user_id, password):
    return manage_user(request, target_id=user_id, temporary_password=password)
