from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, StudentProfile, TeacherProfile


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "email", "first_name", "last_name", "role", "is_staff")
    list_filter = ("role", "is_staff", "is_superuser", "is_active")
    fieldsets = BaseUserAdmin.fieldsets + (
        ("بيانات الدور الإضافية", {"fields": ("role", "phone", "avatar")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("بيانات الدور الإضافية", {"fields": ("role", "phone", "avatar")}),
    )


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ("student_id", "user", "institution", "birth_date", "rfid_card")
    search_fields = ("student_id", "user__username", "user__first_name", "user__last_name")
    list_filter = ("institution",)


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ("teacher_id", "user", "institution", "specialization")
    search_fields = ("teacher_id", "user__username", "user__first_name", "user__last_name")
    list_filter = ("institution",)

