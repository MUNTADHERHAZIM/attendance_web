from django.urls import path
from .views import (
    # API Views
    StartAttendanceSessionView,
    GetDynamicQRTokenView,
    StudentCheckInView,
    ManualAttendanceRecordUpdateView,
    CloseAttendanceSessionView,
    # Web Template Views
    start_session_web_view,
    session_detail_web_view,
    session_students_list_partial,
    manual_update_web_view,
    bulk_update_web_view,
    student_checkin_web_view,
    qr_image_view,
    student_records_web_view,
    teacher_sessions_web_view,
    create_custom_session_view,
    quick_create_course_view,
    check_session_conflict_api,
    toggle_freeze_qr_view,
    printable_qr_sheet_view,
    quick_offline_checkin_view,
    get_hotspot_info_api,
    offline_emergency_mode_view,
    flexible_session_route_view,
    edit_session_view,
    quick_start_scheduled_session_view,
    cancel_scheduled_session_view,
)

app_name = "attendance"

urlpatterns = [
    # ─── REST API Endpoints ───────────────────────────────────────────
    path("api/session/start/", StartAttendanceSessionView.as_view(), name="start_session"),
    path("api/session/<int:session_id>/qr/", GetDynamicQRTokenView.as_view(), name="get_qr_token"),
    path("api/session/<int:session_id>/toggle-freeze/", toggle_freeze_qr_view, name="toggle_freeze_qr"),
    path("api/session/<int:session_id>/close/", CloseAttendanceSessionView.as_view(), name="close_session"),
    path("api/checkin/", StudentCheckInView.as_view(), name="checkin"),
    path("api/record/update/", ManualAttendanceRecordUpdateView.as_view(), name="manual_update"),
    path("api/record/quick-offline-checkin/", quick_offline_checkin_view, name="quick_offline_checkin"),
    path("api/hotspot-info/", get_hotspot_info_api, name="hotspot_info_api"),
    path("api/course/quick-create/", quick_create_course_view, name="quick_create_course"),
    path("api/check-conflict/", check_session_conflict_api, name="check_conflict_api"),

    # ─── Web Template Views ───────────────────────────────────────────
    path("session/start-web/", start_session_web_view, name="start_session_web"),
    path("session/<int:session_id>/", session_detail_web_view, name="session_detail_web"),
    path("session/<int:session_id>/edit/", edit_session_view, name="edit_session"),
    path("session/<int:session_id>/quick-start/", quick_start_scheduled_session_view, name="quick_start_session"),
    path("session/<int:session_id>/cancel-scheduled/", cancel_scheduled_session_view, name="cancel_scheduled_session"),
    path("session/<int:session_id>/print/", printable_qr_sheet_view, name="printable_qr_sheet"),
    path("session/<int:session_id>/students-list/", session_students_list_partial, name="session_students_list"),
    path("record/manual-update-web/", manual_update_web_view, name="manual_update_web"),
    path("record/bulk-update-web/", bulk_update_web_view, name="bulk_update_web"),
    path("checkin/", student_checkin_web_view, name="student_checkin_web"),
    path("qr-image/", qr_image_view, name="qr_image"),
    path("student/records/", student_records_web_view, name="student_records_web"),
    path("records/", student_records_web_view, name="student_records"),
    path("teacher/sessions/", teacher_sessions_web_view, name="teacher_sessions_web"),
    path("session/create-custom/", create_custom_session_view, name="create_custom_session"),
    path("session/create-custom/<int:timetable_id>/", create_custom_session_view, name="create_custom_session_with_id"),
    # 📶 Offline Emergency Mode (No Internet required)
    path("session/<int:session_id>/offline/", offline_emergency_mode_view, name="offline_emergency"),
    # 🔄 Smart routing for compound session URLs (prevents 404)
    path("session/<int:arg1>/<int:arg2>/", flexible_session_route_view, name="flexible_session_route_two_args"),
    path("session/<int:arg1>/<str:extra>/", flexible_session_route_view, name="flexible_session_route_extra"),
]
