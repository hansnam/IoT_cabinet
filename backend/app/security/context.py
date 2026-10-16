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