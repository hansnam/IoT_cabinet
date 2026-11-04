"""
Device Read Routes Module (Thành viên 2 phụ trách - Pha B)
Cung cấp các endpoint REST API trong Blueprint 'devices' với tiền tố /api/v1/devices:
1. GET /api/v1/devices: Danh sách thiết bị trong phạm vi quyền (SQL scope).
2. GET /api/v1/devices/<device_id>: Chi tiết thiết bị.
3. GET /api/v1/devices/<device_id>/latest: Trạng thái đo lường mới nhất theo profile.
4. GET /api/v1/devices/<device_id>/shadow: Trạng thái desired/reported shadow.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError as SQLAlchemyTimeoutError

from backend.app.devices.schemas import (
    DeviceQueryValidationError,
    parse_device_list_query_params,
)
from backend.app.devices.service import DeviceReadService
from backend.app.extensions import db
from backend.app.permissions.decorators import require_permission
from backend.app.permissions.service import (
    AuthorizationError,
    AuthRequired,
    DeviceScopeNotFound,
    PermissionDenied,
)
from backend.app.security.errors import make_error_response
from backend.app.security.messages import ErrorCode, ErrorMessage
from backend.app.shadows.service import ShadowService


DB_UNAVAILABLE_ERRORS = (OperationalError, InterfaceError, SQLAlchemyTimeoutError)

devices_bp = Blueprint("devices", __name__, url_prefix="/api/v1/devices")


@devices_bp.route("", methods=["GET"])
@require_permission("device.read")
def list_devices():
    """GET /api/v1/devices
    Lấy danh sách các thiết bị thuộc phạm vi quyền của người dùng hiện tại.
    """
    try:
        params = parse_device_list_query_params(request.args)
    except DeviceQueryValidationError:
        return make_error_response(ErrorCode.BAD_REQUEST, ErrorMessage.BAD_REQUEST, 400)

    try:
        items, meta = DeviceReadService.list_devices(
            params,
            actor=request.auth,
            db_session=db.session,
        )
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


@devices_bp.route("/<string:device_id>", methods=["GET"])
@require_permission("device.read", device_id_param="device_id")
def get_device(device_id: str):
    """GET /api/v1/devices/<device_id>
    Lấy chi tiết thiết bị theo ID (trả về 404 nếu không tồn tại hoặc ngoài VIEW scope).
    """
    try:
        data = DeviceReadService.get_device(
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


@devices_bp.route("/<string:device_id>/latest", methods=["GET"])
@require_permission("telemetry.read", device_id_param="device_id")
def get_device_latest(device_id: str):
    """GET /api/v1/devices/<device_id>/latest
    Lấy số đo cảm biến và trạng thái mới nhất của thiết bị.
    """
    try:
        data = DeviceReadService.get_device_latest(
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


@devices_bp.route("/<string:device_id>/shadow", methods=["GET"])
@require_permission("device.read", device_id_param="device_id")
def get_device_shadow(device_id: str):
    """GET /api/v1/devices/<device_id>/shadow
    Lấy trạng thái desired/reported shadow của thiết bị.
    """
    try:
        data = ShadowService.get_device_shadow(
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

