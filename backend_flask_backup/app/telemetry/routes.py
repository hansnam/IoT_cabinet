"""
Telemetry Routes Module (Thành viên 2 phụ trách - Pha B)
Cung cấp các endpoint REST API trong Blueprint 'telemetry' với tiền tố /api/v1/devices:
1. GET /api/v1/devices/<device_id>/telemetry: Lịch sử telemetry theo khoảng [from, to).
2. GET /api/v1/devices/<device_id>/statistics: Thống kê min/max/avg/count và coverage/gaps.
3. GET /api/v1/devices/<device_id>/telemetry/export: Xuất dữ liệu lịch sử ra CSV.
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError as SQLAlchemyTimeoutError

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
from backend.app.telemetry.schemas import (
    TelemetryQueryValidationError,
    parse_telemetry_export_params,
    parse_telemetry_history_params,
    parse_telemetry_stats_params,
)
from backend.app.telemetry.service import TelemetryService


DB_UNAVAILABLE_ERRORS = (OperationalError, InterfaceError, SQLAlchemyTimeoutError)

telemetry_bp = Blueprint("telemetry", __name__, url_prefix="/api/v1/devices")


@telemetry_bp.route("/<string:device_id>/telemetry", methods=["GET"])
@require_permission("telemetry.read", device_id_param="device_id")
def list_device_telemetry(device_id: str):
    """GET /api/v1/devices/<device_id>/telemetry
    Truy vấn lịch sử telemetry theo khoảng [from, to) (tối đa 31 ngày, phân trang).
    """
    try:
        params = parse_telemetry_history_params(request.args)
    except TelemetryQueryValidationError:
        return make_error_response(ErrorCode.BAD_REQUEST, ErrorMessage.BAD_REQUEST, 400)

    try:
        items, meta = TelemetryService.query_history(
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


@telemetry_bp.route("/<string:device_id>/statistics", methods=["GET"])
@require_permission("telemetry.read", device_id_param="device_id")
def get_device_statistics(device_id: str):
    """GET /api/v1/devices/<device_id>/statistics
    Tính thống kê min/max/avg/count và coverage/gaps cho thiết bị trong khoảng [from, to).
    """
    try:
        params = parse_telemetry_stats_params(request.args)
    except TelemetryQueryValidationError:
        return make_error_response(ErrorCode.BAD_REQUEST, ErrorMessage.BAD_REQUEST, 400)

    try:
        data = TelemetryService.query_statistics(
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

    return jsonify({"data": data}), 200


@telemetry_bp.route("/<string:device_id>/telemetry/export", methods=["GET"])
@require_permission("telemetry.export", device_id_param="device_id")
def export_device_telemetry(device_id: str):
    """GET /api/v1/devices/<device_id>/telemetry/export
    Xuất dữ liệu lịch sử telemetry của thiết bị ra CSV kèm ghi nhận nhật ký làm việc.
    """
    try:
        params = parse_telemetry_export_params(request.args)
    except TelemetryQueryValidationError:
        return make_error_response(ErrorCode.BAD_REQUEST, ErrorMessage.BAD_REQUEST, 400)

    try:
        csv_content = TelemetryService.export_csv(
            device_id,
            params,
            actor=request.auth,
            db_session=db.session,
        )
    except TelemetryQueryValidationError:
        try:
            db.session.rollback()
        except Exception:
            pass
        return make_error_response(ErrorCode.BAD_REQUEST, ErrorMessage.BAD_REQUEST, 400)
    except DeviceScopeNotFound as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return make_error_response(e.code, e.message, e.status_code)
    except AuthRequired:
        try:
            db.session.rollback()
        except Exception:
            pass
        return make_error_response(ErrorCode.UNAUTHORIZED, ErrorMessage.UNAUTHORIZED, 401)
    except PermissionDenied as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return make_error_response(e.code, e.message, e.status_code)
    except AuthorizationError as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return make_error_response(e.code, e.message, e.status_code)
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

    safe_filename = "".join(c for c in device_id if c.isalnum() or c in ("-", "_")) or "device"
    return Response(
        csv_content,
        status=200,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="telemetry_{safe_filename}.csv"',
            "Cache-Control": "no-store",
        },
    )

