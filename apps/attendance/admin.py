from django.contrib import admin
from .models import AttendanceSession, AttendanceRecord, SyncQueue


@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = ("session", "date", "created_by", "is_active", "requires_wifi", "start_time")
    list_filter = ("is_active", "requires_wifi", "date")
    search_fields = ("session__course__name", "session__course__code", "created_by__username")


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("student", "attendance_session", "status", "method", "timestamp", "ip_address")
    list_filter = ("status", "method", "attendance_session__date")
    search_fields = ("student__student_id", "student__user__first_name", "student__user__last_name", "student__user__username")


@admin.register(SyncQueue)
class SyncQueueAdmin(admin.ModelAdmin):
    list_display = ("model_name", "record_id", "action", "is_synced", "created_at")
    list_filter = ("is_synced", "model_name", "action")
