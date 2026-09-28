import os
import sys
import django
from datetime import time, date

# Set up Django environment
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model
from apps.accounts.models import StudentProfile, TeacherProfile
from apps.academics.models import Institution, AcademicYear, Semester, Department, ClassSection, Course, Session

User = get_user_model()

def seed():
    print("Starting database seeding...")

    # 1. Create Super Admin
    admin_user, created = User.objects.get_or_create(
        username="admin",
        defaults={
            "email": "admin@attendance.local",
            "role": User.Roles.SUPER_ADMIN,
            "is_staff": True,
            "is_superuser": True
        }
    )
    if created:
        admin_user.set_password("admin123")
        admin_user.save()
        print("Created Super Admin user: admin/admin123")

    # 2. Create Institution
    institution, _ = Institution.objects.get_or_create(
        name="جامعة بغداد - كلية علوم الحاسوب وتكنولوجيا المعلومات",
        defaults={
            "address": "بغداد، جمهورية العراق",
            "allowed_wifi_ssid": "UOB_Students_WiFi",
            "allowed_ip_subnet": "192.168.1.0/24"
        }
    )
    print(f"Institution: {institution.name}")

    # 3. Create Academic Year and Semester
    academic_year, _ = AcademicYear.objects.get_or_create(
        name="2025/2026",
        defaults={
            "start_date": date(2025, 9, 1),
            "end_date": date(2026, 6, 30),
            "is_active": True
        }
    )
    
    semester, _ = Semester.objects.get_or_create(
        academic_year=academic_year,
        name="الفصل الأول",
        defaults={
            "start_date": date(2025, 9, 1),
            "end_date": date(2026, 1, 15),
            "is_active": True
        }
    )
    print(f"Academic Year: {academic_year.name}, Semester: {semester.name}")

    # 4. Create Department and Class Section
    department, _ = Department.objects.get_or_create(
        institution=institution,
        name="قسم علوم الحاسب",
        defaults={"code": "CS"}
    )
    
    class_section, _ = ClassSection.objects.get_or_create(
        department=department,
        name="شعبة أ",
        defaults={"level": "المرحلة الثالثة"}
    )
    print(f"Department: {department.name}, Section: {class_section}")

    # 5. Create Course
    course, _ = Course.objects.get_or_create(
        department=department,
        code="CS211",
        defaults={"name": "تراكيب البيانات (Data Structures)"}
    )
    print(f"Course: {course.name}")

    # 6. Create Teacher
    teacher_user, created = User.objects.get_or_create(
        username="teacher1",
        defaults={
            "email": "teacher1@uobaghdad.edu.iq",
            "role": User.Roles.TEACHER,
            "first_name": "حيدر",
            "last_name": "الساعدي",
            "phone": "07724978301"
        }
    )
    if created:
        teacher_user.set_password("teacher123")
        teacher_user.save()
        
    teacher_profile, _ = TeacherProfile.objects.get_or_create(
        user=teacher_user,
        defaults={
            "teacher_id": "T1001",
            "institution": institution,
            "specialization": "علوم الحاسوب والبرمجيات"
        }
    )
    print(f"Teacher: {teacher_user.get_full_name()} ({teacher_profile.teacher_id})")

    # 7. Create Session (Lecture)
    session, _ = Session.objects.get_or_create(
        course=course,
        class_section=class_section,
        teacher=teacher_profile,
        day_of_week=Session.WeekDays.SUNDAY,
        defaults={
            "start_time": time(9, 0),
            "end_time": time(10, 50),
            "room": "قاعة 102"
        }
    )
    print(f"Session Created: {session}")

    # 8. Create Students
    student_data = [
        ("student1", "علي", "الكعبي", "S2001"),
        ("student2", "كرار", "الزبيدي", "S2002"),
        ("student3", "مصطفى", "الجابري", "S2003"),
        ("student4", "حسين", "العبيدي", "S2004"),
        ("student5", "يوسف", "السامرائي", "S2005"),
    ]

    for username, first_name, last_name, student_id in student_data:
        student_user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": f"{username}@uobaghdad.edu.iq",
                "role": User.Roles.STUDENT,
                "first_name": first_name,
                "last_name": last_name,
                "phone": f"0771234567{student_id[-1]}"
            }
        )
        if created:
            student_user.set_password("student123")
            student_user.save()
            
        student_profile, _ = StudentProfile.objects.get_or_create(
            user=student_user,
            defaults={
                "student_id": student_id,
                "institution": institution,
                "birth_date": date(2005, 1, 15)
            }
        )
        print(f"Student: {student_user.get_full_name()} ({student_profile.student_id})")

    # 10. Create Institution Admin User
    inst_admin_user, created = User.objects.get_or_create(
        username="uob_admin",
        defaults={
            "email": "admin@uobaghdad.edu.iq",
            "role": User.Roles.INSTITUTION_ADMIN,
            "is_staff": True,
            "is_superuser": True,
            "first_name": "منتظر",
            "last_name": "حازم",
            "phone": "07724978301"
        }
    )
    if created:
        inst_admin_user.set_password("admin123")
        inst_admin_user.save()
    print(f"Institution Admin: {inst_admin_user.get_full_name()}")

    print("Seeding completed successfully!")

if __name__ == "__main__":
    seed()
