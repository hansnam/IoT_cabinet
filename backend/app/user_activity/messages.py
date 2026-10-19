from enum import StrEnum


class ActivityMessage(StrEnum):
    LOG_ACTOR_CONFLICT = 'Explicit actor parameters conflict with verified AuthContext'
    LOG_ACTOR_SPOOFING_DETECTED = 'Actor spoofing detected: cannot override verified actor'
    LOG_ACTOR_TYPE_REQUIRED = 'actor_type is required when actor context is not provided'
    LOG_ASYNC_OUTCOME_MISMATCH = 'Async operation requires outcome ACCEPTED'
    LOG_FIELD_REQUIRED = "Log field '{field}' is required and must not be empty"
    LOG_INVALID_ACTION = 'Invalid activity action: {action}'
    LOG_INVALID_ACTOR_TYPE = 'Invalid actor type: {actor_type}'
    LOG_INVALID_FIELD_TYPE = "Invalid type for field '{field}': expected {expected}"
    LOG_INVALID_OUTCOME = 'Invalid activity outcome: {outcome}'
    LOG_PAYLOAD_SERIALIZATION_FAILED = "Log payload serialization failed for field '{field}'"
    LOG_REQUEST_ID_REQUIRED = 'request_id is required for logging'
    LOG_SENSITIVE_DATA_DETECTED = 'Sensitive data detected in log payload: {key}'
    LOG_SESSION_REQUIRED = 'Database session is required for logging'
    LOG_SUMMARY_PARAM_MISSING = "Missing required summary parameter '{param}' for function '{function_key}'"
    LOG_UNALLOWED_KEY = "Unallowed key '{key}' in log payload for '{target}'"
    LOG_UNKNOWN_FUNCTION_KEY = 'Unknown or disallowed function key: {function_key}'
