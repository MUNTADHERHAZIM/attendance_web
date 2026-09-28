import pytest
import uuid
import time
from django.utils import timezone
from apps.academics.models import Institution, Session, Course, Department, ClassSection, AcademicYear, Semester
from apps.accounts.models import User, StudentProfile, TeacherProfile
from apps.attendance.models import AttendanceSession, AttendanceRecord
from apps.attendance.utils import generate_qr_token, verify_qr_token, is_ip_in_subnet

@pytest.fixture
def test_db_setup(db):
    # Create Institution
    inst = Institution.objects.create(
        name="كلية الحاسب التجريبية",
        allowed_wifi_ssid="Test_WiFi",
        allowed_ip_subnet="192.168.4.0/24"
    )
    
    # Create Academic Structure
    yr = AcademicYear.objects.create(name="2026", start_date=timezone.now().date(), end_date=timezone.now().date())
    sem = Semester.objects.create(academic_year=yr, name="Semester 1", start_date=timezone.now().date(), end_date=timezone.now().date())
    dept = Department.objects.create(institution=inst, name="CS")
    cls_sec = ClassSection.objects.create(department=dept, name="A", level="L1")
    crs = Course.objects.create(department=dept, name="Programming", code="CS11")
    
    # Create Teacher
    teacher_user = User.objects.create_user(username="t1", password="password", role=User.Roles.TEACHER)
    teacher_prof = TeacherProfile.objects.create(user=teacher_user, teacher_id="T1", institution=inst)
    
    # Create Session
    session = Session.objects.create(
        course=crs, class_section=cls_sec, teacher=teacher_prof, 
        day_of_week=0, start_time="09:00:00", end_time="10:50:00"
    )
    
    # Create Attendance Session
    att_session = AttendanceSession.objects.create(
        session=session,
        date=timezone.now().date(),
        end_time=timezone.now() + timezone.timedelta(minutes=30),
        is_active=True
    )
    
    return att_session


def test_qr_token_validation_success(test_db_setup):
    """
    Validates that a correctly signed QR token within 15 seconds succeeds.
    """
    att_session = test_db_setup
    token = generate_qr_token(att_session)
    
    session_result, error = verify_qr_token(token, max_age=15)
    assert session_result == att_session
    assert error is None


def test_qr_token_signature_tampered(test_db_setup):
    """
    Validates that a modified token signature fails verification.
    """
    att_session = test_db_setup
    token = generate_qr_token(att_session)
    
    # Tamper token (append character)
    tampered_token = token + "a"
    
    session_result, error = verify_qr_token(tampered_token, max_age=15)
    assert session_result is None
    assert "غير صالح" in error or "انتهت صلاحية" in error or "توقيع" in error


def test_qr_token_salt_rotation_invalidates(test_db_setup):
    """
    Validates that rotating the salt immediately invalidates all previously generated tokens.
    """
    att_session = test_db_setup
    token = generate_qr_token(att_session)
    
    # Rotate salt in database
    att_session.qr_salt = uuid.uuid4()
    att_session.save()
    
    session_result, error = verify_qr_token(token, max_age=15)
    assert session_result is None
    assert "تم تجديد الرمز" in error


def test_ip_subnet_matching():
    """
    Tests the IP subnet validation helper.
    """
    subnet = "192.168.4.0/24"
    
    # Valid IPs
    assert is_ip_in_subnet("192.168.4.5", subnet) is True
    assert is_ip_in_subnet("192.168.4.254", subnet) is True
    
    # Invalid IPs
    assert is_ip_in_subnet("192.168.1.5", subnet) is False
    assert is_ip_in_subnet("10.0.0.1", subnet) is False
    
    # Edge/Local cases
    assert is_ip_in_subnet("::1", None) is True
