from django.contrib import admin
from apps.accounts.admin_site import cabinet_admin_site
from apps.commands.models import Command, OutboxMessage


@admin.register(Command, site=cabinet_admin_site)
class CommandAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'device', 'command_type', 'status',
        'correlation_id', 'actor', 'created_at', 'acked_at'
    )
    list_filter = ('command_type', 'status', 'created_at')
    search_fields = ('id', 'correlation_id', 'device__id', 'actor__username')
    readonly_fields = (
        'id', 'device', 'command_type', 'payload', 'status',
        'correlation_id', 'actor', 'created_at', 'sent_at',
        'acked_at', 'error_message'
    )


@admin.register(OutboxMessage, site=cabinet_admin_site)
class OutboxMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'topic', 'status', 'retry_count', 'created_at', 'published_at')
    list_filter = ('status', 'created_at')
    search_fields = ('topic', 'command__id', 'command__correlation_id')
    readonly_fields = ('command', 'topic', 'payload', 'qos', 'created_at', 'published_at', 'last_error')
