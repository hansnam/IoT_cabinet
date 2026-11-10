from django.contrib import admin

from apps.accounts.admin_site import cabinet_admin_site
from apps.accounts.policies.roles import has_permission
from apps.audit.models import AuditLog


@admin.register(AuditLog, site=cabinet_admin_site)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "actor_name_snapshot", "action", "outcome", "target_type")
    list_filter = ("outcome", "actor_type", "action")
    search_fields = ("actor_name_snapshot", "target_id", "function_key")
    date_hierarchy = "occurred_at"
    readonly_fields = tuple(f.name for f in AuditLog._meta.fields)
    actions = None

    def has_view_permission(self, request, obj=None):
        return has_permission(request.user, "AUDIT_VIEW")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
