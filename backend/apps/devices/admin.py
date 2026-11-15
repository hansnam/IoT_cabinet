from django.contrib import admin
from django import forms
from django.core.validators import RegexValidator
from django.utils import timezone
from apps.accounts.admin_site import cabinet_admin_site
from apps.devices.models import Device, DeviceAccess, DeviceConfig
from apps.devices.access_services import grant_device_access, revoke_device_access


class DeviceForm(forms.ModelForm):
    """Mã tủ cũng là một đoạn topic MQTT, vì vậy không nhận '/', '+' hoặc '#'."""
    class Meta:
        model = Device
        fields = '__all__'

    def clean_id(self):
        value = self.cleaned_data['id']
        RegexValidator(r'\A[A-Za-z0-9_-]{1,64}\Z', 'Mã tủ chỉ gồm chữ, số, _ và -.')(value)
        return value


@admin.register(Device, site=cabinet_admin_site)
class DeviceAdmin(admin.ModelAdmin):
    form = DeviceForm
    actions = None

    def has_delete_permission(self, request, obj=None):
        return False  # Giữ lịch sử; đổi trạng thái DECOMMISSIONED thay cho xóa cứng.
    list_display = (
        'id', 'name', 'status', 'source', 'latest_temperature',
        'latest_humidity', 'latest_fan_status', 'latest_mode', 'last_seen_at'
    )
    list_filter = ('status', 'source', 'latest_fan_status', 'latest_mode')
    search_fields = ('id', 'name', 'ip_address', 'mac_address')
    readonly_fields = (
        'latest_temperature', 'latest_humidity', 'latest_fan_status',
        'latest_door_status', 'latest_mode', 'last_seen_at',
        'created_at', 'updated_at', 'desired_config', 'applied_config'
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


class DeviceAccessForm(forms.ModelForm):
    """Hiện lỗi ngay trên form, trước khi service lưu grant."""
    class Meta:
        model = DeviceAccess
        fields = '__all__'

    def clean_expires_at(self):
        value = self.cleaned_data.get('expires_at')
        if value and value <= timezone.now():
            raise forms.ValidationError('Thời điểm hết hạn phải ở tương lai.')
        return value


@admin.register(DeviceAccess, site=cabinet_admin_site)
class DeviceAccessAdmin(admin.ModelAdmin):
    form = DeviceAccessForm
    list_display = ('user', 'device', 'role', 'granted_by', 'granted_at', 'expires_at')
    list_filter = ('role', 'granted_at')
    search_fields = ('user__username', 'device__id', 'device__name')
    readonly_fields = ('granted_by', 'granted_at')
    actions = None  # Thu hồi từng grant qua service để ghi audit.

    def save_model(self, request, obj, form, change):
        """Form Admin gọi chung service cấp quyền, không lưu hai lần."""
        grant = grant_device_access(request, obj.user, obj.device, obj.role, obj.expires_at)
        obj.pk = grant.pk

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields + (('user', 'device') if obj else ())

    def delete_model(self, request, obj):
        revoke_device_access(request, obj)


@admin.register(DeviceConfig, site=cabinet_admin_site)
class DeviceConfigAdmin(admin.ModelAdmin):
    list_display = ('device', 'version', 'applied', 'applied_at', 'created_at')
    list_filter = ('applied', 'created_at')
    search_fields = ('device__id',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
