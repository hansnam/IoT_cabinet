import os
from flask import Flask, jsonify
from sqlalchemy import text
from backend.app.security import register_security, make_error_response
from backend.app.security.messages import ErrorCode, ErrorMessage
from backend.app.config import config_by_name
from backend.app.extensions import db, migrate, jwt, cors
import backend.app.models  # Nạp toàn bộ 27 models vào metadata của SQLAlchemy

def create_app(config_name=None, test_config=None):
    """Application Factory cho Flask Backend (Mục 2.2 & 10)"""
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development")

    app = Flask(__name__)
    if config_name not in config_by_name:
        raise ValueError(ErrorMessage.UNKNOWN_ENVIRONMENT)
    config_obj = config_by_name[config_name]
    app.config.from_object(config_obj)
    if test_config is not None:
        app.config.update(test_config)
    app.config["APP_ENV"] = config_name

    register_security(app)

    # Khởi tạo extensions
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    cors.init_app(
        app,
        origins=app.config["CORS_ALLOWED_ORIGINS"],
        supports_credentials=False,
    )

    # Health Endpoints (Mục 2.2 & 7.2)
    @app.route("/health/live", methods=["GET"])
    def liveness():
        return jsonify({"status": "live"}), 200

    @app.route("/health/ready", methods=["GET"])
    def readiness():
        try:
            # Kiểm tra kết nối DB cơ bản
            db.session.execute(text("SELECT 1"))
            return jsonify({
                "status": "ready",
                "database": "connected"
            }), 200
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            return make_error_response(
                ErrorCode.DEPENDENCY_UNAVAILABLE, ErrorMessage.DATABASE_UNAVAILABLE, 503
            )

    # Đăng ký Blueprints (Sẽ được nạp dần ở Pha A, Pha B, Pha C)
    register_blueprints(app)

    # Đăng ký CLI commands
    @app.cli.command("seed")
    def seed():
        """Nạp dữ liệu nền ban đầu (Roles, Permissions, Users, Devices)"""
        from backend.seed import run_seed
        run_seed()

    return app

def register_blueprints(app):
    """Đăng ký các Blueprints chức năng"""
    # Placeholder cho các Blueprint sẽ tạo ở Pha A, B, C
    pass
