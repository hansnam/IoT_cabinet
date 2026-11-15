"""
Views quản trị tài khoản người dùng (User Management REST API).
Chỉ cho phép tài khoản có vai trò ADMIN đang hoạt động thao tác.
Mọi mutation đều gọi service tập trung manage_user() để đảm bảo:
- Khóa advisory transaction chống xung đột last-admin
- Xác thực mật khẩu theo chuẩn Django
- Thu hồi phiên tự động khi đổi quyền / khóa / đổi mật khẩu
- Ghi nhận AuditLog đầy đủ
"""
import uuid
from typing import Any, ClassVar, cast

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.policies.roles import ROLE_ADMIN, get_user_role
from apps.accounts.security.authentication import RegistrySessionAuthentication
from apps.accounts.serializers.management import (
    CreateUserManagementSerializer,
    UpdateUserManagementSerializer,
    UserManagementSerializer,
)
from apps.accounts.services.administration import manage_user
from apps.core.errors import ErrorCode, make_error_response


def ensure_admin_actor(request: Request) -> str | None:
    """Xác thực người gửi yêu cầu là Quản trị viên (ADMIN) đang hoạt động."""
    user = cast(User, request.user)
    if not user.is_active or user.must_change_password or get_user_role(user) != ROLE_ADMIN:
        return "Yêu cầu tài khoản Quản trị viên (ADMIN) đang hoạt động và đã đổi mật khẩu."
    return None


class UserManagementListView(APIView):
    """
    API Danh sách và Tạo tài khoản người dùng:
    - GET  /api/v1/management/users/  : Tra cứu danh sách tài khoản (hỗ trợ search, role, status)
    - POST /api/v1/management/users/  : Tạo mới tài khoản với mật khẩu tạm
    """
    authentication_classes: ClassVar[list] = [RegistrySessionAuthentication]
    permission_classes: ClassVar[list] = [IsAuthenticated]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        request_id = getattr(request._request, "request_id", None)
        admin_err = ensure_admin_actor(request)
        if admin_err:
            return make_error_response(ErrorCode.FORBIDDEN, admin_err, 403, request_id)

        qs = User.objects.all().order_by("-created_at")

        # Tìm kiếm theo tên đăng nhập, họ tên hoặc email
        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(username__icontains=search) |
                Q(full_name__icontains=search) |
                Q(email__icontains=search)
            )

        # Lọc theo vai trò (ADMIN / OPERATOR / VIEWER)
        role = request.query_params.get("role", "").strip().upper()
        if role in ("ADMIN", "OPERATOR", "VIEWER"):
            qs = qs.filter(groups__name=role)

        # Lọc theo trạng thái tài khoản (ACTIVE / LOCKED / ARCHIVED)
        status_filter = request.query_params.get("status", "").strip().upper()
        if status_filter in User.Status.values:
            qs = qs.filter(status=status_filter)

        serializer = UserManagementSerializer(qs, many=True)
        return Response({"data": serializer.data, "total": qs.count()}, status=status.HTTP_200_OK)

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        request_id = getattr(request._request, "request_id", None)
        admin_err = ensure_admin_actor(request)
        if admin_err:
            return make_error_response(ErrorCode.FORBIDDEN, admin_err, 403, request_id)

        serializer = CreateUserManagementSerializer(data=request.data)
        if not serializer.is_valid():
            first_err = next(iter(serializer.errors.values()))[0] if serializer.errors else "Dữ liệu không hợp lệ."
            return make_error_response(ErrorCode.VALIDATION_ERROR, str(first_err), 400, request_id)

        data = cast(dict, serializer.validated_data)

        try:
            new_user = manage_user(
                request=request._request,
                target_id=None,
                username=data["username"],
                full_name=data["full_name"],
                email=data.get("email", ""),
                role=data["role"],
                status=data["status"],
                temporary_password=data["temporary_password"],
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            return make_error_response(ErrorCode.VALIDATION_ERROR, msg, 400, request_id)
        except PermissionDenied as exc:
            return make_error_response(ErrorCode.FORBIDDEN, str(exc), 403, request_id)

        out_serializer = UserManagementSerializer(new_user)
        return Response(
            {"data": out_serializer.data, "message": "Đã tạo tài khoản người dùng thành công."},
            status=status.HTTP_201_CREATED
        )


class UserManagementDetailView(APIView):
    """
    API Chi tiết và Cập nhật tài khoản người dùng:
    - GET   /api/v1/management/users/{id}/ : Xem chi tiết tài khoản
    - PATCH /api/v1/management/users/{id}/ : Cập nhật họ tên, email, vai trò, trạng thái, đặt lại mật khẩu tạm
    """
    authentication_classes: ClassVar[list] = [RegistrySessionAuthentication]
    permission_classes: ClassVar[list] = [IsAuthenticated]

    def get(self, request: Request, pk: uuid.UUID, *args: Any, **kwargs: Any) -> Response:
        request_id = getattr(request._request, "request_id", None)
        admin_err = ensure_admin_actor(request)
        if admin_err:
            return make_error_response(ErrorCode.FORBIDDEN, admin_err, 403, request_id)

        target_user = get_object_or_404(User, pk=pk)
        serializer = UserManagementSerializer(target_user)
        return Response({"data": serializer.data}, status=status.HTTP_200_OK)

    def patch(self, request: Request, pk: uuid.UUID, *args: Any, **kwargs: Any) -> Response:
        request_id = getattr(request._request, "request_id", None)
        admin_err = ensure_admin_actor(request)
        if admin_err:
            return make_error_response(ErrorCode.FORBIDDEN, admin_err, 403, request_id)

        target_user = get_object_or_404(User, pk=pk)

        serializer = UpdateUserManagementSerializer(data=request.data)
        if not serializer.is_valid():
            first_err = next(iter(serializer.errors.values()))[0] if serializer.errors else "Dữ liệu không hợp lệ."
            return make_error_response(ErrorCode.VALIDATION_ERROR, str(first_err), 400, request_id)

        data = cast(dict, serializer.validated_data)
        changes = {}
        if "full_name" in data:
            changes["full_name"] = data["full_name"]
        if "email" in data:
            changes["email"] = data["email"]
        if "role" in data:
            changes["role"] = data["role"]
        if "status" in data:
            changes["status"] = data["status"]
        if data.get("temporary_password"):
            changes["temporary_password"] = data["temporary_password"]

        try:
            updated_user = manage_user(
                request=request._request,
                target_id=target_user.pk,
                **changes
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            return make_error_response(ErrorCode.VALIDATION_ERROR, msg, 400, request_id)
        except PermissionDenied as exc:
            return make_error_response(ErrorCode.FORBIDDEN, str(exc), 403, request_id)

        out_serializer = UserManagementSerializer(updated_user)
        return Response(
            {"data": out_serializer.data, "message": "Đã cập nhật thông tin tài khoản thành công."},
            status=status.HTTP_200_OK
        )
