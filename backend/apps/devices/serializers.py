import re
from rest_framework import serializers
from apps.devices.models import Device, DeviceAccess, DeviceConfig


class CreateDeviceSerializer(serializers.ModelSerializer):
    """
    Serializer tạo mới thiết bị tủ mạng (UI-04).
    Chỉ Quản trị viên (ADMIN) mới được tạo.
    Mã tủ chỉ gồm chữ cái, chữ số, gạch dưới và gạch nối.
    Thiết bị mới luôn bắt đầu ở trạng thái REGISTERED (chưa kết nối).
    """
    id = serializers.CharField(
        max_length=64,
        required=True,
        help_text="Mã định danh duy nhất (ví dụ: CABINET-01)"
    )

    class Meta:
        model = Device
        fields = ['id', 'name', 'source', 'hardware_version', 'firmware_version']

    def validate_id(self, value):
        val = value.strip()
        if not re.fullmatch(r'^[A-Za-z0-9_-]{1,64}$', val):
            raise serializers.ValidationError(
                "Mã tủ mạng chỉ được gồm chữ cái, chữ số, gạch dưới (_) hoặc gạch nối (-), tối đa 64 ký tự."
            )
        if Device.objects.filter(pk=val).exists():
            raise serializers.ValidationError(f"Mã tủ mạng '{val}' đã tồn tại trong hệ thống.")
        return val

    def validate_name(self, value):
        val = value.strip()
        if not val:
            raise serializers.ValidationError("Tên tủ mạng không được để trống.")
        return val


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
    user_full_name = serializers.CharField(source='user.full_name', read_only=True)
    device_name = serializers.CharField(source='device.name', read_only=True)
    granted_by_username = serializers.CharField(source='granted_by.username', read_only=True)
    is_valid = serializers.BooleanField(read_only=True)

    class Meta:
        model = DeviceAccess
        fields = [
            'id', 'user', 'username', 'user_full_name', 'device', 'device_name',
            'role', 'granted_by', 'granted_by_username', 'granted_at', 'expires_at', 'is_valid'
        ]
        read_only_fields = ['granted_at', 'granted_by']


class GrantDeviceAccessSerializer(serializers.Serializer):
    """
    Serializer tiếp nhận yêu cầu cấp hoặc cập nhật quyền truy cập tủ.
    Không nhận granted_by từ client; actor được lấy từ phiên đăng nhập.
    """
    user_id = serializers.UUIDField(required=True)
    device_id = serializers.CharField(max_length=64, required=True)
    role = serializers.ChoiceField(choices=['VIEW', 'OPERATE'], required=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True, default=None)


class DeviceControlSerializer(serializers.Serializer):
    """
    Serializer kiểm tra dữ liệu đầu vào khi người dùng gửi lệnh điều khiển.
    Hỗ trợ format đơn giản { "fan": "ON" } hoặc chuẩn { "command": "SET_FAN", "fan": "ON" }.
    """
    command = serializers.ChoiceField(choices=['SET_FAN', 'SET_MODE'], required=False)
    duration_s = serializers.IntegerField(min_value=10, max_value=300, required=False, default=60)
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
        """Chặn số không hợp lệ và ngoài ngưỡng firmware trước khi dựng JSON cấu hình."""
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

        fan_on = float(value['fan_on_c']) if 'fan_on_c' in value else None
        fan_off = float(value['fan_off_c']) if 'fan_off_c' in value else None
        tele_ms = int(value['telemetry_interval_ms']) if 'telemetry_interval_ms' in value else None
        man_ttl = int(value['manual_timeout_s']) if 'manual_timeout_s' in value else None
        temp_alert = float(value['temp_alert_c']) if 'temp_alert_c' in value else (
            float(value['max_temp']) if 'max_temp' in value else None
        )
        hum_alert = float(value['humidity_alert_pct']) if 'humidity_alert_pct' in value else (
            float(value['max_hum']) if 'max_hum' in value else None
        )

        if fan_on is not None and not (-40.0 <= fan_on <= 80.0):
            raise serializers.ValidationError("fan_on_c phải nằm trong khoảng -40°C đến 80°C.")
        if fan_off is not None and not (-40.0 <= fan_off <= 80.0):
            raise serializers.ValidationError("fan_off_c phải nằm trong khoảng -40°C đến 80°C.")
        if fan_on is not None and fan_off is not None and fan_off >= fan_on:
            raise serializers.ValidationError("fan_off_c phải nhỏ hơn fan_on_c để đảm bảo trễ điều khiển (hysteresis).")
        if temp_alert is not None and not (-40.0 <= temp_alert <= 80.0):
            raise serializers.ValidationError("Ngưỡng cảnh báo nhiệt độ phải nằm trong khoảng -40°C đến 80°C.")
        if hum_alert is not None and not (0.0 <= hum_alert <= 100.0):
            raise serializers.ValidationError("Ngưỡng cảnh báo độ ẩm phải nằm trong khoảng 0% đến 100%.")
        if tele_ms is not None and not (1000 <= tele_ms <= 60000):
            raise serializers.ValidationError("telemetry_interval_ms phải nằm trong khoảng 1000 đến 60000 ms.")
        if man_ttl is not None and not (10 <= man_ttl <= 300):
            raise serializers.ValidationError("manual_timeout_s phải nằm trong khoảng 10 đến 300 giây.")
        return value


class UpdateDeviceAccessSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=["VIEW", "OPERATE"], required=False)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
