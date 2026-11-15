"""
Authentication Views Module
Cung cấp các API endpoint quản lý phiên đăng nhập của hệ thống IoT Cabinet:
- GET  /api/v1/auth/csrf/: Cung cấp token CSRF và thiết lập cookie csrftoken.
- POST /api/v1/auth/login/: Đăng nhập có kiểm tra CSRF, rate limit, xoay vòng session & CSRF token.
- POST /api/v1/auth/logout/: Đăng xuất, thu hồi phiên trong registry và hủy session cookie.
"""
from typing import Any, ClassVar, cast

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse  # Thêm import này
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.security.authentication import RegistrySessionAuthentication
from apps.accounts.serializers.auth import LoginRequestSerializer, UserSummarySerializer
from apps.accounts.services.auth import (
    authenticate_credentials,
    establish_session,
    logout_session,
)
from apps.core.errors import ErrorCode, make_error_response


class CsrfTokenView(APIView):
    authentication_classes: ClassVar[list] = [] 
    permission_classes: ClassVar[list] = [AllowAny]

    @method_decorator(ensure_csrf_cookie)
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        csrf_token = get_token(request._request)
        return Response({"csrf_token": csrf_token}, status=status.HTTP_200_OK)


class LoginView(APIView):
    authentication_classes: ClassVar[list] = []
    permission_classes: ClassVar[list] = [AllowAny]

    @method_decorator(csrf_protect)
    # Sửa: Thêm JsonResponse vào return type
    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response | JsonResponse:
        request_id = getattr(request._request, "request_id", None)

        # 1. Kiểm tra payload gửi lên
        serializer = LoginRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return make_error_response(
                ErrorCode.VALIDATION_ERROR,
                "Dữ liệu đăng nhập không hợp lệ.",
                400,
                request_id
            )

        # Sửa: Dùng cast để Pylance hiểu validated_data không còn None
        validated_data = cast(dict, serializer.validated_data)
        username = validated_data.get("username", "")
        password = validated_data.get("password", "")

        # 2. Xác thực credentials và kiểm tra Rate Limiter
        user, error_code, retry_after = authenticate_credentials(
            request._request,
            username=username,
            password=password
        )

        if error_code == "RATE_LIMITED":
            resp = make_error_response(
                ErrorCode.RATE_LIMITED,
                f"Quá nhiều lần thử đăng nhập thất bại. Vui lòng thử lại sau {retry_after} giây.",
                429,
                request_id
            )
            resp["Retry-After"] = str(retry_after)
            return resp

        if not user:
            # Thông báo lỗi chung bảo đảm an toàn chống dò quét username
            return make_error_response(
                ErrorCode.NOT_AUTHENTICATED,
                "Tên đăng nhập hoặc mật khẩu không chính xác.",
                401,
                request_id
            )

        # 3. Thiết lập phiên làm việc chính thức trong transaction atomic an toàn
        try:
            auth_session = establish_session(request._request, user)
        except PermissionDenied:
            return make_error_response(
                ErrorCode.NOT_AUTHENTICATED,
                "Tên đăng nhập hoặc mật khẩu không chính xác.",
                401,
                request_id
            )

        # 4. Trả về thông tin người dùng và metadata phiên
        user_data = UserSummarySerializer(user).data
        return Response(
            {
                "message": "Đăng nhập thành công.",
                "user": user_data,
                "session": {
                    "session_id": str(auth_session.id),
                    "expires_at": auth_session.expires_at.isoformat(),
                },
            },
            status=status.HTTP_200_OK
        )


class LogoutView(APIView):
    authentication_classes: ClassVar[list] = [RegistrySessionAuthentication]
    permission_classes: ClassVar[list] = [IsAuthenticated]

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        logout_session(request._request)
        return Response(
            {"message": "Đăng xuất thành công."},
            status=status.HTTP_200_OK
        )