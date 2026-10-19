"""
Permissions, SQL Device Scope & Dispatch Authorization Test Suite (Revision 3)
Kiểm thử toàn diện các hợp đồng và các ca phát hiện từ TV3-04A Re-check Revision 2:
1. Ma trận phân quyền Role x Grant (ADMIN, OPERATOR, VIEWER x VIEW/OPERATE).
2. Quy tắc 404 trước 403: thiết bị ngoài VIEW scope hoặc không tồn tại luôn trả 404.
3. Từ chối mã quyền lạ và 3 mã dự phòng hoãn EXT (kể cả ADMIN).
4. Hiệu lực tức thì khi thu hồi grant hoặc đổi vai trò (không lưu cache ORM).
5. visible_devices_query tạo câu lệnh SQL chuẩn xác, count/phân trang không rò rỉ thiết bị ngoài scope.
6. Helper get_user_role và get_user_permissions cho 03B /auth/me.
7. Decorator @require_permission:
   - Trích xuất đúng tham số route (id thay vì device_id).
   - Bắt buộc fail-closed 400 nếu cấu hình device_id_param sai hoặc thiếu.
   - Xếp chồng với @require_auth không kiểm tra lặp.
8. Worker gọi trực tiếp PermissionService không cần Flask request context:
   - Bắt buộc từ chối nếu session bị thu hồi hoặc hết hạn.
   - Bắt buộc từ chối khi actor mang session của user khác (cross-user session probe).
9. Guard thẩm quyền dispatch (authorize_dispatch):
   - Đọc scalar snapshot hiện tại từ DB, bỏ qua dirty/cached instance trong RAM (kiểm tra phân kỳ quyết định giữa DB và RAM).
   - Thẩm định sự tồn tại của DeviceConfig trước mọi nhánh allow (từ chối CONFIG_NOT_FOUND).
   - Lệnh thủ công USER: phiên hết hạn/bị thu hồi, user bị khóa thì từ chối.
   - Lệnh APPLY_CONFIG: cấu hình bền vững, phiên tác giả hết hạn không hủy desired config,
     re-sync config đã applied vẫn được cho phép, SYSTEM không được lén bypass tác giả bị khóa.
10. Fail-closed 503 khi DB gặp sự cố kết nối.
11. Tuân thủ check constraint ck_auth_sessions_expires_after_created, kiểm soát thời gian bằng clock injection.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4
import pytest
from flask import jsonify, request
from sqlalchemy import func, select, text
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError as SQLAlchemyTimeoutError

from backend.app.auth.tokens import issue_access_token
from backend.app.models.command import Command
from backend.app.models.device import Device, DeviceAccess, DeviceConfig
from backend.app.models.iam import AuthSession, Permission, Role, RolePermission, User, UserRole
from backend.app.permissions.decorators import require_auth, require_permission
from backend.app.permissions.messages import DispatchReason
from backend.app.permissions.service import (
    AuthRequired,
    DeviceScopeNotFound,
    DispatchAuthorizationResult,
    PermissionDenied,
    PermissionService,
)
from backend.app.security.context import AuthContext
from backend.app.security.messages import ErrorCode, ErrorMessage


# ==============================================================================
# FIXTURES DỰNG MÔI TRƯỜNG KIỂM THỬ THỰC TẾ
# ==============================================================================

@pytest.fixture
def rbac_setup(transaction_session):
    """Thiết lập sẵn vai trò, quyền hạn P0 và thiết bị mẫu trong PostgreSQL test."""
    roles = {}
    for code, name in [("ADMIN", "System Admin"), ("OPERATOR", "Operator"), ("VIEWER", "Viewer")]:
        r = transaction_session.query(Role).filter_by(code=code).first()
        if not r:
            r = Role(code=code, name=name, is_system=True)
            transaction_session.add(r)
            transaction_session.flush()
        roles[code] = r

    perms = {}
    perm_definitions = [
        ("device.read", "DEVICE"),
        ("telemetry.read", "DEVICE"),
        ("device.control", "DEVICE"),
        ("device.configure", "DEVICE"),
        ("user.read", "GLOBAL"),
        ("user.create", "GLOBAL"),
        ("profile.update", "SELF"),
        # 3 mã hoãn EXT (dù có trong DB vẫn phải bị deny ở tầng service)
        ("user_activity.self.read", "SELF"),
        ("session.self.manage", "SELF"),
        ("session.admin.revoke", "GLOBAL"),
    ]
    for code, scope in perm_definitions:
        p = transaction_session.query(Permission).filter_by(code=code).first()
        if not p:
            p = Permission(code=code, scope_type=scope, description=code)
            transaction_session.add(p)
            transaction_session.flush()
        perms[code] = p

    # Gán quyền cho từng vai trò
    for p in perms.values():
        if not transaction_session.query(RolePermission).filter_by(role_id=roles["ADMIN"].id, permission_id=p.id).first():
            transaction_session.add(RolePermission(role_id=roles["ADMIN"].id, permission_id=p.id))

    for code in ["device.read", "telemetry.read", "device.control", "device.configure"]:
        pid = perms[code].id
        if not transaction_session.query(RolePermission).filter_by(role_id=roles["OPERATOR"].id, permission_id=pid).first():
            transaction_session.add(RolePermission(role_id=roles["OPERATOR"].id, permission_id=pid))

    for code in ["device.read", "telemetry.read"]:
        pid = perms[code].id
        if not transaction_session.query(RolePermission).filter_by(role_id=roles["VIEWER"].id, permission_id=pid).first():
            transaction_session.add(RolePermission(role_id=roles["VIEWER"].id, permission_id=pid))

    transaction_session.flush()

    # Thiết bị mẫu
    dev1 = transaction_session.query(Device).filter_by(device_id="cabinet-001").first()
    if not dev1:
        dev1 = Device(
            device_id="cabinet-001",
            name="Cabinet 001",
            device_type="CONTROLLER",
            data_source="REAL",
            lifecycle_state="ACTIVE",
        )
        transaction_session.add(dev1)

    dev2 = transaction_session.query(Device).filter_by(device_id="sim-unassigned").first()
    if not dev2:
        dev2 = Device(
            device_id="sim-unassigned",
            name="Simulator Unassigned",
            device_type="CONTROLLER",
            data_source="SIMULATED",
            lifecycle_state="ACTIVE",
        )
        transaction_session.add(dev2)

    transaction_session.flush()

    def create_user_helper(role_code: str, username_prefix: str = "user"):
        """Helper tạo User, Session và cấp JWT token chuẩn mực bằng AccessTokenData."""
        uid = uuid4()
        user = User(
            id=uid,
            username=f"{username_prefix}_{uid.hex[:8]}",
            password_hash="$argon2id$v=19$m=19456,t=2,p=1$mockhash",
            full_name=f"Test {role_code}",
            status="ACTIVE",
            token_version=1,
        )
        transaction_session.add(user)
        transaction_session.flush()

        transaction_session.add(UserRole(user_id=user.id, role_id=roles[role_code].id))
        transaction_session.flush()

        now = datetime.now(timezone.utc)
        sess = AuthSession(
            id=uuid4(),
            user_id=user.id,
            token_version=1,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(seconds=900),
        )
        transaction_session.add(sess)
        transaction_session.flush()

        token_data = issue_access_token(
            user_id=user.id,
            session_id=sess.id,
            token_version=1,
            expires_delta=900,
        )
        sess.expires_at = token_data.expires_at
        transaction_session.flush()

        return user, sess, token_data.access_token

    return {
        "roles": roles,
        "perms": perms,
        "create_user": create_user_helper,
    }


# ==============================================================================
# 1. KIỂM THỬ MA TRẬN ROLE X GRANT (VIEW / OPERATE)
# ==============================================================================

def test_admin_has_global_device_scope_and_requires_db_perm(transaction_session, rbac_setup):
    """ADMIN có global scope trên mọi thiết bị nhưng vẫn phải có permission trong DB."""
    admin_user, admin_sess, _ = rbac_setup["create_user"]("ADMIN", "admin")
    actor = AuthContext(admin_user.id, admin_sess.id, 1, "req-1")

    assert PermissionService.require("device.read", device_id="cabinet-001", actor=actor, db_session=transaction_session) is True
    assert PermissionService.require("device.control", device_id="cabinet-001", actor=actor, db_session=transaction_session) is True
    assert PermissionService.require("device.read", device_id="sim-unassigned", actor=actor, db_session=transaction_session) is True

    # Xóa quyền device.control khỏi role ADMIN trong DB
    role_admin = rbac_setup["roles"]["ADMIN"]
    perm_ctrl = rbac_setup["perms"]["device.control"]
    transaction_session.query(RolePermission).filter_by(role_id=role_admin.id, permission_id=perm_ctrl.id).delete()
    transaction_session.flush()

    with pytest.raises(PermissionDenied):
        PermissionService.require("device.control", device_id="cabinet-001", actor=actor, db_session=transaction_session)


def test_operator_operate_vs_view_grant(transaction_session, rbac_setup):
    """Operator có quyền device.control ở vai trò nhưng cần grant OPERATE trên thiết bị."""
    op_user, op_sess, _ = rbac_setup["create_user"]("OPERATOR", "op")
    actor = AuthContext(op_user.id, op_sess.id, 1, "req-2")

    grant = DeviceAccess(
        device_id="cabinet-001",
        user_id=op_user.id,
        access_level="VIEW",
        granted_by=op_user.id,
    )
    transaction_session.add(grant)
    transaction_session.flush()

    assert PermissionService.require("device.read", device_id="cabinet-001", actor=actor, db_session=transaction_session) is True

    with pytest.raises(PermissionDenied):
        PermissionService.require("device.control", device_id="cabinet-001", actor=actor, db_session=transaction_session)

    grant.access_level = "OPERATE"
    transaction_session.flush()

    assert PermissionService.require("device.control", device_id="cabinet-001", actor=actor, db_session=transaction_session) is True


def test_viewer_with_operate_grant_cannot_exceed_role(transaction_session, rbac_setup):
    """Viewer dù có grant OPERATE cũng không thể điều khiển (grant không vượt role)."""
    vw_user, vw_sess, _ = rbac_setup["create_user"]("VIEWER", "viewer")
    actor = AuthContext(vw_user.id, vw_sess.id, 1, "req-3")

    grant = DeviceAccess(
        device_id="cabinet-001",
        user_id=vw_user.id,
        access_level="OPERATE",
        granted_by=vw_user.id,
    )
    transaction_session.add(grant)
    transaction_session.flush()

    assert PermissionService.require("device.read", device_id="cabinet-001", actor=actor, db_session=transaction_session) is True

    with pytest.raises(PermissionDenied):
        PermissionService.require("device.control", device_id="cabinet-001", actor=actor, db_session=transaction_session)


# ==============================================================================
# 2. KIỂM THỬ QUY TẮC 404 TRƯỚC 403 (CHỐNG LỘ THÔNG TIN THIẾT BỊ)
# ==============================================================================

def test_device_outside_view_scope_returns_404_before_action_check(transaction_session, rbac_setup):
    """Thiết bị ngoài VIEW scope hoặc không tồn tại phải trả về 404 trước khi kiểm tra action."""
    op_user, op_sess, _ = rbac_setup["create_user"]("OPERATOR", "op_scope")
    actor = AuthContext(op_user.id, op_sess.id, 1, "req-4")

    with pytest.raises(DeviceScopeNotFound):
        PermissionService.require("device.read", device_id="sim-unassigned", actor=actor, db_session=transaction_session)

    with pytest.raises(DeviceScopeNotFound):
        PermissionService.require("device.control", device_id="sim-unassigned", actor=actor, db_session=transaction_session)

    with pytest.raises(DeviceScopeNotFound):
        PermissionService.require("completely.invalid.permission", device_id="sim-unassigned", actor=actor, db_session=transaction_session)

    with pytest.raises(DeviceScopeNotFound):
        PermissionService.require("device.read", device_id="non-existent-device-xyz", actor=actor, db_session=transaction_session)


# ==============================================================================
# 3. KIỂM THỬ TỪ CHỐI MÃ QUYỀN LẠ VÀ MÃ HOÃN EXT
# ==============================================================================

@pytest.mark.parametrize("invalid_perm", [
    "user_activity.self.read",
    "session.self.manage",
    "session.admin.revoke",
    "fan.manage",
    "ai.inference",
    "unknown.permission.code",
])
def test_unknown_and_ext_reserved_permissions_always_deny(transaction_session, rbac_setup, invalid_perm):
    """Mọi quyền lạ và mã hoãn EXT đều bị từ chối 403, kể cả đối với ADMIN."""
    admin_user, admin_sess, _ = rbac_setup["create_user"]("ADMIN", "admin_ext")
    actor = AuthContext(admin_user.id, admin_sess.id, 1, "req-5")

    with pytest.raises(PermissionDenied):
        PermissionService.require(invalid_perm, actor=actor, db_session=transaction_session)

    with pytest.raises(PermissionDenied):
        PermissionService.require(invalid_perm, device_id="cabinet-001", actor=actor, db_session=transaction_session)


# ==============================================================================
# 4. HIỆU LỰC TỨC THÌ KHI GRANT HẾT HẠN HOẶC VAI TRÒ THAY ĐỔI
# ==============================================================================

def test_grant_expiry_takes_effect_immediately(transaction_session, rbac_setup):
    """Grant hết hạn (expires_at <= now) lập tức biến thiết bị thành 404 ở lần kiểm tra tiếp."""
    op_user, op_sess, _ = rbac_setup["create_user"]("OPERATOR", "op_expiry")
    actor = AuthContext(op_user.id, op_sess.id, 1, "req-6")

    now = datetime.now(timezone.utc)
    grant = DeviceAccess(
        device_id="cabinet-001",
        user_id=op_user.id,
        access_level="OPERATE",
        expires_at=now + timedelta(seconds=60),
        granted_by=op_user.id,
    )
    transaction_session.add(grant)
    transaction_session.flush()

    assert PermissionService.require("device.read", device_id="cabinet-001", actor=actor, db_session=transaction_session, now=now) is True

    # Clock injection sau 61 giây
    future_clock = now + timedelta(seconds=61)
    with pytest.raises(DeviceScopeNotFound):
        PermissionService.require("device.read", device_id="cabinet-001", actor=actor, db_session=transaction_session, now=future_clock)


def test_user_role_change_takes_effect_immediately(transaction_session, rbac_setup):
    """Thay đổi vai trò người dùng trong DB có hiệu lực ngay lập tức, không phụ thuộc cache."""
    user, sess, _ = rbac_setup["create_user"]("OPERATOR", "demote_user")
    actor = AuthContext(user.id, sess.id, 1, "req-7")

    transaction_session.add(DeviceAccess(
        device_id="cabinet-001",
        user_id=user.id,
        access_level="OPERATE",
        granted_by=user.id,
    ))
    transaction_session.flush()

    assert PermissionService.require("device.control", device_id="cabinet-001", actor=actor, db_session=transaction_session) is True

    # Giáng cấp trực tiếp bằng SQL
    viewer_role = rbac_setup["roles"]["VIEWER"]
    transaction_session.execute(
        text("UPDATE user_roles SET role_id = :rid WHERE user_id = :uid"),
        {"rid": viewer_role.id, "uid": user.id}
    )
    transaction_session.flush()

    with pytest.raises(PermissionDenied):
        PermissionService.require("device.control", device_id="cabinet-001", actor=actor, db_session=transaction_session)


# ==============================================================================
# 5. LỌC PHẠM VI THIẾT BỊ TẠI TẦNG SQL (VISIBLE_DEVICES_QUERY)
# ==============================================================================

def test_visible_devices_query_scoping_and_count(transaction_session, rbac_setup):
    """visible_devices_query lọc tại SQL, count/phân trang không làm rò rỉ thiết bị ngoài scope."""
    op_user, _, _ = rbac_setup["create_user"]("OPERATOR", "op_sql")
    admin_user, _, _ = rbac_setup["create_user"]("ADMIN", "admin_sql")

    transaction_session.add(DeviceAccess(
        device_id="cabinet-001",
        user_id=op_user.id,
        access_level="VIEW",
        granted_by=admin_user.id,
    ))
    transaction_session.flush()

    op_stmt = PermissionService.visible_devices_query(op_user, db_session=transaction_session)
    op_devices = transaction_session.scalars(op_stmt).all()
    assert len(op_devices) == 1
    assert op_devices[0].device_id == "cabinet-001"

    op_count = transaction_session.scalar(select(func.count()).select_from(op_stmt.subquery()))
    assert op_count == 1

    admin_stmt = PermissionService.visible_devices_query(admin_user, db_session=transaction_session)
    admin_devices = transaction_session.scalars(admin_stmt).all()
    assert len(admin_devices) >= 2
    dev_ids = {d.device_id for d in admin_devices}
    assert "cabinet-001" in dev_ids
    assert "sim-unassigned" in dev_ids


# ==============================================================================
# 6. HELPER ROLE & PERMISSIONS CHO BƯỚC 03B /auth/me
# ==============================================================================

def test_helpers_get_user_role_and_permissions(transaction_session, rbac_setup):
    """get_user_role và get_user_permissions trả về đúng vai trò và danh sách quyền P0."""
    admin_user, _, _ = rbac_setup["create_user"]("ADMIN", "admin_helper")
    vw_user, _, _ = rbac_setup["create_user"]("VIEWER", "viewer_helper")

    assert PermissionService.get_user_role(admin_user.id, db_session=transaction_session) == "ADMIN"
    assert PermissionService.get_user_role(vw_user.id, db_session=transaction_session) == "VIEWER"

    admin_perms = PermissionService.get_user_permissions(admin_user.id, db_session=transaction_session)
    assert "device.control" in admin_perms
    assert "user.create" in admin_perms
    assert "user_activity.self.read" not in admin_perms
    assert "session.self.manage" not in admin_perms

    vw_perms = PermissionService.get_user_permissions(vw_user.id, db_session=transaction_session)
    assert "device.read" in vw_perms
    assert "device.control" not in vw_perms


# ==============================================================================
# 7. KIỂM THỬ DECORATOR @require_permission QUA ROUTE FLASK
# ==============================================================================

def test_require_permission_decorator_routes(app, transaction_session, rbac_setup):
    """Kiểm tra @require_permission với tham số động (id thay vì device_id) và HTTP status."""
    op_user, _, op_token = rbac_setup["create_user"]("OPERATOR", "op_route")
    vw_user, _, vw_token = rbac_setup["create_user"]("VIEWER", "vw_route")

    transaction_session.add(DeviceAccess(
        device_id="cabinet-001",
        user_id=op_user.id,
        access_level="OPERATE",
        granted_by=op_user.id,
    ))
    transaction_session.add(DeviceAccess(
        device_id="cabinet-001",
        user_id=vw_user.id,
        access_level="VIEW",
        granted_by=op_user.id,
    ))
    transaction_session.flush()

    @app.route("/test-api/devices/<id>/control", methods=["POST"])
    @require_permission("device.control", device_id_param="id")
    def test_control_view(id):
        return jsonify({"status": "SUCCESS", "device_id": id}), 200

    client = app.test_client()

    # Chưa gửi token -> 401
    res = client.post("/test-api/devices/cabinet-001/control")
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED

    # Operator có OPERATE grant -> 200 OK
    res = client.post(
        "/test-api/devices/cabinet-001/control",
        headers={"Authorization": f"Bearer {op_token}"}
    )
    assert res.status_code == 200
    assert res.get_json()["status"] == "SUCCESS"

    # Viewer chỉ có VIEW grant gọi điều khiển -> 403 Forbidden
    res = client.post(
        "/test-api/devices/cabinet-001/control",
        headers={"Authorization": f"Bearer {vw_token}"}
    )
    assert res.status_code == 403
    assert res.get_json()["error"]["code"] == ErrorCode.FORBIDDEN

    # Operator gọi trên sim-unassigned (ngoài scope) -> 404 Not Found
    res = client.post(
        "/test-api/devices/sim-unassigned/control",
        headers={"Authorization": f"Bearer {op_token}"}
    )
    assert res.status_code == 404
    assert res.get_json()["error"]["code"] == ErrorCode.NOT_FOUND


def test_require_permission_wrong_device_param_fails_closed(app, transaction_session, rbac_setup):
    """Khi decorator cấu hình device_id_param nhưng route không có param đó -> bắt buộc fail-closed 400."""
    op_user, _, op_token = rbac_setup["create_user"]("OPERATOR", "op_wrong_param")
    transaction_session.add(DeviceAccess(
        device_id="cabinet-001",
        user_id=op_user.id,
        access_level="OPERATE",
        granted_by=op_user.id,
    ))
    transaction_session.flush()

    # Route định nghĩa tham số là <id>, nhưng decorator yêu cầu device_id_param="device_id"
    @app.route("/test-api/wrong-param/<id>", methods=["POST"])
    @require_permission("device.control", device_id_param="device_id")
    def wrong_param_view(id):
        return jsonify({"status": "SUCCESS"}), 200

    client = app.test_client()
    # Gọi trên sim-unassigned mà Operator không có quyền
    res = client.post(
        "/test-api/wrong-param/sim-unassigned",
        headers={"Authorization": f"Bearer {op_token}"}
    )
    # Bắt buộc fail-closed 400 Bad Request, tuyệt đối không được trả 200
    assert res.status_code == 400
    assert res.get_json()["error"]["code"] == ErrorCode.BAD_REQUEST


def test_require_permission_stacked_with_require_auth(app, transaction_session, rbac_setup):
    """Khi xếp chồng @require_auth và @require_permission, request.auth được tái sử dụng, không verify DB lặp lại."""
    op_user, _, op_token = rbac_setup["create_user"]("OPERATOR", "op_stacked")
    transaction_session.add(DeviceAccess(
        device_id="cabinet-001",
        user_id=op_user.id,
        access_level="OPERATE",
        granted_by=op_user.id,
    ))
    transaction_session.flush()

    @app.route("/test-api/devices/<id>/stacked", methods=["POST"])
    @require_auth
    @require_permission("device.control", device_id_param="id")
    def stacked_view(id):
        return jsonify({"status": "SUCCESS", "user_id": str(request.auth.user_id)}), 200

    client = app.test_client()
    res = client.post(
        "/test-api/devices/cabinet-001/stacked",
        headers={"Authorization": f"Bearer {op_token}"}
    )
    assert res.status_code == 200
    assert res.get_json()["status"] == "SUCCESS"
    assert res.get_json()["user_id"] == str(op_user.id)


# ==============================================================================
# 8. WORKER GỌI TRỰC TIẾP KHÔNG CẦN FLASK REQUEST CONTEXT
# ==============================================================================

def test_worker_direct_call_without_request_context(transaction_session, rbac_setup):
    """Worker nền gọi trực tiếp PermissionService với actor hợp lệ, không cần request context."""
    op_user, op_sess, _ = rbac_setup["create_user"]("OPERATOR", "worker_user")
    actor = AuthContext(op_user.id, op_sess.id, 1, "worker-job-1", actor_type="USER")

    transaction_session.add(DeviceAccess(
        device_id="cabinet-001",
        user_id=op_user.id,
        access_level="OPERATE",
        granted_by=op_user.id,
    ))
    transaction_session.flush()

    result = PermissionService.require(
        "device.control",
        device_id="cabinet-001",
        actor=actor,
        db_session=transaction_session,
    )
    assert result is True


def test_worker_direct_call_with_revoked_session_fails(transaction_session, rbac_setup):
    """Worker truyền actor có session đã revoke -> require bắt buộc từ chối AuthRequired."""
    op_user, op_sess, _ = rbac_setup["create_user"]("OPERATOR", "worker_revoked")
    transaction_session.add(DeviceAccess(
        device_id="cabinet-001",
        user_id=op_user.id,
        access_level="OPERATE",
        granted_by=op_user.id,
    ))
    transaction_session.flush()

    # Thu hồi session trong DB
    op_sess.revoked_at = datetime.now(timezone.utc)
    transaction_session.flush()

    actor = AuthContext(op_user.id, op_sess.id, 1, "worker-job-revoked", actor_type="USER")

    with pytest.raises(AuthRequired):
        PermissionService.require("device.control", device_id="cabinet-001", actor=actor, db_session=transaction_session)


def test_worker_direct_call_with_cross_user_session_fails(transaction_session, rbac_setup):
    """Worker truyền actor ADMIN nhưng mang session của VIEWER (dù token_version đều là 1) -> bắt buộc AuthRequired."""
    admin_user, _, _ = rbac_setup["create_user"]("ADMIN", "admin_cross")
    vw_user, vw_sess, _ = rbac_setup["create_user"]("VIEWER", "viewer_cross")

    # Ghép user_id của ADMIN với session_id của VIEWER (cả hai đều có token_version=1)
    forged_actor = AuthContext(
        user_id=admin_user.id,
        session_id=vw_sess.id,
        token_version=1,
        request_id="worker-cross-probe",
        actor_type="USER",
    )

    # Bắt buộc từ chối AuthRequired vì session không thuộc sở hữu của admin_user
    with pytest.raises(AuthRequired):
        PermissionService.require("user.read", actor=forged_actor, db_session=transaction_session)


# ==============================================================================
# 9. GUARD THẨM QUYỀN DISPATCH (AUTHORIZE_DISPATCH)
# ==============================================================================

def test_authorize_dispatch_manual_user_command(transaction_session, rbac_setup):
    """authorize_dispatch kiểm tra phiên, trạng thái user và quyền trước khi dispatch lệnh thủ công."""
    op_user, op_sess, _ = rbac_setup["create_user"]("OPERATOR", "dispatch_op")
    transaction_session.add(DeviceAccess(
        device_id="cabinet-001",
        user_id=op_user.id,
        access_level="OPERATE",
        granted_by=op_user.id,
    ))
    transaction_session.flush()

    cmd = Command(
        command_id=uuid4(),
        device_id="cabinet-001",
        requested_by=op_user.id,
        source="USER",
        requesting_session_id=op_sess.id,
        type="SET_FAN",
        params={"on": True},
        device_command_seq=1,
        status="PENDING",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    transaction_session.add(cmd)
    transaction_session.flush()

    # 1. Hợp lệ
    res = PermissionService.authorize_dispatch(cmd, db_session=transaction_session)
    assert res.allowed is True
    assert res.reason == DispatchReason.AUTHORIZED

    # 2. Phiên bị thu hồi
    op_sess.revoked_at = datetime.now(timezone.utc)
    transaction_session.flush()
    res = PermissionService.authorize_dispatch(cmd, db_session=transaction_session)
    assert res.allowed is False
    assert res.reason == DispatchReason.SESSION_REVOKED

    # 3. Phiên hết hạn (cập nhật created_at về quá khứ để thỏa mãn check constraint created_at < expires_at)
    now = datetime.now(timezone.utc)
    op_sess.revoked_at = None
    op_sess.created_at = now - timedelta(minutes=30)
    op_sess.last_seen_at = now - timedelta(minutes=20)
    op_sess.expires_at = now - timedelta(seconds=1)
    transaction_session.flush()
    res = PermissionService.authorize_dispatch(cmd, db_session=transaction_session)
    assert res.allowed is False
    assert res.reason == DispatchReason.SESSION_EXPIRED

    # 4. Tài khoản bị khóa
    op_sess.expires_at = now + timedelta(minutes=15)
    op_user.status = "LOCKED"
    transaction_session.flush()
    res = PermissionService.authorize_dispatch(cmd, db_session=transaction_session)
    assert res.allowed is False
    assert res.reason == DispatchReason.USER_INACTIVE

    # 5. Session không thuộc về user yêu cầu lệnh (cross-user session)
    _, vw_sess, _ = rbac_setup["create_user"]("VIEWER", "dispatch_vw")
    op_user.status = "ACTIVE"
    cmd.requesting_session_id = vw_sess.id
    transaction_session.flush()
    res = PermissionService.authorize_dispatch(cmd, db_session=transaction_session)
    assert res.allowed is False
    assert res.reason == DispatchReason.SESSION_NOT_FOUND


def test_authorize_dispatch_ignores_dirty_orm_state_and_enforces_db_truth(transaction_session, rbac_setup):
    """Chứng minh authorize_dispatch đọc DB scalar snapshot, bỏ qua RAM dirty state:
    - Trong DB: Device.applied_config_version là NULL (chưa từng apply).
    - Tác giả cấu hình version 2 bị khóa (User.status = 'LOCKED').
    - Nếu xét theo DB: First apply phải bị TỪ CHỐI (AUTHOR_INACTIVE).
    - Trong RAM: Gán bẩn dev.applied_config_version = 2 mà không flush.
    - Nếu đọc RAM bẩn: Helper sẽ tưởng là Re-sync cấu hình đã apply và cho phép (AUTHORIZED_APPLIED_CONFIG).
    - Kết quả: Helper bắt buộc đọc DB và từ chối AUTHOR_INACTIVE.
    """
    admin_user, admin_sess, _ = rbac_setup["create_user"]("ADMIN", "admin_dirty_test")

    # 1. Tạo DeviceConfig version 2 do admin tạo
    cfg = DeviceConfig(
        device_id="cabinet-001",
        version=2,
        profile="CONTROLLER",
        created_by=admin_user.id,
        canonical_json="{}",
        content_hash="mockhash_v2",
    )
    transaction_session.add(cfg)

    # 2. Tạo Command APPLY_CONFIG version 2 trong DB
    cmd = Command(
        command_id=uuid4(),
        device_id="cabinet-001",
        source="SYSTEM",
        type="APPLY_CONFIG",
        config_version=2,
        device_command_seq=15,
        status="PENDING",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    transaction_session.add(cmd)

    # 3. Khóa tài khoản tác giả cấu hình trong DB
    admin_user.status = "LOCKED"

    # Flush toàn bộ trạng thái baseline vào PostgreSQL
    transaction_session.flush()

    # 4. Sau khi flush xong, gán bẩn vào RAM mà KHÔNG flush:
    dev = transaction_session.query(Device).filter_by(device_id="cabinet-001").one()
    dev.applied_config_version = 2  # Chỉ nằm trong bộ nhớ Python

    # 5. Gọi helper ngay lập tức mà không flush:
    res = PermissionService.authorize_dispatch(cmd, db_session=transaction_session)

    # Bắt buộc từ chối AUTHOR_INACTIVE dựa trên dữ liệu thật của DB!
    assert res.allowed is False
    assert res.reason == DispatchReason.AUTHOR_INACTIVE


def test_authorize_dispatch_missing_config_denied(transaction_session, rbac_setup):
    """Command yêu cầu config_version không tồn tại trong DeviceConfig -> từ chối CONFIG_NOT_FOUND."""
    cmd = Command(
        command_id=uuid4(),
        device_id="cabinet-001",
        source="SYSTEM",
        type="APPLY_CONFIG",
        config_version=999,
        device_command_seq=16,
        status="PENDING",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    transaction_session.add(cmd)
    transaction_session.flush()

    res = PermissionService.authorize_dispatch(cmd, db_session=transaction_session)
    assert res.allowed is False
    assert res.reason == DispatchReason.CONFIG_NOT_FOUND


def test_authorize_dispatch_unpersisted_command_denied(transaction_session, rbac_setup):
    """Lệnh chỉ khởi tạo trong RAM mà chưa được lưu vào DB (unpersisted) phải bị từ chối ngay."""
    op_user, op_sess, _ = rbac_setup["create_user"]("OPERATOR", "unpersisted_op")
    cmd = Command(
        command_id=uuid4(),
        device_id="cabinet-001",
        requested_by=op_user.id,
        source="USER",
        requesting_session_id=op_sess.id,
        type="SET_FAN",
        device_command_seq=99,
        status="PENDING",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    # Chưa add hay flush vào transaction_session
    res = PermissionService.authorize_dispatch(cmd, db_session=transaction_session)
    assert res.allowed is False
    assert res.reason == DispatchReason.COMMAND_NOT_PERSISTED


def test_authorize_dispatch_apply_config_lifecycle(transaction_session, rbac_setup):
    """Kiểm tra chính sách cấu hình bền vững cho APPLY_CONFIG theo mục 2.2 V1.3."""
    admin_user, admin_sess, _ = rbac_setup["create_user"]("ADMIN", "author_admin")

    cfg = DeviceConfig(
        device_id="cabinet-001",
        version=2,
        profile="CONTROLLER",
        created_by=admin_user.id,
        canonical_json="{}",
        content_hash="mockhash123",
    )
    transaction_session.add(cfg)
    transaction_session.flush()

    cmd = Command(
        command_id=uuid4(),
        device_id="cabinet-001",
        requested_by=admin_user.id,
        source="USER",
        requesting_session_id=admin_sess.id,
        type="APPLY_CONFIG",
        config_version=2,
        device_command_seq=2,
        status="PENDING",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    transaction_session.add(cmd)
    transaction_session.flush()

    # 1. First apply hợp lệ -> AUTHORIZED_FIRST_APPLY
    res = PermissionService.authorize_dispatch(cmd, db_session=transaction_session)
    assert res.allowed is True
    assert res.reason == DispatchReason.AUTHORIZED_FIRST_APPLY

    # 2. Phiên của tác giả hết hạn: First apply VẪN ĐƯỢC PHÉP (phiên hết hạn không hủy desired config)
    now = datetime.now(timezone.utc)
    admin_sess.created_at = now - timedelta(minutes=30)
    admin_sess.expires_at = now - timedelta(seconds=10)
    transaction_session.flush()
    res = PermissionService.authorize_dispatch(cmd, db_session=transaction_session)
    assert res.allowed is True
    assert res.reason == DispatchReason.AUTHORIZED_FIRST_APPLY

    # 3. Tác giả bị khóa tài khoản trước first apply -> AUTHOR_INACTIVE (chặn apply)
    admin_user.status = "LOCKED"
    transaction_session.flush()
    res = PermissionService.authorize_dispatch(cmd, db_session=transaction_session)
    assert res.allowed is False
    assert res.reason == DispatchReason.AUTHOR_INACTIVE

    # 4. Khi thiết bị đã áp dụng thành công (re-sync / reconnect):
    dev = transaction_session.query(Device).filter_by(device_id="cabinet-001").one()
    dev.applied_config_version = 2
    transaction_session.flush()

    # Lệnh re-sync từ SYSTEM
    sync_cmd = Command(
        command_id=uuid4(),
        device_id="cabinet-001",
        source="SYSTEM",
        type="APPLY_CONFIG",
        config_version=2,
        device_command_seq=3,
        status="PENDING",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    transaction_session.add(sync_cmd)
    transaction_session.flush()

    res = PermissionService.authorize_dispatch(sync_cmd, db_session=transaction_session)
    assert res.allowed is True
    assert res.reason == DispatchReason.AUTHORIZED_APPLIED_CONFIG


def test_authorize_dispatch_read_only_invariance(transaction_session, rbac_setup):
    """authorize_dispatch hoàn toàn không làm thay đổi hay commit dữ liệu của caller."""
    op_user, op_sess, _ = rbac_setup["create_user"]("OPERATOR", "ro_op")
    cmd = Command(
        command_id=uuid4(),
        device_id="cabinet-001",
        requested_by=op_user.id,
        source="USER",
        requesting_session_id=op_sess.id,
        type="SET_FAN",
        device_command_seq=4,
        status="PENDING",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    transaction_session.add(cmd)
    transaction_session.flush()

    PermissionService.authorize_dispatch(cmd, db_session=transaction_session)

    assert cmd.status == "PENDING"
    assert transaction_session.is_modified(cmd) is False


# ==============================================================================
# 10. FAIL-CLOSED 503 KHI DATABASE KHÔNG KHẢ DỤNG
# ==============================================================================

@pytest.mark.parametrize("exc_cls", [OperationalError, InterfaceError, SQLAlchemyTimeoutError])
def test_decorator_fail_closed_503_on_db_unavailable(app, transaction_session, rbac_setup, exc_cls):
    """Khi DB mất kết nối hoặc timeout, @require_permission trả về 503 fail-closed."""
    admin_user, _, admin_token = rbac_setup["create_user"]("ADMIN", "db_err_user")

    @app.route("/test-api/db-error-check", methods=["GET"])
    @require_permission("user.read")
    def error_check_view():
        return jsonify({"status": "OK"}), 200

    client = app.test_client()

    with patch.object(PermissionService, "require", side_effect=exc_cls("statement", "params", "DB down")):
        res = client.get(
            "/test-api/db-error-check",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert res.status_code == 503
        data = res.get_json()
        assert data["error"]["code"] == ErrorCode.SERVICE_UNAVAILABLE
        assert data["error"]["message"] == ErrorMessage.SERVICE_UNAVAILABLE
