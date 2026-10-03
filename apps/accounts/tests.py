import pytest
from django.test import Client
from rest_framework.test import APIClient

from apps.accounts.models import StudentProfile, TeacherProfile, User
from apps.academics.models import ClassSection, Department, Institution
from apps.core.models import SystemSetting


@pytest.mark.django_db
def test_teacher_verification_code_is_not_disclosed_or_weakly_overridden():
    client = APIClient()
    current_code = SystemSetting.get_teacher_code()

    assert client.get("/accounts/api/teacher-code/").status_code == 405

    response = client.post(
        "/accounts/api/teacher-code/",
        {"code": "FACULTY"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data == {"valid": False}

    session = client.session
    session["captcha_register"] = "AB12CD"
    session.save()
    response = client.post(
        "/accounts/api/register/",
        {
            "name": "teacher_candidate",
            "password": "strong-test-password",
            "phone": "07724978301",
            "role": User.Roles.TEACHER,
            "teacher_code": "EDU2026",
            "captcha": "AB12CD",
        },
        format="json",
    )
    assert response.status_code == 400
    assert not User.objects.filter(username="teacher_candidate").exists()
    assert current_code not in response.content.decode()

    login_page = client.get("/login/")
    assert login_page.status_code == 200
    assert current_code.encode() not in login_page.content


@pytest.mark.django_db
def test_teacher_registration_does_not_grant_django_admin_access():
    code = SystemSetting.get_teacher_code()
    client = APIClient()
    session = client.session
    session["captcha_register"] = "AB12CD"
    session.save()
    response = client.post(
        "/accounts/api/register/",
        {
            "name": "verified_teacher",
            "password": "strong-test-password",
            "phone": "07724978302",
            "role": User.Roles.TEACHER,
            "teacher_code": code,
            "captcha": "AB12CD",
        },
        format="json",
    )

    assert response.status_code == 201
    teacher = User.objects.get(username="verified_teacher")
    assert teacher.is_staff is False


@pytest.mark.django_db
def test_registration_requires_one_time_server_captcha():
    client = APIClient()
    payload = {
        "name": "captcha_required_student",
        "password": "strong-test-password",
        "phone": "07724978303",
        "role": User.Roles.STUDENT,
    }

    response = client.post("/api/register/", payload, format="json")
    assert response.status_code == 400
    assert not User.objects.filter(username=payload["name"]).exists()

    session = client.session
    session["captcha_register"] = "AB12CD"
    session.save()
    response = client.post(
        "/api/register/",
        {**payload, "captcha": "AB12CD"},
        format="json",
    )

    assert response.status_code == 201
    assert User.objects.filter(username=payload["name"]).exists()


@pytest.mark.django_db
def test_login_captcha_is_validated_on_server_and_consumed_once():
    user = User.objects.create_user(
        username="captcha_login_user",
        password="strong-test-password",
        role=User.Roles.STUDENT,
    )
    client = Client()
    session = client.session
    session["captcha_login"] = "AB12CD"
    session.save()

    response = client.post(
        "/login/",
        {
            "username": user.username,
            "password": "strong-test-password",
            "captcha_login": "WRONG1",
        },
    )
    assert response.status_code == 200
    assert not response.wsgi_request.user.is_authenticated

    session = client.session
    session["captcha_login"] = "AB12CD"
    session.save()
    response = client.post(
        "/login/",
        {
            "username": user.username,
            "password": "strong-test-password",
            "captcha_login": "AB12CD",
        },
    )
    assert response.status_code == 302
    assert int(client.session["_auth_user_id"]) == user.id


@pytest.mark.django_db
def test_captcha_image_is_not_cached_and_does_not_expose_answer():
    response = Client().get("/captcha/image/?purpose=login")

    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert "no-store" in response["Cache-Control"]
    assert b"captcha_login" not in response.content


@pytest.mark.django_db
def test_only_super_admin_can_change_captcha_setting():
    from django.test import Client as DjangoClient

    setting, _ = SystemSetting.objects.update_or_create(
        key="ENABLE_CAPTCHA",
        defaults={"value": "true"},
    )
    user = User.objects.create_user(
        username="captcha_admin",
        password="password",
        role=User.Roles.SUPER_ADMIN,
    )
    client = DjangoClient()
    client.force_login(user)

    response = client.post("/reports/toggle-captcha/", {"enabled": "false"})

    assert response.status_code == 302
    setting.refresh_from_db()
    assert setting.value == "false"

    setting_admin_user = User.objects.create_superuser(
        username="django_captcha_admin",
        password="admin-test-password",
        email="django-captcha-admin@example.test",
    )
    client.force_login(setting_admin_user)
    response = client.get(f"/admin/core/systemsetting/{setting.pk}/change/")
    assert response.status_code == 200
    assert "تفعيل CAPTCHA".encode() in response.content

    response = client.post("/admin/core/systemsetting/captcha-toggle/")
    assert response.status_code == 302
    setting.refresh_from_db()
    assert setting.value == "true"

    response = client.post("/admin/core/systemsetting/captcha-toggle/")
    assert response.status_code == 302
    setting.refresh_from_db()
    assert setting.value == "false"

    institution_admin = User.objects.create_user(
        username="captcha_institution_admin",
        password="password",
        role=User.Roles.INSTITUTION_ADMIN,
    )
    client.force_login(institution_admin)
    response = client.post("/reports/toggle-captcha/", {"enabled": "true"})

    assert response.status_code == 302
    setting.refresh_from_db()
    assert setting.value == "false"


@pytest.mark.django_db
def test_disabling_captcha_allows_registration_without_a_challenge():
    SystemSetting.objects.update_or_create(
        key="ENABLE_CAPTCHA",
        defaults={"value": "false"},
    )
    response = APIClient().post(
        "/api/register/",
        {
            "name": "captcha_disabled_student",
            "password": "strong-test-password",
            "phone": "07724978304",
            "role": User.Roles.STUDENT,
        },
        format="json",
    )

    assert response.status_code == 201
    assert User.objects.filter(username="captcha_disabled_student").exists()


@pytest.mark.django_db
def test_teacher_students_page_renders_without_department_or_specialization():
    institution = Institution.objects.create(name="Test Institution")
    teacher_user = User.objects.create_user(
        username="teacher_without_department",
        password="strong-test-password",
        role=User.Roles.TEACHER,
    )
    TeacherProfile.objects.create(
        user=teacher_user,
        teacher_id="T-NO-DEPT",
        institution=institution,
        department=None,
        specialization=None,
    )
    client = Client()
    client.force_login(teacher_user)

    response = client.get("/accounts/teacher/students/")

    assert response.status_code == 200
    assert "القسم الأكاديمي".encode() in response.content


@pytest.mark.django_db
def test_teacher_can_import_pasted_names_with_generated_student_ids():
    institution = Institution.objects.create(name="Paste Import Institution")
    department = Department.objects.create(institution=institution, name="Computer Science")
    section = ClassSection.objects.create(
        department=department,
        name="A",
        level="المرحلة الأولى",
        shift="MORNING",
    )
    teacher_user = User.objects.create_user(
        username="paste_import_teacher",
        password="strong-test-password",
        role=User.Roles.TEACHER,
    )
    TeacherProfile.objects.create(
        user=teacher_user,
        teacher_id="T-PASTE",
        institution=institution,
        department=department,
    )

    existing_user = User.objects.create_user(
        username="existing_paste_student",
        first_name="باسم",
        last_name="محمد",
        role=User.Roles.STUDENT,
    )
    existing_student = StudentProfile.objects.create(
        user=existing_user,
        student_id="S-EXISTING",
        institution=institution,
        study_shift="MORNING",
    )
    existing_student.sections.add(section)

    client = Client()
    client.force_login(teacher_user)
    response = client.post(
        "/accounts/teacher/students/import/",
        {
            "import_mode": "paste",
            "section_id": str(section.id),
            "student_names": "فاطمة علي حسن\nفاطمه علي حسن\nمريم صالح\nباسم محمد",
        },
    )

    assert response.status_code == 302
    students = StudentProfile.objects.filter(sections=section).select_related("user")
    assert students.count() == 3
    imported = students.exclude(id=existing_student.id)
    assert {student.user.get_full_name() for student in imported} == {
        "فاطمة علي حسن",
        "مريم صالح",
    }
    assert all(student.student_id.startswith("AUTO-") for student in imported)
    assert all(not student.user.has_usable_password() for student in imported)


@pytest.mark.django_db
def test_teacher_data_isolation_between_different_teachers():
    institution = Institution.objects.create(name="Isolation Test University")
    department = Department.objects.create(institution=institution, name="Computer Dept")
    from apps.academics.models import Course, Session

    # Teacher 1 setup
    teacher1_user = User.objects.create_user(
        username="teacher_alpha",
        password="strong-test-password",
        role=User.Roles.TEACHER,
    )
    teacher1_profile = TeacherProfile.objects.create(
        user=teacher1_user,
        teacher_id="T-ALPHA",
        institution=institution,
        department=department,
    )
    sec1 = ClassSection.objects.create(department=department, name="Section Alpha", level="المرحلة الأولى", shift="MORNING")
    course1 = Course.objects.create(department=department, name="Algorithms", code="ALG101")
    Session.objects.create(
        teacher=teacher1_profile,
        course=course1,
        class_section=sec1,
        day_of_week=0,
        start_time="09:00",
        end_time="10:00",
        shift="MORNING",
    )

    std1_user = User.objects.create_user(username="student_alpha", first_name="Ali", last_name="Ahmed", role=User.Roles.STUDENT)
    std1 = StudentProfile.objects.create(user=std1_user, student_id="STD-001", institution=institution, study_shift="MORNING")
    std1.sections.add(sec1)

    # Teacher 2 setup (Brand new teacher)
    teacher2_user = User.objects.create_user(
        username="teacher_beta",
        password="strong-test-password",
        role=User.Roles.TEACHER,
    )
    TeacherProfile.objects.create(
        user=teacher2_user,
        teacher_id="T-BETA",
        institution=institution,
        department=department,
    )

    client2 = Client()
    client2.force_login(teacher2_user)

    # 1. Teacher 2 visits student management screen -> MUST see 0 students and 0 sections
    resp = client2.get("/accounts/teacher/students/")
    assert resp.status_code == 200
    assert resp.context["total_count"] == 0
    assert len(resp.context["students_list"]) == 0
    assert resp.context["my_sections"].count() == 0

    # 2. Teacher 2 visits create custom session GET -> MUST only see their own courses/sections (empty for new teacher)
    resp_create_get = client2.get("/attendance/session/create-custom/")
    assert resp_create_get.status_code == 200
    assert resp_create_get.context["courses"].count() == 0
    assert resp_create_get.context["sections"].count() == 0

    # 3. Teacher 2 creates a new custom session with new course and section
    resp_create_post = client2.post(
        "/attendance/session/create-custom/",
        {
            "new_course_name": "Database Systems",
            "new_section_name": "Section Beta",
            "shift": "MORNING",
            "duration_minutes": "60",
            "room": "Room 204",
        },
    )
    assert resp_create_post.status_code == 302
    from apps.attendance.models import AttendanceSession
    new_att_sess = AttendanceSession.objects.filter(session__teacher__user=teacher2_user).first()
    assert new_att_sess is not None
    assert new_att_sess.session.course.name == "Database Systems"
    assert new_att_sess.session.class_section.name == "Section Beta"

    # The new section must start with 0 students
    assert new_att_sess.session.class_section.students.count() == 0
