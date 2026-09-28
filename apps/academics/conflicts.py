"""
Academic and Schedule Conflict Detection Engine for Universities.
Ensures zero overlap between:
1. Teachers (Instructors cannot be in two places at once)
2. Students / Class Sections (Sections cannot take two classes at the same time)
3. Rooms / Laboratories (Rooms cannot host overlapping classes)
4. Morning vs. Evening Shifts (Prevents cross-shift mismatches and student collisions)
"""

from datetime import datetime, time
from django.utils import timezone


def time_overlap(start1, end1, start2, end2):
    """Returns True if the interval [start1, end1] overlaps with [start2, end2]."""
    if not (start1 and end1 and start2 and end2):
        return False
    # If comparing datetime objects vs time objects, normalize to time
    if isinstance(start1, datetime):
        start1 = start1.time()
    if isinstance(end1, datetime):
        end1 = end1.time()
    if isinstance(start2, datetime):
        start2 = start2.time()
    if isinstance(end2, datetime):
        end2 = end2.time()

    return max(start1, start2) < min(end1, end2)


def check_session_conflict(
    teacher,
    course,
    class_section,
    day_of_week,
    start_time,
    end_time,
    room=None,
    shift="MORNING",
    session_date=None,
    exclude_session_id=None,
    exclude_attendance_session_id=None,
):
    """
    Checks for all types of scheduling and attendance conflicts.
    Returns:
        dict: {
            "has_conflict": bool,
            "conflict_type": str or None,
            "message": str or None,
            "severity": "error" or "warning"
        }
    """
    from apps.academics.models import Session
    from apps.attendance.models import AttendanceSession

    if isinstance(start_time, str):
        try:
            start_time = datetime.strptime(start_time, "%H:%M").time()
        except ValueError:
            start_time = None

    if isinstance(end_time, str):
        try:
            end_time = datetime.strptime(end_time, "%H:%M").time()
        except ValueError:
            end_time = None

    if not start_time or not end_time:
        return {"has_conflict": False, "conflict_type": None, "message": None, "severity": "info"}

    # 1. Shift Mismatch Check
    if class_section and hasattr(class_section, "shift") and class_section.shift:
        if class_section.shift != shift:
            shift_names = {"MORNING": "الصباحية", "EVENING": "المسائية"}
            sec_shift = shift_names.get(class_section.shift, class_section.shift)
            cur_shift = shift_names.get(shift, shift)
            return {
                "has_conflict": True,
                "conflict_type": "SHIFT_MISMATCH",
                "severity": "error",
                "message": f"❌ تعارض في نوع الدراسة: الشعبة ({class_section.name}) مخصصة للدراسة ({sec_shift})، بينما حددت المحاضرة كـ ({cur_shift}). يرجى توحيد نوع الدراسة لمنع تداخل القوائم.",
            }

    # 2. Shift Time sanity checks
    if shift == "MORNING" and start_time.hour >= 15:
        return {
            "has_conflict": True,
            "conflict_type": "SHIFT_TIME_WARNING",
            "severity": "warning",
            "message": f"⚠️ تنبيه التوقيت: وقت البدء ({start_time.strftime('%H:%M')}) يقع في الفترة المسائية بينما نوع الدراسة المحدد (صباحي). يرجى التأكد من التوقيت أو تحويل المحاضرة لمسائي.",
        }

    if shift == "EVENING" and start_time.hour < 12:
        return {
            "has_conflict": True,
            "conflict_type": "SHIFT_TIME_WARNING",
            "severity": "warning",
            "message": f"⚠️ تنبيه التوقيت: وقت البدء ({start_time.strftime('%H:%M')}) يقع في الفترة الصباحية بينما نوع الدراسة المحدد (مسائي). تبدأ الدراسة المسائية عادة بعد الظهر.",
        }

    # 3. Teacher Conflict Check (Scheduled & Live)
    teacher_profile = teacher
    if teacher and hasattr(teacher, "teacher_profile"):
        teacher_profile = teacher.teacher_profile

    if teacher_profile:
        # Check scheduled sessions on same day_of_week
        teacher_sessions = Session.objects.filter(
            teacher=teacher_profile,
            day_of_week=day_of_week,
        )
        if exclude_session_id:
            teacher_sessions = teacher_sessions.exclude(id=exclude_session_id)

        for s in teacher_sessions:
            if time_overlap(start_time, end_time, s.start_time, s.end_time):
                # If course and section are the exact same, it's modifying or starting that session
                if course and s.course_id == course.id and class_section and s.class_section_id == class_section.id:
                    continue
                return {
                    "has_conflict": True,
                    "conflict_type": "TEACHER_CONFLICT",
                    "severity": "error",
                    "message": f"❌ تضارب في جدول الأستاذ: لديك بالفعل محاضرة أخرى مجدولة ({s.course.name}) لشعبة ({s.class_section.name}) في نفس التوقيت ({s.start_time.strftime('%H:%M')} - {s.end_time.strftime('%H:%M')}).",
                }

        # Check active live attendance sessions on session_date
        if session_date:
            active_teacher_sessions = AttendanceSession.objects.filter(
                session__teacher=teacher_profile,
                date=session_date,
                is_active=True,
            )
            if exclude_attendance_session_id:
                active_teacher_sessions = active_teacher_sessions.exclude(id=exclude_attendance_session_id)

            for att in active_teacher_sessions:
                if time_overlap(start_time, end_time, att.start_time.time(), att.end_time.time()):
                    if course and att.session.course_id == course.id and class_section and att.session.class_section_id == class_section.id:
                        continue
                    return {
                        "has_conflict": True,
                        "conflict_type": "TEACHER_CONFLICT",
                        "severity": "error",
                        "message": f"❌ تضارب مباشر: توجد جلسة تحضير نشطة حالياً لمادة ({att.session.course.name}) مستمرة حتى ({att.end_time.strftime('%H:%M')}).",
                    }

    # 4. Section / Students Conflict Check
    if class_section:
        section_sessions = Session.objects.filter(
            class_section=class_section,
            day_of_week=day_of_week,
        )
        if exclude_session_id:
            section_sessions = section_sessions.exclude(id=exclude_session_id)

        for s in section_sessions:
            if time_overlap(start_time, end_time, s.start_time, s.end_time):
                if teacher and s.teacher_id == teacher.id and course and s.course_id == course.id:
                    continue
                teacher_name = s.teacher.user.get_full_name() or s.teacher.user.username if s.teacher else "أستاذ آخر"
                return {
                    "has_conflict": True,
                    "conflict_type": "SECTION_CONFLICT",
                    "severity": "error",
                    "message": f"❌ تضارب في جدول الطلاب: طلاب ({class_section.name}) لديهم محاضرة لمادة ({s.course.name}) مع الأستاذ ({teacher_name}) في نفس التوقيت ({s.start_time.strftime('%H:%M')} - {s.end_time.strftime('%H:%M')}).",
                }

    # 5. Room / Lab Conflict Check
    if room and len(room.strip()) > 3:
        clean_room = room.strip().lower()
        room_sessions = Session.objects.filter(
            day_of_week=day_of_week,
            room__iexact=clean_room,
        )
        if exclude_session_id:
            room_sessions = room_sessions.exclude(id=exclude_session_id)

        for s in room_sessions:
            if time_overlap(start_time, end_time, s.start_time, s.end_time):
                if teacher and s.teacher_id == teacher.id and course and s.course_id == course.id:
                    continue
                return {
                    "has_conflict": True,
                    "conflict_type": "ROOM_CONFLICT",
                    "severity": "error",
                    "message": f"❌ تضارب في القاعة: ({room}) محجوزة في نفس الوقت لمادة ({s.course.name}) لشعبة ({s.class_section.name}). يرجى اختيار قاعة أخرى.",
                }

    return {"has_conflict": False, "conflict_type": None, "message": None, "severity": "success"}
