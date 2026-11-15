"""
Management Command: bootstrap_admin
Khởi tạo tài khoản quản trị viên đầu tiên (Bootstrap Admin) một cách an toàn và bảo mật:
- Kiểm tra tính hợp lệ của username canonical (3-64 ký tự).
- Không nhận mật khẩu qua tham số CLI argument (--password) để chống lộ qua process table / shell history (Rule 17).
  Mật khẩu được nạp qua biến môi trường DJANGO_BOOTSTRAP_ADMIN_PASSWORD / BOOTSTRAP_ADMIN_PASSWORD
  hoặc nhập qua dấu nhắc ẩn (getpass.getpass) khi chạy tương tác.
- Kiểm tra mật khẩu qua hệ thống AUTH_PASSWORD_VALIDATORS của Django (12-128 ký tự).
- Idempotent: Nếu tài khoản admin đã tồn tại, lệnh kết thúc an toàn, không thay đổi mật khẩu và không tự ý nâng quyền.
- Tài khoản được thiết lập: status=ACTIVE, must_change_password=True, is_staff=True, is_superuser=False, thuộc nhóm ADMIN.
"""
import getpass
import os
from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.accounts.models.user import validate_canonical_username
from apps.accounts.policies.roles import ROLE_ADMIN, assign_role


class Command(BaseCommand):
    help = "Khởi tạo tài khoản quản trị viên đầu tiên (Bootstrap Admin) an toàn và bảo mật."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--username",
            type=str,
            default=os.environ.get("DJANGO_BOOTSTRAP_ADMIN_USERNAME")
            or os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "admin"),
            help="Tên đăng nhập admin (mặc định lấy từ biến môi trường hoặc 'admin').",
        )
        parser.add_argument(
            "--full-name",
            type=str,
            default=os.environ.get("DJANGO_BOOTSTRAP_ADMIN_FULLNAME")
            or os.environ.get("BOOTSTRAP_ADMIN_FULLNAME", "Hệ Thống Quản Trị Viên"),
            help="Họ và tên đầy đủ của quản trị viên khởi tạo.",
        )
        parser.add_argument(
            "--email",
            type=str,
            default=os.environ.get("DJANGO_BOOTSTRAP_ADMIN_EMAIL")
            or os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "admin@iotcabinet.local"),
            help="Địa chỉ email quản trị viên khởi tạo.",
        )
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Chạy ở chế độ không tương tác. Bắt buộc cung cấp mật khẩu qua biến môi trường BOOTSTRAP_ADMIN_PASSWORD.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        raw_username = options["username"]
        full_name = options["full_name"]
        email = options["email"]
        no_input = options["no_input"]

        # 1. Chuẩn hóa và kiểm tra username canonical
        canonical_username = raw_username.strip().lower()
        try:
            validate_canonical_username(canonical_username)
        except ValidationError as e:
            raise CommandError(f"Username '{raw_username}' không hợp lệ: {e.message}") from e

        # 2. Idempotent check: Kiểm tra nếu tài khoản đã tồn tại
        if User.objects.filter(username=canonical_username).exists():
            self.stdout.write(
                self.style.WARNING(
                    f"Tài khoản quản trị viên '{canonical_username}' đã tồn tại trong hệ thống.\n"
                    "Bỏ qua khởi tạo để bảo đảm an toàn dữ liệu và không ghi đè mật khẩu hiện tại."
                )
            )
            return

        # 3. Lấy mật khẩu an toàn (Rule 17: không dùng CLI argument)
        password = os.environ.get("DJANGO_BOOTSTRAP_ADMIN_PASSWORD") or os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
        if not password:
            if no_input:
                raise CommandError(
                    "Không tìm thấy mật khẩu trong biến môi trường (DJANGO_BOOTSTRAP_ADMIN_PASSWORD hoặc "
                    "BOOTSTRAP_ADMIN_PASSWORD) khi chạy với tùy chọn --no-input."
                )
            # Nhập mật khẩu qua dấu nhắc ẩn
            password = getpass.getpass(f"Nhập mật khẩu cho Bootstrap Admin '{canonical_username}' (12-128 ký tự): ")
            password_confirm = getpass.getpass("Xác nhận lại mật khẩu: ")
            if password != password_confirm:
                raise CommandError("Mật khẩu xác nhận không khớp. Hủy thao tác khởi tạo.")

        # 4. Kiểm tra mật khẩu qua hệ thống AUTH_PASSWORD_VALIDATORS của Django
        try:
            validate_password(password)
        except ValidationError as e:
            raise CommandError(
                f"Mật khẩu không đáp ứng chính sách bảo mật: {'; '.join(e.messages)}"
            ) from e

        # 5. Đảm bảo các Vai trò hệ thống đã được đồng bộ
        call_command("seed_roles", verbosity=0)

        # 6. Khởi tạo tài khoản trong transaction atomic
        with transaction.atomic():
            user = User.objects.create_user(
                username=canonical_username,
                full_name=full_name,
                email=email,
                password=password
            )
            user.status = User.Status.ACTIVE
            user.must_change_password = True
            user.is_staff = True
            user.is_superuser = False
            user.save(update_fields=["status", "must_change_password", "is_staff", "is_superuser"])

            # Gán vai trò ADMIN duy nhất
            assign_role(user, ROLE_ADMIN)

        self.stdout.write(
            self.style.SUCCESS(
                f"Khởi tạo thành công tài khoản Bootstrap Admin '{canonical_username}'.\n"
                f"  - Họ và tên: {user.full_name}\n"
                f"  - Vai trò: {ROLE_ADMIN}\n"
                f"  - is_staff: {user.is_staff} | is_superuser: {user.is_superuser}\n"
                f"  - Bắt buộc đổi mật khẩu ở lần đăng nhập đầu tiên: {user.must_change_password}"
            )
        )