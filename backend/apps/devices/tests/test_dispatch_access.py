"""Kiểm tra các trường hợp quan trọng khi quyền thay đổi trước lúc worker gửi lệnh."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied
from django.core.management.base import CommandError
from django.core.management import call_command
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User, AuthSession
from apps.accounts.policies.roles import assign_role, ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER
from apps.commands.dispatch import dispatch_outbox
from apps.commands.models import Command, OutboxMessage
from apps.devices.access_services import grant_device_access
from apps.devices.broker_security import device_acl, broker_command
from apps.devices.models import Device, DeviceAccess, DeviceConfig
from apps.devices.policies import require_device_permission


class DispatchAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dispatch_test', full_name='Dispatch Test', password='TestPassword123!')
        assign_role(self.user, ROLE_OPERATOR)
        self.device = Device.objects.create(id='dispatch-cab', name='Test', status='ACTIVE')
        self.grant = DeviceAccess.objects.create(user=self.user, device=self.device, role='OPERATE')
        self.session = AuthSession.objects.create(user=self.user, session_key='dispatch-session',
            expires_at=timezone.now() + timedelta(minutes=15))
        self.command = Command.objects.create(device=self.device, actor=self.user, command_type='SET_FAN',
            payload={'type': 'SET_FAN', '_auth_session_id': str(self.session.pk)})
        self.outbox = OutboxMessage.objects.create(command=self.command, topic='unused', payload=self.command.payload)
        self.publisher = Mock(return_value=True)

    def assert_cancelled(self):
        self.assertFalse(dispatch_outbox(self.outbox.pk, self.publisher))
        self.publisher.assert_not_called()
        self.command.refresh_from_db()
        self.assertEqual(self.command.status, 'CANCELLED')

    def test_revoked_grant_blocks_pending_command(self):
        self.grant.delete()
        self.assert_cancelled()

    def test_revoked_session_blocks_pending_command(self):
        self.session.revoked_at = timezone.now()
        self.session.save()
        self.assert_cancelled()

    def test_locked_device_blocks_pending_command(self):
        self.device.status = 'LOCKED'
        self.device.save()
        self.assert_cancelled()

    def test_old_command_is_not_replayed(self):
        Command.objects.filter(pk=self.command.pk).update(created_at=timezone.now() - timedelta(seconds=61))
        self.assert_cancelled()

    def test_internal_session_id_is_not_published_and_outbox_is_idempotent(self):
        self.assertTrue(dispatch_outbox(self.outbox.pk, self.publisher))
        self.assertTrue(dispatch_outbox(self.outbox.pk, self.publisher))
        self.publisher.assert_called_once_with(self.device.pk, {'type': 'SET_FAN'})

    def test_broker_failure_keeps_outbox_pending(self):
        self.publisher.return_value = False
        self.assertFalse(dispatch_outbox(self.outbox.pk, self.publisher))
        self.outbox.refresh_from_db()
        self.assertEqual(self.outbox.status, 'PENDING')

    def test_viewer_cannot_operate_even_with_operate_grant(self):
        assign_role(self.user, ROLE_VIEWER)
        with self.assertRaises(PermissionDenied):
            require_device_permission(self.user, self.device, 'OPERATE')

    def test_expired_grant_does_not_allow_view(self):
        self.grant.expires_at = timezone.now() - timedelta(seconds=1)
        self.grant.save()
        with self.assertRaises(PermissionDenied):
            require_device_permission(self.user, self.device, 'VIEW')

    def test_grant_rolls_back_if_audit_fails(self):
        assign_role(self.user, ROLE_ADMIN)
        self.grant.delete()
        with patch('apps.devices.access_services.AuditService.record_event', side_effect=RuntimeError('audit')):
            with self.assertRaises(RuntimeError):
                grant_device_access(SimpleNamespace(user=self.user), self.user, self.device, 'VIEW')
        self.assertFalse(DeviceAccess.objects.exists())

    def test_system_resync_only_accepts_previously_applied_snapshot(self):
        DeviceConfig.objects.create(device=self.device, version=1, config_data={'max_temp': 35}, applied=True)
        self.command.actor = None
        self.command.command_type = 'APPLY_CONFIG'
        self.command.payload = {'_source': 'APPLIED_CONFIG_RESYNC', 'config_version': 1, 'config': {'max_temp': 35}}
        self.command.save()
        self.assertTrue(dispatch_outbox(self.outbox.pk, self.publisher))

    def test_system_resync_rejects_unapplied_config(self):
        DeviceConfig.objects.create(device=self.device, version=1, config_data={'max_temp': 35}, applied=False)
        self.command.actor = None
        self.command.command_type = 'APPLY_CONFIG'
        self.command.payload = {'_source': 'APPLIED_CONFIG_RESYNC', 'config_version': 1, 'config': {'max_temp': 35}}
        self.command.save()
        self.assert_cancelled()

    @patch('apps.devices.management.commands.resync_device_config.publish_command_to_broker', return_value=True)
    def test_resync_command_queues_and_dispatches_applied_config(self, publisher):
        from io import StringIO
        DeviceConfig.objects.create(device=self.device, version=1, config_data={'max_temp': 35}, applied=True)
        call_command('resync_device_config', self.device.pk, stdout=StringIO())
        command = Command.objects.filter(command_type='APPLY_CONFIG').get()
        self.assertIsNone(command.actor_id)
        self.assertEqual(command.status, 'SENT')
        self.assertEqual(publisher.call_count, 1)


class BrokerAclTests(SimpleTestCase):
    def test_device_acl_never_allows_another_device_topic(self):
        rules = device_acl('cab-01')
        allowed = [rule for rule in rules if rule['allow']]
        self.assertEqual(len(allowed), 5)
        self.assertTrue(all(rule['topic'].startswith('cabinet/v3/devices/cab-01/') for rule in allowed))
        self.assertTrue(any(rule['topic'] == '#' and not rule['allow'] for rule in rules))

    def test_topic_wildcards_are_rejected(self):
        for identifier in ('+', '#', 'cab/other', ''):
            with self.subTest(identifier=identifier), self.assertRaises(CommandError):
                device_acl(identifier)


@override_settings(MQTT_ADMIN_USERNAME='admin-test', MQTT_ADMIN_PASSWORD='not-real', MQTT_CA_FILE='')
class BrokerTransportTests(SimpleTestCase):
    def run_transport(self, response_kind):
        """Giả lập vòng kết nối/subscribe/phản hồi, không suy diễn thành test broker thật."""
        import json
        client = Mock()
        def loop_start():
            client.on_connect(client, None, {}, 0)
            client.on_subscribe(client, None, 1, [1])
        def publish(topic, payload, qos):
            command = json.loads(payload)['commands'][0]
            response = {'command': command['command'], 'correlationData': command['correlationData']}
            if response_kind == 'wrong':
                response['correlationData'] = 'another-request'
            if response_kind == 'error':
                response['error'] = 'Not authorized'
            client.on_message(client, None, SimpleNamespace(payload=json.dumps({'responses': [response]}).encode()))
        client.loop_start.side_effect = loop_start
        client.publish.side_effect = publish
        with patch('apps.devices.broker_security.mqtt.Client', return_value=client):
            # Giảm timeout trong test; Event.set vẫn hoạt động như thật.
            with patch('apps.devices.broker_security.threading.Event.wait', autospec=True,
                       side_effect=lambda event, timeout: event.is_set()):
                return broker_command('disableClient', username='cab-01')

    def test_matching_response_is_success(self):
        self.assertEqual(self.run_transport('ok'), {})

    def test_wrong_correlation_is_not_success(self):
        with self.assertRaises(CommandError):
            self.run_transport('wrong')

    def test_broker_error_is_not_success(self):
        with self.assertRaises(CommandError):
            self.run_transport('error')
