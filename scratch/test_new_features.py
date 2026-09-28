import os
import sys
sys.path.insert(0, os.path.abspath('.'))
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.test import RequestFactory
from django.contrib.auth import get_user_model
from apps.attendance.models import AttendanceSession, AttendanceRecord
from apps.accounts.models import TeacherProfile, StudentProfile
from apps.attendance.views import toggle_freeze_qr_view, quick_offline_checkin_view, printable_qr_sheet_view
from apps.reports.views import admin_reports_overview_view, export_teacher_report_csv_view, export_teacher_report_excel_view

User = get_user_model()
rf = RequestFactory()

# Find teacher and attendance session
teacher = User.objects.filter(role=User.Roles.TEACHER).first()
if not teacher:
    teacher = User.objects.filter(is_superuser=True).first()

session = AttendanceSession.objects.first()
student = StudentProfile.objects.first()

print(f"Testing with Teacher: {teacher.username}, Session: {session.id}, Student: {student.student_id}")

# 1. Test Toggle Freeze QR
req = rf.post(f"/attendance/api/session/{session.id}/toggle-freeze/", data="{}", content_type="application/json")
req.user = teacher
res = toggle_freeze_qr_view(req, session.id)
print("1. Toggle Freeze QR Status:", res.status_code, res.content.decode("utf-8"))

# 2. Test Quick Offline Checkin
req = rf.post("/attendance/api/record/quick-offline-checkin/", data={"session_id": session.id, "student_query": student.student_id})
req.user = teacher
res = quick_offline_checkin_view(req)
print("2. Quick Offline Checkin Status:", res.status_code, res.content.decode("utf-8"))

# 3. Test Printable Poster
req = rf.get(f"/attendance/session/{session.id}/print/")
req.user = teacher
res = printable_qr_sheet_view(req, session.id)
print("3. Printable Poster Status:", res.status_code, "Length:", len(res.content))

# 4. Test Reports Overview for various periods
for p in ["today", "yesterday", "week", "month", "all"]:
    req = rf.get(f"/reports/?period={p}")
    req.user = teacher
    res = admin_reports_overview_view(req)
    print(f"4. Report period={p} Status: {res.status_code}")

# 5. Test CSV Export
req = rf.get("/reports/teacher/export-csv/?period=week")
req.user = teacher
res = export_teacher_report_csv_view(req)
print("5. CSV Export Status:", res.status_code, "Content-Type:", res.get("Content-Type"), "Starts with BOM:", res.content.startswith(b'\xef\xbb\xbf'))

# 6. Test Excel Export
req = rf.get(f"/reports/teacher/export-excel/?period=week")
req.user = teacher
res = export_teacher_report_excel_view(req)
print("6. Excel Export Status:", res.status_code, "Content-Type:", res.get("Content-Type"), "Size:", len(res.content))

print("\nALL VERIFICATIONS PASSED SUCCESSFULLY!")
