"""Validate after factory overrides, before initializing extensions."""
from datetime import timedelta
from urllib.parse import urlsplit
from sqlalchemy.engine import make_url

from backend.app.security.messages import ErrorMessage

_PLACEHOLDERS = (
    "replace", "change_this", "changeme", "dev-secret-key", "jwt-secret-key-minimum",
    "auth-log-hmac-key-for", "test-secret", "test-jwt", "test-auth", "test-flask",
    "worker_service_secret_pass", "broker_admin_secret_pass",
)

def _secret(value, name, minimum):
    if ((not isinstance(value, str) or not value.strip())
            or len(value.encode("utf-8")) < minimum
            or any(marker in value.lower() for marker in _PLACEHOLDERS)):
        raise ValueError(ErrorMessage.INVALID_CONFIG.format(name=name))

def _origins(value, name, required):
    if not isinstance(value, (list, tuple)) or (required and not value):
        raise ValueError(ErrorMessage.INVALID_CONFIG.format(name=name))

    for origin in value:
        try:
            parsed = urlsplit(origin)
            valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                     and not parsed.username and not parsed.password
                     and not parsed.path and not parsed.query and not parsed.fragment
                     and origin == f"{parsed.scheme}://{parsed.netloc}"
                     and "*" not in origin and parsed.port != 0)
        except(TypeError, ValueError):
            valid = False

        if not valid:
            raise ValueError(ErrorMessage.INVALID_CONFIG.format(name=name))

def validate_config(app):
    config = app.config
    for name in ("SECRET_KEY", "JWT_SECRET_KEY", "AUTH_LOG_HMAC_KEY"):
        _secret(config.get(name), name, 32)

    if len({config[name] for name in ("SECRET_KEY", "JWT_SECRET_KEY", "AUTH_LOG_HMAC_KEY")}) != 3:
        raise ValueError(ErrorMessage.SEPARATE_KEYS_REQUIRED)

    if config.get("APP_ENV") in {"production", "release"}:
        if app.debug or app.testing:
            raise ValueError(ErrorMessage.RELEASE_FLAGS_DISABLED)
        for name in ("MQTT_WORKER_PASSWORD", "MQTT_CONTROLLER_PASSWORD"):
            _secret(config.get(name), name, 1)

    _origins(config.get("AUTH_ALLOWED_ORIGINS"), "AUTH_ALLOWED_ORIGINS", True)
    _origins(config.get("CORS_ALLOWED_ORIGINS"), "CORS_ALLOWED_ORIGINS", False)
    try:
        uri = make_url(config.get("SQLALCHEMY_DATABASE_URI", ""))
        valid_db = uri.get_backend_name() == "postgresql" and bool(uri.database)
    except Exception:
        raise ValueError(ErrorMessage.INVALID_CONFIG.format(name="SQLALCHEMY_DATABASE_URI")) from None
    if not valid_db:
        raise ValueError(ErrorMessage.INVALID_CONFIG.format(name="SQLALCHEMY_DATABASE_URI"))

    ttl = config.get("JWT_ACCESS_TOKEN_EXPIRES")
    if not isinstance(ttl, (str, int, timedelta)):
        raise ValueError(ErrorMessage.INVALID_CONFIG.format(name="JWT_ACCESS_TOKEN_EXPIRES"))
    if isinstance(ttl, timedelta):
        seconds = ttl.total_seconds()
    else:
        try:
            seconds = int(ttl)
        except(TypeError, ValueError, OverflowError):
            raise ValueError(ErrorMessage.INVALID_CONFIG.format(name="JWT_ACCESS_TOKEN_EXPIRES")) from None

    if (isinstance(ttl, bool) or seconds != int(seconds) or not 1 <= seconds <= 900
            or (not app.testing and seconds != 900)):
        raise ValueError(ErrorMessage.INVALID_JWT_TTL)
    config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(seconds=seconds)
    config["SESSION_LIFETIME_SECONDS"] = int(seconds)
    expected = {
        "JWT_ALGORITHM": "HS256", "JWT_DECODE_ALGORITHMS": ["HS256"],
        "JWT_ENCODE_ISSUER": "iot-cabinet-api", "JWT_DECODE_ISSUER": "iot-cabinet-api",
        "JWT_ENCODE_AUDIENCE": "iot-cabinet-web", "JWT_DECODE_AUDIENCE": "iot-cabinet-web",
        "JWT_TOKEN_LOCATION": ["headers"], "JWT_HEADER_NAME": "Authorization",
        "JWT_HEADER_TYPE": "Bearer",
    }
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError(ErrorMessage.JWT_CONTRACT_MISMATCH)
