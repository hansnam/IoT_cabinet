"""
Auth Sessions & Database Guard Module
Chịu trách nhiệm:
1. Xác minh sự tồn tại và tính hợp lệ của User, Session, và Role trong PostgreSQL.
   - Truy vấn scalar column trực tiếp để luôn đọc trạng thái DB hiện tại (không dùng identity map cache).
   - Sử dụng no_autoflush để không tự flush các thay đổi dở dang của caller.
   - So sánh datetime UTC chính xác đến từng microsecond với mốc exp của JWT.
   - Nạp cờ must_change_password vào request context phục vụ kiểm tra ở guard dùng chung.
2. Cung cấp helper nội bộ thu hồi session (revoke_session, revoke_all_user_sessions)
   - Sử dụng conditional UPDATE ... WHERE revoked_at IS NULL để bảo đảm tính nguyên tử khi chạy đồng thời.
   - Caller-managed transaction: không tự commit, không xóa row, bảo toàn timestamp/reason cũ.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.app.models.iam import User, AuthSession, UserRole, Role
from backend.app.security.context import AuthContext

def _coerce_uuid(val: UUID | str | None) -> UUID | None:
    """Chuyển đổi an toàn chuỗi hoặc UUID sang kiểu UUID. Trả về None nếu không hợp lệ."""
    if val is None:
        return None
    if isinstance(val, UUID):
        return val
    try:
        return UUID(str(val).strip())
    except (ValueError, TypeError):
        return None

def verify_session_and_user(
    jwt_claims: dict,
    db_session: Session,
    current_time: datetime | None = None,
) -> AuthContext | None:
    """Xác thực danh tính và phiên đăng nhập đối chiếu trực tiếp với database.
    - jwt_claims: Claims đã được pipeline 02B giải mã và xác minh chữ ký mã hóa.
    - db_session: SQLAlchemy session đang hoạt động của caller.
    - current_time: Mốc thời gian UTC (cho phép inject phục vụ kiểm thử không sleep).

    Trả về AuthContext nếu toàn bộ điều kiện sau thỏa mãn:
    1. User tồn tại, status == 'ACTIVE', token_version khớp claim 'ver'.
    2. Session tồn tại, sid thuộc đúng sub, chưa bị revoked (revoked_at is None),
       session.token_version khớp cả ver JWT và user.token_version.
    3. Session chưa hết hạn (expires_at > now UTC) và expires_at khớp chính xác
       với mốc exp của JWT dưới dạng datetime UTC.
    4. User có ít nhất 1 role hợp lệ gán trong user_roles.
    Nếu bất kỳ điều kiện nào không đạt, trả về None (fail closed -> 401).
    Nếu DB mất kết nối/timeout, để lỗi lan truyền để decorator trả 503.
    """
    if not isinstance(jwt_claims, dict) or db_session is None:
        return None

    sub_uuid = _coerce_uuid(jwt_claims.get("sub"))
    sid_uuid = _coerce_uuid(jwt_claims.get("sid"))
    ver = jwt_claims.get("ver")
    exp_ts = jwt_claims.get("exp")

    if sub_uuid is None or sid_uuid is None or not isinstance(ver, int) or isinstance(ver, bool):
        return None
    if exp_ts is None or not isinstance(exp_ts, (int, float)) or isinstance(exp_ts, bool):
        return None

    now_utc = current_time or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    jwt_exp_dt = datetime.fromtimestamp(exp_ts, tz=timezone.utc)

    with db_session.no_autoflush:
        row = (
            db_session.query(
                User.status,
                User.token_version.label("user_ver"),
                User.must_change_password.label("must_change_password"),
                AuthSession.user_id.label("session_user_id"),
                AuthSession.token_version.label("session_ver"),
                AuthSession.expires_at,
                AuthSession.revoked_at,
                Role.id.label("role_id"),
            )
            .select_from(User)
            .join(AuthSession, AuthSession.id == sid_uuid)
            .outerjoin(UserRole, UserRole.user_id == User.id)
            .outerjoin(Role, Role.id == UserRole.role_id)
            .filter(User.id == sub_uuid)
            .first()
        )

    if row is None:
        return None

    # 1. User bắt buộc tồn tại, ACTIVE và đúng token_version
    if row.status != "ACTIVE" or row.user_ver != ver:
        return None

    # 2. Session bắt buộc thuộc đúng sub, chưa revoked, token_version khớp JWT và User
    if (
        row.session_user_id != sub_uuid
        or row.revoked_at is not None
        or row.session_ver != ver
        or row.session_ver != row.user_ver
    ):
        return None

    # 3. Kiểm tra thời hạn session và khớp chính xác mốc thời gian exp của JWT
    sess_exp = row.expires_at
    if sess_exp.tzinfo is None:
        sess_exp = sess_exp.replace(tzinfo=timezone.utc)

    if sess_exp <= now_utc:
        return None

    if sess_exp != jwt_exp_dt:
        return None

    # 4. Xác nhận user có role hợp lệ trong DB (không tin role claim)
    if row.role_id is None:
        return None

    # 5. Khởi tạo AuthContext chuẩn bảo mật và gắn cờ must_change_password vào request
    from flask import g, request, has_request_context
    req_id = ""
    if has_request_context():
        req_id = getattr(g, "request_id", None) or getattr(request, "request_id", "")
        request.must_change_password = bool(row.must_change_password)

    return AuthContext(
        user_id=sub_uuid,
        session_id=sid_uuid,
        token_version=ver,
        request_id=str(req_id),
        actor_type="USER",
    )

def revoke_session(
    session_id: UUID | str,
    reason: str | None = None,
    db_session: Session | None = None,
) -> bool:
    """Helper nội bộ thu hồi một phiên đăng nhập
    - Thao tác caller-managed: Caller chịu trách nhiệm commit transaction; hàm KHÔNG tự commit.
    - Sử dụng conditional UPDATE nguyên tử WHERE revoked_at IS NULL để tránh ghi đè đồng thời.
    - Không xóa dòng (không DELETE row) để bảo toàn audit trail.
    - Idempotent: Nếu phiên đã bị thu hồi trước đó, không ghi đè thời điểm hoặc lý do cũ.
    """
    s_uuid = _coerce_uuid(session_id)
    if s_uuid is None or db_session is None:
        return False

    now_utc = datetime.now(timezone.utc)
    clean_reason = reason.strip() if reason else None

    stmt = (
        update(AuthSession)
        .where(AuthSession.id == s_uuid, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now_utc, revoke_reason=clean_reason)
        .execution_options(synchronize_session="fetch")
    )
    result = db_session.execute(stmt)
    return bool(result.rowcount > 0)

def revoke_all_user_sessions(
    user_id: UUID | str,
    reason: str | None = None,
    db_session: Session | None = None,
) -> int:
    """Helper nội bộ thu hồi toàn bộ phiên của người dùng khi đổi mật khẩu, reset, khóa tài khoản.
    - Thao tác caller-managed: KHÔNG tự commit, KHÔNG xóa row.
    - Sử dụng conditional UPDATE hàng loạt WHERE revoked_at IS NULL để tránh ghi đè đồng thời.
    - Trả về số lượng phiên vừa được thu hồi.
    """
    u_uuid = _coerce_uuid(user_id)
    if u_uuid is None or db_session is None:
        return 0

    now_utc = datetime.now(timezone.utc)
    clean_reason = reason.strip() if reason else None

    stmt = (
        update(AuthSession)
        .where(AuthSession.user_id == u_uuid, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now_utc, revoke_reason=clean_reason)
        .execution_options(synchronize_session="fetch")
    )
    result = db_session.execute(stmt)
    return int(result.rowcount)