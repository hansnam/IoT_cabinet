import uuid
from sqlalchemy import (
    Column, String, Text, Boolean, Integer,
    ForeignKey, CheckConstraint, UniqueConstraint, Index, text, func
)
from sqlalchemy.dialects.postgresql import UUID, INET, TIMESTAMP
from backend.app.extensions import db

class User(db.Model):
    """Bảng users: Người dùng hệ thống"""
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    full_name = Column(String(128), nullable=False)
    email = Column(String(254), nullable=True)
    status = Column(String(20), nullable=False, default="ACTIVE")
    must_change_password = Column(Boolean, nullable=False, default=False)
    token_version = Column(Integer, nullable=False, default=1)
    password_changed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    failed_login_count = Column(Integer, nullable=False, default=0)
    locked_until = Column(TIMESTAMP(timezone=True), nullable=True)
    last_login_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'LOCKED', 'ARCHIVED')", name="ck_users_status"),
        CheckConstraint("token_version >= 1", name="ck_users_token_version"),
        CheckConstraint("failed_login_count >= 0", name="ck_users_failed_login_count"),
        Index("uq_users_lower_email", func.lower(email), unique=True, postgresql_where=email.isnot(None)),
    )


class Role(db.Model):
    """Bảng roles: Vai trò hệ thống"""
    __tablename__ = "roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(32), unique=True, nullable=False)
    name = Column(String(80), nullable=False)
    description = Column(Text, nullable=True)
    is_system = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


class Permission(db.Model):
    """Bảng permissions: Quyền hạn hệ thống"""
    __tablename__ = "permissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(80), unique=True, nullable=False)
    scope_type = Column(String(20), nullable=False)
    description = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("scope_type IN ('GLOBAL', 'DEVICE', 'SELF')", name="ck_permissions_scope_type"),
    )


class RolePermission(db.Model):
    """Bảng role_permissions: Gán quyền cho vai trò (Mục 6.2)"""
    __tablename__ = "role_permissions"

    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True)
    permission_id = Column(UUID(as_uuid=True), ForeignKey("permissions.id", ondelete="RESTRICT"), primary_key=True)
    granted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_role_permissions_permission_id", "permission_id"),
    )


class UserRole(db.Model):
    """Bảng user_roles: Gán vai trò cho người dùng"""
    __tablename__ = "user_roles"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True)
    assigned_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_one_role_per_user"),
        Index("ix_user_roles_role_id", "role_id"),
    )


class AuthSession(db.Model):
    """Bảng auth_sessions: Quản lý phiên đăng nhập (Mục 6.2)"""
    __tablename__ = "auth_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # Session ID (sid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    token_version = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    last_seen_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    revoked_at = Column(TIMESTAMP(timezone=True), nullable=True)
    revoke_reason = Column(Text, nullable=True)
    ip_address = Column(INET, nullable=True)
    user_agent = Column(String(512), nullable=True)
    reauthenticated_at = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="ck_auth_sessions_expires_after_created"),
        Index("ix_auth_sessions_user_created", "user_id", text("created_at DESC")),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )


class RefreshToken(db.Model):
    """Bảng refresh_tokens: Opaque Refresh Tokens (SHA-256 digest) với cơ chế rotation & revoke"""
    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("auth_sessions.id", ondelete="RESTRICT"), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    parent_token_id = Column(UUID(as_uuid=True), nullable=True)
    replaced_by_token_id = Column(UUID(as_uuid=True), nullable=True)
    issued_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    used_at = Column(TIMESTAMP(timezone=True), nullable=True)
    revoked_at = Column(TIMESTAMP(timezone=True), nullable=True)
    reuse_detected_at = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("expires_at > issued_at", name="ck_refresh_tokens_expires_after_issued"),
        Index(
            "uq_active_refresh_token", 
            "session_id", 
            unique=True, 
            postgresql_where=text("used_at IS NULL AND revoked_at IS NULL")
        ),
        Index("ix_refresh_tokens_session_issued", "session_id", "issued_at"),
    )


class LoginAttempt(db.Model):
    """Bảng login_attempts: Ghi nhận lịch sử thử đăng nhập chống brute-force"""
    __tablename__ = "login_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username_key = Column(String(64), nullable=False)  # HMAC hash
    ip_key = Column(String(64), nullable=False)        # HMAC hash
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    success = Column(Boolean, nullable=False)
    failure_code = Column(Text, nullable=True)
    occurred_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    request_id = Column(UUID(as_uuid=True), nullable=False)

    __table_args__ = (
        Index("ix_login_attempts_username_occurred", "username_key", "occurred_at"),
        Index("ix_login_attempts_ip_occurred", "ip_key", "occurred_at"),
    )
