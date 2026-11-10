"""
Profile Views Module
Cung cấp API xem/sửa hồ sơ và đổi mật khẩu:
- GET/PATCH /api/v1/profile/me/ (kèm alias /api/v1/auth/me/)
- POST      /api/v1/profile/change-password/ (kèm alias /api/v1/auth/change-password/)
"""
from typing import Any, ClassVar, cast

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.security.authentication import RegistrySessionAuthentication
from apps.accounts.models import User
from apps.accounts.serializers.profile import (
    ChangePasswordRequestSerializer,
    UpdateProfileRequestSerializer,
    UserProfileSerializer,
)
from apps.accounts.services.users import change_password, update_profile
from apps.core.errors import ErrorCode, make_error_response


class ProfileMeView(APIView):
    """
    API endpoint xem và cập nhật hồ sơ cá nhân của chính người dùng hiện tại.
    """
    authentication_classes: ClassVar[list] = [RegistrySessionAuthentication]
    permission_classes: ClassVar[list] = [IsAuthenticated]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = UserProfileSerializer(request.user, context={"request": request})
        return Response({"data": serializer.data}, status=status.HTTP_200_OK)

    def patch(self, request: Request, *args: Any, **kwargs: Any) -> Response | JsonResponse:
        request_id = getattr(request._request, "request_id", None)

        serializer = UpdateProfileRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return make_error_response(
                ErrorCode.VALIDATION_ERROR,
                "Dữ liệu cập nhật hồ sơ không hợp lệ.",
                400,
                request_id
            )

        validated_data = cast(dict, serializer.validated_data)
        full_name = validated_data["full_name"]

        try:
            updated_user = update_profile(
                user=cast(User, request.user),
                full_name=full_name,
                request=request._request
            )
        except PermissionDenied as exc:
            return make_error_response(ErrorCode.FORBIDDEN, str(exc), 403, request_id)

        resp_serializer = UserProfileSerializer(updated_user, context={"request": request})
        return Response({"data": resp_serializer.data}, status=status.HTTP_200_OK)


class ChangePasswordView(APIView):
    """
    API endpoint đổi mật khẩu:
    - Bắt buộc xác thực và kiểm tra CSRF.
    - Rate limit độc lập qua PostgreSQL advisory lock.
    - Đổi mật khẩu thành công: thu hồi toàn bộ session cũ, logout và yêu cầu đăng nhập lại.
    """
    authentication_classes: ClassVar[list] = [RegistrySessionAuthentication]
    permission_classes: ClassVar[list] = [IsAuthenticated]

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response | JsonResponse:
        request_id = getattr(request._request, "request_id", None)

        serializer = ChangePasswordRequestSerializer(data=request.data, context={"user": request.user})
        if not serializer.is_valid():
            return make_error_response(
                ErrorCode.VALIDATION_ERROR,
                "Dữ liệu đổi mật khẩu không hợp lệ.",
                400,
                request_id
            )

        validated_data = cast(dict, serializer.validated_data)
        old_password = validated_data["old_password"]
        new_password = validated_data["new_password"]

        try:
            updated_user, error_code, retry_after = change_password(
                user=cast(User, request.user),
                old_password=old_password,
                new_password=new_password,
                request=request._request
            )
        except PermissionDenied as exc:
            return make_error_response(ErrorCode.FORBIDDEN, str(exc), 403, request_id)
        except ValueError as exc:
            return make_error_response(ErrorCode.VALIDATION_ERROR, str(exc), 400, request_id)

        if error_code == "RATE_LIMITED":
            resp = make_error_response(
                ErrorCode.RATE_LIMITED,
                f"Quá nhiều lần thử đổi mật khẩu thất bại. Vui lòng thử lại sau {retry_after} giây.",
                429,
                request_id
            )
            resp["Retry-After"] = str(retry_after)
            return resp

        if error_code == "INVALID_CREDENTIALS" or not updated_user:
            return make_error_response(
                ErrorCode.NOT_AUTHENTICATED,
                "Mật khẩu hiện tại không chính xác.",
                401,
                request_id
            )

        return Response(
            {
                "message": "Đổi mật khẩu thành công. Vui lòng đăng nhập lại.",
                "data": {
                    "login_required": True,
                },
            },
            status=status.HTTP_200_OK
        )
