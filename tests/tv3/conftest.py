"""Read-only foundations. Never migrate, create_all, seed or drop in fixtures."""
import os
import secrets
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from backend.app.security.messages import ErrorMessage
from backend.app import create_app
from backend.app.extensions import db

TEST_DB_ALLOWLIST = frozenset({"iot_cabinet_test"})


def _parse_target(value):
    try:
        if not isinstance(value, str) or not value.strip():
            raise ValueError
        url = make_url(value)
        if (url.drivername not in {"postgresql", "postgresql+psycopg2", "postgresql+psycopg"}
                or not url.host or not url.username or not url.database
                or url.query or not 1 <= (url.port or 5432) <= 65535):
            raise ValueError
        return url
    except Exception:
        # No query-string DSN overrides (host/dbname/service/options) in 01A.
        raise ValueError(ErrorMessage.EXPLICIT_POSTGRES_URL_REQUIRED) from None


def verify_test_database_target(app_db_url, test_db_url):
    main = _parse_target(app_db_url)
    target = _parse_target(test_db_url)
    # Stronger than host comparison: same name is rejected even across servers.
    # Thus aliases, driver/user/password differences never bypass separation.
    if main.database == target.database:
        raise ValueError(ErrorMessage.DATABASE_NAMES_MUST_DIFFER)
    if target.database not in TEST_DB_ALLOWLIST:
        raise ValueError(ErrorMessage.TEST_DATABASE_NOT_ALLOWED)
    return main, target


def verify_connection(connection, expected_name, expected_server=None):
    row = connection.execute(text(
        "SELECT current_database(), inet_server_addr()::text, inet_server_port(), "
        "current_setting('server_version_num')::int, "
        "current_setting('transaction_read_only')"
    )).one()
    name, host, port, version, read_only = row
    if (name != expected_name or not host or not port or version < 160000
            or read_only != "on"):
        raise ValueError(ErrorMessage.DATABASE_IDENTITY_FAILED)
    server = (host, port, version)
    if expected_server is not None and server != expected_server:
        raise ValueError(ErrorMessage.TEST_DATABASE_SERVER_MISMATCH)
    return server


def verify_database_pair(app_db_url, test_db_url, *, engine_factory=None):
    main, target = verify_test_database_target(app_db_url, test_db_url)
    factory = engine_factory or create_engine
    servers = []
    for url in (main, target):
        engine = None
        try:
            engine = factory(
                url, hide_parameters=True,
                connect_args={"connect_timeout": 3,
                              "options": "-c default_transaction_read_only=on"},
            )
            with engine.connect() as connection:
                servers.append(verify_connection(connection, url.database))
        except Exception:
            raise ValueError(ErrorMessage.DATABASE_SEPARATION_UNVERIFIED) from None
        finally:
            if engine is not None:
                engine.dispose()
    return target, servers[1]


def _test_settings(uri):
    return {
        "TESTING": True, "DEBUG": False,
        "SQLALCHEMY_DATABASE_URI": uri,
        "SECRET_KEY": secrets.token_hex(32),
        "JWT_SECRET_KEY": secrets.token_hex(32),
        "AUTH_LOG_HMAC_KEY": secrets.token_hex(32),
        "AUTH_ALLOWED_ORIGINS": ["http://localhost:5173"],
        "CORS_ALLOWED_ORIGINS": ["http://localhost:5173"],
        "JWT_ACCESS_TOKEN_EXPIRES": 5,
    }


@pytest.fixture
def db_guard():
    # No importing tests.tv3.conftest (tests is not a Python package).
    return SimpleNamespace(parse=verify_test_database_target, connection=verify_connection,
                           pair=verify_database_pair)


@pytest.fixture
def foundation_app():
    # Lazy engine only, no connection; invalid port prevents accidental real DB use.
    app = create_app("testing", test_config=_test_settings(
        "postgresql://fixture:fixture@127.0.0.1:1/iot_cabinet_test"
    ))
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def app():
    # config.py loads root .env on the user's machine; never print its values.
    target, server = verify_database_pair(os.getenv("DATABASE_URL"), os.getenv("TEST_DATABASE_URL"))
    instance = create_app("testing", test_config=_test_settings(target))
    try:
        with instance.app_context():
            # Verify the actual Flask engine too, before handing it to tests.
            db.session.execute(text("SET TRANSACTION READ ONLY"))
            verify_connection(db.session, target.database, server)
            db.session.rollback()
            db.session.remove()
        # No session-scoped app context: Flask removes sessions per request.
        yield instance
    finally:
        with instance.app_context():
            db.session.remove()
            db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()