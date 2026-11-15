"""
API Views quản lý cấu hình kết nối MQTT Broker và kiểm tra kết nối (UI-05).
Chỉ dành cho Quản trị viên (ADMIN).
"""
import uuid
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditLog
from apps.audit.services import AuditService
from apps.devices.access_services import require_access_admin
from apps.devices.broker_config import (
    get_runtime_mqtt_config,
    test_broker_connection,
    validate_broker_host,
    validate_broker_port,
    validate_ca_pem,
)
from apps.devices.crypto import encrypt_secret
from apps.devices.models import BrokerConnection


class BrokerSettingsView(APIView):
    """
    GET /api/v1/mqtt/settings/
    PATCH /api/v1/mqtt/settings/
    Quản lý cấu hình MQTT Broker tập trung tại runtime.
    Chỉ cho phép ADMIN. Không bao giờ trả về mật khẩu (chỉ trả cờ boolean _set).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        require_access_admin(request.user)

        conn = BrokerConnection.objects.first()
        fallback_cfg = get_runtime_mqtt_config(for_admin=False)
        fallback_admin = get_runtime_mqtt_config(for_admin=True)

        if not conn:
            data = {
                'host': fallback_cfg['host'],
                'port': fallback_cfg['port'],
                'use_tls': fallback_cfg['use_tls'],
                'has_ca_cert': bool(fallback_cfg.get('ca_file') or fallback_cfg.get('ca_cert_pem')),
                'ca_cert_pem': fallback_cfg.get('ca_cert_pem', ''),
                'worker_username': fallback_cfg['username'],
                'worker_password_set': bool(fallback_cfg['password']),
                'admin_username': fallback_admin['username'],
                'admin_password_set': bool(fallback_admin['password']),
                'revision': 0,
                'worker_last_seen_revision': 0,
                'worker_status': 'DISCONNECTED',
                'worker_last_error': '',
                'worker_heartbeat_at': None,
                'source': 'SETTINGS',
                'updated_at': None,
            }
            return Response(data)

        data = {
            'host': conn.host,
            'port': conn.port,
            'use_tls': conn.use_tls,
            'has_ca_cert': bool(conn.ca_cert_pem),
            'ca_cert_pem': conn.ca_cert_pem,
            'worker_username': conn.worker_username,
            'worker_password_set': bool(conn.worker_password_encrypted),
            'admin_username': conn.admin_username,
            'admin_password_set': bool(conn.admin_password_encrypted),
            'revision': conn.revision,
            'worker_last_seen_revision': conn.worker_last_seen_revision,
            'worker_status': conn.worker_status,
            'worker_last_error': conn.worker_last_error,
            'worker_heartbeat_at': conn.worker_heartbeat_at.isoformat() if conn.worker_heartbeat_at else None,
            'source': 'DATABASE',
            'updated_at': conn.updated_at.isoformat() if conn.updated_at else None,
        }
        return Response(data)

    def patch(self, request):
        require_access_admin(request.user)

        payload = request.data or {}
        changed_fields = []

        try:
            with transaction.atomic():
                conn = BrokerConnection.objects.select_for_update().first()
                if not conn:
                    conn = BrokerConnection.objects.create(
                        worker_password_encrypted=encrypt_secret(getattr(settings, 'MQTT_PASSWORD', '')),
                        admin_password_encrypted=encrypt_secret(getattr(settings, 'MQTT_ADMIN_PASSWORD', '')),
                        host=getattr(settings, 'MQTT_BROKER_HOST', '127.0.0.1'),
                        port=int(getattr(settings, 'MQTT_BROKER_PORT', 1883)),
                        worker_username=getattr(settings, 'MQTT_USERNAME', ''),
                        admin_username=getattr(settings, 'MQTT_ADMIN_USERNAME', ''),
                        use_tls=bool(getattr(settings, 'MQTT_CA_FILE', '')),
                    )

                if 'host' in payload:
                    new_host = validate_broker_host(payload['host'])
                    if conn.host != new_host:
                        conn.host = new_host
                        changed_fields.append('host')

                if 'port' in payload:
                    new_port = validate_broker_port(payload['port'])
                    if conn.port != new_port:
                        conn.port = new_port
                        changed_fields.append('port')

                if 'use_tls' in payload:
                    new_tls = bool(payload['use_tls'])
                    if conn.use_tls != new_tls:
                        conn.use_tls = new_tls
                        changed_fields.append('use_tls')

                if 'ca_cert_pem' in payload:
                    new_ca = validate_ca_pem(payload['ca_cert_pem'])
                    if conn.ca_cert_pem != new_ca:
                        conn.ca_cert_pem = new_ca
                        changed_fields.append('ca_cert_pem')

                if 'worker_username' in payload:
                    new_user = str(payload['worker_username']).strip()
                    if conn.worker_username != new_user:
                        conn.worker_username = new_user
                        changed_fields.append('worker_username')

                if payload.get('clear_worker_password'):
                    conn.worker_password_encrypted = ''
                    changed_fields.append('worker_password_cleared')
                elif payload.get('worker_password'):
                    conn.worker_password_encrypted = encrypt_secret(payload['worker_password'])
                    changed_fields.append('worker_password')

                if 'admin_username' in payload:
                    new_admin = str(payload['admin_username']).strip()
                    if conn.admin_username != new_admin:
                        conn.admin_username = new_admin
                        changed_fields.append('admin_username')

                if payload.get('clear_admin_password'):
                    conn.admin_password_encrypted = ''
                    changed_fields.append('admin_password_cleared')
                elif payload.get('admin_password'):
                    conn.admin_password_encrypted = encrypt_secret(payload['admin_password'])
                    changed_fields.append('admin_password')

                draft = {'host': conn.host, 'port': conn.port, 'use_tls': conn.use_tls,
                         'ca_cert_pem': conn.ca_cert_pem, 'worker_username': conn.worker_username,
                         'admin_username': conn.admin_username}
                from apps.devices.crypto import decrypt_secret
                draft['worker_password'] = decrypt_secret(conn.worker_password_encrypted)
                draft['admin_password'] = decrypt_secret(conn.admin_password_encrypted)
                result = test_broker_connection(draft)
                if not result['success']:
                    transaction.set_rollback(True)
                    return Response({'error': {'message': result['message']}, 'steps': result['steps']}, status=400)

                if changed_fields:
                    conn.revision += 1
                    conn.updated_by = request.user
                    conn.save()

                    request_id = getattr(request, 'request_id', None) or str(uuid.uuid4())
                    AuditService.record_event(
                        function_key="MQTT_CONFIG",
                        action="SETTINGS_UPDATED",
                        outcome="SUCCESS",
                        summary=f"Cập nhật cấu hình MQTT Broker v{conn.revision} ({', '.join(changed_fields)})",
                        request_id=request_id,
                        actor_user=request.user,
                        actor_type=AuditLog.ActorType.USER,
                        target_type="SYSTEM",
                        target_id="MQTT_BROKER",
                        metadata={'reason': 'MQTT_CONFIG_UPDATED'}
                    )
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except RuntimeError as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return self.get(request)


class BrokerTestConnectionView(APIView):
    """
    POST /api/v1/mqtt/settings/test/
    Kiểm tra bản nháp cấu hình Broker mà không lưu vào DB.
    Chỉ dành cho Quản trị viên (ADMIN).
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        require_access_admin(request.user)

        data = request.data or {}
        # Nếu password không truyền lên trong test form, thử lấy password đã lưu trong DB (nếu có)
        try:
            host = validate_broker_host(data.get('host', '127.0.0.1'))
            port = validate_broker_port(data.get('port', 1883))
            use_tls = bool(data.get('use_tls', False))
            ca_cert_pem = validate_ca_pem(data.get('ca_cert_pem', ''))
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # Trích xuất thông tin kiểm tra
        worker_user = str(data.get('worker_username', '')).strip()
        worker_pass = data.get('worker_password', '')

        admin_user = str(data.get('admin_username', '')).strip()
        admin_pass = data.get('admin_password', '')

        # Fallback lấy mật khẩu đã lưu nếu người dùng không gõ lại mật khẩu
        if not worker_pass:
            runtime_worker = get_runtime_mqtt_config(for_admin=False)
            if worker_user == runtime_worker.get('username'):
                worker_pass = runtime_worker.get('password', '')

        if not admin_pass:
            runtime_admin = get_runtime_mqtt_config(for_admin=True)
            if admin_user == runtime_admin.get('username'):
                admin_pass = runtime_admin.get('password', '')

        draft = {
            'host': host,
            'port': port,
            'use_tls': use_tls,
            'ca_cert_pem': ca_cert_pem,
            'worker_username': worker_user,
            'worker_password': worker_pass,
            'admin_username': admin_user,
            'admin_password': admin_pass,
        }

        result = test_broker_connection(draft)
        return Response(result, status=status.HTTP_200_OK)
