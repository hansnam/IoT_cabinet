"""
Management Command: seed_roles
Khởi tạo và đồng bộ 3 Vai trò hệ thống: ADMIN, OPERATOR, VIEWER cùng bộ quyền tương ứng.
Lệnh này có tính chất Idempotent: có thể chạy nhiều lần an toàn mà không làm nhân bản dữ liệu.
Nếu thiếu bất kỳ permission bắt buộc nào trong database (chưa migrate), lệnh ném CommandError
và rollback toàn bộ transaction để tránh tình trạng hệ thống rơi vào ma trận phân quyền bị thiếu.
"""
from typing import Any

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.policies.catalog import get_permission_for_action
from apps.accounts.policies.roles import ALL_ROLES, ROLE_PERMISSIONS


class Command(BaseCommand):
    help = "Khởi tạo và đồng bộ 3 nhóm vai trò (ADMIN, OPERATOR, VIEWER) và gán quyền hạn chuẩn."

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write("Bắt đầu khởi tạo và đồng bộ các vai trò hệ thống...")
        
        with transaction.atomic():
            for role_name in ALL_ROLES:
                group, created = Group.objects.get_or_create(name=role_name)
                action_list = ROLE_PERMISSIONS.get(role_name, [])

                # Thu thập các đối tượng Permission của Django cho danh sách action
                target_permissions = []
                for action in action_list:
                    perm_str = get_permission_for_action(action)
                    app_label, codename = perm_str.split(".", 1)
                    perm = Permission.objects.filter(
                        content_type__app_label=app_label,
                        codename=codename,
                    ).first()

                    if not perm:
                        raise CommandError(
                            f"Thiếu permission bắt buộc '{perm_str}' cho action '{action}'. "
                            "Toàn bộ thao tác seed_roles bị hủy. Vui lòng chạy 'python manage.py migrate' trước."
                        )

                    target_permissions.append(perm)

                # Đồng bộ quyền hạn vào Group (Idempotent: set() cập nhật danh sách chuẩn)
                group.permissions.set(target_permissions)

                status_label = "Tạo mới" if created else "Đồng bộ"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  - [{status_label}] Nhóm '{role_name}': Đã gán {len(target_permissions)} quyền hạn."
                    )
                )

        self.stdout.write(self.style.SUCCESS("Hoàn tất đồng bộ các vai trò và quyền hạn."))
