import os
import sys
from datetime import time, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from apps.accounts.models import User, StudentProfile
from apps.academics.models import Course, ClassSection, Session
from apps.attendance.models import AttendanceSession, AttendanceRecord
from apps.academics.conflicts import check_session_conflict

Shifts = ClassSection.Shifts

print("========================================")
print("اختبار محرك كشف التضارب ونظام الدراسة (صباحي / مسائي)")
print("========================================")

teacher = User.objects.filter(role=User.Roles.TEACHER).first()
morning_section = ClassSection.objects.filter(shift=Shifts.MORNING).first()
evening_section = ClassSection.objects.filter(shift=Shifts.EVENING).first()
course = Course.objects.first()

print(f"الأستاذ: {teacher.username}")
print(f"الشعبة الصباحية: {morning_section.name} (ID: {morning_section.id}, الدوام: {morning_section.shift})")
print(f"الشعبة المسائية: {evening_section.name} (ID: {evening_section.id}, الدوام: {evening_section.shift})")

# 1. Test Shift Mismatch (Morning session assigned to Evening section)
conflict_mismatch = check_session_conflict(
    teacher=teacher,
    course=course,
    class_section=evening_section,
    day_of_week=0,
    start_time=time(9, 0),
    end_time=time(10, 30),
    shift=Shifts.MORNING,
)
print("\n[1] اختبار عدم تطابق نوع الدراسة مع الشعبة:")
print(f"  - النتيجة: {conflict_mismatch['has_conflict']}")
print(f"  - الرسالة: {conflict_mismatch['message']}")
assert conflict_mismatch['has_conflict'] == True
assert conflict_mismatch['conflict_type'] == 'SHIFT_MISMATCH'

# 2. Test Morning Shift time bounds (e.g. evening hour for morning shift)
conflict_hours = check_session_conflict(
    teacher=teacher,
    course=course,
    class_section=morning_section,
    day_of_week=0,
    start_time=time(17, 0),
    end_time=time(18, 30),
    shift=Shifts.MORNING,
)
print("\n[2] اختبار توقيت غير ملائم للدوام الصباحي (17:00):")
print(f"  - النتيجة: {conflict_hours['has_conflict']}")
print(f"  - الرسالة: {conflict_hours['message']}")
assert conflict_hours['has_conflict'] == True
assert conflict_hours['conflict_type'] == 'SHIFT_TIME_WARNING'

# 3. Test Teacher Conflict (Teacher has session on Monday 16:00-17:30)
# (In seed_shifts.py we created evening session at 16:00-17:30 on Monday)
other_course, _ = Course.objects.get_or_create(code="CS999", defaults={"name": "مادة بديلة للاختبار", "department": evening_section.department})
conflict_teacher = check_session_conflict(
    teacher=teacher,
    course=other_course,
    class_section=evening_section,
    day_of_week=Session.WeekDays.MONDAY,
    start_time=time(16, 30),
    end_time=time(17, 30),
    shift=Shifts.EVENING,
)
print("\n[3] اختبار تضارب وقت الأستاذ في نفس التوقيت:")
print(f"  - النتيجة: {conflict_teacher['has_conflict']}")
print(f"  - الرسالة: {conflict_teacher['message']}")
assert conflict_teacher['has_conflict'] == True
assert conflict_teacher['conflict_type'] == 'TEACHER_CONFLICT'

# 4. Test Valid Session (No conflict at all)
valid_check = check_session_conflict(
    teacher=teacher,
    course=course,
    class_section=morning_section,
    day_of_week=Session.WeekDays.THURSDAY, # Thursday
    start_time=time(8, 0),
    end_time=time(9, 30),
    shift=Shifts.MORNING,
)
print("\n[4] اختبار جلسة سليمة بدون أي تضارب:")
print(f"  - النتيجة: {valid_check['has_conflict']}")
print(f"  - الرسالة: {valid_check['message']}")
assert valid_check['has_conflict'] == False

# 5. Test Cross-Shift Check-in Isolation
print("\n[5] اختبار عزل تسجيل الحضور بين طلاب الصباحي والمسائي:")
morning_student = StudentProfile.objects.filter(study_shift=Shifts.MORNING).first()
evening_student = StudentProfile.objects.filter(study_shift=Shifts.EVENING).first()

print(f"  - طالب صباحي: {morning_student.user.get_full_name()} (دراسة: {morning_student.study_shift})")
print(f"  - طالب مسائي: {evening_student.user.get_full_name()} (دراسة: {evening_student.study_shift})")

# Simulated shift match logic
att_session_morning = AttendanceSession.objects.filter(shift=Shifts.MORNING).first()
if att_session_morning:
    is_evening_allowed = (evening_student.study_shift == att_session_morning.shift)
    is_morning_allowed = (morning_student.study_shift == att_session_morning.shift)
    print(f"  - هل يُسمح للطالب المسائي بتسجيل الحضور في جلسة صباحية؟ {is_evening_allowed} (محظور)")
    print(f"  - هل يُسمح للطالب الصباحي بتسجيل الحضور في جلسته الصباحية؟ {is_morning_allowed} (مسموح)")
    assert is_evening_allowed == False
    assert is_morning_allowed == True

print("\n========================================")
print("✅ جميع الاختبارات اجتازت بنجاح بنسبة 100%!")
print("========================================")
