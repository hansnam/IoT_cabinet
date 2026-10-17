"""
Crypto Primitives Test Suite (Thành viên 3 - TV3-02B Revision 2)
Kiểm thử toàn diện:
1. Password primitives: độ dài 12-128 ký tự, không trim, băm Argon2id explicit, salt khác nhau,
   verify đúng/sai/malformed hash, kiểm tra needs_rehash và verify_and_rehash,
   đảm bảo verify không nuốt ngoại lệ hệ thống ngoài dự kiến.
2. Access JWT primitives: cấu trúc claims bắt buộc (sub, sid, ver, jti, iat, exp, iss, aud, type='access'),
   chặn ver boolean, chặn UUID sai, chặn ghi đè claims bảo mật, chặn refresh token,
   chặn token thiếu riêng từng claim (type, sub, sid, ver, jti, iat, exp, iss, aud),
   chặn token có lifetime > 900 giây (901s hoặc 24 giờ), chặn exp <= iat,
   kiểm tra token hết hạn có kiểm soát (không sleep 15 phút).
3. HTTP callbacks integration: route fixture tạm thời gắn @jwt_required(), kiểm tra token thiếu/sai/hết hạn,
   token lifetime 24 giờ, token thiếu type, token sai alg (HS384) hoặc sai issuer/audience
   đều trả về đúng HTTP 401 với envelope chuẩn, header Cache-Control: no-store, và không có header Set-Cookie.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import UUID, uuid4
import pytest
from flask import jsonify
import jwt as pyjwt
from flask_jwt_extended import create_access_token, get_jwt, jwt_required

from backend.app.auth.messages import AuthMessage
from backend.app.auth.passwords import (
    hash_password,
    needs_rehash,
    validate_password_complexity,
    verify_and_rehash,
    verify_password,
)
from backend.app.auth.tokens import (
    AccessTokenData,
    decode_and_verify_access_token,
    is_valid_access_token_claims,
    issue_access_token,
    validate_access_token_claims,
)
from backend.app.extensions import get_password_hasher
from backend.app.security.messages import ErrorCode, ErrorMessage


# ==============================================================================
# PHẦN 1: KIỂM THỬ MẬT KHẨU (ARGON2ID PRIMITIVES - KHÔNG CẦN DB)
# ==============================================================================

def test_password_hash_success():
    """Băm mật khẩu hợp lệ trả về chuỗi hash Argon2id explicit."""
    raw_pass = "SecureP@ssw0rd123"
    h = hash_password(raw_pass)

    assert isinstance(h, str)
    assert h.startswith("$argon2id$v=19$m=")
    assert verify_password(raw_pass, h) is True


def test_password_different_salt_each_time():
    """Hai lần băm cùng một mật khẩu phải sinh ra hai chuỗi hash khác nhau do salt ngẫu nhiên."""
    raw_pass = "UniqueSaltPass!99"
    h1 = hash_password(raw_pass)
    h2 = hash_password(raw_pass)

    assert h1 != h2
    assert verify_password(raw_pass, h1) is True
    assert verify_password(raw_pass, h2) is True


def test_password_verify_wrong_password_returns_false():
    """Mật khẩu sai trả về False an toàn, không ném ngoại lệ."""
    raw_pass = "CorrectP@ssword12"
    h = hash_password(raw_pass)

    assert verify_password("WrongP@ssword99", h) is False
    assert verify_password("", h) is False


def test_password_verify_malformed_hash_returns_false_safely():
    """Chuỗi hash bị rỗng, hỏng hoặc không đúng định dạng trả về False, không gây lỗi unhandled."""
    raw_pass = "AnyPassword123"
    assert verify_password(raw_pass, "") is False
    assert verify_password(raw_pass, "not_a_valid_argon2_hash") is False
    assert verify_password(raw_pass, "$argon2id$v=19$corrupted_payload") is False
    assert verify_password(raw_pass, None) is False  # type: ignore[arg-type]


def test_password_verify_does_not_swallow_unexpected_system_exceptions():
    """Verify password không bắt Exception chung; các lỗi hệ thống ngoài dự kiến phải được giữ nguyên."""
    mock_hasher = MagicMock()
    mock_hasher.verify.side_effect = RuntimeError("System memory or CFFI driver failure")

    with pytest.raises(RuntimeError) as exc:
        verify_password("AnyPassword123", "$argon2id$v=19$m=dummy", hasher=mock_hasher)
    assert "System memory or CFFI driver failure" in str(exc.value)


def test_password_length_validation_bounds():
    """Độ dài mật khẩu bắt buộc 12-128 ký tự."""
    # Quá ngắn (< 12 ký tự)
    with pytest.raises(ValueError) as exc1:
        validate_password_complexity("Short123456")  # 11 ký tự
    assert str(exc1.value) == AuthMessage.PASSWORD_LENGTH_INVALID

    with pytest.raises(ValueError) as exc_empty:
        validate_password_complexity("")
    assert str(exc_empty.value) == AuthMessage.PASSWORD_LENGTH_INVALID

    # Vừa đủ 12 ký tự -> Thành công
    validate_password_complexity("123456789012")

    # Vừa đủ 128 ký tự -> Thành công
    valid_128 = "A" * 128
    validate_password_complexity(valid_128)

    # Quá dài (129 ký tự) -> Lỗi
    with pytest.raises(ValueError) as exc2:
        validate_password_complexity("A" * 129)
    assert str(exc2.value) == AuthMessage.PASSWORD_LENGTH_INVALID

    # Sai kiểu dữ liệu
    with pytest.raises(ValueError) as exc3:
        validate_password_complexity(None)  # type: ignore[arg-type]
    assert str(exc3.value) == AuthMessage.PASSWORD_INVALID_TYPE


def test_password_no_trimming_policy():
    """Mật khẩu có khoảng trắng đầu/cuối KHÔNG bị trim."""
    spaced_pass = "  spaces_secret_pass  "  # 22 ký tự
    h = hash_password(spaced_pass)

    assert verify_password(spaced_pass, h) is True
    assert verify_password("spaces_secret_pass", h) is False


def test_password_needs_rehash_and_upgrade():
    """Kiểm tra và thực hiện băm lại khi cấu hình tham số hasher thay đổi."""
    raw_pass = "RehashPassword123"
    old_hasher = get_password_hasher(memory_cost=8192, time_cost=1, parallelism=1)
    old_hash = hash_password(raw_pass, hasher=old_hasher)

    new_hasher = get_password_hasher(memory_cost=19456, time_cost=2, parallelism=1)

    assert needs_rehash(old_hash, hasher=new_hasher) is True

    is_valid, new_hash = verify_and_rehash(raw_pass, old_hash, hasher=new_hasher)
    assert is_valid is True
    assert new_hash is not None
    assert new_hash != old_hash
    assert new_hasher.check_needs_rehash(new_hash) is False

    is_valid_curr, no_new_hash = verify_and_rehash(raw_pass, new_hash, hasher=new_hasher)
    assert is_valid_curr is True
    assert no_new_hash is None


# ==============================================================================
# PHẦN 2: KIỂM THỬ ACCESS JWT PRIMITIVES (APP CONTEXT)
# ==============================================================================

def test_jwt_issue_access_token_success(app):
    """Cấp phát Access Token hợp lệ trả về đầy đủ metadata và thời hạn đồng nhất."""
    with app.app_context():
        u_id = uuid4()
        s_id = uuid4()
        ver = 1

        result = issue_access_token(user_id=u_id, session_id=s_id, token_version=ver)

        assert isinstance(result, AccessTokenData)
        assert isinstance(result.access_token, str)
        assert result.token_type == "Bearer"
        assert result.expires_in == int(app.config["JWT_ACCESS_TOKEN_EXPIRES"].total_seconds())
        assert isinstance(result.expires_at, datetime)
        assert result.expires_at.tzinfo == timezone.utc
        assert isinstance(result.jti, str)
        assert result.user_id == u_id
        assert result.session_id == s_id
        assert result.token_version == ver

        d = result.to_dict()
        assert d["access_token"] == result.access_token
        assert d["token_type"] == "Bearer"
        assert d["expires_in"] == result.expires_in
        assert "expires_at" in d


def test_jwt_claims_structure_and_signature_verification(app):
    """Giải mã và xác minh chữ ký toàn diện Access JWT."""
    with app.app_context():
        u_id = uuid4()
        s_id = uuid4()
        ver = 3

        result = issue_access_token(user_id=u_id, session_id=s_id, token_version=ver)
        payload = decode_and_verify_access_token(result.access_token)

        assert payload["sub"] == str(u_id)
        assert payload["sid"] == str(s_id)
        assert payload["ver"] == 3
        assert payload["type"] == "access"
        assert payload["iss"] == "iot-cabinet-api"
        assert payload["aud"] == "iot-cabinet-web"
        assert payload["jti"] == result.jti
        assert payload["exp"] == int(result.expires_at.timestamp())


def test_jwt_rejects_boolean_token_version(app):
    """Từ chối token_version dạng boolean (True/False không được coi là số nguyên hợp lệ)."""
    with app.app_context():
        u_id = uuid4()
        s_id = uuid4()

        with pytest.raises(ValueError) as exc1:
            issue_access_token(user_id=u_id, session_id=s_id, token_version=True)  # type: ignore[arg-type]
        assert str(exc1.value) == AuthMessage.TOKEN_VER_INVALID

        with pytest.raises(ValueError) as exc2:
            issue_access_token(user_id=u_id, session_id=s_id, token_version=False)  # type: ignore[arg-type]
        assert str(exc2.value) == AuthMessage.TOKEN_VER_INVALID

        with pytest.raises(ValueError) as exc3:
            issue_access_token(user_id=u_id, session_id=s_id, token_version=0)
        assert str(exc3.value) == AuthMessage.TOKEN_VER_INVALID


def test_jwt_rejects_invalid_uuid_inputs(app):
    """Từ chối user_id hoặc session_id không phải là UUID hợp lệ."""
    with app.app_context():
        valid_uuid = uuid4()

        with pytest.raises(ValueError) as exc1:
            issue_access_token(user_id="invalid-uuid-string", session_id=valid_uuid, token_version=1)
        assert str(exc1.value) == AuthMessage.TOKEN_SUB_INVALID

        with pytest.raises(ValueError) as exc2:
            issue_access_token(user_id=valid_uuid, session_id="not-a-uuid", token_version=1)
        assert str(exc2.value) == AuthMessage.TOKEN_SID_INVALID


def test_jwt_reserved_claims_cannot_be_overridden(app):
    """Chặn hành vi dùng additional_claims để ghi đè các claims bảo mật cốt lõi."""
    with app.app_context():
        u_id = uuid4()
        s_id = uuid4()

        for reserved_key in ("sub", "sid", "ver", "type", "iss", "aud", "exp", "jti"):
            with pytest.raises(ValueError) as exc:
                issue_access_token(
                    user_id=u_id,
                    session_id=s_id,
                    token_version=1,
                    additional_claims={reserved_key: "malicious_override"},
                )
            assert "reserved security claim cannot be overridden" in str(exc.value)


def test_jwt_rejects_refresh_token_type(app):
    """Từ chối token có type='refresh'."""
    with app.app_context():
        raw_refresh = create_access_token(
            identity=str(uuid4()),
            additional_claims={"sid": str(uuid4()), "ver": 1, "type": "refresh"},
        )
        with pytest.raises(ValueError) as exc1:
            validate_access_token_claims({"type": "refresh", "sub": str(uuid4()), "sid": str(uuid4()), "ver": 1})
        assert str(exc1.value) == AuthMessage.TOKEN_TYPE_INVALID

        with pytest.raises(ValueError) as exc2:
            decode_and_verify_access_token(raw_refresh)
        assert str(exc2.value) == AuthMessage.TOKEN_TYPE_INVALID


def test_jwt_rejects_token_missing_type_claim(app):
    """Token ký đúng key nhưng thiếu hẳn claim 'type' phải bị từ chối, không nhận default của thư viện."""
    with app.app_context():
        # Dựng token ký bằng secret đúng nhưng không có claim 'type'
        now_ts = int(datetime.now(timezone.utc).timestamp())
        payload_no_type = {
            "sub": str(uuid4()),
            "sid": str(uuid4()),
            "ver": 1,
            "jti": str(uuid4()),
            "iat": now_ts,
            "exp": now_ts + 300,
            "iss": "iot-cabinet-api",
            "aud": "iot-cabinet-web",
        }
        token_no_type = pyjwt.encode(payload_no_type, app.config["JWT_SECRET_KEY"], algorithm="HS256")

        with pytest.raises(ValueError) as exc:
            decode_and_verify_access_token(token_no_type)
        assert str(exc.value) == AuthMessage.TOKEN_TYPE_INVALID


@pytest.mark.parametrize("missing_claim", [
    "sub", "sid", "ver", "jti", "iat", "exp", "iss", "aud"
])
def test_jwt_rejects_token_missing_each_required_claim_individually(app, missing_claim):
    """Token thiếu riêng từng claim bắt buộc đều bị từ chối xác thực."""
    with app.app_context():
        now_ts = int(datetime.now(timezone.utc).timestamp())
        full_claims = {
            "type": "access",
            "sub": str(uuid4()),
            "sid": str(uuid4()),
            "ver": 1,
            "jti": str(uuid4()),
            "iat": now_ts,
            "exp": now_ts + 300,
            "iss": "iot-cabinet-api",
            "aud": "iot-cabinet-web",
        }
        del full_claims[missing_claim]
        bad_token = pyjwt.encode(full_claims, app.config["JWT_SECRET_KEY"], algorithm="HS256")

        with pytest.raises(ValueError):
            decode_and_verify_access_token(bad_token)


def test_jwt_rejects_lifetime_exceeding_900_seconds(app):
    """Token ký đúng key nhưng có lifetime > 900 giây (ví dụ 901s hoặc 24 giờ) phải bị từ chối."""
    with app.app_context():
        now_ts = int(datetime.now(timezone.utc).timestamp())

        # Ca 1: Lifetime 901 giây (vượt ngưỡng 1 giây)
        claims_901 = {
            "type": "access",
            "sub": str(uuid4()),
            "sid": str(uuid4()),
            "ver": 1,
            "jti": str(uuid4()),
            "iat": now_ts,
            "exp": now_ts + 901,
            "iss": "iot-cabinet-api",
            "aud": "iot-cabinet-web",
        }
        token_901 = pyjwt.encode(claims_901, app.config["JWT_SECRET_KEY"], algorithm="HS256")
        with pytest.raises(ValueError) as exc1:
            decode_and_verify_access_token(token_901)
        assert "lifetime must be between 1 and 900 seconds" in str(exc1.value)

        # Ca 2: Lifetime 24 giờ (86400 giây)
        claims_24h = dict(claims_901, exp=now_ts + 86400)
        token_24h = pyjwt.encode(claims_24h, app.config["JWT_SECRET_KEY"], algorithm="HS256")
        with pytest.raises(ValueError) as exc2:
            decode_and_verify_access_token(token_24h)
        assert "lifetime must be between 1 and 900 seconds" in str(exc2.value)


def test_jwt_rejects_exp_less_than_or_equal_to_iat(app):
    """Token có exp <= iat phải bị từ chối xác thực."""
    with app.app_context():
        now_ts = int(datetime.now(timezone.utc).timestamp())
        claims_equal = {
            "type": "access",
            "sub": str(uuid4()),
            "sid": str(uuid4()),
            "ver": 1,
            "jti": str(uuid4()),
            "iat": now_ts,
            "exp": now_ts,  # exp == iat
            "iss": "iot-cabinet-api",
            "aud": "iot-cabinet-web",
        }
        token_equal = pyjwt.encode(claims_equal, app.config["JWT_SECRET_KEY"], algorithm="HS256")
        with pytest.raises(ValueError, match="expiration must be after issue time"):
            validate_access_token_claims(claims_equal)
        with pytest.raises(pyjwt.ExpiredSignatureError):
            decode_and_verify_access_token(token_equal)


def test_jwt_rejects_non_finite_or_boolean_timestamps(app):
    """Token chứa iat hoặc exp là boolean hoặc không hữu hạn phải bị từ chối."""
    with app.app_context():
        claims_bool = {
            "type": "access", "sub": str(uuid4()), "sid": str(uuid4()), "ver": 1,
            "jti": str(uuid4()), "iat": True, "exp": 1000, "iss": "iot-cabinet-api", "aud": "iot-cabinet-web"
        }
        with pytest.raises(ValueError):
            validate_access_token_claims(claims_bool)

        claims_inf = {
            "type": "access", "sub": str(uuid4()), "sid": str(uuid4()), "ver": 1,
            "jti": str(uuid4()), "iat": 1000, "exp": float("inf"), "iss": "iot-cabinet-api", "aud": "iot-cabinet-web"
        }
        with pytest.raises(ValueError) as exc_inf:
            validate_access_token_claims(claims_inf)
        assert "must be finite" in str(exc_inf.value)


def test_jwt_rejects_wrong_issuer_or_audience(app):
    """Từ chối claims có issuer hoặc audience không đúng cấu hình."""
    valid_claims = {
        "type": "access",
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "ver": 1,
        "jti": str(uuid4()),
        "iat": 100000,
        "exp": 100500,
        "iss": "iot-cabinet-api",
        "aud": "iot-cabinet-web",
    }

    bad_iss = dict(valid_claims, iss="untrusted-issuer")
    with pytest.raises(ValueError) as exc1:
        validate_access_token_claims(bad_iss)
    assert str(exc1.value) == AuthMessage.TOKEN_ISSUER_INVALID

    bad_aud = dict(valid_claims, aud="untrusted-client")
    with pytest.raises(ValueError) as exc2:
        validate_access_token_claims(bad_aud)
    assert str(exc2.value) == AuthMessage.TOKEN_AUDIENCE_INVALID


def test_jwt_controlled_expiration_no_sleep(app):
    """Kiểm tra token hết hạn một cách có kiểm soát mà không cần sleep 15 phút."""
    with app.app_context():
        u_id = uuid4()
        s_id = uuid4()

        past_delta = timedelta(seconds=-10)
        expired_token = create_access_token(
            identity=str(u_id),
            expires_delta=past_delta,
            additional_claims={"sid": str(s_id), "ver": 1},
        )

        with pytest.raises(Exception) as exc:
            decode_and_verify_access_token(expired_token)
        assert "expired" in str(exc.value).lower() or "signature has expired" in str(exc.value).lower()


# ==============================================================================
# PHẦN 3: KIỂM THỬ TÍCH HỢP HTTP CALLBACKS VÀ PROTECTED ROUTE FIXTURE
# ==============================================================================

@pytest.fixture
def protected_client(app):
    """Đăng ký route test tạm thời sử dụng @jwt_required() trên app fixture."""
    if "_test_protected_resource" not in app.view_functions:
        @app.route("/_test/protected", methods=["GET"])
        @jwt_required()
        def _test_protected_resource():
            claims = get_jwt()
            return jsonify({
                "status": "ok",
                "sub": claims.get("sub"),
                "sid": claims.get("sid"),
                "ver": claims.get("ver"),
            }), 200

    return app.test_client()


def test_http_callback_missing_token_returns_401_envelope(protected_client):
    """Request thiếu Authorization token trả về đúng HTTP 401 với envelope chuẩn."""
    response = protected_client.get("/_test/protected")

    assert response.status_code == 401
    data = response.get_json()
    assert "error" in data
    assert data["error"]["code"] == ErrorCode.UNAUTHORIZED
    assert data["error"]["message"] == ErrorMessage.UNAUTHORIZED
    assert data["error"]["request_id"] is not None
    assert response.headers.get("Cache-Control") == "no-store"
    assert "Set-Cookie" not in response.headers


def test_http_callback_invalid_token_returns_401_envelope(protected_client):
    """Request chứa token rác trả về 401 envelope chuẩn, không echo token string."""
    headers = {"Authorization": "Bearer malformed.token.string"}
    response = protected_client.get("/_test/protected", headers=headers)

    assert response.status_code == 401
    data = response.get_json()
    assert data["error"]["code"] == ErrorCode.UNAUTHORIZED
    assert data["error"]["message"] == ErrorMessage.UNAUTHORIZED
    assert "malformed" not in response.text
    assert "Set-Cookie" not in response.headers


def test_http_callback_tampered_signature_returns_401(app, protected_client):
    """Token bị chỉnh sửa chữ ký trả về 401."""
    with app.app_context():
        token_res = issue_access_token(uuid4(), uuid4(), 1)
        tampered_token = token_res.access_token[:-4] + "WXYZ"

    response = protected_client.get(
        "/_test/protected",
        headers={"Authorization": f"Bearer {tampered_token}"}
    )
    assert response.status_code == 401
    data = response.get_json()
    assert data["error"]["code"] == ErrorCode.UNAUTHORIZED


def test_http_callback_expired_token_returns_401(app, protected_client):
    """Token hết hạn trả về 401 envelope chuẩn."""
    with app.app_context():
        expired_token = create_access_token(
            identity=str(uuid4()),
            expires_delta=timedelta(seconds=-5),
            additional_claims={"sid": str(uuid4()), "ver": 1},
        )

    response = protected_client.get(
        "/_test/protected",
        headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert response.status_code == 401
    data = response.get_json()
    assert data["error"]["code"] == ErrorCode.UNAUTHORIZED
    assert "Set-Cookie" not in response.headers


def test_http_callback_24h_lifetime_token_rejected_returns_401(app, protected_client):
    """Token ký đúng key nhưng có lifetime 24 giờ bị từ chối 401 qua HTTP endpoint."""
    with app.app_context():
        now_ts = int(datetime.now(timezone.utc).timestamp())
        claims_24h = {
            "type": "access",
            "sub": str(uuid4()),
            "sid": str(uuid4()),
            "ver": 1,
            "jti": str(uuid4()),
            "iat": now_ts,
            "exp": now_ts + 86400,  # 24 giờ
            "iss": "iot-cabinet-api",
            "aud": "iot-cabinet-web",
        }
        token_24h = pyjwt.encode(claims_24h, app.config["JWT_SECRET_KEY"], algorithm="HS256")

    response = protected_client.get(
        "/_test/protected",
        headers={"Authorization": f"Bearer {token_24h}"}
    )
    assert response.status_code == 401
    data = response.get_json()
    assert data["error"]["code"] == ErrorCode.UNAUTHORIZED


@pytest.mark.parametrize("missing_claim", [
    "type", "sub", "sid", "ver", "jti", "iat", "exp", "iss", "aud"
])
def test_http_missing_original_claim_returns_401(app, protected_client, missing_claim):
    with app.app_context():
        now = int(datetime.now(timezone.utc).timestamp())
        claims = {
            "type": "access", "sub": str(uuid4()), "sid": str(uuid4()), "ver": 1,
            "jti": str(uuid4()), "iat": now, "exp": now + 300,
            "iss": "iot-cabinet-api", "aud": "iot-cabinet-web",
        }
        del claims[missing_claim]
        token = pyjwt.encode(claims, app.config["JWT_SECRET_KEY"], algorithm="HS256")
    response = protected_client.get("/_test/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json["error"]["code"] == ErrorCode.UNAUTHORIZED


@pytest.mark.parametrize("ttl", [timedelta(seconds=1.9), 0, 901, True, 1.5])
def test_issue_rejects_invalid_or_fractional_ttl(app, ttl):
    with app.app_context(), pytest.raises(ValueError, match="Token TTL"):
        issue_access_token(uuid4(), uuid4(), 1, expires_delta=ttl)


def test_http_callback_wrong_algorithm_rejected_returns_401(app, protected_client):
    """Token ký bằng thuật toán khác HS256 (ví dụ HS384) bị từ chối 401 qua HTTP endpoint."""
    with app.app_context():
        now_ts = int(datetime.now(timezone.utc).timestamp())
        claims = {
            "type": "access", "sub": str(uuid4()), "sid": str(uuid4()), "ver": 1,
            "jti": str(uuid4()), "iat": now_ts, "exp": now_ts + 300,
            "iss": "iot-cabinet-api", "aud": "iot-cabinet-web"
        }
        token_hs384 = pyjwt.encode(claims, app.config["JWT_SECRET_KEY"], algorithm="HS384")

    response = protected_client.get(
        "/_test/protected",
        headers={"Authorization": f"Bearer {token_hs384}"}
    )
    assert response.status_code == 401
    data = response.get_json()
    assert data["error"]["code"] == ErrorCode.UNAUTHORIZED


@pytest.mark.parametrize("field,value", [("iss", "untrusted-issuer"), ("aud", "untrusted-client")])
def test_http_callback_wrong_issuer_or_audience_rejected_returns_401(app, protected_client, field, value):
    """Token ký đúng key nhưng sai issuer hoặc audience bị từ chối 401 qua HTTP endpoint."""
    with app.app_context():
        now_ts = int(datetime.now(timezone.utc).timestamp())
        claims_bad_iss = {
            "type": "access", "sub": str(uuid4()), "sid": str(uuid4()), "ver": 1,
            "jti": str(uuid4()), "iat": now_ts, "exp": now_ts + 300,
            "iss": "iot-cabinet-api", "aud": "iot-cabinet-web"
        }
        claims_bad_iss[field] = value
        token_bad_iss = pyjwt.encode(claims_bad_iss, app.config["JWT_SECRET_KEY"], algorithm="HS256")

    response = protected_client.get(
        "/_test/protected",
        headers={"Authorization": f"Bearer {token_bad_iss}"}
    )
    assert response.status_code == 401
    data = response.get_json()
    assert data["error"]["code"] == ErrorCode.UNAUTHORIZED


def test_http_valid_access_token_accesses_protected_route_success(app, protected_client):
    """Token chuẩn hợp lệ truy cập thành công HTTP 200, header no-store, không có Set-Cookie."""
    u_id = uuid4()
    s_id = uuid4()
    ver = 2

    with app.app_context():
        token_res = issue_access_token(user_id=u_id, session_id=s_id, token_version=ver)

    response = protected_client.get(
        "/_test/protected",
        headers={"Authorization": f"Bearer {token_res.access_token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["sub"] == str(u_id)
    assert data["sid"] == str(s_id)
    assert data["ver"] == 2
    assert response.headers.get("Cache-Control") == "no-store"
    assert "Set-Cookie" not in response.headers
