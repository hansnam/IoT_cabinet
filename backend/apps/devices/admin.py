from django.contrib import admin
from apps.accounts.admin_site import cabinet_admin_site
from apps.devices.models import Device, DeviceAccess, DeviceConfig


@admin.register(Device, site=cabinet_admin_site)
class DeviceAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'name', 'status', 'source', 'latest_temperature',
        'latest_humidity', 'latest_fan_status', 'latest_mode', 'last_seen_at'
    )
    list_filter = ('status', 'source', 'latest_fan_status', 'latest_mode')
    search_fields = ('id', 'name', 'ip_address', 'mac_address')
    readonly_fields = (
        'latest_temperature', 'latest_humidity', 'latest_fan_status',
        'latest_door_status', 'latest_mode', 'last_seen_at',
        'created_at', 'updated_at'
    )
    fieldsets = (
        ('Thông tin cơ bản', {
            'fields': ('id', 'name', 'source', 'status')
        }),
        ('Phần cứng & Kết nối', {
            'fields': ('hardware_version', 'firmware_version', 'ip_address', 'mac_address')
        }),
        ('Trạng thái số đo mới nhất (Projection)', {
            'fields': (
                'last_seen_at', 'latest_temperature', 'latest_humidity',
                'latest_fan_status', 'latest_door_status', 'latest_mode'
            )
        }),
        ('Cấu hình (Config JSON)', {
            'classes': ('collapse',),
            'fields': ('desired_config', 'applied_config')
        }),
        ('Thời gian', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(DeviceAccess, site=cabinet_admin_site)
class DeviceAccessAdmin(admin.ModelAdmin):
    list_display = ('user', 'device', 'role', 'granted_by', 'granted_at', 'expires_at')
    list_filter = ('role', 'granted_at')
    search_fields = ('user__username', 'device__id', 'device__name')


@admin.register(DeviceConfig, site=cabinet_admin_site)
class DeviceConfigAdmin(admin.ModelAdmin):
    list_display = ('device', 'version', 'applied', 'applied_at', 'created_at')
    list_filter = ('applied', 'created_at')
    search_fields = ('device__id',)
