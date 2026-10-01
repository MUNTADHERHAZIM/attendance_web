import pytest
import uuid
import time
from rest_framework.test import APIClient
from django.utils import timezone
from apps.academics.models import Institution, Session, Course, Department, ClassSection, AcademicYear, Semester
from apps.accounts.models import User, StudentProfile, TeacherProfile
from apps.attendance.models import AttendanceSession, AttendanceRecord
from apps.attendance.utils import generate_qr_token, verify_qr_token, is_ip_in_subnet
from apps.core.models import AuditLog

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


@pytest.fixture
def attendance_api_setup(test_db_setup):
    attendance_session = test_db_setup
    institution = attendance_session.session.course.department.institution
    section = attendance_session.session.class_section

    other_user = User.objects.create_user(
        username="other_teacher",
        password="strong-test-password",
        role=User.Roles.TEACHER,
    )
    TeacherProfile.objects.create(
        user=other_user,
        teacher_id="T2",
        institution=institution,
    )

    student_user = User.objects.create_user(
        username="enrolled_student",
        password="strong-test-password",
        role=User.Roles.STUDENT,
    )
    student = StudentProfile.objects.create(
        user=student_user,
        student_id="S100",
        institution=institution,
    )
    student.sections.add(section)

    record = AttendanceRecord.objects.create(
        student=student,
        attendance_session=attendance_session,
        status=AttendanceRecord.Statuses.ABSENT,
    )
    return attendance_session, other_user, student_user, student, record


def test_teacher_cannot_read_another_teachers_qr(attendance_api_setup):
    attendance_session, other_user, _, _, _ = attendance_api_setup
    client = APIClient()
    client.force_authenticate(user=other_user)

    response = client.get(
        f"/attendance/api/session/{attendance_session.id}/qr/"
    )

    assert response.status_code == 404


def test_malformed_freeze_qr_json_returns_bad_request(attendance_api_setup):
    from django.test import Client

    attendance_session, _, _, _, _ = attendance_api_setup
    client = Client()
    client.force_login(attendance_session.session.teacher.user)

    response = client.post(
        f"/attendance/api/session/{attendance_session.id}/toggle-freeze/",
        data="{",
        content_type="application/json",
    )

    assert response.status_code == 400


def test_teacher_cannot_change_another_teachers_attendance_record(attendance_api_setup):
    _, other_user, _, _, record = attendance_api_setup
    client = APIClient()
    client.force_authenticate(user=other_user)

    response = client.post(
        "/attendance/api/record/update/",
        {"record_id": record.id, "status": AttendanceRecord.Statuses.PRESENT},
        format="json",
    )

    assert response.status_code == 404
    record.refresh_from_db()
    assert record.status == AttendanceRecord.Statuses.ABSENT


def test_same_institution_unenrolled_student_can_check_in_for_teacher_review(attendance_api_setup):
    attendance_session, _, _, student, _ = attendance_api_setup
    student.sections.clear()
    client = APIClient()
    client.force_authenticate(user=student.user)

    response = client.post(
        "/attendance/api/checkin/",
        {"token": generate_qr_token(attendance_session)},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["requires_teacher_review"] is True
    assert "غير مسجل" in response.data["message"]
    assert not student.sections.filter(
        id=attendance_session.session.class_section_id
    ).exists()
    record = AttendanceRecord.objects.get(
        student=student,
        attendance_session=attendance_session,
    )
    assert record.status in [AttendanceRecord.Statuses.PRESENT, AttendanceRecord.Statuses.LATE]
    assert "يتطلب مراجعة الأستاذ" in record.notes
    assert AuditLog.objects.filter(
        action="تسجيل حضور طالب يتطلب مراجعة الأستاذ",
        details__student_profile_id=student.id,
    ).exists()


def test_unknown_guest_is_recorded_as_provisional_student(attendance_api_setup):
    attendance_session, _, _, _, _ = attendance_api_setup
    client = APIClient()

    response = client.post(
        "/attendance/api/checkin/",
        {
            "token": generate_qr_token(attendance_session),
            "student_name": "سارة حسن",
            "device_id": "shared-tablet-1",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["is_new_student"] is True
    assert response.data["requires_teacher_review"] is True
    assert "بانتظار مراجعة الأستاذ" in response.data["message"]
    profile = StudentProfile.objects.get(student_id__startswith="NEW-")
    record = AttendanceRecord.objects.get(
        student=profile,
        attendance_session=attendance_session,
    )
    assert profile.institution_id == attendance_session.session.class_section.department.institution_id
    assert not profile.sections.exists()
    assert not profile.user.has_usable_password()
    assert "يتطلب مراجعة الأستاذ" in record.notes
    assert AuditLog.objects.filter(
        action="تسجيل حضور طالب يتطلب مراجعة الأستاذ",
        details__student_profile_id=profile.id,
    ).exists()


def test_offline_guest_queue_keeps_the_submitted_student_name_for_signed_in_users(
    attendance_api_setup,
):
    attendance_session, _, student_user, _, _ = attendance_api_setup
    client = APIClient()
    client.force_authenticate(user=student_user)

    response = client.post(
        "/attendance/api/checkin/",
        {
            "token": generate_qr_token(attendance_session),
            "student_name": "ضيف من الجهاز",
            "guest_checkin": True,
            "device_id": "offline-shared-device",
        },
        format="json",
    )

    assert response.status_code == 200
    profile = StudentProfile.objects.get(student_id__startswith="NEW-")
    assert profile.user.get_full_name() == "ضيف من الجهاز"
    assert not AttendanceRecord.objects.filter(
        student__user=student_user,
        attendance_session=attendance_session,
        status__in=[AttendanceRecord.Statuses.PRESENT, AttendanceRecord.Statuses.LATE],
    ).exists()


def test_same_device_can_register_multiple_students_and_same_name_is_idempotent(
    attendance_api_setup,
):
    attendance_session, _, _, _, _ = attendance_api_setup
    client = APIClient()
    token = generate_qr_token(attendance_session)

    for index in range(6):
        response = client.post(
            "/attendance/api/checkin/",
            {
                "token": token,
                "student_name": f"طالب جديد {index}",
                "device_id": "shared-tablet-many-students",
            },
            format="json",
        )
        assert response.status_code == 200

    assert StudentProfile.objects.filter(student_id__startswith="NEW-").count() == 6
    assert AttendanceRecord.objects.filter(
        attendance_session=attendance_session,
        student__student_id__startswith="NEW-",
    ).count() == 6

    duplicate_response = client.post(
        "/attendance/api/checkin/",
        {
            "token": token,
            "student_name": "طالب جديد 0",
            "device_id": "shared-tablet-many-students",
        },
        format="json",
    )
    assert duplicate_response.status_code == 200
    assert duplicate_response.data["already_recorded"] is True
    assert StudentProfile.objects.filter(student_id__startswith="NEW-").count() == 6


def test_student_from_another_institution_cannot_check_in(attendance_api_setup):
    attendance_session, _, _, _, _ = attendance_api_setup
    other_institution = Institution.objects.create(name="مؤسسة أخرى")
    other_user = User.objects.create_user(
        username="cross_institution_student",
        password="test-password",
        role=User.Roles.STUDENT,
    )
    StudentProfile.objects.create(
        user=other_user,
        student_id="OTHER-1",
        institution=other_institution,
    )
    client = APIClient()
    client.force_authenticate(user=other_user)

    response = client.post(
        "/attendance/api/checkin/",
        {"token": generate_qr_token(attendance_session)},
        format="json",
    )

    assert response.status_code == 403
    assert not AttendanceRecord.objects.filter(
        student__user=other_user,
        attendance_session=attendance_session,
    ).exists()


def test_bad_qr_does_not_create_a_provisional_student(attendance_api_setup):
    attendance_session, _, _, _, _ = attendance_api_setup
    client = APIClient()

    response = client.post(
        "/attendance/api/checkin/",
        {"token": "invalid-signature", "student_name": "اسم غير مدرج"},
        format="json",
    )

    assert response.status_code == 400
    assert not StudentProfile.objects.filter(student_id__startswith="NEW-").exists()


def test_offline_fallback_has_self_contained_checkin_controls(db):
    response = APIClient().get("/offline/")

    assert response.status_code == 200
    page = response.content.decode()
    assert 'id="offline-student-name"' in page
    assert 'id="offline-qr-token"' in page
    assert "function saveOfflineCheckin()" in page
    assert "function startOfflineScanner()" in page
    assert "offline_attendance_queue" in page
    assert "cdn.tailwindcss.com" not in page
    assert 'href="/attendance/checkin/"' not in page


def test_teacher_live_list_shows_review_alert_for_new_student(attendance_api_setup):
    attendance_session, _, _, _, _ = attendance_api_setup
    client = APIClient()
    client.post(
        "/attendance/api/checkin/",
        {
            "token": generate_qr_token(attendance_session),
            "student_name": "طالب يحتاج مراجعة",
            "device_id": "review-alert-device",
        },
        format="json",
    )
    client.force_login(attendance_session.session.teacher.user)

    response = client.get(
        f"/attendance/session/{attendance_session.id}/students-list/"
    )

    assert response.status_code == 200
    assert "تنبيه للأستاذ" in response.content.decode()
    assert "طالب جديد" in response.content.decode()


def test_weak_or_client_claimed_inaccurate_gps_cannot_bypass_geofence(
    attendance_api_setup,
):
    attendance_session, _, student_user, student, _ = attendance_api_setup
    attendance_session.requires_geofence = True
    attendance_session.latitude = 33.3152
    attendance_session.longitude = 44.3661
    attendance_session.save(update_fields=["requires_geofence", "latitude", "longitude"])
    client = APIClient()
    client.force_authenticate(user=student_user)

    response = client.post(
        "/attendance/api/checkin/",
        {
            "token": generate_qr_token(attendance_session),
            "latitude": 33.3152,
            "longitude": 44.3661,
            "gps_accuracy": 151,
        },
        format="json",
    )

    assert response.status_code == 400
    assert not AttendanceRecord.objects.filter(
        student=student,
        attendance_session=attendance_session,
        status__in=[AttendanceRecord.Statuses.PRESENT, AttendanceRecord.Statuses.LATE],
    ).exists()


def test_expired_attendance_session_is_not_extended_by_checkin(attendance_api_setup):
    attendance_session, _, student_user, student, _ = attendance_api_setup
    expired_at = timezone.now() - timezone.timedelta(minutes=1)
    attendance_session.end_time = expired_at
    attendance_session.save(update_fields=["end_time"])
    client = APIClient()
    client.force_authenticate(user=student_user)

    response = client.post(
        "/attendance/api/checkin/",
        {"token": generate_qr_token(attendance_session)},
        format="json",
    )

    assert response.status_code == 400
    attendance_session.refresh_from_db()
    assert attendance_session.end_time == expired_at
    assert not AttendanceRecord.objects.filter(
        student=student,
        attendance_session=attendance_session,
        status__in=[AttendanceRecord.Statuses.PRESENT, AttendanceRecord.Statuses.LATE],
    ).exists()
