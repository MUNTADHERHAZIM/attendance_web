from django.urls import path
from .views import (
    ImportStudentsView,
    ExportAttendanceExcelView,
    admin_import_web_view,
    admin_reports_overview_view,
    admin_student_detail_view,
    export_teacher_report_excel_view,
    export_teacher_report_csv_view,
    quick_create_academic_entity_view,
    delete_audit_log_view,
    bulk_delete_audit_logs_view,
    clear_all_audit_logs_view,
    toggle_captcha_view,
)

app_name = "reports"

urlpatterns = [
    # REST API endpoints
    path("api/students/import/", ImportStudentsView.as_view(), name="import_students"),
    path("api/session/<int:session_id>/export/", ExportAttendanceExcelView.as_view(), name="export_excel"),
    path("api/quick-create/", quick_create_academic_entity_view, name="quick_create_entity"),
    path("teacher/export-excel/", export_teacher_report_excel_view, name="teacher_export_excel"),
    path("teacher/export-csv/", export_teacher_report_csv_view, name="teacher_export_csv"),
    
    # Web template views
    path("admin/import/", admin_import_web_view, name="admin_import_web"),
    path("admin/overview/", admin_reports_overview_view, name="admin_reports_overview"),
    path("overview/", admin_reports_overview_view, name="web_dashboard"),
    path("admin/student/<int:student_id>/", admin_student_detail_view, name="admin_student_detail"),

    # Audit log management
    path("audit-log/<int:log_id>/delete/", delete_audit_log_view, name="delete_audit_log"),
    path("audit-log/bulk-delete/", bulk_delete_audit_logs_view, name="bulk_delete_audit_logs"),
    path("audit-log/clear-all/", clear_all_audit_logs_view, name="clear_all_audit_logs"),

    # Captcha toggle
    path("toggle-captcha/", toggle_captcha_view, name="toggle_captcha"),
]
