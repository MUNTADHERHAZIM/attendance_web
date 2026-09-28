from django.contrib import admin
from .models import AuditLog , SystemSetting


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("user", "action", "ip_address", "timestamp")
    search_fields = ("user__username", "action", "ip_address")
    list_filter = ("action", "timestamp")


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ("description", "key", "value", "updated_at")
    search_fields = ("key", "value", "description")
    list_editable = ("value",)

