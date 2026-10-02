import logging
from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def send_absence_email_task(self, student_id, course_name, date_str):
    """
    Sends an immediate email + in-app notification to the student's guardian
    when an absence is recorded.
    """
    from apps.accounts.models import StudentProfile
    from .models import Notification

    try:
        student = StudentProfile.objects.select_related(
            "user", "guardian__user"
        ).get(student_id=student_id)

        student_name = student.user.get_full_name() or student.user.username
        title = f"إشعار غياب: {student_name}"
        message_body = (
            f"نود إحاطتكم علماً بأن الطالب (ة) {student_name} "
            f"قد تم تسجيله غائباً في:\n"
            f"المادة: {course_name}\n"
            f"التاريخ: {date_str}\n\n"
            f"يرجى التواصل مع إدارة المؤسسة في حال وجود عذر."
        )

        # ─── In-app notification to student ──────────────────────────
        Notification.objects.create(
            recipient=student.user,
            notification_type=Notification.Types.ABSENCE,
            title=title,
            message=message_body,
        )

        # ─── In-app + email notification to guardian ──────────────────
        if student.guardian:
            Notification.objects.create(
                recipient=student.guardian.user,
                notification_type=Notification.Types.ABSENCE,
                title=title,
                message=message_body,
            )

            guardian_email = student.guardian.user.email
            if guardian_email:
                guardian_name = (
                    student.guardian.user.get_full_name()
                    or student.guardian.user.username
                )
                try:
                    send_mail(
                        subject=title,
                        message=f"عزيزي ولي الأمر {guardian_name}،\n\n{message_body}\n\nدمتم بخير،\nإدارة شؤون الطلاب",
                        from_email=settings.DEFAULT_FROM_EMAIL or "noreply@attendance.local",
                        recipient_list=[guardian_email],
                        fail_silently=False,
                    )
                    logger.info(f"تم إرسال إشعار الغياب لولي أمر الطالب {student_id}")
                except Exception as mail_exc:
                    logger.error(f"فشل إرسال البريد لولي أمر الطالب {student_id}: {mail_exc}")
                    raise self.retry(exc=mail_exc)
        else:
            logger.info(f"الطالب {student_id} لا يملك ولي أمر مرتبط. تم تخطي البريد الإلكتروني.")

        return "تم إرسال الإشعار بنجاح"

    except StudentProfile.DoesNotExist:
        logger.error(f"ملف الطالب {student_id} غير موجود.")
        return "الملف غير موجود"
    except Exception as e:
        logger.error(f"خطأ في إرسال إشعار الغياب للطالب {student_id}: {str(e)}")
        raise self.retry(exc=e)


@shared_task
def send_weekly_report_task():
    """
    Periodic task (weekly via Celery Beat) that aggregates attendance stats
    and emails a report to each teacher.
    """
    from apps.accounts.models import TeacherProfile
    from apps.attendance.models import AttendanceRecord

    teachers = TeacherProfile.objects.select_related("user").filter(
        user__email__isnull=False
    ).exclude(user__email="")

    if not teachers.exists():
        return "لا يوجد معلمون مسجلون بعناوين بريد إلكتروني"

    sent_count = 0
    today = timezone.now().date()
    one_week_ago = today - timezone.timedelta(days=7)

    for teacher in teachers:
        sessions = teacher.sessions.select_related("course", "class_section").all()
        if not sessions.exists():
            continue

        report_lines = [
            f"التقرير الأسبوعي للحضور والغياب ({one_week_ago} → {today})",
            "=" * 50,
            "",
        ]
        has_data = False

        for sess in sessions:
            records = AttendanceRecord.objects.filter(
                attendance_session__session=sess,
                attendance_session__date__range=[one_week_ago, today],
            )
            total = records.count()
            if total == 0:
                continue

            has_data = True
            absent = records.filter(status="ABSENT").count()
            present = records.filter(status__in=["PRESENT", "LATE"]).count()
            absence_rate = (absent / total) * 100

            report_lines += [
                f"📚 المادة: {sess.course.name} | الشعبة: {sess.class_section}",
                f"   الحضور: {present} | الغياب: {absent} | المجموع: {total}",
                f"   نسبة الغياب: {absence_rate:.1f}%",
                "",
            ]

        if not has_data:
            continue

        report_content = "\n".join(report_lines)

        send_mail(
            subject=f"تقرير الحضور الأسبوعي — {today}",
            message=report_content,
            from_email=settings.DEFAULT_FROM_EMAIL or "noreply@attendance.local",
            recipient_list=[teacher.user.email],
            fail_silently=True,
        )
        sent_count += 1
        logger.info(f"تم إرسال التقرير الأسبوعي للمعلم {teacher.user.username}")

    return f"تم إرسال {sent_count} تقرير للمعلمين"


@shared_task
def mark_absent_for_closed_sessions():
    """
    After a session closes, automatically creates ABSENT records for students
    who did not check in. This ensures complete attendance records.
    """
    from apps.attendance.models import AttendanceSession, AttendanceRecord
    from apps.accounts.models import StudentProfile

    now = timezone.now()
    recently_closed = AttendanceSession.objects.filter(
        is_active=False,
        end_time__gte=now - timezone.timedelta(minutes=5),
        end_time__lte=now,
    ).select_related("session__class_section")

    created_count = 0

    for att_session in recently_closed:
        class_section = att_session.session.class_section
        enrolled_students = StudentProfile.objects.filter(sections=class_section)
        already_recorded = set(
            AttendanceRecord.objects.filter(
                attendance_session=att_session
            ).values_list("student_id", flat=True)
        )

        absent_records = []
        for student in enrolled_students:
            if student.id not in already_recorded:
                absent_records.append(
                    AttendanceRecord(
                        student=student,
                        attendance_session=att_session,
                        status=AttendanceRecord.Statuses.ABSENT,
                        method=AttendanceRecord.Methods.MANUAL,
                        notes="تسجيل غياب تلقائي عند إغلاق الجلسة",
                    )
                )

        if absent_records:
            AttendanceRecord.objects.bulk_create(absent_records, ignore_conflicts=True)
            created_count += len(absent_records)

            # Notify guardians asynchronously
            for record in absent_records:
                send_absence_email_task.delay(
                    student_id=record.student.student_id,
                    course_name=att_session.session.course.name,
                    date_str=str(att_session.date),
                )

    logger.info(f"تم إنشاء {created_count} سجل غياب تلقائي عند إغلاق الجلسات.")
    return f"سجلات الغياب التلقائية: {created_count}"
