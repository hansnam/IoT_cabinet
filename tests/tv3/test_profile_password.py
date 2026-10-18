"""
Auth Profile Update & Change Password Test Suite (TV3-03C Revision 4)
Kiểm thử toàn diện trên PostgreSQL test database thực tế:
1. PATCH /api/v1/auth/me thành công: cập nhật full_name (chuỗi 1-128, hỗ trợ Unicode tiếng Việt, trim khoảng trắng).
2. Kiểm tra allowlist cập nhật profile: từ chối các trường lạ (user_id, role, status, email, token_version) bằng HTTP 400.
3. Kiểm tra tính độc lập của profile update: không tự ý thay đổi password, token_version hay thu hồi phiên hiện tại.
4. Kiểm tra phân quyền profile: người dùng thiếu quyền profile.update bị từ chối bằng HTTP 403 Forbidden.
5. Recheck sau initial guard: thu hồi role-permission trong DB sau guard khiến service ném 403 Forbidden.
6. POST /api/v1/auth/change-password: từ chối mật khẩu cũ sai bằng HTTP 401 Unauthorized, không thay đổi hash/version.
7. Từ chối mật khẩu mới trùng mật khẩu cũ bằng HTTP 400 Bad Request, không clear cờ must_change_password.
8. Rate limiting khi thử sai mật khẩu cũ:
   - Tối đa 5 lần lỗi/user -> lần 6 nhận HTTP 429 Retry-After, không gọi hàm băm/xác minh mật khẩu.
   - Tối đa 20 lần lỗi/IP từ 20 user khác nhau -> lần 21 nhận HTTP 429 Retry-After.
   - Phân tách domain hai chiều: lỗi change-password không ảnh hưởng login và lỗi login không ảnh hưởng change-password (chuỗi mật khẩu sai hợp lệ >= 12 ký tự).
   - Kiểm soát tính toán Retry-After bằng clock injection mà không sleep.
9. Đổi mật khẩu thành công:
   - Nâng token_version lên đúng 1 đơn vị.
   - Xóa cờ must_change_password về False và cập nhật password_changed_at UTC.
   - Băm mật khẩu mới bằng Argon2id.
   - Thu hồi toàn bộ phiên đăng nhập của người dùng (kể cả phiên hiện tại).
   - Bảo toàn mốc thời gian và lý do của các phiên đã bị thu hồi từ trước; không ảnh hưởng phiên của người dùng khác.
   - Trả về HTTP 200 với data.login_required=true, không trả access token mới hay cookie.
10. Kiểm tra hậu nghiệm sau đổi mật khẩu:
   - Toàn bộ access token cũ nhận HTTP 401 Unauthorized.
   - Mật khẩu cũ không đăng nhập được (HTTP 401).
   - Mật khẩu mới đăng nhập thành công (HTTP 200) và cấp token mang token_version mới.
11. Luồng tài khoản must_change_password=True:
    - Được phép gọi PATCH /me và POST /change-password.
    - Gọi route điều khiển bị chặn HTTP 403 Forbidden.
    - Sau khi đổi mật khẩu, đăng nhập lại để nhận token mới và truy cập bình thường.
12. Khóa dòng với populate_existing=True và recheck sau lock cho cả profile và password.
13. Probe clock tiến qua expiry lúc câu lệnh SELECT FOR UPDATE thực thi:
    - Phải từ chối bằng HTTP 401 AuthenticationFailed.
    - Không thực hiện bất kỳ mutation nào (tên, hash, version, must_change giữ nguyên) và không revoke session.
14. Bền vững dữ liệu và giao dịch nguyên tử:
    - Tham số hóa profile x change-password x writer failure x commit failure.
    - Sai old password hoặc bị rate limit mà writer/commit lỗi không được trả 401/429 giả mạo.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID, uuid4
import pytest
from flask import jsonify
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from backend.app.auth.messages import AuthFailureCode
from backend.app.auth.passwords import hash_password, verify_password
from backend.app.auth.rate_limit import (
    RATE_LIMIT_PURPOSE_CHANGE_PASSWORD,
    compute_rate_limit_keys,
)
from backend.app.auth.service import AuthService, AuthenticationFailed
from backend.app.models.iam import (
    AuthSession,
    LoginAttempt,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from backend.app.models.logs import AuditLog, UserActivityLog
from backend.app.permissions.decorators import require_auth
from backend.app.permissions.service import PermissionDenied
from backend.app.security.context import AuthContext
from backend.app.security.messages import ErrorCode, ErrorMessage


# ==============================================================================
# FIXTURES DỰNG MÔI TRƯỜNG KIỂM THỬ THỰC TẾ
# ==============================================================================

@pytest.fixture
def auth_seed_data(transaction_session):
    """Tạo người dùng, vai trò OPERATOR và cấp sẵn quyền SELF trong database test."""
    role_op = transaction_session.query(Role).filter_by(code="OPERATOR").first()
    if role_op is None:
        role_op = Role(code="OPERATOR", name="Operator Role", is_system=True)
        transaction_session.add(role_op)
        transaction_session.flush()

    # Bổ sung idempotent Permission và RolePermission cho profile.update & password.change
    for perm_code in ["profile.update", "password.change"]:
        perm = transaction_session.query(Permission).filter_by(code=perm_code).first()
        if not perm:
            perm = Permission(code=perm_code, scope_type="SELF", description=perm_code)
            transaction_session.add(perm)
            transaction_session.flush()
        rp = transaction_session.query(RolePermission).filter_by(role_id=role_op.id, permission_id=perm.id).first()
        if not rp:
            transaction_session.add(RolePermission(role_id=role_op.id, permission_id=perm.id))
            transaction_session.flush()

    raw_password = "CorrectPassword123!"
    pw_hash = hash_password(raw_password)

    user = User(
        id=uuid4(),
        username=f"test_user_{uuid4().hex[:6]}",
        password_hash=pw_hash,
        full_name="Original Name",
        status="ACTIVE",
        must_change_password=False,
        token_version=1,
    )
    transaction_session.add(user)
    transaction_session.flush()

    user_role = UserRole(user_id=user.id, role_id=role_op.id)
    transaction_session.add(user_role)
    transaction_session.flush()

    transaction_session.commit()

    return {
        "user": user,
        "username": user.username,
        "password": raw_password,
        "password_hash": pw_hash,
        "role": role_op,
    }


@pytest.fixture
def client_with_control_route(app):
    """Đăng ký route kiểm thử được bảo vệ bởi @require_auth phục vụ kiểm tra cờ must_change_password."""
    @app.route("/api/v1/test/control-endpoint-03c", methods=["POST"])
    @require_auth
    def test_control():
        return jsonify({"status": "allowed"}), 200

    return app.test_client()


def _login_and_get_token(client, username, password):
    """Helper đăng nhập hệ thống để lấy Access JWT hợp lệ."""
    res = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Origin": "http://localhost:5173"},
    )
    assert res.status_code == 200
    return res.get_json()["data"]["access_token"]


# ==============================================================================
# 1. KIỂM THỬ CẬP NHẬT HỒ SƠ CÁ NHÂN (PATCH /api/v1/auth/me)
# ==============================================================================

def test_update_profile_success(client, auth_seed_data, transaction_session):
    """(Ca 1) Cập nhật full_name thành công: trim khoảng trắng, hỗ trợ Unicode tiếng Việt."""
    token = _login_and_get_token(client, auth_seed_data["username"], auth_seed_data["password"])
    new_name = "  Đỗ Mạnh Hùng (Kỹ Sư Vận Hành)  "
    expected_trimmed = "Đỗ Mạnh Hùng (Kỹ Sư Vận Hành)"

    res = client.patch(
        "/api/v1/auth/me",
        json={"full_name": new_name},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["full_name"] == expected_trimmed
    assert data["username"] == auth_seed_data["username"]
    assert data["id"] == str(auth_seed_data["user"].id)
    assert data["role_code"] == "OPERATOR"
    assert data["status"] == "ACTIVE"
    assert data["must_change_password"] is False

    # Kiểm tra trực tiếp trong PostgreSQL
    user_db = transaction_session.query(User).filter_by(id=auth_seed_data["user"].id).one()
    assert user_db.full_name == expected_trimmed
    assert user_db.password_hash == auth_seed_data["password_hash"]
    assert user_db.token_version == 1

    # Kiểm tra audit_logs và user_activity_logs
    audit = transaction_session.query(AuditLog).filter_by(
        target_id=str(user_db.id),
        action="USER_UPDATE"
    ).order_by(AuditLog.created_at.desc()).first()
    assert audit is not None
    assert audit.success is True
    assert audit.detail.get("fields") == ["full_name"]

    activity = transaction_session.query(UserActivityLog).filter_by(
        target_id=str(user_db.id),
        function_key="user.update"
    ).order_by(UserActivityLog.occurred_at.desc()).first()
    assert activity is not None
    assert activity.outcome == "SUCCESS"
    assert activity.summary == f"Cập nhật thông tin người dùng: {user_db.username}"

    # Phiên làm việc không bị thu hồi, token tiếp tục hợp lệ
    me_res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 200


@pytest.mark.parametrize("invalid_payload", [
    {},                                                    # Thiếu trường full_name
    {"full_name": ""},                                     # Rỗng
    {"full_name": "     "},                                # Toàn khoảng trắng
    {"full_name": "A" * 129},                              # Quá 128 ký tự
    {"full_name": 12345},                                  # Sai kiểu dữ liệu
    {"full_name": "Valid Name", "email": "evil@test.com"}, # Cố tình inject trường email
    {"full_name": "Valid Name", "role": "ADMIN"},          # Cố tình inject trường role
    {"full_name": "Valid Name", "status": "LOCKED"},       # Cố tình inject trường status
    {"full_name": "Valid Name", "token_version": 99},      # Cố tình inject token_version
    {"full_name": "Valid Name", "user_id": str(uuid4())},  # Cố tình inject user_id
])
def test_update_profile_validation_and_mass_assignment_rejected(client, auth_seed_data, invalid_payload):
    """(Ca 2 - 10 cases) Từ chối cập nhật khi thiếu trường, sai độ dài hoặc chứa trường lạ ngoài allowlist."""
    token = _login_and_get_token(client, auth_seed_data["username"], auth_seed_data["password"])

    res = client.patch(
        "/api/v1/auth/me",
        json=invalid_payload,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400
    assert res.get_json()["error"]["code"] == ErrorCode.BAD_REQUEST


@pytest.mark.parametrize("bad_header", [
    None,                           # Không gửi Authorization header
    "Bearer invalid.jwt.signature", # Token sai chữ ký
])
def test_update_profile_unauthorized(client, bad_header):
    """(Ca 3 - 2 cases) Từ chối cập nhật hồ sơ khi không có token hoặc token sai định dạng."""
    headers = {"Authorization": bad_header} if bad_header else {}
    res = client.patch("/api/v1/auth/me", json={"full_name": "New Name"}, headers=headers)
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED


def test_update_profile_denied_when_missing_permission(client, transaction_session):
    """(Ca 4) Từ chối bằng HTTP 403 Forbidden nếu vai trò người dùng không có quyền profile.update."""
    raw_pass = "TestPassword123!"
    restricted_role = Role(code=f"LIMITED_{uuid4().hex[:6]}", name="Limited Role", is_system=False)
    transaction_session.add(restricted_role)
    transaction_session.flush()

    user = User(
        id=uuid4(),
        username=f"limited_{uuid4().hex[:6]}",
        password_hash=hash_password(raw_pass),
        full_name="Limited User",
        status="ACTIVE",
        must_change_password=False,
        token_version=1,
    )
    transaction_session.add(user)
    transaction_session.flush()

    transaction_session.add(UserRole(user_id=user.id, role_id=restricted_role.id))
    transaction_session.commit()

    token = _login_and_get_token(client, user.username, raw_pass)

    res = client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Attempted Name Change"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403
    assert res.get_json()["error"]["code"] == ErrorCode.FORBIDDEN


def test_recheck_permission_revoked_after_initial_guard_profile(auth_seed_data, transaction_session):
    """(Ca 5) Probe thu quyền profile.update trong DB sau initial guard:
    Service thực thi tái kiểm tra sau khi có khóa dòng và ném PermissionDenied (HTTP 403).
    """
    actor = AuthContext(
        user_id=auth_seed_data["user"].id,
        session_id=uuid4(),
        token_version=1,
        request_id=str(uuid4()),
        actor_type="USER",
    )
    # Tạo session hợp lệ cho actor
    sess = AuthSession(
        id=actor.session_id,
        user_id=actor.user_id,
        token_version=1,
        created_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=900),
    )
    transaction_session.add(sess)
    transaction_session.commit()

    # Thu hồi quyền profile.update của OPERATOR trong DB (mô phỏng race condition sau guard)
    perm = transaction_session.query(Permission).filter_by(code="profile.update").first()
    transaction_session.query(RolePermission).filter_by(
        role_id=auth_seed_data["role"].id,
        permission_id=perm.id
    ).delete()
    transaction_session.commit()

    # Gọi trực tiếp service với actor đã qua guard
    with pytest.raises(PermissionDenied):
        AuthService.update_profile(
            actor=actor,
            full_name="Race Condition Name",
            request_id=uuid4(),
            db_session=transaction_session,
        )


# ==============================================================================
# 2. KIỂM THỬ ĐỔI MẬT KHẨU (POST /api/v1/auth/change-password)
# ==============================================================================

def test_change_password_wrong_old_password(client, auth_seed_data, transaction_session):
    """(Ca 6) Sai mật khẩu cũ: trả về HTTP 401, không đổi hash/version, ghi attempt thất bại."""
    token = _login_and_get_token(client, auth_seed_data["username"], auth_seed_data["password"])

    res = client.post(
        "/api/v1/auth/change-password",
        json={
            "old_password": "WrongPassword123!",
            "new_password": "BrandNewPassword456!",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED

    # Kiểm tra trạng thái DB: không biến động
    user_db = transaction_session.query(User).filter_by(id=auth_seed_data["user"].id).one()
    assert user_db.password_hash == auth_seed_data["password_hash"]
    assert user_db.token_version == 1
    assert user_db.failed_login_count == 0  # Không tăng bộ đếm failed_login_count của login

    # Kiểm tra bản ghi LoginAttempt mang mục đích change-password
    u_key, _ = compute_rate_limit_keys(
        auth_seed_data["username"],
        "127.0.0.1",
        purpose=RATE_LIMIT_PURPOSE_CHANGE_PASSWORD
    )
    attempt = transaction_session.query(LoginAttempt).filter_by(username_key=u_key).first()
    assert attempt is not None
    assert attempt.success is False
    assert attempt.failure_code == AuthFailureCode.INVALID_CREDENTIALS

    # Kiểm tra audit và activity được ghi nhận với outcome DENIED
    audit = transaction_session.query(AuditLog).filter_by(
        target_id=str(user_db.id),
        action="USER_PASSWORD_CHANGE"
    ).order_by(AuditLog.created_at.desc()).first()
    assert audit is not None
    assert audit.success is False

    activity = transaction_session.query(UserActivityLog).filter_by(
        target_id=str(user_db.id),
        function_key="auth.password_change"
    ).order_by(UserActivityLog.occurred_at.desc()).first()
    assert activity is not None
    assert activity.outcome == "DENIED"


@pytest.mark.parametrize("invalid_payload", [
    {},                                                                   # Thiếu cả hai
    {"old_password": "ValidPass123!"},                                    # Thiếu new_password
    {"new_password": "ValidPass123!"},                                    # Thiếu old_password
    {"old_password": "short", "new_password": "ValidPassword123!"},       # old_password < 12 ký tự
    {"old_password": "ValidPassword123!", "new_password": "short"},       # new_password < 12 ký tự
    {"old_password": "A" * 129, "new_password": "ValidPassword123!"},     # old_password > 128 ký tự
    {"old_password": "ValidPassword123!", "new_password": "B" * 129},     # new_password > 128 ký tự
    {"old_password": "ValidPassword123!", "new_password": "ValidPassword456!", "role": "ADMIN"}, # Field lạ
])
def test_change_password_validation_errors(client, auth_seed_data, invalid_payload):
    """(Ca 7 - 8 cases) Từ chối khi payload đổi mật khẩu sai cấu trúc hoặc không thỏa mãn độ dài 12-128."""
    token = _login_and_get_token(client, auth_seed_data["username"], auth_seed_data["password"])

    res = client.post(
        "/api/v1/auth/change-password",
        json=invalid_payload,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400
    assert res.get_json()["error"]["code"] == ErrorCode.BAD_REQUEST


@pytest.mark.parametrize("must_change", [False, True])
def test_change_password_same_as_old_rejected(client, auth_seed_data, transaction_session, must_change):
    """(Ca 8 - 2 cases) Mật khẩu mới trùng hoàn toàn mật khẩu cũ bị từ chối 400 Bad Request."""
    raw_pass = "SamePassword123!"
    role_op = auth_seed_data["role"]

    user = User(
        id=uuid4(),
        username=f"same_pw_{uuid4().hex[:6]}",
        password_hash=hash_password(raw_pass),
        full_name="Same Password User",
        status="ACTIVE",
        must_change_password=must_change,
        token_version=1,
    )
    transaction_session.add(user)
    transaction_session.flush()
    transaction_session.add(UserRole(user_id=user.id, role_id=role_op.id))
    transaction_session.commit()

    token = _login_and_get_token(client, user.username, raw_pass)

    res = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": raw_pass, "new_password": raw_pass},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 400
    assert res.get_json()["error"]["code"] == ErrorCode.BAD_REQUEST

    user_db = transaction_session.query(User).filter_by(id=user.id).one()
    assert user_db.token_version == 1
    assert user_db.must_change_password is must_change
    assert verify_password(raw_pass, user_db.password_hash) is True


def test_change_password_rate_limiting_user(client, auth_seed_data):
    """(Ca 9) Giới hạn tốc độ: thử sai mật khẩu cũ 5 lần liên tiếp -> lần 6 nhận HTTP 429 Retry-After."""
    token = _login_and_get_token(client, auth_seed_data["username"], auth_seed_data["password"])

    # Thử sai 5 lần liên tiếp
    for i in range(5):
        res = client.post(
            "/api/v1/auth/change-password",
            json={"old_password": f"WrongPassword_{i}!", "new_password": "ValidNewPassword123!"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 401

    # Lần thứ 6 bị rate limit chặn đứng
    with patch("backend.app.auth.service.verify_password") as mock_verify:
        res_blocked = client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "WrongPassword_6!", "new_password": "ValidNewPassword123!"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res_blocked.status_code == 429
        assert res_blocked.get_json()["error"]["code"] == ErrorCode.TOO_MANY_REQUESTS
        assert "Retry-After" in res_blocked.headers
        assert int(res_blocked.headers["Retry-After"]) >= 1

        # Tuyệt đối không gọi hàm băm hay xác minh mật khẩu khi đã chạm ngưỡng khóa
        mock_verify.assert_not_called()


def test_change_password_rate_limiting_ip(client, auth_seed_data, transaction_session):
    """(Ca 10) Giới hạn tốc độ theo IP: 20 lần thử sai từ cùng 1 IP qua 20 user khác nhau -> lần 21 nhận 429."""
    role_op = auth_seed_data["role"]
    users = []
    tokens = []
    fixed_ip = "192.168.10.99"

    for i in range(21):
        u = User(
            id=uuid4(),
            username=f"ip_limit_{i}_{uuid4().hex[:4]}",
            password_hash=hash_password("ValidPassword123!"),
            full_name=f"IP User {i}",
            status="ACTIVE",
            must_change_password=False,
            token_version=1,
        )
        transaction_session.add(u)
        transaction_session.flush()
        transaction_session.add(UserRole(user_id=u.id, role_id=role_op.id))
        users.append(u)
    transaction_session.commit()

    for i in range(21):
        tokens.append(_login_and_get_token(client, users[i].username, "ValidPassword123!"))

    # 20 user đầu tiên thử sai từ fixed_ip -> 401
    for i in range(20):
        res = client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "WrongOldPass123!", "new_password": "ValidNewPassword123!"},
            headers={"Authorization": f"Bearer {tokens[i]}"},
            environ_base={"REMOTE_ADDR": fixed_ip},
        )
        assert res.status_code == 401

    # User thứ 21 thử sai từ cùng fixed_ip -> 429 Too Many Requests
    res_blocked = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "WrongOldPass123!", "new_password": "ValidNewPassword123!"},
        headers={"Authorization": f"Bearer {tokens[20]}"},
        environ_base={"REMOTE_ADDR": fixed_ip},
    )
    assert res_blocked.status_code == 429
    assert res_blocked.get_json()["error"]["code"] == ErrorCode.TOO_MANY_REQUESTS


@pytest.mark.parametrize("direction", ["change_to_login", "login_to_change"])
def test_change_password_rate_limit_domain_separation(client, auth_seed_data, direction):
    """(Ca 11 - 2 cases) Phân tách domain rate limit hai chiều hoàn toàn:
    - change_to_login: User bị rate limit ở change-password vẫn có thể login 200 bình thường.
    - login_to_change: User bị rate limit ở login không làm ảnh hưởng đến token hợp lệ gọi change-password.
    Độ dài chuỗi mật khẩu sai luôn bảo đảm >= 12 ký tự để không bị chặn ở tầng schema validation (HTTP 400).
    """
    token = _login_and_get_token(client, auth_seed_data["username"], auth_seed_data["password"])

    if direction == "change_to_login":
        # 1. Thử sai 5 lần với password sai đủ 12+ ký tự (WrongOldPassword_{i}!) -> đều nhận 401
        for i in range(5):
            res = client.post(
                "/api/v1/auth/change-password",
                json={"old_password": f"WrongOldPassword_{i}!", "new_password": "ValidNewPass123!"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert res.status_code == 401

        # Lần thứ 6 bị rate limit chặn đứng nhận 429
        change_res = client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "WrongOldPassword_6!", "new_password": "ValidNewPass123!"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert change_res.status_code == 429
        assert change_res.get_json()["error"]["code"] == ErrorCode.TOO_MANY_REQUESTS

        # Đăng nhập bằng tài khoản này vẫn thành công 200 OK (không bị chia sẻ limit)
        login_res = client.post(
            "/api/v1/auth/login",
            json={"username": auth_seed_data["username"], "password": auth_seed_data["password"]},
            headers={"Origin": "http://localhost:5173"},
        )
        assert login_res.status_code == 200

    else:
        # 2. Làm đầy counter login tới mức 429
        for i in range(5):
            client.post(
                "/api/v1/auth/login",
                json={"username": auth_seed_data["username"], "password": f"WrongLoginPass_{i}!"},
                headers={"Origin": "http://localhost:5173"},
            )
        login_blocked = client.post(
            "/api/v1/auth/login",
            json={"username": auth_seed_data["username"], "password": "WrongLoginPass_6!"},
            headers={"Origin": "http://localhost:5173"},
        )
        assert login_blocked.status_code == 429

        # Đổi mật khẩu qua token hiện có vẫn thành công 200 OK
        new_pass = "ValidNewPassAfterLoginLimit123!"
        change_ok = client.post(
            "/api/v1/auth/change-password",
            json={"old_password": auth_seed_data["password"], "new_password": new_pass},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert change_ok.status_code == 200


def test_change_password_rate_limit_retry_after_clock_control(auth_seed_data, transaction_session):
    """(Ca 12) Kiểm soát tính toán Retry-After bằng clock injection không cần sleep."""
    from backend.app.auth.rate_limit import check_rate_limit

    u_key, ip_key = compute_rate_limit_keys(
        auth_seed_data["username"],
        "127.0.0.1",
        purpose=RATE_LIMIT_PURPOSE_CHANGE_PASSWORD
    )

    t0 = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
    # Ghi nhận 5 attempt thất bại tại t0
    for _ in range(5):
        attempt = LoginAttempt(
            id=uuid4(),
            username_key=u_key,
            ip_key=ip_key,
            user_id=auth_seed_data["user"].id,
            success=False,
            failure_code=AuthFailureCode.INVALID_CREDENTIALS,
            occurred_at=t0,
            request_id=uuid4(),
        )
        transaction_session.add(attempt)
    transaction_session.commit()

    # Tại t0 + 300s (sau 5 phút), thời gian chờ còn lại phải là đúng 600s
    t_check = t0 + timedelta(seconds=300)
    is_limited, retry_after = check_rate_limit(transaction_session, u_key, ip_key, now=t_check)
    assert is_limited is True
    assert retry_after == 600

    # Tại t0 + 901s (sau 15 phút 1 giây), rate limit đã tự động mở
    t_open = t0 + timedelta(seconds=901)
    is_limited_open, _ = check_rate_limit(transaction_session, u_key, ip_key, now=t_open)
    assert is_limited_open is False


def test_change_password_success_and_lifecycle(client, auth_seed_data, transaction_session):
    """(Ca 13) Đổi mật khẩu thành công:
    - user.token_version tăng 1.
    - user.must_change_password chuyển False, password_changed_at được cập nhật.
    - Toàn bộ session của user bị thu hồi.
    - Token cũ nhận 401, password cũ nhận 401, password mới nhận 200.
    """
    token_old = _login_and_get_token(client, auth_seed_data["username"], auth_seed_data["password"])
    new_password = "BrandNewSuperSecurePass456!"

    # Tạo thêm một phiên giả lập của cùng user đã bị thu hồi trước đó
    now_past = datetime.now(timezone.utc) - timedelta(hours=1)
    past_session = AuthSession(
        id=uuid4(),
        user_id=auth_seed_data["user"].id,
        token_version=1,
        created_at=now_past,
        last_seen_at=now_past,
        expires_at=now_past + timedelta(seconds=900),
        revoked_at=now_past,
        revoke_reason="Pre-existing revocation reason",
    )
    transaction_session.add(past_session)

    # Tạo thêm một phiên của người dùng KHÁC để kiểm tra tính cô lập
    other_user = User(
        id=uuid4(),
        username=f"other_{uuid4().hex[:6]}",
        password_hash=hash_password("OtherPassword123!"),
        full_name="Other User",
        status="ACTIVE",
        token_version=1,
    )
    transaction_session.add(other_user)
    transaction_session.flush()

    now = datetime.now(timezone.utc)
    other_session = AuthSession(
        id=uuid4(),
        user_id=other_user.id,
        token_version=1,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=900),
    )
    transaction_session.add(other_session)
    transaction_session.commit()

    # Thực hiện đổi mật khẩu
    res = client.post(
        "/api/v1/auth/change-password",
        json={
            "old_password": auth_seed_data["password"],
            "new_password": new_password,
        },
        headers={"Authorization": f"Bearer {token_old}"},
    )

    assert res.status_code == 200
    assert res.get_json()["data"] == {"login_required": True}
    assert "access_token" not in res.get_json()["data"]
    assert "Set-Cookie" not in res.headers

    # 1. Kiểm tra trạng thái User trong database
    user_db = transaction_session.query(User).filter_by(id=auth_seed_data["user"].id).one()
    assert user_db.token_version == 2
    assert user_db.must_change_password is False
    assert user_db.password_changed_at is not None
    assert verify_password(new_password, user_db.password_hash) is True
    assert verify_password(auth_seed_data["password"], user_db.password_hash) is False

    # 2. Kiểm tra thu hồi phiên: toàn bộ phiên của user bị ngắt
    active_sessions = transaction_session.query(AuthSession).filter(
        AuthSession.user_id == user_db.id,
        AuthSession.revoked_at.is_(None)
    ).all()
    assert len(active_sessions) == 0

    # Phiên bị thu hồi từ trước bảo toàn nguyên vẹn timestamp và lý do
    re_past = transaction_session.query(AuthSession).filter_by(id=past_session.id).one()
    assert re_past.revoke_reason == "Pre-existing revocation reason"
    assert re_past.revoked_at == now_past

    # Phiên của người dùng khác không bị ảnh hưởng
    re_other = transaction_session.query(AuthSession).filter_by(id=other_session.id).one()
    assert re_other.revoked_at is None

    # 3. Access token cũ lập tức bị từ chối 401
    old_call = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_old}"})
    assert old_call.status_code == 401

    # 4. Đăng nhập bằng mật khẩu cũ bị từ chối 401
    old_login = client.post(
        "/api/v1/auth/login",
        json={"username": auth_seed_data["username"], "password": auth_seed_data["password"]},
        headers={"Origin": "http://localhost:5173"},
    )
    assert old_login.status_code == 401

    # 5. Đăng nhập bằng mật khẩu mới thành công 200
    new_login = client.post(
        "/api/v1/auth/login",
        json={"username": auth_seed_data["username"], "password": new_password},
        headers={"Origin": "http://localhost:5173"},
    )
    assert new_login.status_code == 200
    token_new = new_login.get_json()["data"]["access_token"]

    # 6. Dùng token mới đọc hồ sơ thành công
    new_call = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_new}"})
    assert new_call.status_code == 200


# ==============================================================================
# 3. KIỂM THỬ TÀI KHOẢN TẠM (must_change_password = True)
# ==============================================================================

def test_must_change_password_user_journey(client_with_control_route, auth_seed_data, transaction_session):
    """(Ca 14) Luồng tài khoản có cờ must_change_password=True."""
    raw_pass = "TempPassword123!"
    role_op = auth_seed_data["role"]
    user = User(
        id=uuid4(),
        username=f"temp_user_{uuid4().hex[:6]}",
        password_hash=hash_password(raw_pass),
        full_name="Temporary User",
        status="ACTIVE",
        must_change_password=True,
        token_version=1,
    )
    transaction_session.add(user)
    transaction_session.flush()
    transaction_session.add(UserRole(user_id=user.id, role_id=role_op.id))
    transaction_session.commit()

    token = _login_and_get_token(client_with_control_route, user.username, raw_pass)

    # 1. Gọi endpoint điều khiển bị chặn 403 Forbidden
    ctrl_res = client_with_control_route.post(
        "/api/v1/test/control-endpoint-03c",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ctrl_res.status_code == 403
    assert ctrl_res.get_json()["error"]["code"] == ErrorCode.FORBIDDEN

    # 2. Gọi PATCH /api/v1/auth/me -> Được phép (200 OK)
    patch_res = client_with_control_route.patch(
        "/api/v1/auth/me",
        json={"full_name": "Updated Temp Name"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert patch_res.status_code == 200
    assert patch_res.get_json()["data"]["must_change_password"] is True

    # 3. Gọi POST /api/v1/auth/change-password -> Được phép (200 OK)
    new_pass = "PermanentSecurePass123!"
    change_res = client_with_control_route.post(
        "/api/v1/auth/change-password",
        json={"old_password": raw_pass, "new_password": new_pass},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert change_res.status_code == 200

    # 4. Đăng nhập lại bằng mật khẩu mới
    new_login = client_with_control_route.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": new_pass},
        headers={"Origin": "http://localhost:5173"},
    )
    assert new_login.status_code == 200
    assert new_login.get_json()["data"]["user"]["must_change_password"] is False
    token_fresh = new_login.get_json()["data"]["access_token"]

    # 5. Gọi lại endpoint điều khiển bằng token mới -> Thành công 200 OK
    ctrl_allowed = client_with_control_route.post(
        "/api/v1/test/control-endpoint-03c",
        headers={"Authorization": f"Bearer {token_fresh}"},
    )
    assert ctrl_allowed.status_code == 200


# ==============================================================================
# 4. KIỂM THỬ KHÓA populate_existing & RECHECK SAU KHÓA
# ==============================================================================

@pytest.mark.parametrize("operation", ["profile", "password"])
@pytest.mark.parametrize("stale_field", ["token_version", "session_revoked"])
def test_stale_cache_and_recheck_after_lock_service(auth_seed_data, transaction_session, operation, stale_field):
    """(Ca 15 - 4 cases) Kiểm tra tính năng populate_existing=True và recheck trạng thái sau khóa trực tiếp trong service:
    Actor đã có token_version=1. Luồng khác đổi DB thành version=2 hoặc revoke session -> service từ chối 401.
    Kiểm tra cả profile và change-password.
    """
    actor = AuthContext(
        user_id=auth_seed_data["user"].id,
        session_id=uuid4(),
        token_version=1,
        request_id=str(uuid4()),
        actor_type="USER",
    )
    now = datetime.now(timezone.utc)
    sess = AuthSession(
        id=actor.session_id,
        user_id=actor.user_id,
        token_version=1,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=900),
    )
    transaction_session.add(sess)
    transaction_session.commit()

    if stale_field == "token_version":
        transaction_session.execute(
            text("UPDATE users SET token_version = 2 WHERE id = :uid"),
            {"uid": actor.user_id}
        )
    else:
        sess.revoked_at = now
        sess.revoke_reason = "Admin revocation probe"
    transaction_session.commit()

    with pytest.raises(AuthenticationFailed):
        if operation == "profile":
            AuthService.update_profile(
                actor=actor,
                full_name="New Name",
                request_id=uuid4(),
                db_session=transaction_session,
            )
        else:
            AuthService.change_password(
                actor=actor,
                old_password=auth_seed_data["password"],
                new_password="NewValidPassword123!",
                client_ip="127.0.0.1",
                request_id=uuid4(),
                db_session=transaction_session,
            )


def test_recheck_permission_revoked_after_initial_guard_password(auth_seed_data, transaction_session):
    """(Ca 16) Thu hồi quyền password.change sau khi actor đã qua initial guard:
    Service tái kiểm tra sau khi có khóa dòng và ném PermissionDenied (HTTP 403), đồng thời ghi log DENIED.
    """
    actor = AuthContext(
        user_id=auth_seed_data["user"].id,
        session_id=uuid4(),
        token_version=1,
        request_id=str(uuid4()),
        actor_type="USER",
    )
    sess = AuthSession(
        id=actor.session_id,
        user_id=actor.user_id,
        token_version=1,
        created_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=900),
    )
    transaction_session.add(sess)
    transaction_session.commit()

    # Thu hồi quyền password.change của OPERATOR
    perm = transaction_session.query(Permission).filter_by(code="password.change").first()
    transaction_session.query(RolePermission).filter_by(
        role_id=auth_seed_data["role"].id,
        permission_id=perm.id
    ).delete()
    transaction_session.commit()

    with pytest.raises(PermissionDenied):
        AuthService.change_password(
            actor=actor,
            old_password=auth_seed_data["password"],
            new_password="NewValidPassword123!",
            client_ip="127.0.0.1",
            request_id=uuid4(),
            db_session=transaction_session,
        )

    # Xác minh log đã được ghi nhận với outcome DENIED
    activity = transaction_session.query(UserActivityLog).filter_by(
        target_id=str(actor.user_id),
        function_key="auth.password_change"
    ).order_by(UserActivityLog.occurred_at.desc()).first()
    assert activity is not None
    assert activity.outcome == "DENIED"
    assert activity.error_code == "PERMISSION_DENIED"


# ==============================================================================
# 5. KIỂM THỬ THỜI ĐIỂM ĐỌC CLOCK NGAY SAU KHÓA (PROBE CLOCK THỜI GIAN CHỜ KHÓA)
# ==============================================================================

@pytest.mark.parametrize("operation", ["profile", "password"])
def test_clock_advance_past_expiry_during_lock_rejected(auth_seed_data, transaction_session, operation):
    """(Ca 17 - 2 cases) Probe kiểm tra thứ tự đọc clock sau lock:
    Khi query SELECT FOR UPDATE hoàn tất thì clock đã tiến vượt qua expires_at của session.
    Hệ thống phát hiện session đã hết hạn sau lock -> raise AuthenticationFailed (HTTP 401),
    không thực hiện bất kỳ mutation nào (tên, hash, version, flag giữ nguyên) và không revoke session.
    """
    actor = AuthContext(
        user_id=auth_seed_data["user"].id,
        session_id=uuid4(),
        token_version=1,
        request_id=str(uuid4()),
        actor_type="USER",
    )
    t0 = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
    # Session hết hạn tại t0 + 1s
    sess = AuthSession(
        id=actor.session_id,
        user_id=actor.user_id,
        token_version=1,
        created_at=t0 - timedelta(minutes=5),
        last_seen_at=t0 - timedelta(minutes=5),
        expires_at=t0 + timedelta(seconds=1),
    )
    transaction_session.add(sess)
    transaction_session.commit()

    # Clock callable mô phỏng thời gian đã tiến tới t0 + 2s ngay sau khi SELECT FOR UPDATE trả về
    def advancing_clock():
        return t0 + timedelta(seconds=2)

    with pytest.raises(AuthenticationFailed):
        if operation == "profile":
            AuthService.update_profile(
                actor=actor,
                full_name="Expired Probe Name",
                request_id=uuid4(),
                db_session=transaction_session,
                clock_fn=advancing_clock,
            )
        else:
            AuthService.change_password(
                actor=actor,
                old_password=auth_seed_data["password"],
                new_password="NewValidPassword123!",
                client_ip="127.0.0.1",
                request_id=uuid4(),
                db_session=transaction_session,
                clock_fn=advancing_clock,
            )

    # Kiểm tra database: không mutation xảy ra
    user_db = transaction_session.query(User).filter_by(id=actor.user_id).one()
    assert user_db.full_name == "Original Name"
    assert user_db.token_version == 1
    assert user_db.must_change_password is False
    assert verify_password(auth_seed_data["password"], user_db.password_hash) is True

    # Session không bị revoke dở dang
    sess_db = transaction_session.query(AuthSession).filter_by(id=actor.session_id).one()
    assert sess_db.revoked_at is None


# ==============================================================================
# 6. KIỂM THỬ ROLLBACK TRÊN MUTATION VÀ FAILURE
# ==============================================================================

@pytest.mark.parametrize("endpoint,error_type", [
    ("profile", "writer"),
    ("profile", "commit"),
    ("password", "writer"),
    ("password", "commit"),
])
def test_mutation_rollback_on_writer_or_commit_error(client, auth_seed_data, transaction_session, endpoint, error_type):
    """(Ca 18 - 4 cases) Lỗi writer hoặc commit trong quá trình mutation:
    - Trả về HTTP 500.
    - Toàn bộ thay đổi bị rollback: tên, mật khẩu, version giữ nguyên trạng; session không bị thu hồi dở dang.
    """
    token = _login_and_get_token(client, auth_seed_data["username"], auth_seed_data["password"])

    patch_target = "backend.app.audit.service.AuditService.record" if error_type == "writer" else "sqlalchemy.orm.Session.commit"

    with patch(patch_target) as mock_fail:
        mock_fail.side_effect = RuntimeError(f"Simulated {error_type} failure")

        if endpoint == "profile":
            res = client.patch(
                "/api/v1/auth/me",
                json={"full_name": "Rollback Name Test"},
                headers={"Authorization": f"Bearer {token}"},
            )
        else:
            res = client.post(
                "/api/v1/auth/change-password",
                json={"old_password": auth_seed_data["password"], "new_password": "NewRollbackPassword123!"},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 500

    # Kiểm tra database: baseline user hoàn toàn nguyên vẹn
    user_db = transaction_session.query(User).filter_by(id=auth_seed_data["user"].id).one()
    assert user_db.full_name == "Original Name"
    assert user_db.token_version == 1
    assert verify_password(auth_seed_data["password"], user_db.password_hash) is True
    # Session vẫn hoạt động, không bị revoke dở dang
    active_sess = transaction_session.query(AuthSession).filter(
        AuthSession.user_id == user_db.id,
        AuthSession.revoked_at.is_(None)
    ).count()
    assert active_sess >= 1


@pytest.mark.parametrize("failure_scenario,error_target", [
    ("wrong_password", "writer"),
    ("wrong_password", "commit"),
    ("rate_limited", "writer"),
    ("rate_limited", "commit"),
])
def test_failure_path_does_not_mask_writer_or_commit_error(client, auth_seed_data, failure_scenario, error_target):
    """(Ca 19 - 4 cases) Khi sai old password hoặc bị rate limit mà writer hoặc commit bị lỗi:
    Hệ thống không được nuốt lỗi thành HTTP 401 hoặc 429 giả mạo mà phải trả về HTTP 500.
    """
    token = _login_and_get_token(client, auth_seed_data["username"], auth_seed_data["password"])
    target = "backend.app.audit.service.AuditService.record" if error_target == "writer" else "sqlalchemy.orm.Session.commit"

    if failure_scenario == "rate_limited":
        # Tạo sẵn 5 lần thử sai để kích hoạt trạng thái rate limit (dùng chuỗi >= 12 ký tự)
        for i in range(5):
            client.post(
                "/api/v1/auth/change-password",
                json={"old_password": f"WrongOldPassword_{i}!", "new_password": "NewValidPass123!"},
                headers={"Authorization": f"Bearer {token}"},
            )

    with patch(target) as mock_fail:
        mock_fail.side_effect = RuntimeError(f"Failure path {error_target} error")
        res = client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "WrongPassword123!", "new_password": "NewValidPassword123!"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 500


@pytest.mark.parametrize("endpoint", ["profile", "password"])
def test_database_unavailable_returns_503(client, auth_seed_data, endpoint):
    """(Ca 20 - 2 cases) Trả về HTTP 503 Service Unavailable khi kết nối database gặp sự cố."""
    token = _login_and_get_token(client, auth_seed_data["username"], auth_seed_data["password"])

    with patch("backend.app.auth.service.acquire_rate_limit_advisory_locks") as mock_lock, \
         patch("backend.app.auth.service.PermissionService.require") as mock_perm:
        mock_lock.side_effect = OperationalError("connection refused", None, None)
        mock_perm.side_effect = OperationalError("connection refused", None, None)

        if endpoint == "profile":
            res = client.patch(
                "/api/v1/auth/me",
                json={"full_name": "New Name"},
                headers={"Authorization": f"Bearer {token}"},
            )
        else:
            res = client.post(
                "/api/v1/auth/change-password",
                json={"old_password": auth_seed_data["password"], "new_password": "NewPassword123!"},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 503
        assert res.get_json()["error"]["code"] == ErrorCode.SERVICE_UNAVAILABLE
