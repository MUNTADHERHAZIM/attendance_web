from django.contrib import admin
from .models import Institution, Department, Course, ClassSection, Session


@admin.register(Institution)
class InstitutionAdmin(admin.ModelAdmin):
    list_display = ("name", "allowed_wifi_ssid", "allowed_ip_subnet")
    search_fields = ("name",)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "institution")
    search_fields = ("name", "code")
    list_filter = ("institution",)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "department")
    search_fields = ("name", "code")
    list_filter = ("department__institution", "department")


@admin.register(ClassSection)
class ClassSectionAdmin(admin.ModelAdmin):
    list_display = ("name", "level", "department")
    search_fields = ("name", "level")
    list_filter = ("department__institution", "department")


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("course", "class_section", "teacher", "day_of_week", "start_time", "end_time", "room")
    search_fields = ("course__name", "course__code", "teacher__user__username", "room")
    list_filter = ("day_of_week", "class_section__department")
