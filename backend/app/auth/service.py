"""
Auth Service Orchestration Engine
Chịu trách nhiệm:
1. Điều phối toàn bộ quy trình đăng nhập (login):
   - Tính toán khóa HMAC và khóa transaction advisory locks.
   - Kiểm tra rate limit (ngưỡng 5 user / 20 IP) và trả mã 429 nếu vượt quá.
   - Khóa bản ghi User bằng SELECT ... FOR UPDATE kèm populate_existing=True để chống stale cache ORM.
   - Xác minh Argon2id (kèm dummy verify cho unknown user để giảm thiểu rủi ro timing attack).
   - Kiểm tra trạng thái ACTIVE và vai trò được gán trong user_roles.
   - Nâng cấp băm mật khẩu (rehash) nếu cấu hình bảo mật thay đổi.
   - Cấp phát Access JWT và tạo bản ghi AuthSession có thời hạn khớp exp của token.
   - Ghi nhận lịch sử kiểm toán audit_logs và nhật ký làm việc user_activity_logs.
2. Quản lý vòng đời transaction và khóa:
   - Duy trì transaction advisory lock xuyên suốt từ lúc check rate-limit tới lúc commit trên mọi nhánh.
   - Gom gọn logic ghi log thất bại và commit vào _record_failure_and_commit.
   - Không nuốt lỗi của writer hoặc commit: nếu ghi log hoặc DB lỗi, chuyển thành exception để route trả 500/503.
3. Điều phối quy trình đăng xuất (logout):
   - Thu hồi phiên hiện tại bằng revoke_session (nguyên tử, không xóa dòng).
   - Ghi audit_logs và user_activity_logs trong cùng transaction.
4. Điều phối quy trình đọc hồ sơ cá nhân (me):
   - Đọc thông tin người dùng, vai trò và danh sách quyền hạn P0 qua PermissionService.
   - Không ghi nhận nhật ký làm việc đối với thao tác đọc (read tracking exclusion).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.auth.messages import AuthFailureCode
from backend.app.auth.passwords import hash_password, verify_and_rehash, verify_password
from backend.app.auth.rate_limit import (
    acquire_rate_limit_advisory_locks,
    check_rate_limit,
    compute_rate_limit_keys,
    record_login_attempt,
)
from backend.app.auth.sessions import revoke_session
from backend.app.auth.tokens import AccessTokenData, issue_access_token
from backend.app.models.iam import AuthSession, User
from backend.app.permissions.service import PermissionService
from backend.app.security.context import AuthContext
from backend.app.security.messages import ErrorMessage
from backend.app.user_activity.service import UserActivityService

# Khởi tạo hash giả định một lần duy nhất khi nạp module
DUMMY_PASSWORD_HASH = hash_password("DummySecurePass123!@#")


class RateLimitExceeded(Exception):
    """Ngoại lệ khi tài khoản hoặc IP vượt quá ngưỡng thử đăng nhập (HTTP 429)."""

    def __init__(self, retry_after: int):
        super().__init__(ErrorMessage.TOO_MANY_REQUESTS)
        self.retry_after = retry_after


class AuthenticationFailed(Exception):
    """Ngoại lệ chung cho toàn bộ các trường hợp sai thông tin đăng nhập (HTTP 401)."""
    pass


@dataclass(frozen=True)
class LoginResult:
    """Kết quả đăng nhập thành công chứa token và thông tin người dùng được phép trả về."""
    token_data: AccessTokenData
    user_data: dict


class AuthService:
    """Dịch vụ trung tâm xử lý xác thực và quản lý phiên đăng nhập."""

    @staticmethod
    def _record_failure_and_commit(
            db_session: Session,
            *,
            username_key: str,
            ip_key: str,
            user_id: UUID | None,
            canonical_username: str,
            client_ip: str,
            request_id: UUID,
            failure_code: AuthFailureCode | str,
            now_utc: datetime,
    ) -> None:
        """Ghi nhận attempt thất bại và nhật ký kiểm toán trong cùng transaction rồi commit.

        Không nuốt ngoại lệ: Nếu writer hoặc commit gặp sự cố, lỗi sẽ lan truyền ra ngoài
        để route trả HTTP 500/503 tương ứng, ngăn chặn việc báo sai credential khi log chưa lưu.
        """
        code_str = str(failure_code)
        record_login_attempt(
            db_session,
            username_key=username_key,
            ip_key=ip_key,
            user_id=user_id,
            success=False,
            failure_code=code_str,
            request_id=request_id,
            occurred_at=now_utc,
        )
        AuditService.record(
            action="USER_LOGIN",
            target_type="USER",
            target_id=str(user_id) if user_id else "UNKNOWN",
            success=False,
            reason_code=code_str,
            request_id=request_id,
            ip_address=client_ip,
            actor_type="USER",
            actor_user_id=user_id,
            actor_name_snapshot=canonical_username,
            detail={"attempted_username": canonical_username, "reason": code_str},
            db_session=db_session,
        )
        UserActivityService.record(
            function_key="auth.login",
            action="LOGIN",
            outcome="DENIED",
            target_type="USER",
            target_id=str(user_id) if user_id else "UNKNOWN",
            target_name_snapshot=canonical_username,
            actor_type="USER",
            actor_user_id=user_id,
            actor_name_snapshot=canonical_username,
            request_id=request_id,
            summary_params={"username": canonical_username},
            error_code=code_str,
            metadata={"username": canonical_username, "ip_address": client_ip, "reason": code_str},
            db_session=db_session,
        )
        db_session.commit()

    @staticmethod
    def login(
            *,
            username: str,
            password: str,
            client_ip: str,
            user_agent: str,
            request_id: UUID | str,
            db_session: Session,
            current_time: datetime | None = None,
    ) -> LoginResult:
        """Thực hiện quy trình xác thực người dùng và cấp phát phiên đăng nhập."""
        now_utc = current_time or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        req_uuid = UUID(str(request_id)) if not isinstance(request_id, UUID) else request_id
        canonical_username = username.strip().lower()

        # 1. Tính toán khóa HMAC định danh phân tách domain
        username_key, ip_key = compute_rate_limit_keys(canonical_username, client_ip)

        # 2. Khóa transaction advisory locks theo thứ tự cố định (giữ xuyên suốt đến commit)
        acquire_rate_limit_advisory_locks(db_session, username_key, ip_key)

        # 3. Kiểm tra rate limit trong cửa sổ 15 phút
        is_limited, retry_after = check_rate_limit(db_session, username_key, ip_key, now=now_utc)
        if is_limited:
            AuthService._record_failure_and_commit(
                db_session,
                username_key=username_key,
                ip_key=ip_key,
                user_id=None,
                canonical_username=canonical_username,
                client_ip=client_ip,
                request_id=req_uuid,
                failure_code=AuthFailureCode.RATE_LIMITED,
                now_utc=now_utc,
            )
            raise RateLimitExceeded(retry_after)

        # 4. Tra cứu và khóa dòng User từ DB kèm populate_existing=True chống stale cache ORM
        user = db_session.execute(
            select(User)
            .where(User.username == canonical_username)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

        # 5. Trường hợp người dùng không tồn tại -> Dummy Verify
        if user is None:
            verify_password(password, DUMMY_PASSWORD_HASH)
            AuthService._record_failure_and_commit(
                db_session,
                username_key=username_key,
                ip_key=ip_key,
                user_id=None,
                canonical_username=canonical_username,
                client_ip=client_ip,
                request_id=req_uuid,
                failure_code=AuthFailureCode.USER_NOT_FOUND,
                now_utc=now_utc,
            )
            raise AuthenticationFailed()

        # 6. Trường hợp tài khoản không ACTIVE
        if user.status != "ACTIVE":
            verify_password(password, user.password_hash)
            AuthService._record_failure_and_commit(
                db_session,
                username_key=username_key,
                ip_key=ip_key,
                user_id=user.id,
                canonical_username=user.username,
                client_ip=client_ip,
                request_id=req_uuid,
                failure_code=AuthFailureCode.USER_INACTIVE,
                now_utc=now_utc,
            )
            raise AuthenticationFailed()

        # 7. Xác minh mật khẩu và kiểm tra nhu cầu băm lại (rehash)
        is_valid, new_hash = verify_and_rehash(password, user.password_hash)
        if not is_valid:
            user.failed_login_count = (user.failed_login_count or 0) + 1
            AuthService._record_failure_and_commit(
                db_session,
                username_key=username_key,
                ip_key=ip_key,
                user_id=user.id,
                canonical_username=user.username,
                client_ip=client_ip,
                request_id=req_uuid,
                failure_code=AuthFailureCode.INVALID_CREDENTIALS,
                now_utc=now_utc,
            )
            raise AuthenticationFailed()

        # 8. Kiểm tra vai trò của người dùng trong DB (chặn cấp quyền giả định)
        role_code = PermissionService.get_user_role(user.id, db_session=db_session)
        if not role_code:
            AuthService._record_failure_and_commit(
                db_session,
                username_key=username_key,
                ip_key=ip_key,
                user_id=user.id,
                canonical_username=user.username,
                client_ip=client_ip,
                request_id=req_uuid,
                failure_code=AuthFailureCode.ROLE_NOT_FOUND,
                now_utc=now_utc,
            )
            raise AuthenticationFailed()

        # 9. Cập nhật trạng thái User thành công
        user.failed_login_count = 0
        user.last_login_at = now_utc
        if new_hash is not None:
            user.password_hash = new_hash

        # 10. Phát hành Access Token và tạo AuthSession trong database
        new_session_id = uuid4()
        token_data = issue_access_token(
            user_id=user.id,
            session_id=new_session_id,
            token_version=user.token_version,
        )

        session_entry = AuthSession(
            id=token_data.session_id,
            user_id=user.id,
            token_version=user.token_version,
            created_at=now_utc,
            last_seen_at=now_utc,
            expires_at=token_data.expires_at,
            reauthenticated_at=None,
            revoked_at=None,
            revoke_reason=None,
            ip_address=client_ip,
            user_agent=user_agent[:512] if user_agent else None,
        )
        db_session.add(session_entry)

        # 11. Ghi nhận lần đăng nhập thành công vào login_attempts, audit_logs và user_activity_logs
        record_login_attempt(
            db_session,
            username_key=username_key,
            ip_key=ip_key,
            user_id=user.id,
            success=True,
            failure_code=None,
            request_id=req_uuid,
            occurred_at=now_utc,
        )
        AuditService.record(
            action="USER_LOGIN",
            target_type="USER",
            target_id=str(user.id),
            success=True,
            request_id=req_uuid,
            ip_address=client_ip,
            actor_type="USER",
            actor_user_id=user.id,
            actor_session_id=session_entry.id,
            actor_name_snapshot=user.username,
            detail={"username": user.username, "session_id": str(session_entry.id), "role_code": role_code},
            db_session=db_session,
        )
        UserActivityService.record(
            function_key="auth.login",
            action="LOGIN",
            outcome="SUCCESS",
            target_type="USER",
            target_id=str(user.id),
            target_name_snapshot=user.username,
            actor_type="USER",
            actor_user_id=user.id,
            actor_session_id=session_entry.id,
            actor_name_snapshot=user.username,
            request_id=req_uuid,
            summary_params={"username": user.username},
            metadata={"username": user.username, "ip_address": client_ip},
            db_session=db_session,
        )

        # 12. Commit toàn bộ transaction thành công
        db_session.commit()

        user_data = {
            "id": str(user.id),
            "username": user.username,
            "full_name": user.full_name,
            "status": user.status,
            "must_change_password": bool(user.must_change_password),
            "role_code": role_code,
        }

        return LoginResult(token_data=token_data, user_data=user_data)

    @staticmethod
    def logout(*, actor: AuthContext, db_session: Session) -> bool:
        """Thực hiện quy trình đăng xuất và thu hồi phiên đăng nhập hiện tại."""
        revoke_session(
            session_id=actor.session_id,
            reason="User logout",
            db_session=db_session,
        )

        AuditService.record(
            action="USER_LOGOUT",
            target_type="AUTH_SESSION",
            target_id=str(actor.session_id),
            success=True,
            actor=actor,
            detail={"session_id": str(actor.session_id), "reason": "User logout"},
            db_session=db_session,
        )
        UserActivityService.record(
            function_key="auth.logout",
            action="LOGOUT",
            outcome="SUCCESS",
            actor=actor,
            metadata={"session_id": str(actor.session_id), "reason": "User logout"},
            db_session=db_session,
        )

        db_session.commit()
        return True

    @staticmethod
    def get_me(*, actor: AuthContext, db_session: Session) -> dict:
        """Lấy thông tin hồ sơ cá nhân và danh sách quyền hạn P0 của người dùng hiện tại."""
        user_row = db_session.execute(
            select(
                User.id,
                User.username,
                User.full_name,
                User.status,
                User.must_change_password,
            ).where(User.id == actor.user_id)
        ).first()

        if user_row is None or user_row.status != "ACTIVE":
            raise AuthenticationFailed()

        role_code = PermissionService.get_user_role(actor.user_id, db_session=db_session)
        permissions = PermissionService.get_user_permissions(actor.user_id, db_session=db_session)

        return {
            "id": str(user_row.id),
            "username": user_row.username,
            "full_name": user_row.full_name,
            "status": user_row.status,
            "must_change_password": bool(user_row.must_change_password),
            "role_code": role_code,
            "permissions": permissions,
        }