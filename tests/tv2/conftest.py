"""Test fixtures for TV2 test suite (isolated SAVEPOINT transactions on iot_cabinet_test)."""
import os
import secrets
from pathlib import Path
import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, scoped_session

# Load backend/.env if present so DATABASE_URL is available automatically
_BACKEND_ENV_PATH = Path(__file__).resolve().parent.parent.parent / "backend" / ".env"
if _BACKEND_ENV_PATH.exists():
    load_dotenv(_BACKEND_ENV_PATH, override=False)

if os.getenv("DATABASE_URL") and not os.getenv("TEST_DATABASE_URL"):
    _main_url = make_url(os.getenv("DATABASE_URL"))
    os.environ["TEST_DATABASE_URL"] = _main_url.set(database="iot_cabinet_test").render_as_string(
        hide_password=False
    )

if not os.getenv("AUTH_ALLOWED_ORIGINS"):
    os.environ["AUTH_ALLOWED_ORIGINS"] = "http://localhost:5173"
if not os.getenv("CORS_ALLOWED_ORIGINS"):
    os.environ["CORS_ALLOWED_ORIGINS"] = "http://localhost:5173"

from backend.app import create_app
from backend.app.extensions import db
from backend.app.security.messages import ErrorMessage

TEST_DB_ALLOWLIST = frozenset({"iot_cabinet_test"})


def _parse_target(value):
    try:
        if not isinstance(value, str) or not value.strip():
            raise ValueError
        url = make_url(value)
        if (
            url.drivername not in {"postgresql", "postgresql+psycopg2", "postgresql+psycopg"}
            or not url.host
            or not url.username
            or not url.database
            or url.query
            or not 1 <= (url.port or 5432) <= 65535
        ):
            raise ValueError
        return url
    except Exception:
        raise ValueError(ErrorMessage.EXPLICIT_POSTGRES_URL_REQUIRED) from None


def verify_test_database_target(app_db_url, test_db_url):
    main = _parse_target(app_db_url)
    target = _parse_target(test_db_url)
    if main.database == target.database:
        raise ValueError(ErrorMessage.DATABASE_NAMES_MUST_DIFFER)
    if target.database not in TEST_DB_ALLOWLIST:
        raise ValueError(ErrorMessage.TEST_DATABASE_NOT_ALLOWED)
    return main, target


def verify_connection(connection, expected_name, expected_server=None):
    row = connection.execute(
        text(
            "SELECT current_database(), inet_server_addr()::text, inet_server_port(), "
            "current_setting('server_version_num')::int, "
            "current_setting('transaction_read_only')"
        )
    ).one()
    name, host, port, version, read_only = row
    if name != expected_name or not host or not port or version < 160000 or read_only != "on":
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
                url,
                hide_parameters=True,
                connect_args={
                    "connect_timeout": 3,
                    "options": "-c default_transaction_read_only=on",
                },
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
        "TESTING": True,
        "DEBUG": False,
        "SQLALCHEMY_DATABASE_URI": uri,
        "SECRET_KEY": secrets.token_hex(32),
        "JWT_SECRET_KEY": secrets.token_hex(32),
        "AUTH_LOG_HMAC_KEY": secrets.token_hex(32),
        "AUTH_ALLOWED_ORIGINS": ["http://localhost:5173"],
        "CORS_ALLOWED_ORIGINS": ["http://localhost:5173"],
        "JWT_ACCESS_TOKEN_EXPIRES": 900,
    }


@pytest.fixture
def app():
    target, server = verify_database_pair(os.getenv("DATABASE_URL"), os.getenv("TEST_DATABASE_URL"))
    instance = create_app("testing", test_config=_test_settings(target))
    try:
        with instance.app_context():
            db.session.execute(text("SET TRANSACTION READ ONLY"))
            verify_connection(db.session, target.database, server)
            db.session.rollback()
            db.session.remove()
        yield instance
    finally:
        with instance.app_context():
            db.session.remove()
            db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def transaction_session(app):
    """Isolated database session using connection transaction + SAVEPOINT."""
    with app.app_context():
        conn = db.engine.connect()
        trans = conn.begin()
        session = Session(bind=conn, join_transaction_mode="create_savepoint")
        old_session = db.session
        db.session = scoped_session(lambda: session)
        try:
            yield session
        finally:
            db.session = old_session
            session.close()
            trans.rollback()
            conn.close()

