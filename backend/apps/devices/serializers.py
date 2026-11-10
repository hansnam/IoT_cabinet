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
    Hỗ trợ format đơn giản { "fan": "ON" } hoặc chuẩn { "command": "SET_FAN", "fan": "ON" }.
    """
    command = serializers.CharField(required=False, default='SET_FAN')
    fan = serializers.ChoiceField(choices=['ON', 'OFF', 1, 0], required=False)
    mode = serializers.ChoiceField(choices=['AUTO', 'MANUAL'], required=False)

    def validate(self, attrs):
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
