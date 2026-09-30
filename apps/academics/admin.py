from django.contrib import admin
from .models import Institution, Department, Course, ClassSection, Session


from django.utils.html import format_html

@admin.register(Institution)
class InstitutionAdmin(admin.ModelAdmin):
    list_display = ("logo_preview", "name", "allowed_wifi_ssid", "allowed_ip_subnet")
    search_fields = ("name",)
    readonly_fields = ("logo_preview",)
    fieldsets = (
        ("هوية النظام والشعار", {
            "fields": ("name", "logo", "logo_preview", "address")
        }),
        ("قيود الشبكة والموقع (WiFi & Subnet)", {
            "fields": ("allowed_wifi_ssid", "allowed_wifi_bssid", "allowed_ip_subnet", "latitude", "longitude", "radius_meters")
        }),
    )

    def logo_preview(self, obj):
        if obj.logo:
            return format_html('<img src="{}" style="width: 45px; height: 45px; object-fit: contain; border-radius: 8px; border: 1px solid #e2e8f0;" />', obj.logo.url)
        return "لا يوجد شعار"
    logo_preview.short_description = "معاينة الشعار"


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
