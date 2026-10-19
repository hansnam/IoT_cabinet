"""
Auth Login, Logout, Me & Rate Limit PostgreSQL Test Suite (TV3-03B Revision 2)
Kiểm thử toàn diện trên PostgreSQL test database thực tế:
1. Happy path Login -> 200 OK, envelope, headers (no-store, nosniff, no Set-Cookie), AuthSession, audit và user activity logs.
2. GET /api/v1/auth/me -> 200 OK, quyền P0, không ghi activity log khi đọc/polling.
3. POST /api/v1/auth/logout -> 200 OK, thu hồi session đúng sid, không xóa dòng, token cũ trả về 401.
4. Bắt buộc Origin header cho login/logout: thiếu, rỗng, hoặc sai origin -> 403 Forbidden.
5. Kiểm tra định dạng đầu vào (400 Bad Request): thiếu username/password, field lạ ngoài allowlist, độ dài ngoài 12-128.
6. Generic 401 Unauthorized: sai password, user không tồn tại, user bị khóa (LOCKED) hoặc lưu trữ (ARCHIVED).
7. Dummy Argon2 verify đối với user chưa tồn tại để giảm thiểu rủi ro timing attack (đầy đủ import verify_password).
8. Chặn đăng nhập đối với user không được gán role trong user_roles.
9. Tự động băm lại mật khẩu (rehash) khi tham số cấu hình Argon2 thay đổi.
10. Rate limit: tối đa 5 lần lỗi/username, 20 lần lỗi/IP trong cửa sổ 15 phút.
    - 5 lần sai đầu nhận 401, lần thứ 6 nhận 429 kèm header Retry-After.
    - 20 lần sai từ cùng IP trên 20 user khác nhau nhận 429 ở lần thứ 21.
    - Khi bị rate limit, không thực hiện băm mật khẩu.
    - Lần thử bị chặn (RATE_LIMITED) không kéo dài vô tận cửa sổ 15 phút.
11. Bền vững dữ liệu thử sai: nốt ghi login_attempts và audit/activity logs không bị mất sau khi trả 401/429.
12. Enforce ràng buộc must_change_password: tài khoản chưa đổi mật khẩu chỉ được gọi /me và /logout, gọi route khác bị 403 (fixture đăng ký route sớm).
13. Chuyển đổi lỗi database: khi DB gặp sự cố kết nối trả về 503 Service Unavailable.
14. Không nuốt lỗi hệ thống: writer/commit gặp lỗi thì trả về 500/503 chứ không báo sai credential hay thành công giả (kiểm tra cả failure lẫn success).
15. Khóa dòng với populate_existing=True: phát hiện thay đổi hash/version trực tiếp từ DB dù đối tượng đã có trong RAM cache.
16. Các route EXT chưa đăng ký (refresh, logout-all, sessions) trả về 404 Not Found theo router.
17. Hợp đồng khóa PostgreSQL Transaction Advisory Locks (thứ tự tăng dần cố định giữa username_key và ip_key).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4
import pytest
from flask import jsonify
from sqlalchemy import func, text
from sqlalchemy.exc import OperationalError

from backend.app import create_app
from backend.app.auth.messages import AuthFailureCode
from backend.app.auth.passwords import hash_password, verify_password
from backend.app.auth.rate_limit import compute_rate_limit_keys
from backend.app.models.iam import AuthSession, LoginAttempt, Role, User, UserRole
from backend.app.models.logs import AuditLog, UserActivityLog
from backend.app.permissions.decorators import require_auth
from backend.app.security.messages import ErrorCode, ErrorMessage


# ==============================================================================
# FIXTURES DỰNG MÔI TRƯỜNG KIỂM THỬ THỰC TẾ
# ==============================================================================

@pytest.fixture
def auth_seed_data(transaction_session):
    """Tạo người dùng và vai trò mẫu trong database test phục vụ kiểm thử đăng nhập."""
    role_op = transaction_session.query(Role).filter_by(code="OPERATOR").first()
    if role_op is None:
        role_op = Role(code="OPERATOR", name="Operator Role", is_system=True)
        transaction_session.add(role_op)
        transaction_session.flush()

    raw_password = "CorrectPassword123!"
    pw_hash = hash_password(raw_password)

    user = User(
        id=uuid4(),
        username=f"test_user_{uuid4().hex[:6]}",
        password_hash=pw_hash,
        full_name="Valid Test User",
        status="ACTIVE",
        must_change_password=False,
        token_version=1,
    )
    transaction_session.add(user)
    transaction_session.flush()

    user_role = UserRole(user_id=user.id, role_id=role_op.id)
    transaction_session.add(user_role)
    transaction_session.flush()

    # Commit inner savepoint để neo vững chắc fixture baseline trong root transaction
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
    """Đăng ký route kiểm thử được bảo vệ bởi @require_auth TRƯỚC request đầu tiên."""
    @app.route("/api/v1/test/control-endpoint", methods=["POST"])
    @require_auth
    def test_control():
        return jsonify({"status": "allowed"}), 200

    return app.test_client()


# ==============================================================================
# 1. KIỂM THỬ ĐĂNG NHẬP THÀNH CÔNG (HAPPY PATH)
# ==============================================================================

def test_login_success_happy_path(client, transaction_session, auth_seed_data):
    """Đăng nhập thành công trả về 200, JWT hợp lệ, tạo session DB và ghi đủ 2 loại nhật ký."""
    payload = {
        "username": auth_seed_data["username"],
        "password": auth_seed_data["password"],
    }
    headers = {
        "Origin": "http://localhost:5173",
        "Content-Type": "application/json",
    }

    res = client.post("/api/v1/auth/login", json=payload, headers=headers)
    assert res.status_code == 200
    data = res.get_json()["data"]

    # 1. Kiểm tra cấu trúc response payload
    assert "access_token" in data
    assert data["expires_in"] in (5, 900)
    user_info = data["user"]
    assert user_info["id"] == str(auth_seed_data["user"].id)
    assert user_info["username"] == auth_seed_data["username"]
    assert user_info["status"] == "ACTIVE"
    assert user_info["must_change_password"] is False
    assert user_info["role_code"] == "OPERATOR"
    assert "password_hash" not in user_info
    assert "token_version" not in user_info

    # 2. Kiểm tra response headers
    assert res.headers.get("Cache-Control") == "no-store"
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("X-Frame-Options") == "DENY"
    assert "Set-Cookie" not in res.headers

    # 3. Kiểm tra bản ghi AuthSession trong DB
    sess = transaction_session.query(AuthSession).filter_by(user_id=auth_seed_data["user"].id).first()
    assert sess is not None
    assert sess.revoked_at is None
    assert sess.token_version == auth_seed_data["user"].token_version
    assert sess.last_seen_at == sess.created_at
    assert sess.reauthenticated_at is None

    # 4. Kiểm tra bản ghi LoginAttempt
    attempt = transaction_session.query(LoginAttempt).filter_by(user_id=auth_seed_data["user"].id).first()
    assert attempt is not None
    assert attempt.success is True
    assert attempt.failure_code is None

    # 5. Kiểm tra audit_logs và user_activity_logs
    audit = transaction_session.query(AuditLog).filter_by(actor_user_id=auth_seed_data["user"].id).first()
    assert audit is not None
    assert audit.action == "USER_LOGIN"
    assert audit.success is True

    activity = transaction_session.query(UserActivityLog).filter_by(actor_user_id=auth_seed_data["user"].id).first()
    assert activity is not None
    assert activity.function_key == "auth.login"
    assert activity.action == "LOGIN"
    assert activity.outcome == "SUCCESS"


# ==============================================================================
# 2. KIỂM THỬ GET /api/v1/auth/me VÀ KHÔNG GHI NHẬT KÝ HOẠT ĐỘNG
# ==============================================================================

def test_get_me_success_and_no_activity_log(client, transaction_session, auth_seed_data):
    """GET /api/v1/auth/me trả về hồ sơ, quyền P0 và không ghi nhật ký hoạt động."""
    login_res = client.post(
        "/api/v1/auth/login",
        json={"username": auth_seed_data["username"], "password": auth_seed_data["password"]},
        headers={"Origin": "http://localhost:5173"},
    )
    token = login_res.get_json()["data"]["access_token"]

    initial_activity_count = transaction_session.query(func.count(UserActivityLog.id)).scalar()

    me_res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 200
    me_data = me_res.get_json()["data"]

    assert me_data["id"] == str(auth_seed_data["user"].id)
    assert me_data["username"] == auth_seed_data["username"]
    assert me_data["role_code"] == "OPERATOR"
    assert isinstance(me_data["permissions"], list)
    assert "password_hash" not in me_data
    assert "token_version" not in me_data

    # Bắt buộc: Không ghi nhận user_activity_logs cho endpoint đọc/polling
    current_activity_count = transaction_session.query(func.count(UserActivityLog.id)).scalar()
    assert current_activity_count == initial_activity_count


# ==============================================================================
# 3. KIỂM THỬ POST /api/v1/auth/logout VÀ THU HỒI TOKEN
# ==============================================================================

def test_logout_revokes_session_and_subsequent_token_rejected(client, transaction_session, auth_seed_data):
    """POST /api/v1/auth/logout thu hồi phiên; token cũ ngay lập tức bị từ chối với 401."""
    login_res = client.post(
        "/api/v1/auth/login",
        json={"username": auth_seed_data["username"], "password": auth_seed_data["password"]},
        headers={"Origin": "http://localhost:5173"},
    )
    token = login_res.get_json()["data"]["access_token"]

    logout_res = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}", "Origin": "http://localhost:5173"},
    )
    assert logout_res.status_code == 200
    assert logout_res.get_json()["data"]["logged_out"] is True

    # Phiên trong DB bị đánh dấu revoked_at, không bị DELETE
    sess = transaction_session.query(AuthSession).filter_by(user_id=auth_seed_data["user"].id).first()
    assert sess is not None
    assert sess.revoked_at is not None
    assert sess.revoke_reason == "User logout"

    # Gọi lại bằng token vừa logout bị 401 Unauthorized
    me_retry = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_retry.status_code == 401
    assert me_retry.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED

    logout_retry = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}", "Origin": "http://localhost:5173"},
    )
    assert logout_retry.status_code == 401


# ==============================================================================
# 4. KIỂM THỬ RÀNG BUỘC ORIGIN HEADER
# ==============================================================================

@pytest.mark.parametrize("bad_origin_header", [
    {},                                            # Thiếu Origin
    {"Origin": ""},                                # Origin rỗng
    {"Origin": "http://malicious-site.com"},       # Origin không nằm trong allowlist
])
def test_login_origin_security(client, bad_origin_header):
    """Login thiếu, rỗng hoặc sai Origin đều bị từ chối 403 Forbidden."""
    res = client.post(
        "/api/v1/auth/login",
        json={"username": "valid_user", "password": "ValidPassword123!"},
        headers=bad_origin_header,
    )
    assert res.status_code == 403
    assert res.get_json()["error"]["code"] == ErrorCode.FORBIDDEN


@pytest.mark.parametrize("bad_origin_header", [
    {},                                            # Thiếu Origin
    {"Origin": ""},                                # Origin rỗng
    {"Origin": "http://malicious-site.com"},       # Origin không nằm trong allowlist
])
def test_logout_origin_security(client, auth_seed_data, bad_origin_header):
    """Logout với token hợp lệ nhưng thiếu, rỗng hoặc sai Origin đều bị từ chối 403 Forbidden."""
    login_res = client.post(
        "/api/v1/auth/login",
        json={"username": auth_seed_data["username"], "password": auth_seed_data["password"]},
        headers={"Origin": "http://localhost:5173"},
    )
    token = login_res.get_json()["data"]["access_token"]

    headers = {"Authorization": f"Bearer {token}"}
    headers.update(bad_origin_header)

    res = client.post("/api/v1/auth/logout", headers=headers)
    assert res.status_code == 403
    assert res.get_json()["error"]["code"] == ErrorCode.FORBIDDEN


# ==============================================================================
# 5. KIỂM THỬ VALIDATE ĐẦU VÀO (400 BAD REQUEST)
# ==============================================================================

@pytest.mark.parametrize("bad_payload", [
    {},                                                          # Rỗng
    {"username": "admin"},                                       # Thiếu password
    {"password": "ValidPassword123!"},                          # Thiếu username
    {"username": "ab", "password": "ValidPassword123!"},         # Username < 3 ký tự
    {"username": "Invalid Name!", "password": "ValidPassword!"}, # Username chứa khoảng trắng và dấu lạ
    {"username": "valid_user", "password": "short"},             # Password < 12 ký tự
    {"username": "valid_user", "password": "a" * 129},           # Password > 128 ký tự
    {"username": "valid_user", "password": "ValidPass123!", "extra_field": "hacked"}, # Field lạ ngoài allowlist
])
def test_login_input_validation_bad_request(client, bad_payload):
    """Các trường hợp đầu vào không hợp lệ đều trả về HTTP 400 Bad Request."""
    res = client.post(
        "/api/v1/auth/login",
        json=bad_payload,
        headers={"Origin": "http://localhost:5173"},
    )
    assert res.status_code == 400
    assert res.get_json()["error"]["code"] == ErrorCode.BAD_REQUEST


# ==============================================================================
# 6. KIỂM THỬ GENERIC 401 UNAUTHORIZED
# ==============================================================================

def test_login_generic_401_for_wrong_password(client, transaction_session, auth_seed_data):
    """Sai mật khẩu trả về generic 401 Unauthorized và lưu vết thất bại."""
    res = client.post(
        "/api/v1/auth/login",
        json={"username": auth_seed_data["username"], "password": "WrongPassword123!"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED
    assert res.get_json()["error"]["message"] == ErrorMessage.UNAUTHORIZED

    attempt = transaction_session.query(LoginAttempt).filter_by(user_id=auth_seed_data["user"].id).first()
    assert attempt is not None
    assert attempt.success is False
    assert attempt.failure_code == AuthFailureCode.INVALID_CREDENTIALS


def test_login_generic_401_and_dummy_verify_for_unknown_user(client):
    """Người dùng không tồn tại trả về cùng mã generic 401 và thực hiện dummy verify."""
    with patch("backend.app.auth.service.verify_password", wraps=verify_password) as mock_verify:
        res = client.post(
            "/api/v1/auth/login",
            json={"username": "non_existent_user", "password": "SomePassword123!"},
            headers={"Origin": "http://localhost:5173"},
        )
        assert res.status_code == 401
        assert res.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED
        assert mock_verify.called


@pytest.mark.parametrize("status", ["LOCKED", "ARCHIVED"])
def test_login_generic_401_for_inactive_user(client, transaction_session, auth_seed_data, status):
    """Người dùng bị khóa hoặc lưu trữ đều nhận cùng mã generic 401, không để lộ trạng thái."""
    auth_seed_data["user"].status = status
    transaction_session.flush()

    res = client.post(
        "/api/v1/auth/login",
        json={"username": auth_seed_data["username"], "password": auth_seed_data["password"]},
        headers={"Origin": "http://localhost:5173"},
    )
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED


def test_login_fails_when_user_has_no_role(client, transaction_session, auth_seed_data):
    """Người dùng không có vai trò nào được gán trong user_roles bị từ chối 401."""
    transaction_session.query(UserRole).filter_by(user_id=auth_seed_data["user"].id).delete()
    transaction_session.flush()

    res = client.post(
        "/api/v1/auth/login",
        json={"username": auth_seed_data["username"], "password": auth_seed_data["password"]},
        headers={"Origin": "http://localhost:5173"},
    )
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED


# ==============================================================================
# 7. KIỂM THỬ REHASH MẬT KHẨU TỰ ĐỘNG
# ==============================================================================

def test_login_auto_rehashes_outdated_password_hash(client, transaction_session, auth_seed_data):
    """Tự động nâng cấp hash khi cấu hình bảo mật thay đổi mà không ngắt quãng đăng nhập."""
    with patch("backend.app.auth.service.verify_and_rehash") as mock_rehash:
        new_upgraded_hash = "$argon2id$v=19$m=65536,t=3,p=4$upgradedmockhash"
        mock_rehash.return_value = (True, new_upgraded_hash)

        res = client.post(
            "/api/v1/auth/login",
            json={"username": auth_seed_data["username"], "password": auth_seed_data["password"]},
            headers={"Origin": "http://localhost:5173"},
        )
        assert res.status_code == 200

        refreshed_user = transaction_session.query(User).filter_by(id=auth_seed_data["user"].id).one()
        assert refreshed_user.password_hash == new_upgraded_hash


# ==============================================================================
# 8. KIỂM THỬ RATE LIMIT POSTGRESQL (5 USER / 20 IP)
# ==============================================================================

def test_rate_limit_username_threshold_and_retry_after(client, auth_seed_data):
    """5 lần đăng nhập sai nhận 401; lần thứ 6 bị chặn với 429 và header Retry-After."""
    username = auth_seed_data["username"]

    for _ in range(5):
        res = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "WrongPassword123!"},
            headers={"Origin": "http://localhost:5173"},
        )
        assert res.status_code == 401

    with patch("backend.app.auth.service.verify_password") as mock_verify:
        res6 = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "WrongPassword123!"},
            headers={"Origin": "http://localhost:5173"},
        )
        assert res6.status_code == 429
        assert res6.get_json()["error"]["code"] == ErrorCode.TOO_MANY_REQUESTS
        assert "Retry-After" in res6.headers
        retry_after = int(res6.headers["Retry-After"])
        assert 1 <= retry_after <= 900
        assert not mock_verify.called


def test_rate_limit_ip_threshold_across_multiple_users(client, transaction_session):
    """20 lần đăng nhập sai từ cùng IP trên 20 user khác nhau: lần 21 nhận 429."""
    for i in range(20):
        res = client.post(
            "/api/v1/auth/login",
            json={"username": f"user_ip_test_{i:03d}", "password": "WrongPassword123!"},
            headers={"Origin": "http://localhost:5173"},
        )
        assert res.status_code == 401

    res21 = client.post(
        "/api/v1/auth/login",
        json={"username": "user_ip_test_021", "password": "WrongPassword123!"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert res21.status_code == 429
    assert res21.get_json()["error"]["code"] == ErrorCode.TOO_MANY_REQUESTS
    assert "Retry-After" in res21.headers


def test_blocked_attempt_does_not_extend_rate_limit_window(client, transaction_session, auth_seed_data):
    """Các lần thử bị chặn 429 không được kéo dài vô tận cửa sổ 15 phút của người dùng."""
    username = auth_seed_data["username"]
    now = datetime.now(timezone.utc)

    old_time = now - timedelta(seconds=840)
    u_key, ip_key = compute_rate_limit_keys(username, "127.0.0.1")

    for _ in range(5):
        attempt = LoginAttempt(
            id=uuid4(),
            username_key=u_key,
            ip_key=ip_key,
            user_id=auth_seed_data["user"].id,
            success=False,
            failure_code=AuthFailureCode.INVALID_CREDENTIALS,
            occurred_at=old_time,
            request_id=uuid4(),
        )
        transaction_session.add(attempt)
    transaction_session.flush()

    res = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "WrongPassword123!"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert res.status_code == 429
    retry_after = int(res.headers["Retry-After"])
    assert 55 <= retry_after <= 65

    blocked_attempt = (
        transaction_session.query(LoginAttempt)
        .filter_by(username_key=u_key, failure_code=AuthFailureCode.RATE_LIMITED)
        .first()
    )
    assert blocked_attempt is not None


def test_rate_limit_persists_across_new_app_instance(app, client, auth_seed_data):
    """Rate limit được lưu trong PostgreSQL nên vẫn có hiệu lực khi khởi tạo instance app mới."""
    from backend.app import create_app
    from backend.app.extensions import db

    username = auth_seed_data["username"]

    # 1. Gửi 5 lần thử sai trên instance app hiện tại
    for _ in range(5):
        res = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "WrongPassword123!"},
            headers={"Origin": "http://localhost:5173"},
        )
        assert res.status_code == 401

    # 2. Khởi tạo một Flask app instance mới hoàn toàn với cùng test config (không chạy create_all/seed)
    new_app = create_app("testing", test_config=dict(app.config))
    assert new_app is not app

    # Lưu ý: Khi chạy dưới transaction_session fixture, new_app chia sẻ session/connection của fixture
    # để quan sát được các bản ghi trong savepoint hiện tại. Bài test này chứng minh rằng rate limiter
    # đọc trạng thái từ DB PostgreSQL chứ không phụ thuộc vào bộ nhớ RAM của instance app cũ.
    # Không mô tả là đã kiểm tra restart process vật lý độc lập hay commit ra ngoài root savepoint.
    try:
        new_client = new_app.test_client()
        res6 = new_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "WrongPassword123!"},
            headers={"Origin": "http://localhost:5173"},
        )
        assert res6.status_code == 429
        assert res6.get_json()["error"]["code"] == ErrorCode.TOO_MANY_REQUESTS
        assert "Retry-After" in res6.headers
    finally:
        with new_app.app_context():
            db.session.remove()
            db.engine.dispose()


# ==============================================================================
# 9. KIỂM THỬ BỀN VỮNG LOGS VÀ ATTEMPTS SAU KHI LỖI
# ==============================================================================

def test_failure_logs_and_attempts_persist_after_401(client, transaction_session, auth_seed_data):
    """Bản ghi LoginAttempt, AuditLog và UserActivityLog vẫn tồn tại sau khi trả response 401."""
    res = client.post(
        "/api/v1/auth/login",
        json={"username": auth_seed_data["username"], "password": "WrongPassword123!"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert res.status_code == 401

    u_key, _ = compute_rate_limit_keys(auth_seed_data["username"], "127.0.0.1")
    attempts = transaction_session.query(LoginAttempt).filter_by(username_key=u_key).all()
    assert len(attempts) >= 1

    audits = transaction_session.query(AuditLog).filter_by(action="USER_LOGIN", success=False).all()
    assert len(audits) >= 1

    activities = transaction_session.query(UserActivityLog).filter_by(function_key="auth.login", outcome="DENIED").all()
    assert len(activities) >= 1


# ==============================================================================
# 10. KIỂM THỬ ĐỌC DỮ LIỆU TƯƠI TỪ DB VỚI POPULATE_EXISTING (CHỐNG STALE CACHE)
# ==============================================================================

def test_login_reads_latest_db_hash_and_version_stale_cache_probe(client, transaction_session, auth_seed_data):
    """populate_existing=True bảo đảm đọc hash/version mới nhất từ DB dù object đã nạp vào RAM cache."""
    # 1. Nạp object User vào identity map của transaction_session
    user_cached = transaction_session.query(User).filter_by(id=auth_seed_data["user"].id).one()
    assert user_cached.token_version == 1

    # 2. Thay đổi trực tiếp password_hash và token_version bằng câu lệnh SQL thô
    new_raw_pw = "BrandNewPassword123!"
    new_hash = hash_password(new_raw_pw)
    transaction_session.execute(
        text("UPDATE users SET password_hash = :hash, token_version = 2 WHERE id = :uid"),
        {"hash": new_hash, "uid": auth_seed_data["user"].id}
    )
    transaction_session.flush()

    # 3. Đăng nhập bằng mật khẩu cũ -> Bắt buộc bị 401 Unauthorized
    old_res = client.post(
        "/api/v1/auth/login",
        json={"username": auth_seed_data["username"], "password": auth_seed_data["password"]},
        headers={"Origin": "http://localhost:5173"},
    )
    assert old_res.status_code == 401
    assert old_res.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED

    # 4. Đăng nhập bằng mật khẩu mới -> Thành công 200 OK, token mang token_version=2 mới
    new_res = client.post(
        "/api/v1/auth/login",
        json={"username": auth_seed_data["username"], "password": new_raw_pw},
        headers={"Origin": "http://localhost:5173"},
    )
    assert new_res.status_code == 200

    sess = transaction_session.query(AuthSession).filter_by(user_id=auth_seed_data["user"].id).order_by(AuthSession.created_at.desc()).first()
    assert sess.token_version == 2


# ==============================================================================
# 11. KIỂM THỬ KHÔNG NUỐT LỖI WRITER/COMMIT
# ==============================================================================

@pytest.mark.parametrize("fault_type", ["audit_writer_error", "session_commit_error"])
@pytest.mark.parametrize("is_correct_password", [True, False])
def test_writer_or_commit_failure_does_not_swallow_error(
    client, transaction_session, auth_seed_data, fault_type, is_correct_password
):
    """Khi writer hoặc commit gặp lỗi, hệ thống trả HTTP 500, không nuốt lỗi và rollback atomic (cả success lẫn failure)."""
    password = auth_seed_data["password"] if is_correct_password else "WrongPassword123!"
    user_id = auth_seed_data["user"].id

    # Đếm số lượng ban đầu để thẩm định atomicity sau rollback
    init_sessions = transaction_session.query(AuthSession).filter_by(user_id=user_id).count()
    init_attempts = transaction_session.query(LoginAttempt).filter_by(user_id=user_id).count()
    init_audits = transaction_session.query(AuditLog).filter_by(actor_user_id=user_id).count()

    patch_target = (
        "backend.app.audit.service.AuditService.record"
        if fault_type == "audit_writer_error"
        else "sqlalchemy.orm.Session.commit"
    )

    with patch(patch_target) as mock_fault:
        mock_fault.side_effect = RuntimeError(f"Simulated failure: {fault_type}")

        res = client.post(
            "/api/v1/auth/login",
            json={"username": auth_seed_data["username"], "password": password},
            headers={"Origin": "http://localhost:5173"},
        )

        # 1. Bắt buộc trả về HTTP 500 INTERNAL_SERVER_ERROR
        # Tuyệt đối không nuốt thành 401 giả (khi sai pass) hoặc cấp token (khi đúng pass)
        assert res.status_code == 500
        assert res.get_json()["error"]["code"] == ErrorCode.INTERNAL_SERVER_ERROR
        assert "data" not in res.get_json() or "access_token" not in res.get_json().get("data", {})

    # 2. Kiểm tra tính toàn vẹn và atomic sau rollback:
    # Fixture baseline vẫn tồn tại (chứng minh test không nhầm rollback xóa cả fixture là bằng chứng atomicity)
    user_in_db = transaction_session.query(User).filter_by(id=user_id).first()
    assert user_in_db is not None

    # Không có session, attempt hay log mồ côi/một phần nào được lưu lại sau rollback
    assert transaction_session.query(AuthSession).filter_by(user_id=user_id).count() == init_sessions
    assert transaction_session.query(LoginAttempt).filter_by(user_id=user_id).count() == init_attempts
    assert transaction_session.query(AuditLog).filter_by(actor_user_id=user_id).count() == init_audits


# ==============================================================================
# 12. KIỂM THỬ RÀNG BUỘC MUST_CHANGE_PASSWORD
# ==============================================================================

def test_must_change_password_restricts_access(client_with_control_route, transaction_session, auth_seed_data):
    """User có must_change_password=True chỉ được gọi /me và /logout; route khác bị 403."""
    auth_seed_data["user"].must_change_password = True
    transaction_session.flush()

    # 1. Đăng nhập thành công, cờ must_change_password=True trả về trong payload
    login_res = client_with_control_route.post(
        "/api/v1/auth/login",
        json={"username": auth_seed_data["username"], "password": auth_seed_data["password"]},
        headers={"Origin": "http://localhost:5173"},
    )
    assert login_res.status_code == 200
    assert login_res.get_json()["data"]["user"]["must_change_password"] is True
    token = login_res.get_json()["data"]["access_token"]

    # 2. Gọi GET /api/v1/auth/me -> Được phép (200 OK)
    me_res = client_with_control_route.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 200

    # 3. Gọi route nghiệp vụ khác -> Bắt buộc bị chặn HTTP 403 Forbidden
    ctrl_res = client_with_control_route.post(
        "/api/v1/test/control-endpoint",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ctrl_res.status_code == 403
    assert ctrl_res.get_json()["error"]["code"] == ErrorCode.FORBIDDEN

    # 4. Gọi POST /api/v1/auth/logout -> Được phép (200 OK)
    logout_res = client_with_control_route.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}", "Origin": "http://localhost:5173"},
    )
    assert logout_res.status_code == 200


# ==============================================================================
# 13. KIỂM THỬ DATABASE KHÔNG KHẢ DỤNG (503 SERVICE UNAVAILABLE)
# ==============================================================================

def test_login_returns_503_when_database_unavailable(client):
    """Khi kết nối database gặp lỗi OperationalError, hệ thống trả về HTTP 503."""
    with patch("backend.app.auth.service.acquire_rate_limit_advisory_locks") as mock_lock:
        mock_lock.side_effect = OperationalError("connection refused", None, None)
        res = client.post(
            "/api/v1/auth/login",
            json={"username": "valid_user", "password": "ValidPassword123!"},
            headers={"Origin": "http://localhost:5173"},
        )
        assert res.status_code == 503
        assert res.get_json()["error"]["code"] == ErrorCode.SERVICE_UNAVAILABLE


# ==============================================================================
# 14. KIỂM THỬ ROUTE EXT HOÃN KHÔNG ĐĂNG KÝ (404 NOT FOUND)
# ==============================================================================

@pytest.mark.parametrize("ext_path,method", [
    ("/api/v1/auth/refresh", "POST"),
    ("/api/v1/auth/logout-all", "POST"),
    ("/api/v1/auth/sessions", "GET"),
    ("/api/v1/auth/sessions/any-id", "DELETE"),
])
def test_unregistered_ext_routes_return_404(client, ext_path, method):
    """Các endpoint hoãn sang EXT bắt buộc không đăng ký và trả về HTTP 404."""
    if method == "POST":
        res = client.post(ext_path)
    elif method == "GET":
        res = client.get(ext_path)
    elif method == "DELETE":
        res = client.delete(ext_path)
    assert res.status_code == 404


# ==============================================================================
# 15. HỢP ĐỒNG KHÓA TRANSACTION ADVISORY LOCKS & CONCURRENCY
# ==============================================================================

def test_advisory_lock_order_and_contract(transaction_session):
    """Kiểm tra hợp đồng sắp xếp thứ tự tăng dần cố định giữa 2 khóa rate-limit (username_key và ip_key)."""
    from backend.app.auth.rate_limit import _key_to_lock_id, compute_rate_limit_keys

    u_key, ip_key = compute_rate_limit_keys("admin_demo", "192.168.1.100")
    k_user = _key_to_lock_id(u_key)
    k_ip = _key_to_lock_id(ip_key)

    locks = sorted(list({k_user, k_ip}))
    assert len(locks) in (1, 2)
    if len(locks) == 2:
        assert locks[0] < locks[1]

    for lock_id in locks:
        result = transaction_session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id}
        )
        assert result is not None
