from django.contrib import admin
from .models import Device, Telemetry, Alert, CommandLog


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = (
        "device_id",
        "name",
        "connection_state",
        "health_state",
        "mode",
        "fan_output",
        "temp_threshold_on",
        "temp_threshold_off",
        "last_seen_at",
    )
    list_filter = ("connection_state", "health_state", "mode", "fan_output")
    search_fields = ("device_id", "name", "location")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Thông tin cơ bản", {"fields": ("device_id", "name", "device_type", "location")}),
        ("Trạng thái hiện tại", {"fields": ("connection_state", "health_state", "mode", "fan_output", "last_seen_at")}),
        ("Cấu hình ngưỡng nhiệt & độ ẩm", {
            "fields": ("temp_threshold_on", "temp_threshold_off", "temp_alert_thresh", "humidity_alert_thresh")
        }),
        ("Dấu vết thời gian", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(Telemetry)
class TelemetryAdmin(admin.ModelAdmin):
    list_display = ("device", "temperature_c", "humidity_pct", "fan_output", "mode", "received_at")
    list_filter = ("device", "fan_output", "mode", "received_at")
    search_fields = ("device__device_id", "device__name")
    readonly_fields = ("received_at",)


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("device", "alert_type", "severity", "status", "message", "created_at", "resolved_at")
    list_filter = ("severity", "status", "alert_type", "device")
    search_fields = ("device__device_id", "message")
    actions = ["mark_resolved", "mark_acknowledged"]

    @admin.action(description="Đánh dấu đã giải quyết (Resolved)")
    def mark_resolved(self, request, queryset):
        from django.utils import timezone
        queryset.update(status="resolved", resolved_at=timezone.now())

    @admin.action(description="Đánh dấu đã tiếp nhận (Acknowledged)")
    def mark_acknowledged(self, request, queryset):
        queryset.update(status="acknowledged")


@admin.register(CommandLog)
class CommandLogAdmin(admin.ModelAdmin):
    list_display = ("device", "command_type", "status", "params", "created_at", "executed_at")
    list_filter = ("command_type", "status", "device")
    search_fields = ("device__device_id", "command_type")
    readonly_fields = ("created_at", "executed_at")
