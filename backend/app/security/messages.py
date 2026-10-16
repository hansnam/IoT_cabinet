"""Single catalog of TV3 error codes and public messages. No Flask/DB imports."""
from enum import StrEnum

class ErrorCode(StrEnum):
    BAD_REQUEST = "BAD_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    CONFLICT = "CONFLICT"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    TOO_MANY_REQUESTS = "TOO_MANY_REQUESTS"
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    HTTP_ERROR = "HTTP_{status}"

class ErrorMessage(StrEnum):
    INVALID_CONFIG = "Invalid configuration: {name}"
    SEPARATE_KEYS_REQUIRED = "Secrets must be separate keys"
    RELEASE_FLAGS_DISABLED = "Release must disable DEBUG and TESTING"
    INVALID_JWT_TTL = "JWT TTL must be 900 seconds; testing permits 1..900"
    JWT_CONTRACT_MISMATCH = "JWT configuration differs from the P0 contract"
    UNKNOWN_ENVIRONMENT = "Unknown application environment"
    EXPLICIT_POSTGRES_URL_REQUIRED = "Explicit PostgreSQL URL required; query parameters are not allowed"
    DATABASE_NAMES_MUST_DIFFER = "Application and test database names must differ"
    TEST_DATABASE_NOT_ALLOWED = "Test database is outside the allowlist"
    DATABASE_IDENTITY_FAILED = "Database identity/read-only verification failed"
    TEST_DATABASE_SERVER_MISMATCH = "Test connection reached a different server"
    DATABASE_SEPARATION_UNVERIFIED = "Cannot verify separate PostgreSQL databases; no mutations allowed"
    ORIGIN_NOT_ALLOWED = "Origin is not allowed"
    DATABASE_UNAVAILABLE = "Database is unavailable"
    INTERNAL_SERVER_ERROR = "Internal server error"
    UNEXPECTED_ERROR_LOG = "Unhandled request error; request_id=%s"
    BAD_REQUEST = "Bad Request"
    UNAUTHORIZED = "Unauthorized"
    FORBIDDEN = "Forbidden"
    NOT_FOUND = "Not Found"
    METHOD_NOT_ALLOWED = "Method Not Allowed"
    CONFLICT = "Conflict"
    UNSUPPORTED_MEDIA_TYPE = "Unsupported Media Type"
    TOO_MANY_REQUESTS = "Too Many Requests"
    SERVICE_UNAVAILABLE = "Service Unavailable"
    HTTP_ERROR = "HTTP request failed"

HTTP_ERRORS = {
    400: (ErrorCode.BAD_REQUEST, ErrorMessage.BAD_REQUEST),
    401: (ErrorCode.UNAUTHORIZED, ErrorMessage.UNAUTHORIZED),
    403: (ErrorCode.FORBIDDEN, ErrorMessage.FORBIDDEN),
    404: (ErrorCode.NOT_FOUND, ErrorMessage.NOT_FOUND),
    405: (ErrorCode.METHOD_NOT_ALLOWED, ErrorMessage.METHOD_NOT_ALLOWED),
    409: (ErrorCode.CONFLICT, ErrorMessage.CONFLICT),
    415: (ErrorCode.UNSUPPORTED_MEDIA_TYPE, ErrorMessage.UNSUPPORTED_MEDIA_TYPE),
    429: (ErrorCode.TOO_MANY_REQUESTS, ErrorMessage.TOO_MANY_REQUESTS),
    500: (ErrorCode.INTERNAL_SERVER_ERROR, ErrorMessage.INTERNAL_SERVER_ERROR),
    503: (ErrorCode.SERVICE_UNAVAILABLE, ErrorMessage.SERVICE_UNAVAILABLE),
}