"""
Session Guard & DB Verification Test Suite
Kiểm thử toàn diện:
1. Happy path: user ACTIVE, session hợp lệ, role hợp lệ -> 200 OK, trả đúng AuthContext,
   header no-store, không có Set-Cookie.
2. Từ chối 401:
   - Sai sid-sub (session thuộc user khác).
   - Thiếu user hoặc thiếu session trong DB.
   - User không có role được gán trong DB.
   - User có status LOCKED hoặc ARCHIVED.
   - Token_version lệch riêng ở user hoặc lệch riêng ở session.
   - Session đã bị thu hồi (revoked_at is not None).
   - Session đã hết hạn (expires_at <= now), tuân thủ check constraint created_at < expires_at.
   - Expiry lệch giữa JWT claim exp và DB session expires_at (kể cả chênh lệch phần giây).
   - Cookie-only (không có Authorization Bearer header).
3. Đọc dữ liệu tươi DB (Bỏ qua cache ORM):
   - Cập nhật trực tiếp DB bằng SQL thô ngoài ORM (LOCKED), guard phát hiện ngay lập tức.
4. Fail-closed 503 (Tham số hóa lỗi DB không khả dụng):
   - Khi DB mất kết nối (OperationalError), lỗi interface (InterfaceError) hoặc timeout
     connection pool (SQLAlchemyTimeoutError) đều trả về HTTP 503, không lộ SQL/traceback.
5. Cô lập context & tính chất Read-only:
   - Ba request liên tiếp (valid -> public -> invalid) chứng minh context gắn trên request.auth
     hoàn toàn không rò rỉ sang public endpoint hay request lỗi dưới cùng outer app context.
   - Polling gửi nhiều request không làm thay đổi last_seen_at hay expires_at trong DB.
6. Helper thu hồi phiên:
   - revoke_session caller-managed dùng begin_nested: không tự commit, rollback hoàn nguyên.
   - revoke_session idempotent dùng conditional UPDATE không ghi đè thời điểm và lý do thu hồi cũ.
   - revoke_all_user_sessions conditional UPDATE thu hồi đúng toàn bộ session của user chỉ định.
   - Kiểm soát thời gian bằng tham số clock, không sleep 15 phút.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4
import pytest
from flask import jsonify
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError as SQLAlchemyTimeoutError

from backend.app.auth.sessions import (
    revoke_all_user_sessions,
    revoke_session,
    verify_session_and_user,
)
from backend.app.auth.tokens import issue_access_token
from backend.app.models.iam import AuthSession, Role, User, UserRole
from backend.app.permissions.decorators import require_auth
from backend.app.security.context import AuthContext, get_current_auth
from backend.app.security.messages import ErrorCode, ErrorMessage


# ==============================================================================
# FIXTURES DÙNG CHUNG CHO KIỂM THỬ GUARD VÀ ROUTE
# ==============================================================================

@pytest.fixture
def auth_setup(transaction_session):
    """Fixture tạo User ACTIVE, Role OPERATOR và gán quyền trong PostgreSQL test."""
    role = transaction_session.query(Role).filter_by(code="OPERATOR").first()
    if role is None:
        role = Role(code="OPERATOR", name="Operator Role", is_system=True)
        transaction_session.add(role)
        transaction_session.flush()

    user = User(
        id=uuid4(),
        username=f"guard_user_{uuid4().hex[:8]}",
        password_hash="$argon2id$v=19$m=19456,t=2,p=1$mockhash",
        full_name="Guard Test User",
        status="ACTIVE",
        token_version=1,
    )
    transaction_session.add(user)
    transaction_session.flush()

    user_role = UserRole(user_id=user.id, role_id=role.id)
    transaction_session.add(user_role)
    transaction_session.flush()

    now = datetime.now(timezone.utc)
    # Khởi tạo session với thời hạn 900 giây
    auth_session = AuthSession(
        id=uuid4(),
        user_id=user.id,
        token_version=1,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=900),
    )
    transaction_session.add(auth_session)
    transaction_session.flush()

    # Cấp phát access token tương ứng với user và session vừa tạo
    token_data = issue_access_token(
        user_id=user.id,
        session_id=auth_session.id,
        token_version=1,
        expires_delta=900,
    )

    # Đảm bảo expires_at của session DB trùng khớp chính xác với claim exp của JWT
    auth_session.expires_at = token_data.expires_at
    transaction_session.flush()

    return user, auth_session, token_data, role


@pytest.fixture
def protected_client(app, transaction_session):
    """Đăng ký endpoint bảo vệ và endpoint public tạm thời trên app fixture."""
    if "_test_session_guard_resource" not in app.view_functions:
        @app.route("/_test/session-guard", methods=["GET"])
        @require_auth
        def _test_session_guard_resource():
            auth = get_current_auth()
            return jsonify({
                "status": "ok",
                "user_id": str(auth.user_id) if auth else None,
                "session_id": str(auth.session_id) if auth else None,
                "token_version": auth.token_version if auth else None,
                "request_id": auth.request_id if auth else None,
                "actor_type": auth.actor_type if auth else None,
            }), 200

    if "_test_public_resource" not in app.view_functions:
        @app.route("/_test/public-resource", methods=["GET"])
        def _test_public_resource():
            auth = get_current_auth()
            return jsonify({
                "has_auth": auth is not None,
                "user_id": str(auth.user_id) if auth else None,
            }), 200

    return app.test_client()


# ==============================================================================
# PHẦN 1: KIỂM THỬ XÁC THỰC THÀNH CÔNG (HAPPY PATH) VÀ BẢO TOÀN CONTEXT
# ==============================================================================

def test_guard_happy_path_access_granted(protected_client, auth_setup):
    """Xác thực thành công trả về HTTP 200, đúng danh tính AuthContext, no-store, không Set-Cookie."""
    user, session, token_data, _ = auth_setup
    headers = {"Authorization": f"Bearer {token_data.access_token}"}

    response = protected_client.get("/_test/session-guard", headers=headers)

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["user_id"] == str(user.id)
    assert data["session_id"] == str(session.id)
    assert data["token_version"] == 1
    assert data["actor_type"] == "USER"
    assert data["request_id"] is not None
    assert response.headers.get("Cache-Control") == "no-store"
    assert "Set-Cookie" not in response.headers


def test_guard_is_read_only_polling_does_not_mutate_session(
    protected_client, auth_setup, transaction_session
):
    """Guard chỉ đọc: gửi nhiều request polling không làm thay đổi last_seen_at hay expires_at."""
    user, session, token_data, _ = auth_setup
    headers = {"Authorization": f"Bearer {token_data.access_token}"}

    original_last_seen = session.last_seen_at
    original_expires_at = session.expires_at

    for _ in range(3):
        res = protected_client.get("/_test/session-guard", headers=headers)
        assert res.status_code == 200

    # SELECT lại trực tiếp từ DB để kiểm tra tính bền vững
    row_after = (
        transaction_session.query(AuthSession.last_seen_at, AuthSession.expires_at)
        .filter_by(id=session.id)
        .one()
    )
    assert row_after.last_seen_at == original_last_seen
    assert row_after.expires_at == original_expires_at


def test_context_isolation_across_consecutive_requests(protected_client, auth_setup):
    """Context (request.auth) cô lập tuyệt đối: request 1 thành công, request public và request 2 thất bại đều không bị rò rỉ context."""
    user, session, token_data, _ = auth_setup

    # Request 1: Hợp lệ -> 200 OK, auth gắn trên request
    res_ok = protected_client.get(
        "/_test/session-guard",
        headers={"Authorization": f"Bearer {token_data.access_token}"}
    )
    assert res_ok.status_code == 200
    assert res_ok.get_json()["user_id"] == str(user.id)

    # Request 2 (Public endpoint): Không có @require_auth -> get_current_auth() phải là None
    res_pub = protected_client.get("/_test/public-resource")
    assert res_pub.status_code == 200
    assert res_pub.get_json()["has_auth"] is False
    assert res_pub.get_json()["user_id"] is None

    # Request 3: Gửi token hỏng -> 401 Unauthorized, get_current_auth() là None
    res_fail = protected_client.get(
        "/_test/session-guard",
        headers={"Authorization": "Bearer invalid.token.payload"}
    )
    assert res_fail.status_code == 401
    assert get_current_auth() is None


# ==============================================================================
# PHẦN 2: CÁC CA TỪ CHỐI BẢO MẬT (HTTP 401 GENERIC ENVELOPE)
# ==============================================================================

def test_guard_reads_fresh_db_state_bypassing_orm_cache(
    protected_client, auth_setup, transaction_session
):
    """Guard đọc trực tiếp từ DB: khi DB bị cập nhật thô bằng SQL (LOCKED), guard phát hiện ngay."""
    user, session, token_data, _ = auth_setup
    headers = {"Authorization": f"Bearer {token_data.access_token}"}

    # Lần 1: Thành công
    res1 = protected_client.get("/_test/session-guard", headers=headers)
    assert res1.status_code == 200

    # Cập nhật trực tiếp dưới DB bằng câu lệnh SQL thô (bỏ qua identity map của ORM)
    transaction_session.execute(
        text("UPDATE users SET status = 'LOCKED' WHERE id = :uid"),
        {"uid": user.id}
    )
    transaction_session.flush()

    # Lần 2: Guard phải đọc thấy status LOCKED từ DB và từ chối 401 ngay lập tức
    res2 = protected_client.get("/_test/session-guard", headers=headers)
    assert res2.status_code == 401
    assert res2.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED


def test_guard_rejects_sid_sub_mismatch(
    protected_client, auth_setup, transaction_session
):
    """Từ chối 401 khi session trong DB thuộc về user khác so với sub trong token."""
    user, session, _, _ = auth_setup

    # Tạo user khác B
    other_user = User(
        id=uuid4(),
        username=f"user_b_{uuid4().hex[:6]}",
        password_hash="$argon2id$mock",
        full_name="User B",
        status="ACTIVE",
        token_version=1,
    )
    transaction_session.add(other_user)
    transaction_session.flush()

    # Ký token với sub=other_user.id nhưng sid=session.id (của user ban đầu)
    mismatch_token = issue_access_token(
        user_id=other_user.id,
        session_id=session.id,
        token_version=1,
    )

    headers = {"Authorization": f"Bearer {mismatch_token.access_token}"}
    response = protected_client.get("/_test/session-guard", headers=headers)

    assert response.status_code == 401
    data = response.get_json()
    assert data["error"]["code"] == ErrorCode.UNAUTHORIZED


def test_guard_rejects_missing_user_in_db(protected_client, auth_setup):
    """Từ chối 401 khi user không tồn tại trong DB."""
    _, session, _, _ = auth_setup
    ghost_user_id = uuid4()

    token = issue_access_token(user_id=ghost_user_id, session_id=session.id, token_version=1)
    headers = {"Authorization": f"Bearer {token.access_token}"}

    response = protected_client.get("/_test/session-guard", headers=headers)
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED


def test_guard_rejects_missing_session_in_db(protected_client, auth_setup):
    """Từ chối 401 khi session không tồn tại trong DB."""
    user, _, _, _ = auth_setup
    ghost_session_id = uuid4()

    token = issue_access_token(user_id=user.id, session_id=ghost_session_id, token_version=1)
    headers = {"Authorization": f"Bearer {token.access_token}"}

    response = protected_client.get("/_test/session-guard", headers=headers)
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED


def test_guard_rejects_user_without_role(protected_client, auth_setup, transaction_session):
    """Từ chối 401 khi user không được gán role nào trong bảng user_roles."""
    user, session, token_data, _ = auth_setup

    # Xóa liên kết role của user
    transaction_session.query(UserRole).filter_by(user_id=user.id).delete()
    transaction_session.flush()

    headers = {"Authorization": f"Bearer {token_data.access_token}"}
    response = protected_client.get("/_test/session-guard", headers=headers)

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED


@pytest.mark.parametrize("bad_status", ["LOCKED", "ARCHIVED"])
def test_guard_rejects_inactive_user_status(
    protected_client, auth_setup, transaction_session, bad_status
):
    """Từ chối 401 khi trạng thái user không phải là ACTIVE (LOCKED hoặc ARCHIVED)."""
    user, session, token_data, _ = auth_setup

    user.status = bad_status
    transaction_session.flush()

    headers = {"Authorization": f"Bearer {token_data.access_token}"}
    response = protected_client.get("/_test/session-guard", headers=headers)

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED


def test_guard_rejects_user_token_version_mismatch(
    protected_client, auth_setup, transaction_session
):
    """Từ chối 401 khi token_version của user tăng lên (ví dụ do đổi mật khẩu)."""
    user, session, token_data, _ = auth_setup

    user.token_version = 2
    transaction_session.flush()

    headers = {"Authorization": f"Bearer {token_data.access_token}"}
    response = protected_client.get("/_test/session-guard", headers=headers)

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED


def test_guard_rejects_session_token_version_mismatch(
    protected_client, auth_setup, transaction_session
):
    """Từ chối 401 khi token_version của session trong DB không khớp với JWT ver."""
    user, session, token_data, _ = auth_setup

    session.token_version = 2
    transaction_session.flush()

    headers = {"Authorization": f"Bearer {token_data.access_token}"}
    response = protected_client.get("/_test/session-guard", headers=headers)

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED


def test_guard_rejects_revoked_session(protected_client, auth_setup, transaction_session):
    """Từ chối 401 khi session đã bị thu hồi (revoked_at IS NOT NULL)."""
    user, session, token_data, _ = auth_setup

    session.revoked_at = datetime.now(timezone.utc)
    session.revoke_reason = "Manual revocation"
    transaction_session.flush()

    headers = {"Authorization": f"Bearer {token_data.access_token}"}
    response = protected_client.get("/_test/session-guard", headers=headers)

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED


def test_guard_rejects_expired_session(protected_client, auth_setup, transaction_session):
    """Từ chối 401 khi session DB đã quá hạn, bảo đảm thỏa mãn check constraint created_at < expires_at."""
    user, session, token_data, _ = auth_setup

    # Đặt created_at và last_seen_at trong quá khứ để created_at < expires_at < now (tránh vi phạm ck_auth_sessions_expires_after_created)
    now = datetime.now(timezone.utc)
    past_created = now - timedelta(seconds=1200)
    past_expires = now - timedelta(seconds=300)

    session.created_at = past_created
    session.last_seen_at = past_created
    session.expires_at = past_expires
    transaction_session.flush()

    # JWT vẫn còn hạn để chắc chắn từ chối là do guard DB phát hiện session hết hạn
    headers = {"Authorization": f"Bearer {token_data.access_token}"}
    response = protected_client.get("/_test/session-guard", headers=headers)

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED


def test_guard_rejects_expiry_mismatch_between_jwt_and_db(
    protected_client, auth_setup, transaction_session
):
    """Từ chối 401 khi mốc expires_at của session DB lệch với claim exp trong JWT."""
    user, session, token_data, _ = auth_setup

    # Làm lệch expires_at của session DB khác với exp của JWT
    session.expires_at = token_data.expires_at + timedelta(seconds=10)
    transaction_session.flush()

    headers = {"Authorization": f"Bearer {token_data.access_token}"}
    response = protected_client.get("/_test/session-guard", headers=headers)

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED


def test_guard_rejects_sub_second_expiry_mismatch(
    protected_client, auth_setup, transaction_session
):
    """Từ chối 401 khi expires_at của session DB lệch một phần giây (0.5s) so với exp của JWT."""
    user, session, token_data, _ = auth_setup

    # Làm lệch 0.5 giây (500,000 microseconds)
    session.expires_at = token_data.expires_at + timedelta(microseconds=500000)
    transaction_session.flush()

    headers = {"Authorization": f"Bearer {token_data.access_token}"}
    response = protected_client.get("/_test/session-guard", headers=headers)

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == ErrorCode.UNAUTHORIZED


def test_guard_rejects_cookie_only_request(protected_client, auth_setup):
    """Từ chối 401 khi token chỉ gửi qua Cookie mà thiếu header Authorization."""
    _, _, token_data, _ = auth_setup

    # Không gửi header Authorization, chỉ set cookie
    protected_client.set_cookie("access_token_cookie", token_data.access_token)
    response = protected_client.get("/_test/session-guard")

    assert response.status_code == 401
    data = response.get_json()
    assert data["error"]["code"] == ErrorCode.UNAUTHORIZED
    assert "Set-Cookie" not in response.headers


# ==============================================================================
# PHẦN 3: KIỂM THỬ XỬ LÝ LỖI DATABASE (HTTP 503 FAIL-CLOSED)
# ==============================================================================

@pytest.mark.parametrize(
    "db_error",
    [
        OperationalError("Connection refused", params=None, orig=Exception()),
        InterfaceError("Connection dropped", params=None, orig=Exception()),
        SQLAlchemyTimeoutError("QueuePool limit exceeded"),
    ],
)
def test_guard_db_failure_returns_503_fail_closed(protected_client, auth_setup, db_error):
    """Khi DB mất kết nối, lỗi interface hoặc timeout connection pool, guard trả về HTTP 503 fail-closed."""
    _, _, token_data, _ = auth_setup
    headers = {"Authorization": f"Bearer {token_data.access_token}"}

    with patch(
        "backend.app.permissions.decorators.verify_session_and_user",
        side_effect=db_error,
    ):
        response = protected_client.get("/_test/session-guard", headers=headers)

    assert response.status_code == 503
    data = response.get_json()
    assert data["error"]["code"] == ErrorCode.SERVICE_UNAVAILABLE
    assert data["error"]["message"] == ErrorMessage.SERVICE_UNAVAILABLE
    assert "Connection refused" not in response.text
    assert "QueuePool" not in response.text
    assert "SELECT" not in response.text


# ==============================================================================
# PHẦN 4: KIỂM THỬ HELPER THU HỒI PHIÊN (CALLER-MANAGED TRANSACTIONS)
# ==============================================================================

def test_revoke_session_caller_managed_transaction(auth_setup, transaction_session):
    """revoke_session hoạt động caller-managed: không tự commit; rollback nested savepoint hoàn nguyên dữ liệu."""
    user, session, _, _ = auth_setup

    with patch.object(transaction_session, "commit", wraps=transaction_session.commit) as mock_commit:
        # Tạo savepoint lồng nhau riêng cho mutation của test
        nested_sp = transaction_session.begin_nested()

        res = revoke_session(session.id, reason="User logout", db_session=transaction_session)
        assert res is True
        # Chứng minh helper không tự ý gọi commit()
        assert mock_commit.call_count == 0

        # Đọc dữ liệu trong savepoint lồng nhau
        revoked_row = (
            transaction_session.query(AuthSession.revoked_at, AuthSession.revoke_reason)
            .filter_by(id=session.id)
            .one()
        )
        assert revoked_row.revoked_at is not None
        assert revoked_row.revoke_reason == "User logout"

        # Rollback savepoint lồng nhau
        nested_sp.rollback()

        # Dữ liệu ban đầu từ auth_setup vẫn nguyên vẹn và revoked_at hoàn nguyên về None
        restored_row = (
            transaction_session.query(AuthSession.revoked_at, AuthSession.revoke_reason)
            .filter_by(id=session.id)
            .one()
        )
        assert restored_row.revoked_at is None
        assert restored_row.revoke_reason == None


def test_revoke_session_idempotent_preserves_initial_timestamp(
    auth_setup, transaction_session
):
    """revoke_session idempotent: gọi lần 2 không ghi đè thời điểm và lý do thu hồi ban đầu."""
    user, session, _, _ = auth_setup

    res1 = revoke_session(session.id, reason="First logout", db_session=transaction_session)
    assert res1 is True

    first_revoked_row = (
        transaction_session.query(AuthSession.revoked_at, AuthSession.revoke_reason)
        .filter_by(id=session.id)
        .one()
    )
    first_revoked_at = first_revoked_row.revoked_at

    # Gọi lần 2
    res2 = revoke_session(session.id, reason="Second logout attempt", db_session=transaction_session)
    assert res2 is False

    second_revoked_row = (
        transaction_session.query(AuthSession.revoked_at, AuthSession.revoke_reason)
        .filter_by(id=session.id)
        .one()
    )
    assert second_revoked_row.revoked_at == first_revoked_at
    assert second_revoked_row.revoke_reason == "First logout"


def test_revoke_all_user_sessions(auth_setup, transaction_session):
    """revoke_all_user_sessions dùng conditional UPDATE thu hồi đúng toàn bộ session của user chưa bị thu hồi."""
    user, session1, _, _ = auth_setup
    now = datetime.now(timezone.utc)

    # Tạo thêm session2 cho cùng user
    session2 = AuthSession(
        id=uuid4(),
        user_id=user.id,
        token_version=1,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=900),
    )
    transaction_session.add(session2)

    # Tạo session3 của user khác
    other_user = User(
        id=uuid4(),
        username=f"other_{uuid4().hex[:6]}",
        password_hash="$argon2id$mock",
        full_name="Other User",
        status="ACTIVE",
    )
    transaction_session.add(other_user)
    transaction_session.flush()

    other_session = AuthSession(
        id=uuid4(),
        user_id=other_user.id,
        token_version=1,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=900),
    )
    transaction_session.add(other_session)
    transaction_session.flush()

    # Thu hồi toàn bộ session của user
    count = revoke_all_user_sessions(user.id, reason="Password changed", db_session=transaction_session)
    assert count == 2

    s1_row = transaction_session.query(AuthSession.revoked_at).filter_by(id=session1.id).one()
    s2_row = transaction_session.query(AuthSession.revoked_at).filter_by(id=session2.id).one()
    other_row = transaction_session.query(AuthSession.revoked_at).filter_by(id=other_session.id).one()

    assert s1_row.revoked_at is not None
    assert s2_row.revoked_at is not None
    assert other_row.revoked_at is None


def test_controlled_clock_session_expiry_no_sleep(auth_setup, transaction_session):
    """Kiểm tra hết hạn bằng mốc thời gian kiểm soát (injected clock), không sleep 15 phút."""
    user, session, token_data, _ = auth_setup

    claims = {
        "sub": str(user.id),
        "sid": str(session.id),
        "ver": 1,
        "exp": int(session.expires_at.timestamp()),
    }

    # 1 giây trước khi hết hạn -> Hợp lệ
    ctx_valid = verify_session_and_user(
        claims,
        db_session=transaction_session,
        current_time=session.expires_at - timedelta(seconds=1),
    )
    assert ctx_valid is not None
    assert ctx_valid.user_id == user.id

    # 1 giây sau khi hết hạn -> Hết hạn (None)
    ctx_expired = verify_session_and_user(
        claims,
        db_session=transaction_session,
        current_time=session.expires_at + timedelta(seconds=1),
    )
    assert ctx_expired is None
