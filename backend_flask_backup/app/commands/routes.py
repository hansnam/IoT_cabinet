"""
Config & Command Routes Module (Thành viên 2 phụ trách - Pha B)
Cung cấp 5 endpoint REST API trong Blueprint 'commands' với tiền tố /api/v1:
1. GET  /api/v1/devices/<device_id>/config: Đọc cấu hình desired/applied và trạng thái pending.
2. PUT  /api/v1/devices/<device_id>/config: Cập nhật cấu hình (202 Accepted, kiểm tra base_version & Idempotency-Key).
3. POST /api/v1/devices/<device_id>/commands: Gửi lệnh điều khiển SET_MODE / SET_FAN (202 Accepted).
4. GET  /api/v1/devices/<device_id>/commands: Lịch sử lệnh của thiết bị.
5. GET  /api/v1/commands/<command_id>: Chi tiết lệnh và danh sách command_attempts.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError as SQLAlchemyTimeoutError

from backend.app.commands.schemas import (
    CommandConflictError,
    CommandValidationError,
    parse_command_list_query_params,
)
from backend.app.commands.service import CommandService
from backend.app.extensions import db
from backend.app.permissions.decorators import require_auth, require_permission
from backend.app.permissions.service import (
    AuthorizationError,
    AuthRequired,
    DeviceScopeNotFound,
    PermissionDenied,
)
from backend.app.security.errors import make_error_response
from backend.app.security.messages import ErrorCode, ErrorMessage


DB_UNAVAILABLE_ERRORS = (OperationalError, InterfaceError, SQLAlchemyTimeoutError)

commands_bp = Blueprint("commands", __name__, url_prefix="/api/v1")


@commands_bp.route("/devices/<string:device_id>/config", methods=["GET"])
@require_permission("device.read", device_id_param="device_id")
def get_device_config(device_id: str):
    """GET /api/v1/devices/<device_id>/config
    Xem cấu hình desired hiện tại, applied version và cờ pending.
    """
    try:
        data = CommandService.get_device_config(
            device_id,
            actor=request.auth,
            db_session=db.session,
        )
    except DeviceScopeNotFound as e:
        return make_error_response(e.code, e.message, e.status_code)
    except AuthRequired:
        return make_error_response(ErrorCode.UNAUTHORIZED, ErrorMessage.UNAUTHORIZED, 401)
    except PermissionDenied as e:
        return make_error_response(e.code, e.message, e.status_code)
    except AuthorizationError as e:
        return make_error_response(e.code, e.message, e.status_code)
    except DB_UNAVAILABLE_ERRORS:
        return make_error_response(ErrorCode.SERVICE_UNAVAILABLE, ErrorMessage.SERVICE_UNAVAILABLE, 503)
    except Exception:
        return make_error_response(ErrorCode.INTERNAL_SERVER_ERROR, ErrorMessage.INTERNAL_SERVER_ERROR, 500)

    return jsonify({"data": data}), 200


@commands_bp.route("/devices/<string:device_id>/config", methods=["PUT"])
@require_auth
def update_device_config(device_id: str):
    """PUT /api/v1/devices/<device_id>/config
    Tạo phiên bản cấu hình mới và phát lệnh APPLY_CONFIG bất đồng bộ (HTTP 202 Accepted).
    """
    payload = request.get_json(silent=True)
    idempotency_key = request.headers.get("Idempotency-Key")

    try:
        data = CommandService.update_device_config(
            device_id,
            payload,
            idempotency_key=idempotency_key,
            actor=request.auth,
            db_session=db.session,
        )
    except DeviceScopeNotFound as e:
        return make_error_response(e.code, e.message, e.status_code)
    except PermissionDenied as e:
        return make_error_response(e.code, e.message, e.status_code)
    except AuthRequired:
        return make_error_response(ErrorCode.UNAUTHORIZED, ErrorMessage.UNAUTHORIZED, 401)
    except AuthorizationError as e:
        return make_error_response(e.code, e.message, e.status_code)
    except CommandValidationError:
        return make_error_response(ErrorCode.BAD_REQUEST, ErrorMessage.BAD_REQUEST, 400)
    except CommandConflictError as e:
        return make_error_response(e.code, ErrorMessage.CONFLICT, 409)
    except DB_UNAVAILABLE_ERRORS:
        try:
            db.session.rollback()
        except Exception:
            pass
        return make_error_response(ErrorCode.SERVICE_UNAVAILABLE, ErrorMessage.SERVICE_UNAVAILABLE, 503)
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return make_error_response(ErrorCode.INTERNAL_SERVER_ERROR, ErrorMessage.INTERNAL_SERVER_ERROR, 500)

    return jsonify({"data": data}), 202


@commands_bp.route("/devices/<string:device_id>/commands", methods=["POST"])
@require_auth
def create_device_command(device_id: str):
    """POST /api/v1/devices/<device_id>/commands
    Gửi lệnh điều khiển SET_MODE / SET_FAN vào hàng đợi Outbox (HTTP 202 Accepted).
    """
    payload = request.get_json(silent=True)
    idempotency_key = request.headers.get("Idempotency-Key")

    try:
        data = CommandService.create_command(
            device_id,
            payload,
            idempotency_key=idempotency_key,
            actor=request.auth,
            db_session=db.session,
        )
    except DeviceScopeNotFound as e:
        return make_error_response(e.code, e.message, e.status_code)
    except PermissionDenied as e:
        return make_error_response(e.code, e.message, e.status_code)
    except AuthRequired:
        return make_error_response(ErrorCode.UNAUTHORIZED, ErrorMessage.UNAUTHORIZED, 401)
    except AuthorizationError as e:
        return make_error_response(e.code, e.message, e.status_code)
    except CommandValidationError:
        return make_error_response(ErrorCode.BAD_REQUEST, ErrorMessage.BAD_REQUEST, 400)
    except CommandConflictError as e:
        return make_error_response(e.code, ErrorMessage.CONFLICT, 409)
    except DB_UNAVAILABLE_ERRORS:
        try:
            db.session.rollback()
        except Exception:
            pass
        return make_error_response(ErrorCode.SERVICE_UNAVAILABLE, ErrorMessage.SERVICE_UNAVAILABLE, 503)
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return make_error_response(ErrorCode.INTERNAL_SERVER_ERROR, ErrorMessage.INTERNAL_SERVER_ERROR, 500)

    return jsonify({"data": data}), 202


@commands_bp.route("/devices/<string:device_id>/commands", methods=["GET"])
@require_permission("command.read", device_id_param="device_id")
def list_device_commands(device_id: str):
    """GET /api/v1/devices/<device_id>/commands
    Lấy danh sách lịch sử lệnh của thiết bị trong phạm vi quyền.
    """
    try:
        params = parse_command_list_query_params(request.args)
    except CommandValidationError:
        return make_error_response(ErrorCode.BAD_REQUEST, ErrorMessage.BAD_REQUEST, 400)

    try:
        items, meta = CommandService.list_device_commands(
            device_id,
            params,
            actor=request.auth,
            db_session=db.session,
        )
    except DeviceScopeNotFound as e:
        return make_error_response(e.code, e.message, e.status_code)
    except AuthRequired:
        return make_error_response(ErrorCode.UNAUTHORIZED, ErrorMessage.UNAUTHORIZED, 401)
    except PermissionDenied as e:
        return make_error_response(e.code, e.message, e.status_code)
    except AuthorizationError as e:
        return make_error_response(e.code, e.message, e.status_code)
    except DB_UNAVAILABLE_ERRORS:
        return make_error_response(ErrorCode.SERVICE_UNAVAILABLE, ErrorMessage.SERVICE_UNAVAILABLE, 503)
    except Exception:
        return make_error_response(ErrorCode.INTERNAL_SERVER_ERROR, ErrorMessage.INTERNAL_SERVER_ERROR, 500)

    return jsonify({
        "data": items,
        "meta": meta,
    }), 200


@commands_bp.route("/commands/<string:command_id>", methods=["GET"])
@require_auth
def get_command_detail(command_id: str):
    """GET /api/v1/commands/<command_id>
    Lấy chi tiết lệnh và lịch sử command_attempts (trả về 404 nếu ngoài scope thiết bị đích).
    """
    try:
        data = CommandService.get_command_by_id(
            command_id,
            actor=request.auth,
            db_session=db.session,
        )
    except DeviceScopeNotFound as e:
        return make_error_response(e.code, e.message, e.status_code)
    except AuthRequired:
        return make_error_response(ErrorCode.UNAUTHORIZED, ErrorMessage.UNAUTHORIZED, 401)
    except PermissionDenied as e:
        return make_error_response(e.code, e.message, e.status_code)
    except AuthorizationError as e:
        return make_error_response(e.code, e.message, e.status_code)
    except DB_UNAVAILABLE_ERRORS:
        return make_error_response(ErrorCode.SERVICE_UNAVAILABLE, ErrorMessage.SERVICE_UNAVAILABLE, 503)
    except Exception:
        return make_error_response(ErrorCode.INTERNAL_SERVER_ERROR, ErrorMessage.INTERNAL_SERVER_ERROR, 500)

    return jsonify({"data": data}), 200
