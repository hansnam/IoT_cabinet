"""
API Views quản lý tài khoản MQTT của từng tủ mạng trên Mosquitto Dynamic Security (UI-06).
Chỉ dành cho Quản trị viên (ADMIN).
"""
import uuid

from django.core.management.base import CommandError
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditLog
from apps.audit.services import AuditService
from apps.devices.access_services import require_access_admin
from apps.devices.device_mqtt_service import (
    disable_device_mqtt,
    enable_device_mqtt,
    get_device_mqtt_metadata,
    provision_device_mqtt,
    reset_device_mqtt_password,
)
from apps.devices.models import Device


class DeviceMqttMetadataView(APIView):
    """
    GET /api/v1/devices/{id}/mqtt/
    Xem thông tin tài khoản MQTT, topic ACL và trạng thái trên Broker của tủ.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, id):
        require_access_admin(request.user)
        try:
            device = Device.objects.get(pk=id)
        except Device.DoesNotExist:
            return Response(
                {"error": {"code": "DEVICE_NOT_FOUND", "message": f"Không tìm thấy tủ mạng '{id}'."}},
                status=status.HTTP_404_NOT_FOUND
            )

        data = get_device_mqtt_metadata(device)
        return Response(data, status=status.HTTP_200_OK)


class DeviceMqttProvisionView(APIView):
    """
    POST /api/v1/devices/{id}/mqtt/provision/
    Khởi tạo tài khoản MQTT mới cho tủ trên Broker.
    Mật khẩu chỉ hiển thị một lần duy nhất trong phản hồi này.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        require_access_admin(request.user)
        try:
            device = Device.objects.get(pk=id)
        except Device.DoesNotExist:
            return Response(
                {"error": {"code": "DEVICE_NOT_FOUND", "message": f"Không tìm thấy tủ mạng '{id}'."}},
                status=status.HTTP_404_NOT_FOUND
            )

        raw_password = (request.data or {}).get('password')
        try:
            generated_password, meta = provision_device_mqtt(device, raw_password)
        except CommandError as e:
            return Response(
                {"error": {"code": "PROVISION_FAILED", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": {"code": "BROKER_ERROR", "message": f"Lỗi tương tác với Mosquitto Broker: {e}"}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        request_id = getattr(request, 'request_id', None) or str(uuid.uuid4())
        AuditService.record_event(
            function_key="DEVICE_MQTT",
            action="MQTT_PROVISIONED",
            outcome="SUCCESS",
            summary=f"Khởi tạo tài khoản MQTT cho tủ {device.id}",
            request_id=request_id,
            actor_user=request.user,
            actor_type=AuditLog.ActorType.USER,
            target_type="DEVICE",
            target_id=device.id,
            device_id=device.id,
            metadata={'username': device.id, 'device_id': device.id}
        )

        return Response({
            'success': True,
            'device_id': device.id,
            'username': device.id,
            'client_id': device.id,
            'password': generated_password,
            'status': 'REGISTERED',
            'is_disabled': True,
            'message': f"Đã khởi tạo tài khoản MQTT cho tủ '{device.id}'. Tài khoản đang ở trạng thái tạm khóa (DISABLED) để an toàn.",
            'note': "LƯU Ý: Mật khẩu này chỉ xuất hiện MỘT LẦN DUY NHẤT. Hãy sao chép để cấu hình vào firmware ESP32 của tủ trước khi bấm 'Kích hoạt tài khoản'."
        }, status=status.HTTP_201_CREATED)


class DeviceMqttEnableView(APIView):
    """
    POST /api/v1/devices/{id}/mqtt/enable/
    Kích hoạt tài khoản MQTT của tủ trên Broker, cho phép tủ kết nối và gửi số đo.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        require_access_admin(request.user)
        try:
            device = Device.objects.get(pk=id)
        except Device.DoesNotExist:
            return Response(
                {"error": {"code": "DEVICE_NOT_FOUND", "message": f"Không tìm thấy tủ mạng '{id}'."}},
                status=status.HTTP_404_NOT_FOUND
            )

        try:
            res = enable_device_mqtt(device)
        except CommandError as e:
            return Response(
                {"error": {"code": "ENABLE_FAILED", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": {"code": "BROKER_ERROR", "message": f"Lỗi tương tác với Mosquitto Broker: {e}"}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        request_id = getattr(request, 'request_id', None) or str(uuid.uuid4())
        AuditService.record_event(
            function_key="DEVICE_MQTT",
            action="MQTT_ENABLED",
            outcome="SUCCESS",
            summary=f"Kích hoạt tài khoản MQTT cho tủ {device.id}",
            request_id=request_id,
            actor_user=request.user,
            actor_type=AuditLog.ActorType.USER,
            target_type="DEVICE",
            target_id=device.id,
            device_id=device.id,
            metadata={'username': device.id, 'device_id': device.id}
        )

        return Response({
            'success': True,
            'device_id': device.id,
            'status': 'ACTIVE',
            'is_disabled': False,
            'message': f"Đã kích hoạt tài khoản MQTT của tủ '{device.id}'. Tủ mạng đã sẵn sàng kết nối."
        }, status=status.HTTP_200_OK)


class DeviceMqttDisableView(APIView):
    """
    POST /api/v1/devices/{id}/mqtt/disable/
    Vô hiệu hóa tài khoản MQTT của tủ trên Broker (ngắt kết nối thật tức thời).
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        require_access_admin(request.user)
        try:
            device = Device.objects.get(pk=id)
        except Device.DoesNotExist:
            return Response(
                {"error": {"code": "DEVICE_NOT_FOUND", "message": f"Không tìm thấy tủ mạng '{id}'."}},
                status=status.HTTP_404_NOT_FOUND
            )

        try:
            res = disable_device_mqtt(device)
        except CommandError as e:
            return Response(
                {"error": {"code": "DISABLE_FAILED", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": {"code": "BROKER_ERROR", "message": f"Lỗi tương tác với Mosquitto Broker: {e}"}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        request_id = getattr(request, 'request_id', None) or str(uuid.uuid4())
        AuditService.record_event(
            function_key="DEVICE_MQTT",
            action="MQTT_DISABLED",
            outcome="SUCCESS",
            summary=f"Vô hiệu hóa tài khoản MQTT cho tủ {device.id}",
            request_id=request_id,
            actor_user=request.user,
            actor_type=AuditLog.ActorType.USER,
            target_type="DEVICE",
            target_id=device.id,
            device_id=device.id,
            metadata={'username': device.id, 'device_id': device.id}
        )

        return Response({
            'success': True,
            'device_id': device.id,
            'status': 'LOCKED',
            'is_disabled': True,
            'message': f"Đã vô hiệu hóa tài khoản MQTT của tủ '{device.id}' trên Broker."
        }, status=status.HTTP_200_OK)


class DeviceMqttResetPasswordView(APIView):
    """
    POST /api/v1/devices/{id}/mqtt/reset-password/
    Đặt lại mật khẩu MQTT cho tủ trên Broker.
    Mật khẩu mới chỉ trả về một lần duy nhất.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, id):
        require_access_admin(request.user)
        try:
            device = Device.objects.get(pk=id)
        except Device.DoesNotExist:
            return Response(
                {"error": {"code": "DEVICE_NOT_FOUND", "message": f"Không tìm thấy tủ mạng '{id}'."}},
                status=status.HTTP_404_NOT_FOUND
            )

        raw_password = (request.data or {}).get('password')
        try:
            new_password = reset_device_mqtt_password(device, raw_password)
        except CommandError as e:
            return Response(
                {"error": {"code": "RESET_PASSWORD_FAILED", "message": str(e)}},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": {"code": "BROKER_ERROR", "message": f"Lỗi tương tác với Mosquitto Broker: {e}"}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        request_id = getattr(request, 'request_id', None) or str(uuid.uuid4())
        AuditService.record_event(
            function_key="DEVICE_MQTT",
            action="MQTT_PASSWORD_RESET",
            outcome="SUCCESS",
            summary=f"Đặt lại mật khẩu MQTT cho tủ {device.id}",
            request_id=request_id,
            actor_user=request.user,
            actor_type=AuditLog.ActorType.USER,
            target_type="DEVICE",
            target_id=device.id,
            device_id=device.id,
            metadata={'username': device.id}
        )

        return Response({
            'success': True,
            'device_id': device.id,
            'username': device.id,
            'password': new_password,
            'message': f"Đã đặt lại mật khẩu MQTT thành công cho tủ '{device.id}'.",
            'note': "LƯU Ý: Bạn cần nạp mật khẩu mới này vào firmware ESP32 của tủ để thiết bị kết nối lại được với Broker."
        }, status=status.HTTP_200_OK)
