import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.accounts.models import User, StudentProfile, TeacherProfile
from apps.academics.models import ClassSection, Course, Session, Institution, Department
from apps.attendance.models import AttendanceSession, AttendanceRecord
from django.utils import timezone
import random

section = ClassSection.objects.first()
teacher = TeacherProfile.objects.filter(user__username='admin1').first() or TeacherProfile.objects.first()
institution = section.department.institution

# Link existing students to section
for s in StudentProfile.objects.all():
    s.sections.add(section)

print(f"Linked existing students to section: {section.name}")

# Create 15 more Iraqi students
iraqi_students = [
    ('زينب', 'علي الخفاجي', '202410106', '07721110001'),
    ('حسين', 'عباس الربيعي', '202410107', '07721110002'),
    ('مريم', 'سعد التميمي', '202410108', '07721110003'),
    ('أحمد', 'ماجد العامري', '202410109', '07812220004'),
    ('نور الهدى', 'جاسم الشمري', '202410110', '07721110005'),
    ('فاطمة', 'فراس الدليمي', '202410111', '07812220006'),
    ('علي', 'كاظم الحسني', '202410112', '07721110007'),
    ('تبارك', 'قاسم الموسوي', '202410113', '07812220008'),
    ('سجاد', 'عماد البهادلي', '202410114', '07721110009'),
    ('زهراء', 'كريم السعدي', '202410115', '07812220010'),
    ('مهدي', 'صالح الخزرجي', '202410116', '07721110011'),
    ('داليا', 'عبد الرضا', '202410117', '07812220012'),
    ('جعفر', 'صادق المياحي', '202410118', '07721110013'),
    ('هديل', 'ثامر الجبوري', '202410119', '07812220014'),
    ('أمير', 'مؤيد العزاوي', '202410120', '07721110015'),
]

for first, last, sid, phone in iraqi_students:
    uname = f"std_{sid}"
    u, created = User.objects.get_or_create(
        username=uname,
        defaults={
            'first_name': first,
            'last_name': last,
            'role': User.Roles.STUDENT,
            'phone': phone,
            'email': f"{uname}@uobaghdad.edu.iq"
        }
    )
    if created:
        u.set_password('student123')
        u.save()
    sp, _ = StudentProfile.objects.get_or_create(
        user=u,
        defaults={'student_id': sid, 'institution': institution}
    )
    sp.sections.add(section)

total_students = section.students.count()
print(f"Total students in section now: {total_students}")

# Also seed past attendance records across past sessions so attendance rates are realistic
past_sessions = AttendanceSession.objects.filter(session__class_section=section)
all_students = list(section.students.all())
records_created = 0

for att_sess in past_sessions:
    for std in all_students:
        # Give ~85% attendance, 10% absent, 5% late
        rand_val = random.random()
        if rand_val < 0.82:
            st = AttendanceRecord.Statuses.PRESENT
            met = random.choice([AttendanceRecord.Methods.QR, AttendanceRecord.Methods.RFID, AttendanceRecord.Methods.OTP])
        elif rand_val < 0.92:
            st = AttendanceRecord.Statuses.LATE
            met = AttendanceRecord.Methods.QR
        else:
            st = AttendanceRecord.Statuses.ABSENT
            met = AttendanceRecord.Methods.MANUAL

        rec, was_created = AttendanceRecord.objects.get_or_create(
            student=std,
            attendance_session=att_sess,
            defaults={
                'status': st,
                'method': met,
                'notes': 'سجل حضور آلي'
            }
        )
        if was_created:
            records_created += 1

print(f"Created {records_created} attendance records for realistic analytics!")
