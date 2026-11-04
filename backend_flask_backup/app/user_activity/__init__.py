"""
User Activity Package (Thành viên 3 chủ trì, TV4 xây dựng UI)
Quản lý và tra cứu nhật ký làm việc (user_activity_logs):
Ghi nhận người dùng nào đã sử dụng chức năng gì, đối tượng nào, kết quả ra sao.
"""

from backend.app.user_activity.catalog import (
    ACTIVITY_CATALOG,
    ActivitySpec,
    get_activity_spec,
)
from backend.app.user_activity.service import UserActivityService

__all__ = [
    "ACTIVITY_CATALOG",
    "ActivitySpec",
    "get_activity_spec",
    "UserActivityService",
]
