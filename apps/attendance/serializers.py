from rest_framework import serializers
from .models import AttendanceSession, AttendanceRecord
from apps.accounts.serializers import StudentProfileSerializer

class AttendanceSessionSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source="session.course.name", read_only=True)
    class_section_name = serializers.CharField(source="session.class_section.name", read_only=True)
    class_level = serializers.CharField(source="session.class_section.level", read_only=True)
    room = serializers.CharField(source="session.room", read_only=True)

    class Meta:
        model = AttendanceSession
        fields = [
            "id", "session", "course_name", "class_section_name", "class_level", 
            "room", "date", "start_time", "end_time", "is_active", 
            "requires_wifi", "requires_geofence"
        ]
        read_only_fields = ["id", "start_time", "is_active"]


class AttendanceRecordSerializer(serializers.ModelSerializer):
    student = StudentProfileSerializer(read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    method_display = serializers.CharField(source="get_method_display", read_only=True)

    class Meta:
        model = AttendanceRecord
        fields = [
            "id", "student", "attendance_session", "status", "status_display",
            "method", "method_display", "timestamp", "ip_address", "notes"
        ]
        read_only_fields = ["id", "timestamp", "ip_address"]
