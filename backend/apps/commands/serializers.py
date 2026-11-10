from rest_framework import serializers
from apps.commands.models import Command


class CommandSerializer(serializers.ModelSerializer):
    device_id = serializers.CharField(source='device.id', read_only=True)
    actor_username = serializers.CharField(source='actor.username', read_only=True)

    class Meta:
        model = Command
        fields = [
            'id', 'device_id', 'command_type', 'payload', 'status',
            'correlation_id', 'actor_username', 'created_at',
            'sent_at', 'acked_at', 'error_message'
        ]
        read_only_fields = ['id', 'created_at', 'sent_at', 'acked_at']
