"""
Views quản trị phân quyền thiết bị (Device Access REST API).
Chỉ Quản trị viên (ADMIN) mới được xem, cấp hoặc thu hồi quyền truy cập tủ.
Mọi mutation đều gọi service access_services (grant_device_access, revoke_device_access).
"""
from typing import Any, ClassVar, cast

from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.policies.roles import ROLE_ADMIN, get_user_role
from apps.accounts.security.authentication import RegistrySessionAuthentication
from apps.core.errors import ErrorCode, make_error_response
from apps.devices.access_services import grant_device_access, revoke_device_access
from apps.devices.models import Device, DeviceAccess
from apps.devices.serializers import DeviceAccessSerializer, GrantDeviceAccessSerializer, UpdateDeviceAccessSerializer


def ensure_admin(request: Request) -> str | None:
    user = cast(User, request.user)
    if not user.is_active or user.must_change_password or get_user_role(user) != ROLE_ADMIN:
        return "Chỉ Quản trị viên (ADMIN) đang hoạt động mới được quản lý quyền truy cập tủ."
    return None


class DeviceAccessListView(APIView):
    """
    API Danh sách và Cấp quyền truy cập thiết bị:
    - GET  /api/v1/device-access/  : Danh sách phân quyền (hỗ trợ lọc ?user_id= & ?device_id=)
    - POST /api/v1/device-access/  : Cấp hoặc cập nhật quyền truy cập tủ cho người dùng
    """
    authentication_classes: ClassVar[list] = [RegistrySessionAuthentication]
    permission_classes: ClassVar[list] = [IsAuthenticated]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        request_id = getattr(request._request, "request_id", None)
        err = ensure_admin(request)
        if err:
            return make_error_response(ErrorCode.FORBIDDEN, err, 403, request_id)

        qs = DeviceAccess.objects.select_related("user", "device", "granted_by").all().order_by("-granted_at")

        user_id = request.query_params.get("user_id")
        if user_id:
            qs = qs.filter(user_id=user_id)

        device_id = request.query_params.get("device_id")
        if device_id:
            qs = qs.filter(device_id=device_id)

        serializer = DeviceAccessSerializer(qs, many=True)
        return Response({"data": serializer.data, "total": qs.count()}, status=status.HTTP_200_OK)

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        request_id = getattr(request._request, "request_id", None)
        err = ensure_admin(request)
        if err:
            return make_error_response(ErrorCode.FORBIDDEN, err, 403, request_id)

        serializer = GrantDeviceAccessSerializer(data=request.data)
        if not serializer.is_valid():
            first_err = next(iter(serializer.errors.values()))[0] if serializer.errors else "Dữ liệu không hợp lệ."
            return make_error_response(ErrorCode.VALIDATION_ERROR, str(first_err), 400, request_id)

        data = cast(dict, serializer.validated_data)
        user = get_object_or_404(User, pk=data["user_id"])
        device = get_object_or_404(Device, pk=data["device_id"])

        try:
            grant = grant_device_access(
                request=request._request,
                user=user,
                device=device,
                role=data["role"],
                expires_at=data.get("expires_at"),
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            return make_error_response(ErrorCode.VALIDATION_ERROR, msg, 400, request_id)
        except PermissionDenied as exc:
            return make_error_response(ErrorCode.FORBIDDEN, str(exc), 403, request_id)

        out_serializer = DeviceAccessSerializer(grant)
        return Response(
            {"data": out_serializer.data, "message": "Đã cấp quyền truy cập tủ thành công."},
            status=status.HTTP_201_CREATED
        )


class DeviceAccessDetailView(APIView):
    """
    API Cập nhật và Thu hồi quyền truy cập thiết bị:
    - PATCH  /api/v1/device-access/{id}/ : Sửa mức quyền (VIEW/OPERATE) hoặc gia hạn
    - DELETE /api/v1/device-access/{id}/ : Thu hồi quyền truy cập tủ (xóa DeviceAccess, có audit)
    """
    authentication_classes: ClassVar[list] = [RegistrySessionAuthentication]
    permission_classes: ClassVar[list] = [IsAuthenticated]

    def patch(self, request: Request, pk: int, *args: Any, **kwargs: Any) -> Response:
        request_id = getattr(request._request, "request_id", None)
        err = ensure_admin(request)
        if err:
            return make_error_response(ErrorCode.FORBIDDEN, err, 403, request_id)

        grant = get_object_or_404(DeviceAccess, pk=pk)

        serializer = UpdateDeviceAccessSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = serializer.validated_data.get("role", grant.role)
        expires_at = serializer.validated_data.get("expires_at", grant.expires_at)

        try:
            updated_grant = grant_device_access(
                request=request._request,
                user=grant.user,
                device=grant.device,
                role=role,
                expires_at=expires_at,
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            return make_error_response(ErrorCode.VALIDATION_ERROR, msg, 400, request_id)
        except PermissionDenied as exc:
            return make_error_response(ErrorCode.FORBIDDEN, str(exc), 403, request_id)

        out_serializer = DeviceAccessSerializer(updated_grant)
        return Response(
            {"data": out_serializer.data, "message": "Đã cập nhật quyền truy cập thành công."},
            status=status.HTTP_200_OK
        )

    def delete(self, request: Request, pk: int, *args: Any, **kwargs: Any) -> Response:
        request_id = getattr(request._request, "request_id", None)
        err = ensure_admin(request)
        if err:
            return make_error_response(ErrorCode.FORBIDDEN, err, 403, request_id)

        grant = get_object_or_404(DeviceAccess, pk=pk)

        try:
            revoke_device_access(request=request._request, grant=grant)
        except PermissionDenied as exc:
            return make_error_response(ErrorCode.FORBIDDEN, str(exc), 403, request_id)

        return Response(
            {"message": "Đã thu hồi quyền truy cập tủ thành công."},
            status=status.HTTP_200_OK
        )
