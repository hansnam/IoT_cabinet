"""Verified identity only; permissions always read current DB state."""
from dataclasses import dataclass
from uuid import UUID

@dataclass(frozen=True)
class AuthContext:
    user_id: UUID
    session_id: UUID
    token_version: int
    request_id: str
    actor_type: str = "USER"

def get_current_auth() -> AuthContext | None:
    """Helper an toàn truy xuất AuthContext từ Flask request context hiện tại."""
    from flask import request, has_request_context
    if has_request_context():
        return getattr(request, "auth", None)
    return None