from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    CustomTokenObtainPairView, 
    UserProfileView, 
    profile_web_view, 
    CheckTeacherCodeView,
    RegisterView,
    teacher_students_view,
    teacher_add_student_view,
    teacher_import_students_view,
    teacher_add_section_view,
    teacher_add_course_view,
    teacher_update_profile_view,
    admin_users_directory_view,
)

app_name = "accounts"

urlpatterns = [
    # Admin Directory & Users Management Hub
    path("admin/users/", admin_users_directory_view, name="admin_users_directory"),

    # Web Profile & Teacher Student Management
    path("profile/", profile_web_view, name="profile_web"),
    path("teacher/students/", teacher_students_view, name="teacher_students"),
    path("teacher/students/add/", teacher_add_student_view, name="teacher_add_student"),
    path("teacher/students/import/", teacher_import_students_view, name="teacher_import_students"),
    path("teacher/sections/add/", teacher_add_section_view, name="teacher_add_section"),
    path("teacher/courses/add/", teacher_add_course_view, name="teacher_add_course"),
    path("teacher/profile/update/", teacher_update_profile_view, name="teacher_update_profile"),

    # REST API Endpoints
    path("api/login/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/register/", RegisterView.as_view(), name="register"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/profile/", UserProfileView.as_view(), name="user_profile"),
    path("api/teacher-code/", CheckTeacherCodeView.as_view(), name="teacher_code"),
]
