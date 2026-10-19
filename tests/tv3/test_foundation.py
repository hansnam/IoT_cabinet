"""Unit tests need no DB; test_postgres_readiness explicitly uses guarded PG."""
from datetime import timedelta
from re import escape
from unittest.mock import MagicMock
from uuid import UUID
import pytest
from flask import abort, g, request
from backend.app import create_app
from backend.app.extensions import db
from backend.app.security import require_auth_origin
from backend.app.security.config_validation import validate_config
from backend.app.security.messages import ErrorCode, ErrorMessage


@pytest.mark.parametrize("environment", ["development", "testing", "production", "release"])
@pytest.mark.parametrize("key", ["SECRET_KEY", "JWT_SECRET_KEY", "AUTH_LOG_HMAC_KEY"])
@pytest.mark.parametrize("value", [None, "short", "REPLACE_WITH_OWN_RANDOM_VALUE" * 2,
                                  "change_this_to_a_secure_random_value_min_32_bytes"])
def test_reject_invalid_keys(foundation_app, environment, key, value):
    config = dict(foundation_app.config)
    config.update(APP_ENV=environment)
    config[key] = value
    foundation_app.config.update(config)
    with pytest.raises(ValueError, match=key):
        validate_config(foundation_app)


def test_validation_after_overrides(foundation_app):
    config = dict(foundation_app.config)
    config.update(TESTING=False, DEBUG=False, JWT_ACCESS_TOKEN_EXPIRES=900,
                  MQTT_WORKER_PASSWORD="fixture-worker-password",
                  MQTT_CONTROLLER_PASSWORD="fixture-controller-password")
    instance = create_app("release", test_config=config)
    assert instance.config["APP_ENV"] == "release"
    assert instance.config["SESSION_LIFETIME_SECONDS"] == 900
    with instance.app_context():
        db.engine.dispose()
    with pytest.raises(ValueError, match=escape(ErrorMessage.UNKNOWN_ENVIRONMENT)):
        create_app("typo-release", test_config=config)
    config["DEBUG"] = True
    with pytest.raises(ValueError, match=escape(ErrorMessage.RELEASE_FLAGS_DISABLED)):
        create_app("release", test_config=config)


@pytest.mark.parametrize("origins", [[], ["*"], ["null"], ["http://localhost:5173/path"],
                                     ["http://user:password@localhost"], ["http://localhost?x=1"],
                                     ["http://*.example.com"], ["https://*.example.com"]])
def test_origin_config_rejected(foundation_app, origins):
    foundation_app.config["AUTH_ALLOWED_ORIGINS"] = origins
    with pytest.raises(ValueError, match="AUTH_ALLOWED_ORIGINS"):
        validate_config(foundation_app)


@pytest.mark.parametrize("ttl", [0, -1, 901, False, 1.5, "invalid", timedelta(seconds=1.5)])
def test_invalid_ttl(foundation_app, ttl):
    foundation_app.config["JWT_ACCESS_TOKEN_EXPIRES"] = ttl
    with pytest.raises(ValueError):
        validate_config(foundation_app)


def test_jwt_settings_and_ttl_are_effective(foundation_app):
    from flask_jwt_extended import create_access_token, decode_token
    from jwt import encode, InvalidAudienceError, InvalidIssuerError
    with foundation_app.app_context():
        token = create_access_token(identity="caed7f3e-27ac-4303-91ad-774d5f32cba8")
        payload = decode_token(token)
        assert payload["iss"] == "iot-cabinet-api"
        assert payload["aud"] == "iot-cabinet-web"
        assert payload["exp"] - payload["iat"] == 5
        assert foundation_app.config["SESSION_LIFETIME_SECONDS"] == 5
        for field, error in [("iss",InvalidIssuerError),("aud",InvalidAudienceError)]:
            modified = dict(payload, **{field:"wrong-value"})
            forged = encode(modified, foundation_app.config["JWT_SECRET_KEY"], algorithm="HS256")
            with pytest.raises(error):
                decode_token(forged)


def test_live_request_metadata_and_headers(foundation_app):
    @foundation_app.get("/_fixture_metadata")
    def metadata():
        return {"g": g.request_id, "request": request.request_id, "ip": g.client_ip}
    client = foundation_app.test_client()
    one = client.get("/_fixture_metadata", headers={"X-Request-ID": "spoof", "X-Forwarded-For": "203.0.113.99"})
    two = client.get("/health/live")
    assert one.status_code == two.status_code == 200
    assert UUID(one.headers["X-Request-ID"]).version == 4
    assert one.headers["X-Request-ID"] != two.headers["X-Request-ID"]
    assert one.json["g"] == one.json["request"] == one.headers["X-Request-ID"]
    assert one.json["ip"] == "127.0.0.1"
    assert one.headers["Cache-Control"] == "no-store"
    assert one.headers["X-Content-Type-Options"] == "nosniff"
    assert one.headers["X-Frame-Options"] == "DENY"
    assert "Set-Cookie" not in one.headers


@pytest.mark.parametrize("origin,expected", [(None,403), ("null",403),
    ("http://evil.invalid",403), ("http://localhost:5173",200)])
def test_origin_helper(foundation_app, origin, expected):
    @foundation_app.post("/_fixture_origin")
    def origin_probe():
        error = require_auth_origin()
        return error if error is not None else {"ok": True}
    headers = {} if origin is None else {"Origin": origin}
    response = foundation_app.test_client().post("/_fixture_origin", headers=headers)
    assert response.status_code == expected
    if expected == 403:
        assert response.json["error"]["code"] == ErrorCode.FORBIDDEN
        assert response.json["error"]["message"] == ErrorMessage.ORIGIN_NOT_ALLOWED


def test_legacy_session_and_csrf_settings_removed(foundation_app):
    for key in ("SESSION_ABSOLUTE_HOURS", "SESSION_IDLE_MINUTES", "CSRF_SECRET_KEY"):
        assert key not in foundation_app.config


@pytest.mark.parametrize("path", ["/auth/refresh", "/auth/logout-all", "/auth/sessions",
    "/auth/reauthenticate", "/auth/sessions/id", "/users/id/revoke-sessions", "/me/activity-logs"])
def test_reserved_routes_remain_404(foundation_app, path):
    response = foundation_app.test_client().post("/api/v1" + path)
    assert response.status_code == 404
    assert response.json["error"]["code"] == "NOT_FOUND"
    assert response.json["error"]["request_id"] == response.headers["X-Request-ID"]
    assert response.headers["Cache-Control"] == "no-store"


def test_errors_safe_and_protocol_headers_preserved(foundation_app):
    @foundation_app.get("/_fixture_bad")
    def bad():
        abort(400, description="secret-password-should-not-leak")
    @foundation_app.get("/_fixture_crash")
    def crash():
        raise RuntimeError("secret-password-should-not-leak")
    client = foundation_app.test_client()
    for path, expected in [("/_fixture_bad",400), ("/_fixture_crash",500)]:
        response = client.get(path)
        assert response.status_code == expected
        assert "secret-password-should-not-leak" not in response.get_data(as_text=True)
        assert set(response.json["error"]) == {"code","message","request_id"}
    response = client.post("/health/live")
    assert response.status_code == 405
    assert "GET" in response.headers["Allow"]
    response = client.get("/health/live", headers={"Origin":"http://localhost:5173"})
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert "Access-Control-Allow-Credentials" not in response.headers


def test_readiness_failure_safe(foundation_app, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("postgresql://hidden:secret@internal/main")
    with foundation_app.app_context():
        monkeypatch.setattr(db.session, "execute", fail)
        rollback = MagicMock()
        monkeypatch.setattr(db.session, "rollback", rollback)
        response = foundation_app.test_client().get("/health/ready")
        assert response.status_code == 503
        assert response.json["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert "hidden" not in response.get_data(as_text=True)
        assert "secret" not in response.get_data(as_text=True)
        assert rollback.call_count == 1


def test_postgres_readiness(client):
    # Real PG test: missing/unverifiable URLs fail; never skip/fallback.
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json == {"status":"ready", "database":"connected"}


_MAIN = "postgresql://fixture:fixture@localhost:5432/iot_cabinet"
_TEST = "postgresql://fixture:fixture@127.0.0.1:5432/iot_cabinet_test"


@pytest.mark.parametrize("main,target", [(None,_TEST),(_MAIN,None),(_MAIN,""),
    (_MAIN,"sqlite:///iot_cabinet_test"),(_MAIN,_TEST+"?host=other"),
    (_MAIN,_TEST+"?dbname=iot_cabinet"),(_MAIN,_TEST.replace("iot_cabinet_test","other")),
    (_MAIN.replace("iot_cabinet","iot_cabinet_test"),_TEST),
    (_MAIN.replace("iot_cabinet","iot_cabinet_test"),
     _TEST.replace("127.0.0.1","[::1]").replace("fixture:fixture","other:different"))])
def test_guard_rejects_unsafe_targets_before_connection(db_guard, main, target):
    factory = MagicMock()
    with pytest.raises(ValueError):
        db_guard.pair(main,target,engine_factory=factory)
    factory.assert_not_called()


@pytest.mark.parametrize("row", [
    ("iot_cabinet","127.0.0.1",5432,180004,"on"),
    ("iot_cabinet_test",None,5432,180004,"on"),
    ("iot_cabinet_test","127.0.0.1",5432,180004,"off"),
    ("iot_cabinet_test","127.0.0.1",5432,150000,"on"),
])
def test_guard_checks_actual_connection(db_guard, row):
    connection = MagicMock()
    connection.execute.return_value.one.return_value = row
    with pytest.raises(ValueError):
        db_guard.connection(connection,"iot_cabinet_test")


def test_guard_valid_pair_read_only_and_disposed(db_guard):
    engines = [MagicMock(),MagicMock()]
    for engine, name in zip(engines,["iot_cabinet","iot_cabinet_test"]):
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.return_value.one.return_value = (name,"127.0.0.1",5432,180004,"on")
    factory = MagicMock(side_effect=engines)
    target, server = db_guard.pair(_MAIN,_TEST,engine_factory=factory)
    assert target.database == "iot_cabinet_test"
    assert server == ("127.0.0.1",5432,180004)
    for call in factory.call_args_list:
        assert "read_only=on" in call.kwargs["connect_args"]["options"]
    for engine in engines:
        engine.dispose.assert_called_once()
    factory = MagicMock(side_effect=RuntimeError("DO_NOT_LEAK_DSN_PASSWORD"))
    with pytest.raises(ValueError) as caught:
        db_guard.pair(_MAIN,_TEST,engine_factory=factory)
    assert "DO_NOT_LEAK" not in str(caught.value)
