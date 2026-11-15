"""Một AdminSite được mount; login/logout/password đi qua service chung."""
from django.contrib.admin import AdminSite
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from apps.accounts.forms import CabinetLoginForm, CabinetPasswordForm
from apps.accounts.models import User
from apps.accounts.policies.roles import ROLE_ADMIN, get_user_role
from apps.accounts.security.paths import ALLOWED_MUST_CHANGE_PATHS
from apps.accounts.services.auth import establish_session, logout_session
from apps.accounts.services.users import change_password


class CabinetLoginView(LoginView):
    authentication_form = CabinetLoginForm
    template_name = "admin/login.html"

    def form_valid(self, form):
        authenticated_user = form.get_user()
        if not isinstance(authenticated_user, User):
            raise PermissionDenied("Không xác định được người dùng.")
        try:
            with transaction.atomic():
                user = User.objects.select_for_update().get(pk=authenticated_user.pk)
                if get_user_role(user) != ROLE_ADMIN or not user.is_staff:
                    raise PermissionDenied
                establish_session(self.request, authenticated_user)
        except PermissionDenied:
            form.add_error(None, "Tài khoản không còn quyền quản trị.")
            return self.form_invalid(form)
        return redirect(self.get_success_url())

    def get_default_redirect_url(self):
        return reverse("admin:index")

    def form_invalid(self, form):
        response = super().form_invalid(form)
        retry_after = getattr(form, "retry_after", None)
        if retry_after:
            response.status_code = 429
            response["Retry-After"] = str(retry_after)
        return response


class CabinetPasswordView(PasswordChangeView):
    form_class = CabinetPasswordForm
    template_name = "registration/password_change_form.html"

    def form_valid(self, form):
        user = self.request.user
        if not isinstance(user, User):
            raise PermissionDenied("Không xác định được người dùng.")
        try:
            _, error, wait = change_password(
                user=user, old_password=form.cleaned_data["old_password"],
                new_password=form.cleaned_data["new_password1"], request=self.request,
            )
        except (PermissionDenied, ValidationError, ValueError):
            form.add_error(None, "Không đổi được mật khẩu. Kiểm tra dữ liệu và phiên đăng nhập.")
            return self.form_invalid(form)
        if error:
            form.add_error(None, "Mật khẩu cũ không đúng hoặc đã vượt giới hạn thử lại.")
            response = self.form_invalid(form)
            if wait:
                response.status_code = 429
                response["Retry-After"] = str(wait)
            return response
        return redirect("admin:login")


class CabinetAdminSite(AdminSite):
    site_header = "IoT Cabinet — Quản trị"
    site_title = "IoT Cabinet"

    def has_permission(self, request):
        user = request.user
        if not isinstance(user, User):
            return False
        return bool(
            user.is_active and user.is_staff and get_user_role(user) == ROLE_ADMIN
            and (not user.must_change_password or request.path in ALLOWED_MUST_CHANGE_PATHS)
        )

    @method_decorator(never_cache)
    def login(self, request, extra_context=None):
        request.current_app = self.name
        context = {**self.each_context(request), "title": "Đăng nhập", "app_path": request.get_full_path()}
        return CabinetLoginView.as_view(extra_context=context)(request)

    @method_decorator(csrf_protect)
    @method_decorator(require_POST)
    def logout(self, request, extra_context=None):
        with transaction.atomic():
            logout_session(request)
        return redirect("admin:login")

    def password_change(self, request, extra_context=None):
        request.current_app = self.name
        return CabinetPasswordView.as_view(extra_context=self.each_context(request))(request)


cabinet_admin_site = CabinetAdminSite(name="admin")
