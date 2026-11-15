"""
Database Guard Test Runner Module
Bảo vệ toàn vẹn dữ liệu: Chặn đứng quá trình kiểm thử nếu phát hiện nguy cơ
"""

from django.db import connections
from django.test.runner import DiscoverRunner

ALLOWED_TEST_DB_NAME = 'iot_cabinet_django_test'

FORBIDDEN_TEST_DATABASES = {
    'iot_cabinet',           # Database nghiệp vụ Flask cũ
    'iot_cabinet_test',      # Database test Flask cũ
    'iot_cabinet_django',    # Database nghiệp vụ Django chính (dev / production)
}


class DatabaseGuardTestRunner(DiscoverRunner):
    """
    Test Runner mở rộng từ DiscoverRunner của Django.
    Trước khi tạo hoặc dọn dẹp (flush/drop) database test, setup_databases sẽ:
    1. Kiểm tra cấu hình database test có tồn tại hay không.
    2. Bắt buộc tên database test phải là 'iot_cabinet_django_test'.
    3. Bắt buộc tên database test phải KHÁC tên database chính (default connection).
    4. Chặn tuyệt đối nếu database test trùng với bất kỳ database nào trong FORBIDDEN_TEST_DATABASES.
    """

    def setup_databases(self, **kwargs):
        for alias in connections:
            connection = connections[alias]
            test_db_name = connection.settings_dict.get('TEST', {}).get('NAME')
            default_db_name = connection.settings_dict.get('NAME')

            # 1. Chặn nếu thiếu cấu hình tên database test
            if not test_db_name:
                raise RuntimeError(
                    f"[DATABASE GUARD ERROR] Connection '{alias}' chưa được cấu hình TEST['NAME']. "
                    f"Bắt buộc phải chỉ định tên database test cụ thể để tránh xóa nhầm dữ liệu."
                )

            # 2. Bắt buộc tên database test phải là iot_cabinet_django_test
            if test_db_name != ALLOWED_TEST_DB_NAME:
                raise RuntimeError(
                    f"[DATABASE GUARD ERROR] Tên database test '{test_db_name}' không hợp lệ. "
                    f"Hệ thống chỉ cho phép chạy test trên database được quy định: '{ALLOWED_TEST_DB_NAME}'."
                )

            # 3. Bắt buộc tên DB test phải khác DB chính
            if test_db_name == default_db_name:
                raise RuntimeError(
                    f"[DATABASE GUARD ERROR] Tên database test '{test_db_name}' trùng hoàn toàn với "
                    f"database chính '{default_db_name}'. Dừng ngay lập tức để chống xóa dữ liệu thật."
                )

            # 4. Chặn nếu nằm trong danh sách cấm
            if test_db_name in FORBIDDEN_TEST_DATABASES:
                raise RuntimeError(
                    f"[DATABASE GUARD ERROR] Nguy hiểm! Database test '{test_db_name}' "
                    f"nằm trong danh sách cấm ({FORBIDDEN_TEST_DATABASES})."
                )

        return super().setup_databases(**kwargs)