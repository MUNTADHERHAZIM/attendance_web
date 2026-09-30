import urllib.parse
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from apps.accounts.models import StudentProfile, TeacherProfile
from apps.academics.models import Session, Department, ClassSection, Institution, Course
from apps.attendance.models import AttendanceSession, AttendanceRecord
from django.contrib.auth import get_user_model
from django.db.models import Count, Q

User = get_user_model()

@login_required
def dashboard_view(request):
    user = request.user
    
    # 1. INSTITUTION / SUPER ADMIN DASHBOARD
    if user.is_institution_admin() or user.is_super_admin():
        # Get institution context
        institution = None
        if user.is_institution_admin() and hasattr(user, "teacher_profile"):
            institution = user.teacher_profile.institution
        elif hasattr(user, "student_profile"):
            institution = user.student_profile.institution
            
        if not institution:
            institution = Institution.objects.first()

        if not institution:
            return render(request, "dashboard/admin.html", {"message": "يرجى تهيئة مؤسسة تعليمية أولاً."})

        # Statistics
        total_students = StudentProfile.objects.filter(institution=institution).count()
        total_teachers = TeacherProfile.objects.filter(institution=institution).count()
        total_classes = ClassSection.objects.filter(department__institution=institution).count()
        
        # Today's active attendance sessions
        today = timezone.now().date()
        active_sessions_today = AttendanceSession.objects.filter(
            session__class_section__department__institution=institution,
            date=today,
            is_active=True
        ).count()

        # Compute attendance rate
        records_today = AttendanceRecord.objects.filter(
            attendance_session__date=today,
            student__institution=institution
        )
        total_present_today = records_today.filter(status__in=["PRESENT", "LATE"]).count()
        total_records_today = records_today.count()
        
        attendance_rate_today = 0
        if total_records_today > 0:
            attendance_rate_today = (total_present_today / total_records_today) * 100

        # Absence Warning list (> 20% absence)
        critical_students = []
        students = StudentProfile.objects.filter(institution=institution).select_related("user")
        for std in students:
            total_records = std.attendance_records.count()
            if total_records > 0:
                absences = std.attendance_records.filter(status="ABSENT").count()
                absence_pct = (absences / total_records) * 100
                if absence_pct >= 20:
                    critical_students.append({
                        "student": std,
                        "total": total_records,
                        "absences": absences,
                        "pct": round(absence_pct, 1)
                    })
        
        # Handle Institution Profile / Logo & Teacher Code updates from Admin Dashboard
        from apps.core.models import SystemSetting
        from django.contrib import messages

        if request.method == "POST":
            if "update_institution" in request.POST:
                inst_name = request.POST.get("institution_name", "").strip()
                inst_logo = request.FILES.get("institution_logo")
                if inst_name:
                    if not institution:
                        institution = Institution.objects.create(name=inst_name)
                    else:
                        institution.name = inst_name
                    if inst_logo:
                        institution.logo = inst_logo
                    institution.save()
                    messages.success(request, "تم حفظ بيانات وشعار المؤسسة بنجاح!")
                    return redirect("dashboard")

            elif "new_teacher_code" in request.POST:
                new_code = request.POST.get("new_teacher_code", "").strip()
                if new_code:
                    SystemSetting.objects.update_or_create(
                        key="TEACHER_VERIFICATION_CODE",
                        defaults={
                            "value": new_code,
                            "description": "كود التحقق الأكاديمي لتسجيل الكادر التعليمي"
                        }
                    )
                    messages.success(request, f"تم تحديث رمز تسجيل الأساتذة بنجاح إلى: {new_code}")
                    return redirect("dashboard")

        # Sort critical students by absence percentage descending
        critical_students = sorted(critical_students, key=lambda x: x["pct"], reverse=True)[:5]

        teacher_code = SystemSetting.get_teacher_code()
        total_courses = Course.objects.filter(department__institution=institution).count() if institution else Course.objects.count()
        total_users = User.objects.count()

        context = {
            "institution": institution,
            "total_students": total_students,
            "total_teachers": total_teachers,
            "total_classes": total_classes,
            "total_courses": total_courses,
            "total_users": total_users,
            "teacher_code": teacher_code,
            "active_sessions_today": active_sessions_today,
            "attendance_rate_today": round(attendance_rate_today, 1),
            "critical_students": critical_students,
        }
        return render(request, "dashboard/admin.html", context)

    # 2. TEACHER DASHBOARD (Multi-Tenant & Isolated)
    elif user.is_teacher():
        teacher_profile = getattr(user, "teacher_profile", None)
        if not teacher_profile:
            inst = Institution.objects.first() or Institution.objects.create(name="جامعة بغداد - كلية علوم الحاسوب وتكنولوجيا المعلومات")
            teacher_profile = TeacherProfile.objects.create(
                user=user,
                teacher_id=f"T{1000 + user.id}",
                institution=inst,
                specialization="عضو هيئة التدريس"
            )

        # 1. Teacher Timetable Sessions
        sessions = Session.objects.filter(teacher=teacher_profile).select_related("course", "class_section").order_by("day_of_week", "start_time")
        
        # 2. Check active & scheduled sessions
        today = timezone.now().date()
        today_day_of_week = (today.weekday() + 2) % 7
        
        # Today's recurring timetable sessions
        today_timetable_sessions = sessions.filter(day_of_week=today_day_of_week)

        active_attendance_sessions = AttendanceSession.objects.filter(
            session__teacher=teacher_profile,
            date=today,
            is_active=True
        ).select_related("session__course", "session__class_section")

        # Upcoming scheduled sessions (dates >= today and not active)
        upcoming_scheduled_sessions = AttendanceSession.objects.filter(
            Q(session__teacher=teacher_profile) | Q(created_by=user),
            date__gte=today,
            is_active=False
        ).select_related("session__course", "session__class_section").order_by("date", "start_time")

        # 3. Courses: Taught in timetable OR owned in department/institution
        course_ids = list(sessions.values_list("course_id", flat=True).distinct())
        dept_courses = Course.objects.filter(department__institution=teacher_profile.institution)
        if teacher_profile.department:
            dept_courses = Course.objects.filter(department=teacher_profile.department)
        
        my_courses = Course.objects.filter(Q(id__in=course_ids) | Q(id__in=dept_courses.values_list("id", flat=True))).distinct()

        # 4. Sections: Taught in timetable OR owned in department/institution
        section_ids = list(sessions.values_list("class_section_id", flat=True).distinct())
        dept_sections = ClassSection.objects.filter(department__institution=teacher_profile.institution)
        if teacher_profile.department:
            dept_sections = ClassSection.objects.filter(department=teacher_profile.department)

        my_sections = ClassSection.objects.filter(Q(id__in=section_ids) | Q(id__in=dept_sections.values_list("id", flat=True))).distinct()

        # 5. Students: Enrolled in teacher's sections strictly within their university
        students_qs = StudentProfile.objects.filter(
            institution=teacher_profile.institution,
            sections__in=my_sections
        ).distinct()
        
        total_students_count = students_qs.count()
        morning_students_count = students_qs.filter(study_shift="MORNING").count()
        evening_students_count = students_qs.filter(study_shift="EVENING").count()

        # 6. Today's Attendance Stats
        today_sessions = AttendanceSession.objects.filter(session__teacher=teacher_profile, date=today)
        today_records = AttendanceRecord.objects.filter(attendance_session__in=today_sessions)
        today_total_records = today_records.count()
        today_present = today_records.filter(status__in=["PRESENT", "LATE"]).count()
        today_rate = round((today_present / today_total_records * 100), 1) if today_total_records > 0 else 0.0

        # 7. Overall / All-Time Teacher Stats
        all_sessions = AttendanceSession.objects.filter(
            Q(session__teacher=teacher_profile) | Q(created_by=request.user)
        ).distinct()
        total_lectures_held = all_sessions.count()
        all_records = AttendanceRecord.objects.filter(attendance_session__in=all_sessions)
        all_total_count = all_records.count()
        all_present_count = all_records.filter(status__in=["PRESENT", "LATE"]).count()
        overall_attendance_rate = round((all_present_count / all_total_count * 100), 1) if all_total_count > 0 else 0.0

        # 8. Recent 5 Completed Sessions
        recent_sessions = all_sessions.order_by("-date", "-start_time")[:5].select_related("session__course", "session__class_section")

        # ─── 9. 🚨 ACADEMIC RISK RADAR (رادار الخطر الأكاديمي) ───────────

        # Study Shifts Attendance Comparison
        m_sessions = all_sessions.filter(shift="MORNING")
        m_recs = AttendanceRecord.objects.filter(attendance_session__in=m_sessions)
        m_tot = m_recs.count()
        m_pres = m_recs.filter(status__in=["PRESENT", "LATE"]).count()
        morning_rate = round((m_pres / m_tot * 100), 1) if m_tot > 0 else 0.0

        e_sessions = all_sessions.filter(shift="EVENING")
        e_recs = AttendanceRecord.objects.filter(attendance_session__in=e_sessions)
        e_tot = e_recs.count()
        e_pres = e_recs.filter(status__in=["PRESENT", "LATE"]).count()
        evening_rate = round((e_pres / e_tot * 100), 1) if e_tot > 0 else 0.0

        # Best performing section
        best_section_name = None
        best_section_rate = -1.0
        for sec in my_sections:
            sec_sessions = all_sessions.filter(session__class_section=sec)
            sec_records = AttendanceRecord.objects.filter(attendance_session__in=sec_sessions)
            s_tot = sec_records.count()
            if s_tot > 0:
                s_pres = sec_records.filter(status__in=["PRESENT", "LATE"]).count()
                s_rate = round((s_pres / s_tot * 100), 1)
                if s_rate > best_section_rate:
                    best_section_rate = s_rate
                    best_section_name = f"{sec.level} - {sec.name} ({sec.get_shift_display()})"

        # Student-level Risk Assessment
        student_records_map = {}
        for r in all_records.select_related("student__user"):
            sid = r.student_id
            if sid not in student_records_map:
                student_records_map[sid] = {"present": 0, "absent": 0, "late": 0}
            if r.status in ["PRESENT", "LATE"]:
                student_records_map[sid]["present"] += 1
            elif r.status == "ABSENT":
                student_records_map[sid]["absent"] += 1

        at_risk_students = []
        critical_count = 0
        warning_count = 0

        for std in students_qs.select_related("user"):
            st_data = student_records_map.get(std.id, {"present": 0, "absent": 0, "late": 0})
            abs_count = st_data["absent"]
            pres_count = st_data["present"]
            tot_st = abs_count + pres_count
            if tot_st == 0:
                continue

            abs_rate = round((abs_count / tot_st * 100), 1)
            is_critical = (abs_count >= 3) or (abs_rate >= 15.0 and tot_st >= 2)
            is_warning = not is_critical and ((abs_count >= 2) or (abs_rate >= 7.0))

            if is_critical or is_warning:
                if is_critical:
                    critical_count += 1
                    risk_badge = "🔴 حرمان وشيك"
                    risk_level = "CRITICAL"
                else:
                    warning_count += 1
                    risk_badge = "🟠 إنذار أولي"
                    risk_level = "WARNING"

                std_name = std.user.get_full_name() or std.user.username
                phone_raw = str(getattr(std.user, "phone", "") or "").strip()
                phone_intl = ""
                if phone_raw.startswith("07"):
                    phone_intl = "964" + phone_raw[1:]
                elif phone_raw.startswith("7"):
                    phone_intl = "964" + phone_raw
                elif phone_raw.startswith("+964"):
                    phone_intl = phone_raw[1:]
                elif phone_raw.startswith("964"):
                    phone_intl = phone_raw

                warn_msg = (
                    f"السلام عليكم عزيزي الطالب {std_name} ({std.student_id})\n"
                    f"نود إشعارك بأن رصيد غيابك في المحاضرات بلغ ({abs_count}) غيابات بنسبة ({abs_rate}%).\n"
                    f"يرجى الالتزام التام بحضور المحاضرات القادمة لتفادي الحرمان الأكاديمي."
                )
                wa_url = f"https://wa.me/{phone_intl}?text={urllib.parse.quote(warn_msg)}" if phone_intl else ""

                at_risk_students.append({
                    "profile": std,
                    "name": std_name,
                    "student_id": std.student_id,
                    "phone": phone_raw,
                    "wa_url": wa_url,
                    "shift": std.study_shift,
                    "absent_count": abs_count,
                    "absence_rate": abs_rate,
                    "attendance_rate": round(100 - abs_rate, 1),
                    "risk_badge": risk_badge,
                    "risk_level": risk_level,
                })

        at_risk_students.sort(key=lambda x: (0 if x["risk_level"] == "CRITICAL" else 1, -x["absent_count"]))

        total_tracked = len(student_records_map)
        safe_count = max(0, total_tracked - len(at_risk_students))
        safe_rate = round((safe_count / total_tracked * 100), 1) if total_tracked > 0 else 100.0

        context = {
            "teacher": teacher_profile,
            "sessions": sessions,
            "today_timetable_sessions": today_timetable_sessions,
            "upcoming_scheduled_sessions": upcoming_scheduled_sessions,
            "active_attendance_sessions": active_attendance_sessions,
            "my_courses": my_courses,
            "my_sections": my_sections,
            "total_students_count": total_students_count,
            "morning_students_count": morning_students_count,
            "evening_students_count": evening_students_count,
            "today_total_records": today_total_records,
            "today_present": today_present,
            "today_rate": today_rate,
            "total_lectures_held": total_lectures_held,
            "overall_attendance_rate": overall_attendance_rate,
            "recent_sessions": recent_sessions,
            # Academic Risk Radar context
            "at_risk_students": at_risk_students[:8],
            "at_risk_total_count": len(at_risk_students),
            "critical_count": critical_count,
            "warning_count": warning_count,
            "safe_rate": safe_rate,
            "morning_rate": morning_rate,
            "evening_rate": evening_rate,
            "best_section_name": best_section_name,
            "best_section_rate": best_section_rate,
        }
        return render(request, "dashboard/teacher.html", context)

    # 3. STUDENT DASHBOARD
    elif user.is_student():
        student_profile = get_object_or_404_lazy(StudentProfile, user=user)
        if not student_profile:
            return render(request, "dashboard/student.html", {"error": "لم يتم ربط حسابك بملف طالب."})

        today = timezone.now().date()
        student_sections = student_profile.sections.all()

        # Today's attendance sessions for student's enrolled sections
        today_student_sessions_qs = AttendanceSession.objects.filter(
            session__class_section__in=student_sections,
            date=today
        ).select_related("session__course", "session__class_section", "session__teacher__user").order_by("start_time")

        my_today_records = {
            r.attendance_session_id: r 
            for r in AttendanceRecord.objects.filter(student=student_profile, attendance_session__date=today)
        }

        today_sessions_data = []
        for att in today_student_sessions_qs:
            rec = my_today_records.get(att.id)
            is_attended = bool(rec and rec.status in ["PRESENT", "LATE"])
            today_sessions_data.append({
                "session": att,
                "record": rec,
                "is_attended": is_attended,
                "status_display": rec.get_status_display() if rec else ("🔴 جارية الآن" if att.is_active else "⏳ مجدولة"),
            })

        # Calculate student stats
        records = AttendanceRecord.objects.filter(student=student_profile)
        total = records.count()
        present = records.filter(status="PRESENT").count()
        late = records.filter(status="LATE").count()
        absent = records.filter(status="ABSENT").count()
        excused = records.filter(status="EXCUSED").count()

        attendance_rate = 0
        if total > 0:
            attendance_rate = ((present + late) / total) * 100

        recent_records = records.order_by("-timestamp")[:10]

        context = {
            "student": student_profile,
            "today_sessions_data": today_sessions_data,
            "total": total,
            "present": present,
            "late": late,
            "absent": absent,
            "excused": excused,
            "attendance_rate": round(attendance_rate, 1),
            "recent_records": recent_records
        }
        return render(request, "dashboard/student.html", context)

    return redirect("login")


def get_object_or_404_lazy(model_class, **kwargs):
    try:
        return model_class.objects.get(**kwargs)
    except model_class.DoesNotExist:
        return None


def custom_404_view(request, exception=None):
    return render(request, "404.html", status=404)


def custom_500_view(request):
    return render(request, "500.html", status=500)


def custom_403_view(request, exception=None):
    return render(request, "403.html", status=403)


def custom_csrf_failure_view(request, reason=""):
    return render(request, "403_csrf.html", {"reason": reason}, status=403)


def offline_view(request):
    """Fallback offline view served by Service Worker when network is unavailable."""
    return render(request, "offline.html")



