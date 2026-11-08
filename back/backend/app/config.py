import os
from dotenv import load_dotenv

# Tải biến môi trường từ .env
load_dotenv()

class BaseConfig:
    """Cấu hình cơ bản cho ứng dụng Flask"""
    SECRET_KEY = os.getenv("SECRET_KEY")
    
    # SQLAlchemy Database
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_size": 10,
        "max_overflow": 20,
    }

    # JWT Authentication (Access token 15 phút, Mục 5.2)
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
    JWT_ACCESS_TOKEN_EXPIRES = os.getenv("JWT_ACCESS_TOKEN_EXPIRES_SECONDS", "900")
    JWT_ALGORITHM = "HS256"
    JWT_DECODE_ALGORITHMS = ["HS256"]
    JWT_ENCODE_ISSUER = "iot-cabinet-api"
    JWT_DECODE_ISSUER = "iot-cabinet-api"
    JWT_ENCODE_AUDIENCE = "iot-cabinet-web"
    JWT_DECODE_AUDIENCE = "iot-cabinet-web"
    JWT_TOKEN_LOCATION = ["headers"]
    JWT_HEADER_NAME = "Authorization"
    JWT_HEADER_TYPE = "Bearer"

    AUTH_LOG_HMAC_KEY = os.getenv("AUTH_LOG_HMAC_KEY")

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
    MQTT_WORKER_PASSWORD = os.getenv("MQTT_WORKER_PASSWORD")
    MQTT_WORKER_CLIENT_ID = os.getenv("MQTT_WORKER_CLIENT_ID", "backend-mqtt-worker")

    MQTT_CONTROLLER_USERNAME = os.getenv("MQTT_CONTROLLER_USERNAME", "broker_admin")
    MQTT_CONTROLLER_PASSWORD = os.getenv("MQTT_CONTROLLER_PASSWORD")
    MQTT_CONTROLLER_CLIENT_ID = os.getenv("MQTT_CONTROLLER_CLIENT_ID", "backend-broker-controller")

    # Explicit origins: no wildcard and no cookie credentials.
    CORS_ALLOWED_ORIGINS = [
        value.strip() for value in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    ]
    AUTH_ALLOWED_ORIGINS = [
        value.strip() for value in os.getenv("AUTH_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    ]
    # Feature Flags
    FAN_MONITOR_ENABLED = os.getenv("FAN_MONITOR_ENABLED", "false").lower() == "true"


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class TestingConfig(BaseConfig):
    TESTING = True
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.getenv("TEST_DATABASE_URL")
    JWT_ACCESS_TOKEN_EXPIRES = os.getenv("TEST_JWT_ACCESS_TOKEN_EXPIRES_SECONDS", "5")


class ProductionConfig(BaseConfig):
    DEBUG = False
    TESTING = False


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "release": ProductionConfig,
    "default": DevelopmentConfig,
}
