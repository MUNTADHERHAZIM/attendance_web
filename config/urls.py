from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
import os

from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

from apps.core.views import (
    dashboard_view, 
    custom_404_view, 
    custom_500_view, 
    custom_403_view
)
from apps.reports.views import admin_import_web_view
from apps.accounts.views import logout_view, RegisterView, CheckTeacherCodeView

# ─── Customize Django Admin to be fully Arabic ───────────────────────
admin.site.site_header = "لوحة إدارة نظام الحضور الذكي"
admin.site.site_title = "نظام الحضور الذكي"
admin.site.index_title = "إدارة البيانات والعمليات الأكاديمية"

class SmartLoginView(auth_views.LoginView):
    template_name = "login.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        try:
            from apps.core.models import SystemSetting
            ctx["teacher_verification_code"] = SystemSetting.get_teacher_code()
        except Exception:
            ctx["teacher_verification_code"] = "EDU2026"
        return ctx


urlpatterns = [
    # ─── Django Admin Panel ───────────────────────────────────────────
    path("admin/", admin.site.urls),

    # ─── Dashboard Home ───────────────────────────────────────────────
    path("", dashboard_view, name="dashboard"),

    # ─── Session Authentication ───────────────────────────────────────
    path("login/", SmartLoginView.as_view(), name="login"),
    path("logout/", logout_view, name="logout"),

    # ─── Password Reset Flow ──────────────────────────────────────────
    # Step 1: User enters email
    path("password-reset/",
         auth_views.PasswordResetView.as_view(
             template_name="auth/password_reset.html",
             email_template_name="auth/password_reset_email.txt",
             subject_template_name="auth/password_reset_subject.txt",
             success_url="/password-reset/done/",
         ),
         name="password_reset"),

    # Step 2: "Check your email" confirmation page
    path("password-reset/done/",
         auth_views.PasswordResetDoneView.as_view(
             template_name="auth/password_reset_done.html",
         ),
         name="password_reset_done"),

    # Step 3: User clicks link in email — set new password
    path("password-reset/<uidb64>/<token>/",
         auth_views.PasswordResetConfirmView.as_view(
             template_name="auth/password_reset_confirm.html",
             success_url="/password-reset/complete/",
         ),
         name="password_reset_confirm"),

    # Step 4: Success — password changed
    path("password-reset/complete/",
         auth_views.PasswordResetCompleteView.as_view(
             template_name="auth/password_reset_complete.html",
         ),
         name="password_reset_complete"),


    # ─── Sub-application routers ──────────────────────────────────────
    path("api/register/", RegisterView.as_view(), name="api_register"),
    path("api/teacher-code/", CheckTeacherCodeView.as_view(), name="api_teacher_code"),
    path("accounts/", include("apps.accounts.urls")),
    path("attendance/", include("apps.attendance.urls")),
    path("reports/", include("apps.reports.urls")),

    # ─── Admin web views ─────────────────────────────────────────────
    path("admin/students/import/", admin_import_web_view, name="admin_import_web"),

    # ─── PWA endpoints ───────────────────────────────────────────────
    path("", include("pwa.urls")),

    # ─── PWA Service Worker (must be served from root scope) ─────────
    path("sw.js", serve, {
        "path": "sw.js",
        "document_root": os.path.join(settings.BASE_DIR, "static"),
    }, name="service_worker"),

    # ─── API Documentation (drf-spectacular) ─────────────────────────
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),

    # ─── Custom Error Page Previews ───────────────────────────────────
    path("error/404/", custom_404_view, name="preview_404"),
    path("error/500/", custom_500_view, name="preview_500"),
    path("error/403/", custom_403_view, name="preview_403"),
]

# ─── Custom Error Handlers ───────────────────────────────────────────
handler404 = "apps.core.views.custom_404_view"
handler500 = "apps.core.views.custom_500_view"
handler403 = "apps.core.views.custom_403_view"

# Serve media and static files in development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

