import sys
import os
import django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.test import RequestFactory
from django.contrib.auth import get_user_model
from apps.attendance.views import create_custom_session_view

User = get_user_model()
teacher_user = User.objects.filter(role='TEACHER').first()
factory = RequestFactory()

# Test GET
req_get = factory.get('/attendance/session/create-custom/')
req_get.user = teacher_user
res_get = create_custom_session_view(req_get)
print("GET status:", res_get.status_code)
assert res_get.status_code == 200, f"Expected 200, got {res_get.status_code}"

# Test POST with new fields (day, date, time, duration, topic, lecture_type, etc.)
from apps.academics.models import Course, ClassSection
course = Course.objects.first()
section = ClassSection.objects.first()

post_data = {
    'course_id': str(course.id),
    'class_section_id': str(section.id),
    'session_date': '2026-09-28',
    'day_of_week': '2',
    'start_time': '10:00',
    'duration_minutes': '45',
    'topic': 'اختبار عملي واجهات المستخدم والـ QR',
    'lecture_type': 'عملي / مختبر',
    'room': 'مختبر الذكاء الاصطناعي - قاعة 3',
    'requires_geofence': 'on',
    'latitude': '33.278900',
    'longitude': '44.376200',
    'radius_meters': '50',
}

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware

req_post = factory.post('/attendance/session/create-custom/', data=post_data)
req_post.user = teacher_user
# Setup session and messages middleware on request
middleware = SessionMiddleware(lambda r: None)
middleware.process_request(req_post)
req_post.session.save()
setattr(req_post, '_messages', FallbackStorage(req_post))

res_post = create_custom_session_view(req_post)
print("POST status:", res_post.status_code, "Redirect target:", getattr(res_post, 'url', None))
assert res_post.status_code == 302, f"Expected 302, got {res_post.status_code}"

from apps.attendance.models import AttendanceSession
latest_session = AttendanceSession.objects.filter(topic='اختبار عملي واجهات المستخدم والـ QR').latest('id')
print("Successfully created session:")
print("  Date:", latest_session.date)
print("  Topic:", latest_session.topic)
print("  Type:", latest_session.lecture_type)
print("  Start:", latest_session.start_time)
print("  End:", latest_session.end_time)
print("  Geofence:", latest_session.requires_geofence, latest_session.latitude, latest_session.longitude)
print("  Day of week in Session:", latest_session.session.get_day_of_week_display())
print("ALL TESTS PASSED PERFECTLY!")
