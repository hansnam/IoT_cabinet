from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect

from apps.accounts.admin_site import cabinet_admin_site
from apps.accounts.forms import ManagedUserForm
from apps.accounts.models import User
from apps.accounts.policies.roles import has_permission
from apps.accounts.services.administration import manage_user


@admin.register(User, site=cabinet_admin_site)
class ManagedUserAdmin(admin.ModelAdmin):
    form = ManagedUserForm
    fields = ("username", "full_name", "email", "role", "status", "temporary_password", "confirm_password")
    list_display = ("username", "full_name", "status", "must_change_password")
    list_filter = ("status", "must_change_password")
    search_fields = ("username", "full_name", "email")
    actions = None

    def has_view_permission(self, request, obj=None):
        return has_permission(request.user, "USER_VIEW")

    def has_add_permission(self, request):
        return has_permission(request.user, "USER_CREATE")

    def has_change_permission(self, request, obj=None):
        return has_permission(request.user, "USER_UPDATE")

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        fields = {k: v for k, v in form.cleaned_data.items() if k != "confirm_password"}
        saved = manage_user(request, target_id=obj.pk if change else None, **fields)
        obj.__dict__.update(saved.__dict__)

    def save_related(self, request, form, formsets, change):
        # Không có inline/m2m; Groups chỉ được cập nhật trong service.
        return None

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except ValidationError:
            # Thoát atomic của Django trước khi báo lỗi; không để mutation dở dang.
            self.message_user(request, "Không lưu được: dữ liệu không hợp lệ hoặc đây là ADMIN hoạt động cuối cùng.", messages.ERROR)
            return redirect(request.path)