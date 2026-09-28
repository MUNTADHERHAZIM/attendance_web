import sys
import os
import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.academics.models import ClassSection, Department, Institution, Session, Course
from apps.accounts.models import TeacherProfile, StudentProfile

# 1. Update existing section to MORNING
sec_morning = ClassSection.objects.filter(name__contains="شعبة أ").first()
if sec_morning:
    sec_morning.name = "شعبة أ - صباحي"
    sec_morning.shift = ClassSection.Shifts.MORNING
    sec_morning.save()
    print("Updated morning section:", sec_morning)

# 2. Create or get Evening section
dept = Department.objects.first()
sec_evening, created = ClassSection.objects.get_or_create(
    name="شعبة ب - مسائي",
    department=dept,
    defaults={
        "level": "المستوى الثالث",
        "shift": ClassSection.Shifts.EVENING,
    }
)
print("Evening section:", sec_evening, "created:", created)

# 3. Associate some students with evening section
students = list(StudentProfile.objects.all())
# Make 12 morning and 8 evening
for idx, std in enumerate(students):
    if idx >= 12:
        std.study_shift = "EVENING"
        std.save()
        std.sections.clear()
        std.sections.add(sec_evening)
    else:
        std.study_shift = "MORNING"
        std.save()
        if sec_morning:
            std.sections.add(sec_morning)

print(f"Shift allocation done: Morning count={StudentProfile.objects.filter(study_shift='MORNING').count()}, Evening count={StudentProfile.objects.filter(study_shift='EVENING').count()}")

# 4. Create an evening course session for the teacher
teacher = TeacherProfile.objects.first()
course_ds = Course.objects.filter(name__contains="تراكيب البيانات").first() or Course.objects.first()
course_ai = Course.objects.filter(name__contains="الذكاء الاصطناعي").first()

import datetime
if course_ai and teacher:
    sess_evening, s_created = Session.objects.get_or_create(
        course=course_ai,
        class_section=sec_evening,
        teacher=teacher,
        day_of_week=Session.WeekDays.MONDAY,
        defaults={
            "start_time": datetime.time(16, 0),
            "end_time": datetime.time(17, 30),
            "shift": ClassSection.Shifts.EVENING,
            "room": "مختبر الحاسوب 2 - المسائي",
        }
    )
    print("Evening session created:", sess_evening, "created:", s_created)

print("Shift seeding complete!")
