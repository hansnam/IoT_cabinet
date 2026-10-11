from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from argon2 import PasswordHasher

# Database Object dùng chung cho toàn bộ ứng dụng (TV2 & TV3)
db = SQLAlchemy()

# Quản lý Database Migrations
migrate = Migrate()

# Quản lý JSON Web Token
jwt = JWTManager()

# Cross-Origin Resource Sharing
cors = CORS()

def get_password_hasher(memory_cost=19456, time_cost=2, parallelism=1) -> PasswordHasher:
    """Khởi tạo Argon2id PasswordHasher theo tham số cấu hình (Mục 2 & 5.2)"""
    return PasswordHasher(
        memory_cost=memory_cost,
        time_cost=time_cost,
        parallelism=parallelism,
        hash_len=32,
        salt_len=16,
    )
