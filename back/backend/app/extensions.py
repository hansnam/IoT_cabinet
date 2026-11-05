from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from argon2 import PasswordHasher, Type
from flask import current_app, has_app_context

# Database Object dùng chung cho toàn bộ ứng dụng (TV2 & TV3)
db = SQLAlchemy()

# Quản lý Database Migrations
migrate = Migrate()

# Quản lý JSON Web Token
jwt = JWTManager()

# Cross-Origin Resource Sharing
cors = CORS()

def get_password_hasher(
    memory_cost: int | None = None,
    time_cost: int | None = None,
    parallelism: int | None = None,
) -> PasswordHasher:
    """Khởi tạo PasswordHasher với type Argon2id explicit theo tham số cấu hình
    - Đọc tham số từ Flask current_app.config nếu đang trong app context.
    - Dự phòng giá trị an toàn theo khuyến nghị OWASP: memory_cost=19456 KiB, time_cost=2, parallelism=1.
    - Cho phép caller truyền tham số ghi đè (phục vụ kiểm thử rehash).
    """
    m = memory_cost
    t = time_cost
    p = parallelism

    if has_app_context():
        cfg = current_app.config
        if m is None:
            m = cfg.get("ARGON2_MEMORY_COST_KIB", 19456)
        if t is None:
            t = cfg.get("ARGON2_TIME_COST", 2)
        if p is None:
            p = cfg.get("ARGON2_PARALLELISM", 1)

    return PasswordHasher(
        memory_cost=m if m is not None else 19456,
        time_cost=t if t is not None else 2,
        parallelism=p if p is not None else 1,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )
