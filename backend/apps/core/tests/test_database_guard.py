"""
Test module kiểm tra chốt chặn an toàn DatabaseGuardTestRunner.
Chạy độc lập không cần kết nối database vật lý (SimpleTestCase).
"""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.core.test_runner import (
    ALLOWED_TEST_DB_NAME,
    FORBIDDEN_TEST_DATABASES,
    DatabaseGuardTestRunner,
)


class DatabaseGuardTestRunnerTests(SimpleTestCase):
    """Xác minh cơ chế chặn nghiêm ngặt của DatabaseGuardTestRunner."""

    def setUp(self):
        self.runner = DatabaseGuardTestRunner()

    def test_missing_test_db_name_is_blocked(self):
        """Xác minh nếu thiếu cấu hình TEST['NAME'] thì bị chặn ngay."""
        mock_connection = MagicMock()
        mock_connection.settings_dict = {
            'NAME': 'iot_cabinet_django',
            'TEST': {'NAME': None},
        }
        with patch('apps.core.test_runner.connections', {'default': mock_connection}):
            with self.assertRaises(RuntimeError) as context:
                self.runner.setup_databases()
            self.assertIn("chưa được cấu hình TEST['NAME']", str(context.exception))

    def test_disallowed_test_db_name_is_blocked(self):
            """Xác minh tên database test khác ALLOWED_TEST_DB_NAME sẽ bị từ chối."""
            mock_connection = MagicMock()
            mock_connection.settings_dict = {
                'NAME': 'iot_cabinet_django',
                'TEST': {'NAME': 'other_test_db'},
            }
            with patch('apps.core.test_runner.connections', {'default': mock_connection}):
                with self.assertRaises(RuntimeError) as context:
                    self.runner.setup_databases()
                self.assertIn("Hệ thống chỉ cho phép chạy test trên database được quy định", str(context.exception))

    def test_test_db_same_as_default_db_is_blocked(self):
            """Xác minh trường hợp tên DB test trùng với DB chính sẽ bị chặn đứng."""
            mock_connection = MagicMock()
            mock_connection.settings_dict = {    
                'NAME': ALLOWED_TEST_DB_NAME,
                'TEST': {'NAME': ALLOWED_TEST_DB_NAME},
            }
            with patch('apps.core.test_runner.connections', {'default': mock_connection}):
                with self.assertRaises(RuntimeError) as context:
                    self.runner.setup_databases()
                self.assertIn("trùng hoàn toàn với database chính", str(context.exception))
    
    def test_forbidden_databases_are_blocked(self):
        """Xác minh mọi database trong danh sách cấm đều bị chặn."""
        for forbidden_db in FORBIDDEN_TEST_DATABASES:
            mock_connection = MagicMock()
            mock_connection.settings_dict = {
                'NAME': 'some_safe_db',
                'TEST': {'NAME': forbidden_db}
            }
            with patch('apps.core.test_runner.connections', {'default': mock_connection}):
                with self.assertRaises(RuntimeError) as context:
                    self.runner.setup_databases()
                self.assertIn('[DATABASE GUARD ERROR]', str(context.exception))

    def test_allowed_test_database_proceeds_to_parent(self):
        """Xác minh cấu hình chuẩn (iot_cabinet_django_test khác iot_cabinet_django) vượt qua guard."""
        mock_connection = MagicMock()
        mock_connection.settings_dict = {
            'NAME': 'iot_cabinet_django',
            'TEST': {'NAME': ALLOWED_TEST_DB_NAME}
        }
        with (
            patch("apps.core.test_runner.connections", {"default": mock_connection}),
            patch("django.test.runner.DiscoverRunner.setup_databases", return_value=["mock_db"]),
        ):
            result = self.runner.setup_databases()
            self.assertEqual(result, ['mock_db'])        