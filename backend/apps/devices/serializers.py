from rest_framework import serializers
from apps.devices.models import Device, DeviceAccess, DeviceConfig


class DeviceSerializer(serializers.ModelSerializer):
    is_online = serializers.SerializerMethodField()
    can_operate = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = [
            'id', 'name', 'source', 'status', 'hardware_version', 'firmware_version',
            'ip_address', 'mac_address', 'desired_config', 'applied_config',
            'last_seen_at', 'latest_temperature', 'latest_humidity',
            'latest_fan_status', 'latest_door_status', 'latest_mode',
            'is_online', 'can_operate', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'last_seen_at', 'latest_temperature', 'latest_humidity',
            'latest_fan_status', 'latest_door_status', 'latest_mode',
            'id', 'is_online', 'can_operate', 'created_at', 'updated_at', 'desired_config', 'applied_config'
        ]

    def get_is_online(self, obj) -> bool:
        return obj.is_online()

    def get_can_operate(self, obj):
        """Frontend dùng cờ này để ẩn nút; backend vẫn tự kiểm quyền khi nhận lệnh."""
        from django.core.exceptions import PermissionDenied
        from apps.devices.policies import require_device_permission
        request = self.context.get('request')
        if not request or obj.status != 'ACTIVE':
            return False
        try:
            return require_device_permission(request.user, obj, 'OPERATE')
        except PermissionDenied:
            return False


class DeviceAccessSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    device_name = serializers.CharField(source='device.name', read_only=True)

    class Meta:
        model = DeviceAccess
        fields = [
            'id', 'user', 'username', 'device', 'device_name',
            'role', 'granted_by', 'granted_at', 'expires_at'
        ]
        read_only_fields = ['granted_at']


class DeviceControlSerializer(serializers.Serializer):
    """
    Serializer kiểm tra dữ liệu đầu vào khi người dùng gửi lệnh điều khiển.
    Hỗ trợ format đơn giản { "fan": "ON" } hoặc chuẩn { "command": "SET_FAN", "fan": "ON" }.
    """
    command = serializers.ChoiceField(choices=['SET_FAN', 'SET_MODE'], required=False)
    duration_s = serializers.IntegerField(min_value=1, max_value=3600, required=False, default=60)
    fan = serializers.ChoiceField(choices=['ON', 'OFF', 1, 0], required=False)
    mode = serializers.ChoiceField(choices=['AUTO', 'MANUAL'], required=False)

    def validate(self, attrs):
        attrs.setdefault('command', 'SET_MODE' if 'mode' in attrs and 'fan' not in attrs else 'SET_FAN')
        if attrs['command'] == 'SET_FAN' and 'fan' not in attrs:
            raise serializers.ValidationError('Lệnh SET_FAN cần tham số fan.')
        if attrs['command'] == 'SET_MODE' and 'mode' not in attrs:
            raise serializers.ValidationError('Lệnh SET_MODE cần tham số mode.')
        if 'fan' not in attrs and 'mode' not in attrs:
            raise serializers.ValidationError("Yêu cầu cung cấp ít nhất một tham số 'fan' ('ON'/'OFF') hoặc 'mode' ('AUTO'/'MANUAL').")
        # Chuẩn hóa fan 1/0 thành ON/OFF
        if 'fan' in attrs:
            if attrs['fan'] in (1, '1', 'ON', 'on'):
                attrs['fan'] = 'ON'
            else:
                attrs['fan'] = 'OFF'
        return attrs


class DeviceConfigSerializer(serializers.Serializer):
    desired_config = serializers.DictField(required=True)

    def validate_desired_config(self, value):
        """Chặn số không hợp lệ trước khi dựng JSON cấu hình, trả 400 thay vì lỗi server."""
        import math
        float_fields = (
            'max_temp', 'max_hum', 'temp_alert_c', 'humidity_alert_pct',
            'fan_on_c', 'fan_off_c', 'temp_clear_margin_c', 'humidity_clear_margin_pct',
        )
        try:
            for key in float_fields:
                if key in value and (isinstance(value[key], bool) or not math.isfinite(float(value[key]))):
                    raise ValueError(key)
            for key in ('telemetry_interval_ms', 'manual_timeout_s'):
                if key in value and (isinstance(value[key], bool) or int(value[key]) != float(value[key]) or int(value[key]) <= 0):
                    raise ValueError(key)
        except (ValueError, TypeError, OverflowError):
            raise serializers.ValidationError('Các ngưỡng phải là số hữu hạn; chu kỳ và thời gian phải là số nguyên dương.')
        return value
