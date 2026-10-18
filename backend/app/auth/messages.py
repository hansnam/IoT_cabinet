from enum import StrEnum


class AuthMessage(StrEnum):
    # Password validation & hashing messages
    PASSWORD_REQUIRED = "Password is required"
    PASSWORD_INVALID_TYPE = "Password must be a string"
    PASSWORD_LENGTH_INVALID = "Password must be between 12 and 128 characters"
    HASH_INVALID_FORMAT = "Invalid password hash format"

    # Token validation & claims messages
    TOKEN_REQUIRED = "Authorization token is missing"
    TOKEN_INVALID = "Token is invalid"
    TOKEN_EXPIRED = "Token has expired"
    TOKEN_REVOKED = "Token has been revoked"
    TOKEN_TYPE_INVALID = "Token type must be access"
    TOKEN_ALGORITHM_NOT_ALLOWED = "Algorithm '{algorithm}' is not allowed"
    TOKEN_ISSUER_INVALID = "Token issuer is invalid"
    TOKEN_AUDIENCE_INVALID = "Token audience is invalid"
    TOKEN_CLAIM_MISSING = "Missing required token claim: {claim}"
    TOKEN_CLAIM_INVALID = "Invalid token claim '{claim}': {reason}"
    TOKEN_SUB_INVALID = "Token sub must be a valid UUID string"
    TOKEN_SID_INVALID = "Token sid must be a valid UUID string"
    TOKEN_VER_INVALID = "Token ver must be a positive integer"
    TOKEN_TTL_INVALID = "Token TTL must be 900 seconds; testing permits whole seconds from 1 to 900"

    # Login & Request validation messages (TV3-03B)
    USERNAME_REQUIRED = "Username is required"
    USERNAME_INVALID_TYPE = "Username must be a string"
    USERNAME_FORMAT_INVALID = "Username must be 3-64 characters, lowercase alphanumeric, underscore, dot or hyphen"


class AuthFailureCode(StrEnum):
    """Mã định danh lý do đăng nhập thất bại phục vụ rate limiting và audit logging (TV3-03B)."""
    RATE_LIMITED = "RATE_LIMITED"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    USER_INACTIVE = "USER_INACTIVE"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    ROLE_NOT_FOUND = "ROLE_NOT_FOUND"
