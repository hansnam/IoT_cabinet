from django.contrib import admin
from django.utils import timezone
from apps.alerts.models import Alert


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'device', 'alert_type', 'severity',
        'status', 'message', 'created_at', 'resolved_at'
    )
    list_filter = ('severity', 'status', 'alert_type', 'created_at')
    search_fields = ('device__id', 'message')
    actions = ['mark_acknowledged', 'mark_resolved']

    @admin.action(description="Đánh dấu các cảnh báo đã chọn là: ĐÃ GHI NHẬN (ACKNOWLEDGED)")
    def mark_acknowledged(self, request, queryset):
        queryset.filter(status='OPEN').update(
            status='ACKNOWLEDGED',
            acknowledged_at=timezone.now(),
            acknowledged_by=request.user
        )

    @admin.action(description="Đánh dấu các cảnh báo đã chọn là: ĐÃ KHẮC PHỤC (RESOLVED)")
    def mark_resolved(self, request, queryset):
        queryset.exclude(status='RESOLVED').update(
            status='RESOLVED',
            resolved_at=timezone.now()
        )
