import sys
import os
import django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.test import RequestFactory
from django.contrib.auth import get_user_model
from apps.reports.views import admin_reports_overview_view, export_teacher_report_excel_view

User = get_user_model()
teacher_user = User.objects.filter(role='TEACHER').first()
factory = RequestFactory()

# 1. Test teacher reports overview with all periods
for period in ['today', 'week', 'month', 'all']:
    req = factory.get(f'/reports/?period={period}')
    req.user = teacher_user
    res = admin_reports_overview_view(req)
    assert res.status_code == 200, f"Period {period} failed: {res.status_code}"
    print(f"Reports Overview [period={period}]: OK (200)")

# 2. Test Excel Export
req_excel = factory.get('/reports/teacher/export-excel/?period=month')
req_excel.user = teacher_user
res_excel = export_teacher_report_excel_view(req_excel)
assert res_excel.status_code == 200, f"Excel export failed: {res_excel.status_code}"
assert res_excel['Content-Type'] == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
print("Excel Export: OK (200, Content-Type correct, Size:", len(res_excel.content), "bytes)")

print("ALL REPORTS TESTS PASSED PERFECTLY!")
