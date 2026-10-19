from datetime import datetime, timezone, timedelta
from uuid import uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.models import (
    User, AuthSession, Location, Device,
    AuditLog, UserActivityLog,
)
from backend.app.security.context import AuthContext
from backend.app.audit import AuditService
from backend.app.user_activity import UserActivityService


@pytest.fixture
def minimal_actor(transaction_session):
    """Fixture tạo user và session tối thiểu phục vụ kiểm thử log writer."""
    user = User(
        id=uuid4(),
        username=f"tester_{uuid4().hex[:8]}",
        password_hash="argon2id$mocked$hash",
        full_name="Log Tester",
        status="ACTIVE",
        token_version=1,
    )
    transaction_session.add(user)
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

    context = AuthContext(
        user_id=user.id,
        session_id=sess.id,
        token_version=1,
        request_id=str(uuid4()),
        actor_type="USER",
    )
    return user, sess, context


@pytest.fixture
def minimal_device(transaction_session, minimal_actor):
    """Fixture tạo Location và Device mẫu phục vụ test FK liên kết thiết bị."""
    user, _, _ = minimal_actor
    loc_code = f"loc_{uuid4().hex[:6]}"
    loc = Location(code=loc_code, name="Test Rack Location")
    transaction_session.add(loc)
    transaction_session.flush()

    dev_id = f"cab_{uuid4().hex[:8]}"
    dev = Device(
        device_id=dev_id,
        name="Test Cabinet Device",
        location_id=loc.id,
        owner_user_id=user.id,
        device_type="CONTROLLER",
        data_source="REAL",
        lifecycle_state="REGISTERED",
        connection_state="UNKNOWN",
        health_state="UNKNOWN",
        desired_config_version=1,
    )
    transaction_session.add(dev)
    transaction_session.flush()
    return dev


# ==============================================================================
# 1. AUDIT LOG WRITER TESTS
# ==============================================================================

def test_audit_service_record_success_user(transaction_session, minimal_actor):
    """(Ca 1) Ghi audit_log thành công cho user hợp lệ, đầy đủ trường schema."""
    user, sess, actor = minimal_actor
    req_id = uuid4()

    entry = AuditService.record(
        action="USER_CREATE",
        target_type="USER",
        target_id="target-user-001",
        success=True,
        request_id=req_id,
        actor=actor,
        actor_name_snapshot="Log Tester",
        detail={"created_username": "target_user_001", "role": "OPERATOR"},
        db_session=transaction_session,
    )

    assert entry.id is not None
    assert entry.actor_user_id == user.id
    assert entry.actor_session_id == sess.id
    assert entry.actor_type == "USER"
    assert entry.action == "USER_CREATE"
    assert entry.success is True
    assert entry.request_id == req_id
    assert entry.detail == {"created_username": "target_user_001", "role": "OPERATOR"}

    saved = transaction_session.query(AuditLog).filter_by(id=entry.id).one()
    assert saved.target_id == "target-user-001"


def test_audit_service_actor_types_allowed_and_rejected(transaction_session):
    """(Ca 2) AuditLog cho phép USER, SYSTEM, SERVICE; từ chối loại actor không hợp lệ."""
    req_id = uuid4()

    # SYSTEM
    log_sys = AuditService.record(
        action="SYSTEM_MAINTENANCE",
        target_type="SYSTEM",
        target_id="system-root",
        success=True,
        actor_type="SYSTEM",
        request_id=req_id,
        db_session=transaction_session,
    )
    assert log_sys.actor_type == "SYSTEM"
    assert log_sys.actor_user_id is None

    # SERVICE
    log_svc = AuditService.record(
        action="DEVICE_PROVISION",
        target_type="SERVICE",
        target_id="worker-01",
        success=True,
        actor_type="SERVICE",
        request_id=uuid4(),
        db_session=transaction_session,
    )
    assert log_svc.actor_type == "SERVICE"

    # Invalid actor_type
    with pytest.raises(ValueError, match="Invalid actor type"):
        AuditService.record(
            action="INVALID",
            target_type="DEVICE",
            target_id="d1",
            success=False,
            actor_type="UNKNOWN_TYPE",
            request_id=uuid4(),
            db_session=transaction_session,
        )


def test_audit_service_actor_type_required_when_context_missing(transaction_session):
    """(Ca 3) Bắt buộc caller phải truyền actor_type khi không có AuthContext (chặn ngầm hiểu SYSTEM)."""
    with pytest.raises(ValueError, match="actor_type is required"):
        AuditService.record(
            action="USER_CREATE",
            target_type="USER",
            target_id="u1",
            success=True,
            actor=None,
            actor_type=None,  # Bỏ quên actor_type -> Phải ném lỗi!
            request_id=uuid4(),
            db_session=transaction_session,
        )


def test_audit_service_rejects_nested_secrets_and_driver_dsn(transaction_session):
    """(Ca 4) Từ chối payload audit chứa nested password, driver-prefixed DSN hoặc key cấm."""
    req_id = uuid4()

    # Khóa cấm cấp ngoài: password
    with pytest.raises(ValueError, match="Sensitive data detected"):
        AuditService.record(
            action="LOGIN_FAIL",
            target_type="USER",
            target_id="u1",
            success=False,
            actor_type="USER",
            request_id=req_id,
            detail={"attempted_username": "alice", "password": "SecretPassword123!"},
            db_session=transaction_session,
        )

    # Khóa lạ không nằm trong allowlist
    with pytest.raises(ValueError, match="Unallowed key"):
        AuditService.record(
            action="USER_CREATE",
            target_type="USER",
            target_id="u1",
            success=True,
            actor_type="SYSTEM",
            request_id=req_id,
            detail={"unknown_custom_key": "some_value"},
            db_session=transaction_session,
        )

    # Cấu trúc lồng nhau tùy ý (nested dict chứa mật khẩu)
    with pytest.raises(ValueError, match="nested dict forbidden"):
        AuditService.record(
            action="USER_UPDATE",
            target_type="USER",
            target_id="u1",
            success=True,
            actor_type="SYSTEM",
            request_id=req_id,
            detail={"target_username": "alice", "reason": {"password": "REVIEW_ONLY_SENTINEL"}},
            db_session=transaction_session,
        )

    # Driver-prefixed DSN: postgresql+psycopg2://...
    with pytest.raises(ValueError, match="Sensitive data detected"):
        AuditService.record(
            action="USER_UPDATE",
            target_type="USER",
            target_id="u1",
            success=False,
            actor_type="SYSTEM",
            request_id=req_id,
            detail={"error": "Connection failed to postgresql+psycopg2://user:pass@localhost:5432/db"},
            db_session=transaction_session,
        )


def test_audit_service_actor_conflict_and_spoofing_rejected(transaction_session, minimal_actor):
    """(Ca 5) Chặn giả mạo actor và chặn gắn user_id hoặc session_id vào actor SYSTEM/SERVICE."""
    user, sess, actor = minimal_actor

    # 1. Cố ý override USER thành SYSTEM khi có AuthContext
    with pytest.raises(ValueError, match="Actor spoofing detected"):
        AuditService.record(
            action="PRIVILEGE_ESCALATE",
            target_type="SYSTEM",
            target_id="root",
            success=True,
            actor=actor,
            actor_type="SYSTEM",
            request_id=uuid4(),
            db_session=transaction_session,
        )

    # 2. Gắn session_id vào actor SYSTEM (không có AuthContext)
    with pytest.raises(ValueError, match="conflict with verified AuthContext"):
        AuditService.record(
            action="SYS_EVENT",
            target_type="SYSTEM",
            target_id="sys1",
            success=True,
            actor_type="SYSTEM",
            actor_session_id=sess.id,
            request_id=uuid4(),
            db_session=transaction_session,
        )

    # 3. Gắn user_id vào actor SYSTEM (không có AuthContext, session_id=None)
    with pytest.raises(ValueError, match="conflict with verified AuthContext"):
        AuditService.record(
            action="SYS_EVENT",
            target_type="SYSTEM",
            target_id="sys2",
            success=True,
            actor_type="SYSTEM",
            actor_user_id=user.id,
            actor_session_id=None,
            request_id=uuid4(),
            db_session=transaction_session,
        )

    # 4. Gắn user_id vào actor SERVICE (không có AuthContext, session_id=None)
    with pytest.raises(ValueError, match="conflict with verified AuthContext"):
        AuditService.record(
            action="SVC_EVENT",
            target_type="SERVICE",
            target_id="svc1",
            success=True,
            actor_type="SERVICE",
            actor_user_id=user.id,
            actor_session_id=None,
            request_id=uuid4(),
            db_session=transaction_session,
        )


def test_audit_service_unauthenticated_pre_login(transaction_session):
    """(Ca 6) Ghi nhận audit trước đăng nhập (user_id/session_id null) mà không tạo fake FK."""
    req_id = uuid4()
    entry = AuditService.record(
        action="AUTH_LOGIN_ATTEMPT",
        target_type="USER",
        target_id="unknown_user",
        success=False,
        actor_user_id=None,
        actor_session_id=None,
        actor_type="USER",
        reason_code="INVALID_CREDENTIALS",
        request_id=req_id,
        ip_address="192.168.1.100",
        detail={"attempted_username": "unknown_user"},
        db_session=transaction_session,
    )
    assert entry.id is not None
    assert entry.actor_user_id is None
    assert entry.actor_session_id is None
    assert entry.success is False
    assert entry.reason_code == "INVALID_CREDENTIALS"


# ==============================================================================
# 2. USER ACTIVITY LOG WRITER TESTS
# ==============================================================================

def test_user_activity_record_success(transaction_session, minimal_actor):
    """(Ca 7) Ghi user_activity_log chuẩn, summary tự sinh theo template server."""
    user, sess, actor = minimal_actor
    req_id = uuid4()

    entry = UserActivityService.record(
        function_key="user.create",
        action="CREATE",
        outcome="SUCCESS",
        target_type="USER",
        target_id=str(user.id),
        target_name_snapshot="bob_operator",
        summary_params={"target_username": "bob_operator", "role_code": "OPERATOR"},
        actor=actor,
        actor_name_snapshot="Log Tester",
        request_id=req_id,
        metadata={"target_username": "bob_operator", "role_code": "OPERATOR"},
        db_session=transaction_session,
    )

    assert entry.id is not None
    assert entry.function_key == "user.create"
    assert entry.action == "CREATE"
    assert entry.outcome == "SUCCESS"
    assert "bob_operator" in entry.summary
    assert entry.metadata_ == {"target_username": "bob_operator", "role_code": "OPERATOR"}

    saved = transaction_session.query(UserActivityLog).filter_by(id=entry.id).one()
    assert saved.metadata_["role_code"] == "OPERATOR"


def test_user_activity_actor_types_user_and_system_only(transaction_session, minimal_actor):
    """(Ca 8) UserActivityLog chỉ chấp nhận USER và SYSTEM; từ chối SERVICE và loại khác."""
    _, _, actor = minimal_actor

    # SYSTEM hợp lệ
    sys_entry = UserActivityService.record(
        function_key="user.lock",
        action="UPDATE",
        outcome="SUCCESS",
        actor_type="SYSTEM",
        request_id=uuid4(),
        summary_params={"target_username": "locked_user"},
        db_session=transaction_session,
    )
    assert sys_entry.actor_type == "SYSTEM"

    # SERVICE bị từ chối
    with pytest.raises(ValueError, match="Invalid actor type"):
        UserActivityService.record(
            function_key="user.lock",
            action="UPDATE",
            outcome="SUCCESS",
            actor_type="SERVICE",
            request_id=uuid4(),
            summary_params={"target_username": "locked_user"},
            db_session=transaction_session,
        )


def test_user_activity_actor_type_required_when_context_missing(transaction_session):
    """(Ca 9) Bắt buộc caller phải truyền actor_type khi không có AuthContext."""
    with pytest.raises(ValueError, match="actor_type is required"):
        UserActivityService.record(
            function_key="user.lock",
            action="UPDATE",
            outcome="SUCCESS",
            actor=None,
            actor_type=None,
            request_id=uuid4(),
            summary_params={"target_username": "test_user"},
            db_session=transaction_session,
        )


def test_user_activity_system_rejects_user_or_session_id(transaction_session, minimal_actor):
    """(Ca 10) Chặn gắn user_id hoặc session_id vào actor_type='SYSTEM' khi không có AuthContext."""
    user, sess, _ = minimal_actor

    # 1. Gắn user_id vào SYSTEM (session_id=None)
    with pytest.raises(ValueError, match="conflict with verified AuthContext"):
        UserActivityService.record(
            function_key="user.lock",
            action="UPDATE",
            outcome="SUCCESS",
            actor_type="SYSTEM",
            actor_user_id=user.id,
            actor_session_id=None,
            request_id=uuid4(),
            summary_params={"target_username": "locked_user"},
            db_session=transaction_session,
        )

    # 2. Gắn session_id vào SYSTEM (user_id=None)
    with pytest.raises(ValueError, match="conflict with verified AuthContext"):
        UserActivityService.record(
            function_key="user.lock",
            action="UPDATE",
            outcome="SUCCESS",
            actor_type="SYSTEM",
            actor_user_id=None,
            actor_session_id=sess.id,
            request_id=uuid4(),
            summary_params={"target_username": "locked_user"},
            db_session=transaction_session,
        )


def test_user_activity_action_and_outcome_validation(transaction_session, minimal_actor):
    """(Ca 11) Kiểm tra khớp action và outcome theo catalog và database check constraints."""
    _, _, actor = minimal_actor
    req_id = uuid4()

    # Action không có trong schema
    with pytest.raises(ValueError, match="Invalid activity action"):
        UserActivityService.record(
            function_key="user.create",
            action="NON_EXISTENT_ACTION",
            outcome="SUCCESS",
            actor=actor,
            request_id=req_id,
            summary_params={"target_username": "user1", "role_code": "OPERATOR"},
            db_session=transaction_session,
        )

    # Action không khớp với function_key (auth.login không thể là DELETE)
    with pytest.raises(ValueError, match="Invalid activity action"):
        UserActivityService.record(
            function_key="auth.login",
            action="DELETE",
            outcome="SUCCESS",
            actor=actor,
            request_id=req_id,
            summary_params={"username": "alice"},
            db_session=transaction_session,
        )

    # Outcome không hợp lệ
    with pytest.raises(ValueError, match="Invalid activity outcome"):
        UserActivityService.record(
            function_key="user.create",
            action="CREATE",
            outcome="UNKNOWN_OUTCOME",
            actor=actor,
            request_id=req_id,
            summary_params={"target_username": "user1", "role_code": "OPERATOR"},
            db_session=transaction_session,
        )


def test_user_activity_async_command_accepted_not_premature_success(transaction_session, minimal_actor, minimal_device):
    """(Ca 12) Lệnh điều khiển bất đồng bộ (202) phải dùng outcome ACCEPTED, ném đúng lỗi ASYNC_OUTCOME_MISMATCH khi dùng SUCCESS."""
    _, _, actor = minimal_actor
    dev = minimal_device
    req_id = uuid4()

    # ACCEPTED hợp lệ
    entry = UserActivityService.record(
        function_key="device.control",
        action="CONTROL",
        outcome="ACCEPTED",
        device_id=dev.device_id,
        summary_params={"command_type": "FAN_ON", "device_id": dev.device_id},
        actor=actor,
        request_id=req_id,
        metadata={"device_id": dev.device_id, "command_type": "FAN_ON", "fan_state": "ON"},
        db_session=transaction_session,
    )
    assert entry.outcome == "ACCEPTED"
    assert "Tiếp nhận lệnh điều khiển quạt" in entry.summary

    # SUCCESS bị từ chối đối với async function -> Phải raise đúng LOG_ASYNC_OUTCOME_MISMATCH
    with pytest.raises(ValueError, match="Async operation requires outcome ACCEPTED"):
        UserActivityService.record(
            function_key="device.control",
            action="CONTROL",
            outcome="SUCCESS",
            device_id=dev.device_id,
            actor=actor,
            request_id=uuid4(),
            summary_params={"command_type": "FAN_ON", "device_id": dev.device_id},
            db_session=transaction_session,
        )


def test_user_activity_unique_constraint_enforced(transaction_session, minimal_actor):
    """(Ca 13) Giữ ràng buộc UNIQUE(request_id, function_key, action), không nuốt lỗi IntegrityError."""
    _, _, actor = minimal_actor
    shared_req_id = uuid4()

    # Ghi lần 1 thành công
    UserActivityService.record(
        function_key="user.lock",
        action="UPDATE",
        outcome="SUCCESS",
        actor=actor,
        request_id=shared_req_id,
        summary_params={"target_username": "user_a"},
        db_session=transaction_session,
    )

    # Ghi lần 2 trùng (request_id, function_key, action) phải raise IntegrityError khi flush
    with pytest.raises(IntegrityError):
        UserActivityService.record(
            function_key="user.lock",
            action="UPDATE",
            outcome="SUCCESS",
            actor=actor,
            request_id=shared_req_id,
            summary_params={"target_username": "user_b"},
            db_session=transaction_session,
        )


def test_user_activity_rejects_nested_metadata_and_secrets(transaction_session, minimal_actor):
    """(Ca 14) Từ chối metadata chứa mật khẩu phẳng hoặc cấu trúc lồng nhau."""
    _, _, actor = minimal_actor
    req_id = uuid4()

    # Mật khẩu trực tiếp
    with pytest.raises(ValueError, match="Sensitive data detected"):
        UserActivityService.record(
            function_key="user.create",
            action="CREATE",
            outcome="SUCCESS",
            actor=actor,
            request_id=req_id,
            summary_params={"target_username": "attacker", "role_code": "VIEWER"},
            metadata={"target_username": "attacker", "password": "PlainTextPassword123!"},
            db_session=transaction_session,
        )

    # Cấu trúc lồng nhau (nested dict)
    with pytest.raises(ValueError, match="nested dict forbidden"):
        UserActivityService.record(
            function_key="user.create",
            action="CREATE",
            outcome="SUCCESS",
            actor=actor,
            request_id=req_id,
            summary_params={"target_username": "attacker", "role_code": "VIEWER"},
            metadata={"reason": {"password": "REVIEW_ONLY_SENTINEL"}},
            db_session=transaction_session,
        )


def test_user_activity_rejects_unallowed_metadata_keys(transaction_session, minimal_actor):
    """(Ca 15) Từ chối các khóa metadata không nằm trong allowlist của nghiệp vụ."""
    _, _, actor = minimal_actor
    with pytest.raises(ValueError, match="Unallowed key"):
        UserActivityService.record(
            function_key="user.lock",
            action="UPDATE",
            outcome="SUCCESS",
            actor=actor,
            request_id=uuid4(),
            summary_params={"target_username": "user_1"},
            metadata={"unallowed_attribute": "malicious_payload"},
            db_session=transaction_session,
        )


def test_user_activity_summary_render_policy(transaction_session, minimal_actor):
    """(Ca 16) Summary bắt buộc render từ template; thiếu tham số ném lỗi; không cho ghi đè outcome."""
    _, _, actor = minimal_actor

    # Thiếu tham số bắt buộc của template (thiếu role_code)
    with pytest.raises(ValueError, match="Missing required summary parameter"):
        UserActivityService.record(
            function_key="user.create",
            action="CREATE",
            outcome="SUCCESS",
            actor=actor,
            request_id=uuid4(),
            summary_params={"target_username": "bob"},  # Thiếu role_code!
            db_session=transaction_session,
        )

    # Cố ý nhét outcome giả vào summary_params -> Template vẫn dùng outcome thật đã validate
    entry = UserActivityService.record(
        function_key="auth.login",
        action="LOGIN",
        outcome="FAILED",
        actor_type="USER",
        request_id=uuid4(),
        summary_params={"username": "hacker", "outcome": "SUCCESS_FAKE"},  # Giả mạo outcome trong params!
        db_session=transaction_session,
    )
    assert "[FAILED]" in entry.summary
    assert "SUCCESS_FAKE" not in entry.summary


def test_user_activity_rejects_routine_read_and_poll_tracking(transaction_session, minimal_actor):
    """(Ca 17) Chặn ghi log đối với các tác vụ đọc/poll thường xuyên (telemetry.read, device.poll)."""
    _, _, actor = minimal_actor

    for forbidden_fk in ["telemetry.read", "device.read", "device.poll", "audit.read", "activity.list"]:
        with pytest.raises(ValueError, match="Unknown or disallowed function key"):
            UserActivityService.record(
                function_key=forbidden_fk,
                action="VIEW",
                outcome="SUCCESS",
                actor=actor,
                request_id=uuid4(),
                db_session=transaction_session,
            )


# ==============================================================================
# 3. TRANSACTION ATOMICITY & ROLLBACK TESTS
# ==============================================================================

def test_atomic_mutation_and_log_rollback(transaction_session, minimal_actor):
    """(Ca 18) Rollback mutation nghiệp vụ phải xóa sạch SUCCESS audit và activity log cùng transaction."""
    _, _, actor = minimal_actor
    req_id = uuid4()
    target_uname = f"tmp_user_{uuid4().hex[:6]}"

    # Dùng SAVEPOINT lồng nhau
    nested = transaction_session.begin_nested()

    # 1. Mutation nghiệp vụ thật: Tạo User
    loc = User(
        id=uuid4(),
        username=target_uname,
        password_hash="argon2id$mocked$hash",
        full_name="Temporary User",
        status="ACTIVE",
        token_version=1,
    )
    transaction_session.add(loc)
    transaction_session.flush()

    # 2. SUCCESS audit và activity log trong cùng transaction
    audit_entry = AuditService.record(
        action="USER_CREATE",
        target_type="USER",
        target_id=target_uname,
        success=True,
        request_id=req_id,
        actor=actor,
        detail={"created_username": target_uname, "role": "VIEWER"},
        db_session=transaction_session,
    )
    activity_entry = UserActivityService.record(
        function_key="user.create",
        action="CREATE",
        outcome="SUCCESS",
        target_type="USER",
        target_id=target_uname,
        actor=actor,
        request_id=req_id,
        summary_params={"target_username": target_uname, "role_code": "VIEWER"},
        db_session=transaction_session,
    )

    # Xác nhận các bản ghi đang hiện diện trong savepoint
    assert transaction_session.query(User).filter_by(username=target_uname).first() is not None
    assert transaction_session.query(AuditLog).filter_by(id=audit_entry.id).first() is not None
    assert transaction_session.query(UserActivityLog).filter_by(id=activity_entry.id).first() is not None

    # Rollback SAVEPOINT nghiệp vụ
    nested.rollback()

    # Đảm bảo cả mutation VÀ log đều bị xóa sạch 100%
    assert transaction_session.query(User).filter_by(username=target_uname).first() is None
    assert transaction_session.query(AuditLog).filter_by(id=audit_entry.id).first() is None
    assert transaction_session.query(UserActivityLog).filter_by(id=activity_entry.id).first() is None


def test_writer_flush_failure_prevents_mutation_commit(transaction_session, minimal_actor):
    """(Ca 19) Lỗi flush trong writer (vi phạm FK device_id) phải lan truyền để caller rollback mutation."""
    _, _, actor = minimal_actor
    target_uname = f"tmp_user_{uuid4().hex[:6]}"

    nested = transaction_session.begin_nested()

    # Mutation nghiệp vụ
    loc = User(
        id=uuid4(),
        username=target_uname,
        password_hash="argon2id$mocked$hash",
        full_name="User In Jeopardy",
        status="ACTIVE",
        token_version=1,
    )
    transaction_session.add(loc)
    transaction_session.flush()

    # Writer cố ý gọi với device_id không tồn tại gây vi phạm FK constraint trong DB
    with pytest.raises(IntegrityError):
        UserActivityService.record(
            function_key="device.lock",
            action="UPDATE",
            outcome="SUCCESS",
            device_id="non-existent-device-xyz-9999",  # Vi phạm FK devices!
            actor=actor,
            request_id=uuid4(),
            summary_params={"device_id": "non-existent-device-xyz-9999"},
            db_session=transaction_session,
        )

    # Rollback mutation do writer gặp lỗi
    nested.rollback()

    # Mutation không được commit
    assert transaction_session.query(User).filter_by(username=target_uname).first() is None


def test_failure_log_persists_in_new_transaction_after_rollback(transaction_session, minimal_actor):
    """(Ca 20) Nghiệp vụ thất bại bị rollback; caller mở transaction/savepoint mới ghi FAILED/DENIED log thành công."""
    _, _, actor = minimal_actor
    req_id = uuid4()
    target_uname = f"failed_user_{uuid4().hex[:6]}"

    # Giai đoạn 1: Mutation gặp lỗi nghiệp vụ và rollback
    nested1 = transaction_session.begin_nested()
    loc = User(
        id=uuid4(),
        username=target_uname,
        password_hash="argon2id$mocked$hash",
        full_name="Failed User",
        status="ACTIVE",
        token_version=1,
    )
    transaction_session.add(loc)
    transaction_session.flush()
    # Giả lập phát hiện lỗi nghiệp vụ -> Rollback
    nested1.rollback()

    # Xác nhận mutation đã mất
    assert transaction_session.query(User).filter_by(username=target_uname).first() is None

    # Giai đoạn 2: Caller ghi failure log trong transaction/savepoint mới
    nested2 = transaction_session.begin_nested()
    failed_audit = AuditService.record(
        action="USER_CREATE",
        target_type="USER",
        target_id=target_uname,
        success=False,
        reason_code="BUSINESS_RULE_VIOLATION",
        request_id=req_id,
        actor=actor,
        db_session=transaction_session,
    )
    denied_activity = UserActivityService.record(
        function_key="user.create",
        action="CREATE",
        outcome="DENIED",
        target_type="USER",
        target_id=target_uname,
        error_code="BUSINESS_RULE_VIOLATION",
        actor=actor,
        request_id=req_id,
        summary_params={"target_username": target_uname, "role_code": "OPERATOR"},
        db_session=transaction_session,
    )
    nested2.commit()

    # Mutation không tồn tại, nhưng failure logs được lưu thành công
    assert transaction_session.query(User).filter_by(username=target_uname).first() is None
    assert transaction_session.query(AuditLog).filter_by(id=failed_audit.id).first() is not None
    assert transaction_session.query(UserActivityLog).filter_by(id=denied_activity.id).first() is not None


def test_writer_does_not_commit_or_rollback_caller_session(transaction_session, minimal_actor):
    """(Ca 21) Writer giữ nguyên trạng thái transaction mở của caller, không tự commit hoặc rollback."""
    _, _, actor = minimal_actor

    assert transaction_session.in_transaction()

    AuditService.record(
        action="USER_UPDATE",
        target_type="SYSTEM",
        target_id="test-1",
        success=True,
        request_id=uuid4(),
        actor=actor,
        db_session=transaction_session,
    )
    # Transaction vẫn đang mở (chưa commit)
    assert transaction_session.in_transaction()


def test_autobegin_safe_after_select_query(transaction_session, minimal_actor):
    """(Ca 22) Writer an toàn khi session đã autobegin sau câu lệnh SELECT (không raise InvalidRequestError)."""
    _, _, actor = minimal_actor

    # Chạy SELECT kích hoạt autobegin trong SQLAlchemy 2.0
    val = transaction_session.execute(text("SELECT 1")).scalar()
    assert val == 1
    assert transaction_session.in_transaction()

    # Writer ghi nhận bình thường, không gọi session.begin() mù
    entry = AuditService.record(
        action="USER_UPDATE",
        target_type="SYSTEM",
        target_id="autobegin",
        success=True,
        request_id=uuid4(),
        actor=actor,
        db_session=transaction_session,
    )
    assert entry.id is not None
