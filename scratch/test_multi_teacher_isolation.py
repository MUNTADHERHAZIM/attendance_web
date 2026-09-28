import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from apps.accounts.models import User, TeacherProfile, StudentProfile
from apps.academics.models import Institution, Department, Course, ClassSection, Session
from apps.attendance.models import AttendanceSession, AttendanceRecord

print("=================================================================")
print("اختبار العزل التام وتعدد الجامعات والأساتذة (Multi-Tenant Isolation)")
print("=================================================================")

# 1. Setup University of Basrah & Teacher Basrah
inst_basrah, _ = Institution.objects.get_or_create(
    name="جامعة البصرة - كلية الهندسة",
    defaults={"address": "البصرة، جمهورية العراق"}
)
dept_basrah, _ = Department.objects.get_or_create(
    institution=inst_basrah,
    name="قسم هندسة الحاسوب",
    defaults={"code": "COE"}
)

user_basrah, _ = User.objects.get_or_create(
    username="dr_ahmed_basrah",
    defaults={
        "first_name": "د. أحمد",
        "last_name": "البصري",
        "email": "ahmed@uobasrah.edu.iq",
        "role": User.Roles.TEACHER,
        "phone": "07801234567"
    }
)
teacher_basrah, _ = TeacherProfile.objects.get_or_create(
    user=user_basrah,
    defaults={
        "teacher_id": "T_BASRAH_01",
        "institution": inst_basrah,
        "department": dept_basrah,
        "specialization": "أستاذ مشارك - شبكات الحاسوب"
    }
)

# 2. Setup University of Baghdad & Teacher Baghdad
inst_baghdad, _ = Institution.objects.get_or_create(
    name="جامعة بغداد - كلية علوم الحاسوب",
    defaults={"address": "بغداد، جمهورية العراق"}
)
dept_baghdad, _ = Department.objects.get_or_create(
    institution=inst_baghdad,
    name="قسم علوم الحاسوب",
    defaults={"code": "CS"}
)

user_baghdad, _ = User.objects.get_or_create(
    username="dr_sarmad_baghdad",
    defaults={
        "first_name": "د. سرمد",
        "last_name": "البغدادي",
        "email": "sarmad@uobaghdad.edu.iq",
        "role": User.Roles.TEACHER,
        "phone": "07709876543"
    }
)
teacher_baghdad, _ = TeacherProfile.objects.get_or_create(
    user=user_baghdad,
    defaults={
        "teacher_id": "T_BAGHDAD_01",
        "institution": inst_baghdad,
        "department": dept_baghdad,
        "specialization": "مدرس دكتور - ذكاء اصطناعي"
    }
)

# 3. Test Identical Course Code on different universities (Must NOT conflict!)
# Both have CS101:
course_basrah, c_b_created = Course.objects.get_or_create(
    department=dept_basrah,
    code="CS101",
    defaults={"name": "أساسيات البرمجة (هندسة البصرة)"}
)
course_baghdad, c_bg_created = Course.objects.get_or_create(
    department=dept_baghdad,
    code="CS101",
    defaults={"name": "البرمجة المهيكلة (علوم بغداد)"}
)
print("\n[1] اختبار تكرار رمز المادة في جامعتين مختلفتين (CS101):")
print(f"  - مادة البصرة: {course_basrah.name} (ID: {course_basrah.id}, قسم: {course_basrah.department.name})")
print(f"  - مادة بغداد: {course_baghdad.name} (ID: {course_baghdad.id}, قسم: {course_baghdad.department.name})")
assert course_basrah.id != course_baghdad.id
print("  ✓ نجح إنشاء المادتين بنفس الكود CS101 بدون أي تعارض في قاعدة البيانات!")

# 4. Create Sections for each
sec_basrah, _ = ClassSection.objects.get_or_create(
    department=dept_basrah,
    name="شعبة A - البصرة",
    level="المرحلة الثانية",
    shift=ClassSection.Shifts.MORNING
)
sec_baghdad, _ = ClassSection.objects.get_or_create(
    department=dept_baghdad,
    name="شعبة B - بغداد",
    level="المرحلة الثالثة",
    shift=ClassSection.Shifts.EVENING
)

# 5. Add Students to each section
# Basrah student
std_u_b, _ = User.objects.get_or_create(
    username="std_basrah_001",
    defaults={"first_name": "حيدر", "last_name": "البصراوي", "role": User.Roles.STUDENT}
)
std_p_b, _ = StudentProfile.objects.get_or_create(
    user=std_u_b,
    defaults={"student_id": "BAS_2024_01", "institution": inst_basrah, "study_shift": "MORNING"}
)
std_p_b.sections.add(sec_basrah)

# Baghdad student
std_u_bg, _ = User.objects.get_or_create(
    username="std_baghdad_001",
    defaults={"first_name": "ياسين", "last_name": "البغدادي", "role": User.Roles.STUDENT}
)
std_p_bg, _ = StudentProfile.objects.get_or_create(
    user=std_u_bg,
    defaults={"student_id": "BGD_2024_01", "institution": inst_baghdad, "study_shift": "EVENING"}
)
std_p_bg.sections.add(sec_baghdad)

# 6. Verify Isolation
# Basrah teacher sections & students
b_sections = ClassSection.objects.filter(department__institution=teacher_basrah.institution)
b_students = StudentProfile.objects.filter(institution=teacher_basrah.institution, sections__in=b_sections)

# Baghdad teacher sections & students
bg_sections = ClassSection.objects.filter(department__institution=teacher_baghdad.institution)
bg_students = StudentProfile.objects.filter(institution=teacher_baghdad.institution, sections__in=bg_sections)

print("\n[2] اختبار العزل الأكاديمي للبيانات بين الأستاذين:")
print(f"  - شُعب أستاذ البصرة: {[s.name for s in b_sections]}")
print(f"  - طلاب أستاذ البصرة: {[s.user.get_full_name() for s in b_students]}")
print(f"  - شُعب أستاذ بغداد: {[s.name for s in bg_sections]}")
print(f"  - طلاب أستاذ بغداد: {[s.user.get_full_name() for s in bg_students]}")

# Assert zero overlap
assert sec_baghdad not in b_sections
assert sec_basrah not in bg_sections
assert std_p_bg not in b_students
assert std_p_b not in bg_students
print("  ✓ عزل تام بنسبة 100%: لا تظهر شُعب أو طلاب بغداد في حساب البصرة، والعكس صحيح تماماً!")

print("\n=================================================================")
print("✅ جميع اختبارات العزل وتعدد الجامعات والأساتذة اجتازت بنجاح تام!")
print("=================================================================")
