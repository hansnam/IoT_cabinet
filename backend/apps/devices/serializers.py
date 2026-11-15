from rest_framework import serializers
from apps.devices.models import Device, DeviceAccess, DeviceConfig


class DeviceSerializer(serializers.ModelSerializer):
    is_online = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = [
            'id', 'name', 'source', 'status', 'hardware_version', 'firmware_version',
            'ip_address', 'mac_address', 'desired_config', 'applied_config',
            'last_seen_at', 'latest_temperature', 'latest_humidity',
            'latest_fan_status', 'latest_door_status', 'latest_mode',
            'is_online', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'last_seen_at', 'latest_temperature', 'latest_humidity',
            'latest_fan_status', 'latest_door_status', 'latest_mode',
            'is_online', 'created_at', 'updated_at'
        ]

    def get_is_online(self, obj) -> bool:
        return obj.is_online()


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
    Hỗ trợ { "fan": "ON" }, { "mode": "MANUAL", "duration_s": 60 },
    hoặc { "command": "SET_FAN" | "SET_MODE", ... }.
    """
    command = serializers.ChoiceField(choices=['SET_FAN', 'SET_MODE'], required=False)
    fan = serializers.ChoiceField(choices=['ON', 'OFF', 1, 0], required=False)
    mode = serializers.ChoiceField(choices=['AUTO', 'MANUAL'], required=False)
    duration_s = serializers.IntegerField(required=False, min_value=10, max_value=300)

    def validate(self, attrs):
        if 'fan' not in attrs and 'mode' not in attrs:
            raise serializers.ValidationError("Yêu cầu cung cấp ít nhất một tham số 'fan' ('ON'/'OFF') hoặc 'mode' ('AUTO'/'MANUAL').")

        if 'command' not in attrs:
            attrs['command'] = 'SET_FAN' if 'fan' in attrs else 'SET_MODE'

        if attrs['command'] == 'SET_FAN' and 'fan' not in attrs:
            raise serializers.ValidationError("Lệnh 'SET_FAN' yêu cầu tham số 'fan' ('ON'/'OFF').")
        if attrs['command'] == 'SET_MODE' and 'mode' not in attrs:
            raise serializers.ValidationError("Lệnh 'SET_MODE' yêu cầu tham số 'mode' ('AUTO'/'MANUAL').")

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
        try:
            fan_on = float(value['fan_on_c']) if 'fan_on_c' in value else None
            fan_off = float(value['fan_off_c']) if 'fan_off_c' in value else None
            tele_ms = int(value['telemetry_interval_ms']) if 'telemetry_interval_ms' in value else None
            man_ttl = int(value['manual_timeout_s']) if 'manual_timeout_s' in value else None
            hum_alert = float(value['humidity_alert_pct']) if 'humidity_alert_pct' in value else (
                float(value['max_hum']) if 'max_hum' in value else None
            )
            temp_alert = float(value['temp_alert_c']) if 'temp_alert_c' in value else (
                float(value['max_temp']) if 'max_temp' in value else None
            )
        except (TypeError, ValueError):
            raise serializers.ValidationError("Các tham số ngưỡng/chu kỳ trong desired_config phải là giá trị số hợp lệ.")

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
