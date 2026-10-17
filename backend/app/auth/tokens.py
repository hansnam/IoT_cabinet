"""
Access JWT Primitives Module
Chịu trách nhiệm cấp phát, định dạng và xác thực Access JWT
Tuân thủ nguyên tắc:
- Chỉ phát hành Access Token, thuật toán HS256, issuer iot-cabinet-api, audience iot-cabinet-web.
- Bắt buộc đầy đủ claims: sub (UUID string), sid (UUID string), ver (integer, từ chối bool),
  type ('access'), jti, iat, exp, iss, aud.
- Giới hạn thời gian sống (lifetime) của token nghiêm ngặt trong khoảng 1–900 giây (exp - iat <= 900).
  Từ chối cả ở helper lẫn HTTP route nếu token có lifetime vượt quá 900 giây (ví dụ 901s hoặc 24 giờ).
- Xác thực cấu trúc token gốc (unverified payload) trước khi nhận dạng để ngăn chặn việc thư viện
  Flask-JWT-Extended tự động bù trường type='access' khi token gốc thiếu type.
- Không cho phép additional_claims tùy tiện ghi đè các claims bảo mật cốt lõi.
- Trả về đối tượng AccessTokenData chứa mốc thời gian expires_at chuẩn xác từ claim exp để session DB
  dùng chung mốc hết hạn, tránh hiện tượng lệch thời gian.
- Helper KHÔNG tự ghi hay thu hồi bản ghi auth_sessions và KHÔNG commit DB.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID
from flask import current_app, has_app_context, request, has_request_context
import jwt as pyjwt
from flask_jwt_extended import create_access_token, decode_token, get_unverified_jwt_headers

from backend.app.auth.messages import AuthMessage

# Danh sách các claims bảo mật cốt lõi không được phép bị ghi đè bởi additional_claims
RESERVED_JWT_CLAIMS = frozenset({
    "sub", "sid", "ver", "type", "jti", "iat", "exp", "nbf", "iss", "aud", "fresh", "csrf"
})

REQUIRED_JWT_CLAIMS = frozenset({
    "sub", "sid", "ver", "type", "jti", "iat", "exp", "iss", "aud"
})

ALLOWED_JWT_ALGORITHMS = frozenset({"HS256"})
DEFAULT_TOKEN_TTL_SECONDS = 900

@dataclass(frozen=True)
class AccessTokenData:
    """Dữ liệu Access Token được cấp phát hoàn chỉnh kèm metadata đồng nhất với session DB."""
    access_token: str
    token_type: str
    expires_in: int
    expires_at: datetime
    jti: str
    user_id: UUID
    session_id: UUID
    token_version: int

    def to_dict(self) -> dict:
        """Chuyển đổi sang dict phục vụ trả dữ liệu response JSON khi đăng nhập."""
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "expires_at": self.expires_at.isoformat(),
            "jti": self.jti,
        }

def _coerce_uuid(val: UUID | str | None, error_message: str) -> UUID:
    """Ép kiểu và kiểm tra UUID nghiêm ngặt."""
    if val is None:
        raise ValueError(error_message)
    if isinstance(val, UUID):
        return val
    if not isinstance(val, str) or not val.strip():
        raise ValueError(error_message)
    try:
        return UUID(val.strip())
    except (ValueError, TypeError):
        raise ValueError(error_message)

def issue_access_token(
    user_id: UUID | str,
    session_id: UUID | str,
    token_version: int,
    additional_claims: dict | None = None,
    expires_delta: timedelta | int | None = None,
) -> AccessTokenData:
    """Cấp phát Access JWT thỏa mãn toàn bộ hợp đồng bảo mật.
    - user_id: ID người dùng, ghi vào claim 'sub' dưới dạng chuỗi UUID chuẩn.
    - session_id: ID phiên đăng nhập (auth_sessions.id), ghi vào claim 'sid' dưới dạng chuỗi UUID.
    - token_version: Phiên bản token của user, ghi vào claim 'ver' dưới dạng số nguyên dương.
    - additional_claims: Các claims bổ sung (từ chối ghi đè claims bảo mật).
    - expires_delta: Thời gian sống của token (timedelta hoặc số giây).
    """
    # 1. Kiểm tra và chuẩn hóa UUID của user_id và session_id
    u_uuid = _coerce_uuid(user_id, AuthMessage.TOKEN_SUB_INVALID)
    s_uuid = _coerce_uuid(session_id, AuthMessage.TOKEN_SID_INVALID)

    # 2. Kiểm tra token_version (bắt buộc int, từ chối bool vì bool là subclass của int trong Python)
    if (
            token_version is None
            or isinstance(token_version, bool)
            or not isinstance(token_version, int)
            or token_version < 1
    ):
        raise ValueError(AuthMessage.TOKEN_VER_INVALID)

    # 3. Kiểm tra chặn ghi đè các claims bảo mật cốt lõi
    extra_claims = dict(additional_claims) if additional_claims else {}
    for reserved in RESERVED_JWT_CLAIMS:
        if reserved in extra_claims:
            raise ValueError(AuthMessage.TOKEN_CLAIM_INVALID.format(
                claim=reserved, reason="reserved security claim cannot be overridden"
            ))

    # The factory already validates the config. Reject fractional/invalid overrides.
    ttl = expires_delta if expires_delta is not None else current_app.config["JWT_ACCESS_TOKEN_EXPIRES"]
    seconds = ttl.total_seconds() if isinstance(ttl, timedelta) else ttl
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not 1 <= seconds <= DEFAULT_TOKEN_TTL_SECONDS
            or seconds != int(seconds)):
        raise ValueError(AuthMessage.TOKEN_TTL_INVALID)
    ttl_seconds = int(seconds)
    if not current_app.testing and ttl_seconds != DEFAULT_TOKEN_TTL_SECONDS:
        raise ValueError(AuthMessage.TOKEN_TTL_INVALID)

    # 5. Ký token bằng Flask-JWT-Extended
    delta = timedelta(seconds=ttl_seconds)
    claims_payload = {
        "sid": str(s_uuid),
        "ver": token_version,
        **extra_claims,
    }

    raw_token = create_access_token(
        identity=str(u_uuid),
        expires_delta=delta,
        additional_claims=claims_payload,
    )

    # 6. Đọc exp và jti từ chính token vừa mã hóa để trả về mốc thời gian tuyệt đối đồng nhất
    decoded = decode_token(raw_token)
    exp_timestamp = int(decoded["exp"])
    jti_str = str(decoded["jti"])
    expires_at = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)

    return AccessTokenData(
        access_token=raw_token,
        token_type="Bearer",
        expires_in=ttl_seconds,
        expires_at=expires_at,
        jti=jti_str,
        user_id=u_uuid,
        session_id=s_uuid,
        token_version=token_version,
    )

def validate_access_token_claims(claims: dict, headers: dict | None = None) -> dict:
    """Xác thực toàn bộ cấu trúc claims và header của Access JWT theo hợp đồng.
    Từ chối claims thiếu, sai kiểu, token refresh, sai alg, hoặc issuer/audience sai.
    Đặc biệt: Bắt buộc exp > iat và giới hạn lifetime trong khoảng 1–900 giây.
    Trả về claims nếu hợp lệ, quăng ValueError nếu vi phạm.
    """
    if not isinstance(claims, dict):
        raise ValueError(AuthMessage.TOKEN_INVALID)

    # 1. Kiểm tra thuật toán nếu header được cung cấp
    if headers is not None:
        alg = headers.get("alg")
        if alg not in ALLOWED_JWT_ALGORITHMS:
            raise ValueError(AuthMessage.TOKEN_ALGORITHM_NOT_ALLOWED.format(algorithm=alg))

    # 2. Bắt buộc type=access (chặn token refresh hoặc các loại khác)
    token_type = claims.get("type")
    if token_type != "access":
        raise ValueError(AuthMessage.TOKEN_TYPE_INVALID)

    # 3. Bắt buộc sub là chuỗi UUID hợp lệ
    sub_raw = claims.get("sub")
    if not sub_raw or not isinstance(sub_raw, str):
        raise ValueError(AuthMessage.TOKEN_SUB_INVALID)
    try:
        UUID(sub_raw)
    except (ValueError, TypeError):
        raise ValueError(AuthMessage.TOKEN_SUB_INVALID)

    # 4. Bắt buộc sid là chuỗi UUID hợp lệ
    sid_raw = claims.get("sid")
    if not sid_raw or not isinstance(sid_raw, str):
        raise ValueError(AuthMessage.TOKEN_SID_INVALID)
    try:
        UUID(sid_raw)
    except (ValueError, TypeError):
        raise ValueError(AuthMessage.TOKEN_SID_INVALID)

    # 5. Bắt buộc ver là int >= 1 và không phải bool
    ver = claims.get("ver")
    if ver is None or isinstance(ver, bool) or not isinstance(ver, int) or ver < 1:
        raise ValueError(AuthMessage.TOKEN_VER_INVALID)

    # 6. Bắt buộc có jti
    jti = claims.get("jti")
    if not jti or not isinstance(jti, str):
        raise ValueError(AuthMessage.TOKEN_CLAIM_MISSING.format(claim="jti"))

    # 7. Bắt buộc iat và exp là số hữu hạn, exp > iat, và lifetime trong khoảng 1–900 giây
    for required_time in ("iat", "exp"):
        val = claims.get(required_time)
        if val is None or isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ValueError(AuthMessage.TOKEN_CLAIM_MISSING.format(claim=required_time))
        if isinstance(val, float) and not math.isfinite(val):
            raise ValueError(AuthMessage.TOKEN_CLAIM_INVALID.format(
                claim=required_time, reason="timestamp must be finite"
            ))

    iat = claims["iat"]
    exp = claims["exp"]
    if exp <= iat:
        raise ValueError(AuthMessage.TOKEN_CLAIM_INVALID.format(
            claim="exp", reason="expiration must be after issue time"
        ))

    lifetime = exp - iat
    if not (1 <= lifetime <= 900):
        raise ValueError(AuthMessage.TOKEN_CLAIM_INVALID.format(
            claim="exp", reason="token lifetime must be between 1 and 900 seconds"
        ))

    # 8. Kiểm tra issuer và audience khớp với cấu hình ứng dụng
    expected_iss = "iot-cabinet-api"
    expected_aud = "iot-cabinet-web"
    if has_app_context():
        expected_iss = current_app.config.get("JWT_DECODE_ISSUER", expected_iss)
        expected_aud = current_app.config.get("JWT_DECODE_AUDIENCE", expected_aud)

    if claims.get("iss") != expected_iss:
        raise ValueError(AuthMessage.TOKEN_ISSUER_INVALID)

    if claims.get("aud") != expected_aud:
        raise ValueError(AuthMessage.TOKEN_AUDIENCE_INVALID)

    return claims

def _require_original_claims(encoded_token: str) -> None:
    """Reject missing original fields only; this does NOT verify identity/signature."""
    try:
        claims = pyjwt.decode(encoded_token, options={"verify_signature": False})
    except pyjwt.InvalidTokenError:
        raise ValueError(AuthMessage.TOKEN_INVALID) from None
    if claims.get("type") != "access":
        raise ValueError(AuthMessage.TOKEN_TYPE_INVALID)
    for claim in REQUIRED_JWT_CLAIMS:
        if claim not in claims:
            raise ValueError(AuthMessage.TOKEN_CLAIM_MISSING.format(claim=claim))

def is_valid_access_token_claims(jwt_header: dict, jwt_payload: dict) -> bool:
    """Called after library signature/time checks; also check original HTTP claims."""
    try:
        if has_request_context():
            parts = request.headers.get("Authorization", "").split()
            if len(parts) != 2 or parts[0] != "Bearer":
                return False
            _require_original_claims(parts[1])
        validate_access_token_claims(jwt_payload, jwt_header)
        return True
    except (ValueError, TypeError):
        return False

def decode_and_verify_access_token(encoded_token: str) -> dict:
    """Validate original fields, then signature/time/issuer/audience and claims."""
    if not isinstance(encoded_token, str) or not encoded_token.strip():
        raise ValueError(AuthMessage.TOKEN_REQUIRED)
    _require_original_claims(encoded_token)
    payload = decode_token(encoded_token)
    validate_access_token_claims(payload, get_unverified_jwt_headers(encoded_token))
    return payload