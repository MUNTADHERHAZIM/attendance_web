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
]
