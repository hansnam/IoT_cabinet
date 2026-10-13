import os
import uuid
from flask import Flask, jsonify, request
from backend.app.config import config_by_name
from backend.app.extensions import db, migrate, jwt, cors
import backend.app.models  # Nạp toàn bộ 27 models vào metadata của SQLAlchemy

def create_app(config_name=None):
    """Application Factory cho Flask Backend (Mục 2.2 & 10)"""
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development")

    app = Flask(__name__)
    config_obj = config_by_name.get(config_name, config_by_name["default"])
    app.config.from_object(config_obj)

    # Khởi tạo extensions
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    cors.init_app(
        app,
        origins=app.config["CORS_ALLOWED_ORIGINS"],
        supports_credentials=True,
    )

    # Request ID middleware
    @app.before_request
    def set_request_id():
        request.request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

    @app.after_request
    def set_response_headers(response):
        response.headers["X-Request-ID"] = getattr(request, "request_id", str(uuid.uuid4()))
        return response

    # Standard JSON Error Handlers (Mục 7)
    @app.errorhandler(400)
    def bad_request(error):
        return jsonify({
            "error": {
                "code": "BAD_REQUEST",
                "message": getattr(error, "description", "Yêu cầu không hợp lệ"),
                "request_id": getattr(request, "request_id", None)
            }
        }), 400

    @app.errorhandler(401)
    def unauthorized(error):
        return jsonify({
            "error": {
                "code": "UNAUTHORIZED",
                "message": getattr(error, "description", "Chưa xác thực hoặc phiên đã hết hạn"),
                "request_id": getattr(request, "request_id", None)
            }
        }), 401

    @app.errorhandler(403)
    def forbidden(error):
        return jsonify({
            "error": {
                "code": "FORBIDDEN",
                "message": getattr(error, "description", "Không có quyền thực hiện hành động này"),
                "request_id": getattr(request, "request_id", None)
            }
        }), 403

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({
            "error": {
                "code": "NOT_FOUND",
                "message": getattr(error, "description", "Tài nguyên không tồn tại"),
                "request_id": getattr(request, "request_id", None)
            }
        }), 404

    @app.errorhandler(409)
    def conflict(error):
        return jsonify({
            "error": {
                "code": "CONFLICT",
                "message": getattr(error, "description", "Xung đột trạng thái hoặc phiên bản"),
                "request_id": getattr(request, "request_id", None)
            }
        }), 409

    @app.errorhandler(429)
    def too_many_requests(error):
        return jsonify({
            "error": {
                "code": "TOO_MANY_REQUESTS",
                "message": getattr(error, "description", "Quá nhiều yêu cầu. Vui lòng thử lại sau"),
                "request_id": getattr(request, "request_id", None)
            }
        }), 429

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "Lỗi máy chủ nội bộ",
                "request_id": getattr(request, "request_id", None)
            }
        }), 500

    # Health Endpoints (Mục 2.2 & 7.2)
    @app.route("/health/live", methods=["GET"])
    def liveness():
        return jsonify({"status": "live"}), 200

    @app.route("/health/ready", methods=["GET"])
    def readiness():
        try:
            # Kiểm tra kết nối DB cơ bản
            db.session.execute(db.text("SELECT 1"))
            return jsonify({
                "status": "ready",
                "database": "connected"
            }), 200
        except Exception as e:
            return jsonify({
                "status": "not_ready",
                "database": "disconnected",
                "error": str(e)
            }), 503

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
