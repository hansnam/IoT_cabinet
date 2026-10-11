import os
from datetime import timedelta
from dotenv import load_dotenv

# Tải biến môi trường từ .env
load_dotenv()

class BaseConfig:
    """Cấu hình cơ bản cho ứng dụng Flask"""
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
    
    # SQLAlchemy Database
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", 
        "postgresql://postgres:postgres123@localhost:5432/iot_cabinet"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_size": 10,
        "max_overflow": 20,
    }

    # JWT Authentication (Access token 15 phút, Mục 5.2)
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "jwt-secret-key-minimum-32-chars-long")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(
        minutes=int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES_MINUTES", "15"))
    )
    JWT_TOKEN_LOCATION = ["headers"]
    JWT_HEADER_NAME = "Authorization"
    JWT_HEADER_TYPE = "Bearer"

    # Session & CSRF Security (Mục 5.2 & 15.2)
    SESSION_ABSOLUTE_HOURS = int(os.getenv("SESSION_ABSOLUTE_HOURS", "8"))
    SESSION_IDLE_MINUTES = int(os.getenv("SESSION_IDLE_MINUTES", "30"))
    CSRF_SECRET_KEY = os.getenv("CSRF_SECRET_KEY", "csrf-secret-key-for-hmac-nonce")
    AUTH_LOG_HMAC_KEY = os.getenv("AUTH_LOG_HMAC_KEY", "auth-log-hmac-key-for-anonymization")

    # Argon2id Parameters (Mục 2 & OWASP)
    ARGON2_MEMORY_COST_KIB = int(os.getenv("ARGON2_MEMORY_COST_KIB", "19456"))
    ARGON2_TIME_COST = int(os.getenv("ARGON2_TIME_COST", "2"))
    ARGON2_PARALLELISM = int(os.getenv("ARGON2_PARALLELISM", "1"))

    # MQTT Broker Configuration
    MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
    MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
    MQTT_BROKER_TLS_PORT = int(os.getenv("MQTT_BROKER_TLS_PORT", "8883"))
    MQTT_BROKER_USE_TLS = os.getenv("MQTT_BROKER_USE_TLS", "false").lower() == "true"
    MQTT_CA_CERT_PATH = os.getenv("MQTT_CA_CERT_PATH", None)

    # MQTT Service Credentials
    MQTT_WORKER_USERNAME = os.getenv("MQTT_WORKER_USERNAME", "worker_service")
    MQTT_WORKER_PASSWORD = os.getenv("MQTT_WORKER_PASSWORD", "worker_service_secret_pass_123")
    MQTT_WORKER_CLIENT_ID = os.getenv("MQTT_WORKER_CLIENT_ID", "backend-mqtt-worker")

    MQTT_CONTROLLER_USERNAME = os.getenv("MQTT_CONTROLLER_USERNAME", "broker_admin")
    MQTT_CONTROLLER_PASSWORD = os.getenv("MQTT_CONTROLLER_PASSWORD", "broker_admin_secret_pass_123")
    MQTT_CONTROLLER_CLIENT_ID = os.getenv("MQTT_CONTROLLER_CLIENT_ID", "backend-broker-controller")

    # CORS Allowlist
    CORS_ALLOWED_ORIGINS = [
        origin.strip() 
        for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    ]

    # Feature Flags
    FAN_MONITOR_ENABLED = os.getenv("FAN_MONITOR_ENABLED", "false").lower() == "true"


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class TestingConfig(BaseConfig):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "TEST_DATABASE_URL", 
        "postgresql://postgres:postgres123@localhost:5432/iot_cabinet_test"
    )
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(seconds=5)


class ProductionConfig(BaseConfig):
    DEBUG = False
    TESTING = False


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
