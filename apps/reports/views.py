import os
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.db import transaction
from django.http import HttpResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.parsers import MultiPartParser

from apps.accounts.permissions import IsInstitutionAdmin, IsTeacher
from apps.academics.models import Institution, Session
from apps.attendance.models import AttendanceSession, AttendanceRecord
from apps.core.models import AuditLog
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.translation import gettext
from datetime import timedelta, datetime, date
from apps.accounts.models import StudentProfile, TeacherProfile
from apps.academics.models import Institution, Session, Course, ClassSection
from .utils import import_students_from_excel, export_attendance_excel

User = get_user_model()

class ImportStudentsView(APIView):
    """
    API view to import students from an Excel/CSV file.
    Accessible by Teachers and Institution Admins.
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        from .utils import import_students_from_excel, parse_students_from_text
        from apps.academics.models import ClassSection

        file_obj = request.FILES.get("file")
        raw_text = request.data.get("raw_text", "").strip()
        institution_id = request.data.get("institution_id")
        section_id = request.data.get("section_id")
        default_shift = request.data.get("study_shift", "MORNING").strip().upper()
        if default_shift not in ["MORNING", "EVENING"]:
            default_shift = "MORNING"

        if not file_obj and not raw_text:
            return Response({"error": "يرجى رفع ملف Excel أو لصق قائمة الأسماء نصياً."}, status=status.HTTP_400_BAD_REQUEST)
            
        if not institution_id:
            inst = Institution.objects.first()
            if not inst:
                return Response({"error": "لا توجد مؤسسة تعليمية مسجلة بالنظام."}, status=status.HTTP_400_BAD_REQUEST)
            institution = inst
        else:
            institution = get_object_or_404(Institution, id=institution_id)

        target_section = None
        if section_id and str(section_id).isdigit():
            target_section = ClassSection.objects.filter(id=int(section_id)).first()

        parsed_students = []
        errors = []

        if file_obj:
            temp_dir = os.path.join(settings.BASE_DIR, "media", "temp")
            os.makedirs(temp_dir, exist_ok=True)
            temp_file_path = os.path.join(temp_dir, file_obj.name)
            
            with open(temp_file_path, "wb+") as destination:
                for chunk in file_obj.chunks():
                    destination.write(chunk)

            parsed_students, errors = import_students_from_excel(temp_file_path, institution, default_shift=default_shift)
            
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
        elif raw_text:
            parsed_students = parse_students_from_text(raw_text, default_shift=default_shift)

        if not parsed_students and not errors:
            errors.append("لم يتم العثور على أي أسماء صالحة في البيانات المدخلة.")

        if errors:
            return Response({
                "message": "فشلت عملية التحقق من صحة الملف",
                "errors": errors
            }, status=status.HTTP_400_BAD_REQUEST)

        # Create or update students in a safe database transaction
        created_count = 0
        updated_count = 0
        try:
            with transaction.atomic():
                for stud in parsed_students:
                    # Check if user with username or email already exists
                    user = User.objects.filter(username=stud["username"]).first()
                    if not user and stud.get("email"):
                        user = User.objects.filter(email=stud["email"]).first()

                    if not user:
                        user = User.objects.create(
                            username=stud["username"],
                            email=stud["email"],
                            first_name=stud["first_name"],
                            last_name=stud["last_name"],
                            phone=stud.get("phone", ""),
                            role=User.Roles.STUDENT
                        )
                        # Default password is their student ID or student123
                        user.set_password(stud["student_id"])
                        user.save()
                        created_count += 1
                    else:
                        updated_count += 1

                    # Get or create StudentProfile
                    st_profile, _ = StudentProfile.objects.get_or_create(
                        user=user,
                        defaults={
                            "student_id": stud["student_id"],
                            "institution": institution,
                            "study_shift": stud.get("study_shift", default_shift)
                        }
                    )
                    # Link to selected Section if provided
                    if target_section:
                        st_profile.sections.add(target_section)
            
            # Log this import to Audit Log
            AuditLog.objects.create(
                user=request.user,
                action="استيراد_طلاب_مرن",
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT"),
                details={
                    "institution": institution.name,
                    "section": target_section.name if target_section else "بدون شعبة محددة",
                    "created_count": created_count,
                    "updated_count": updated_count
                }
            )
            
            msg = f"تم استيراد {created_count} طالب جديد بنجاح"
            if updated_count:
                msg += f" وتحديث {updated_count} طالب موجودين"
            if target_section:
                msg += f" وإضافتهم جميعاً إلى شعبة ({target_section.name})."
            else:
                msg += "."

            return Response({
                "message": msg,
                "created_count": created_count,
                "updated_count": updated_count
            }, status=status.HTTP_201_CREATED)

        except Exception as ex:
            return Response({
                "error": f"حدث خطأ أثناء حفظ البيانات في قاعدة البيانات: {str(ex)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ExportAttendanceExcelView(APIView):
    """
    API view to download an Excel report for a specific attendance session.
    Only accessible by Teachers or Admins.
    """
    permission_classes = [IsTeacher]

    def get(self, request, session_id):
        attendance_session = get_object_or_404(AttendanceSession, id=session_id)
        
        records = AttendanceRecord.objects.filter(
            attendance_session=attendance_session
        ).select_related("student__user")

        wb = export_attendance_excel(attendance_session, records)
        
        filename = f"attendance_{attendance_session.session.course.code}_{attendance_session.date}.xlsx"
        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        
        wb.save(response)
        return response


# =====================================================================
# 3. WEB VIEWS
# =====================================================================

@login_required
def admin_import_web_view(request):
    """
    Renders the Excel student import screen for Teachers and Institution Admins.
    """
    if not (request.user.is_teacher() or request.user.is_institution_admin() or request.user.is_super_admin()):
        messages.error(request, "غير مصرح لك بدخول هذه الصفحة.")
        return redirect("/")

    from apps.academics.models import ClassSection, Institution as InstModel

    institution = None
    if hasattr(request.user, "teacher_profile") and request.user.teacher_profile and request.user.teacher_profile.institution:
        institution = request.user.teacher_profile.institution
    else:
        institution = InstModel.objects.first()

    institutions = InstModel.objects.all()
    sections = ClassSection.objects.all().select_related("department__institution").order_by("department__name", "level", "name")
    if hasattr(request.user, "teacher_profile") and request.user.teacher_profile and request.user.teacher_profile.institution:
        sections = sections.filter(department__institution=request.user.teacher_profile.institution)

    levels = ["المرحلة الأولى", "المرحلة الثانية", "المرحلة الثالثة", "المرحلة الرابعة", "الدراسات العليا"]
    courses = Course.objects.all().order_by("name")

    return render(request, "reports/import.html", {
        "institution": institution,
        "institutions": institutions,
        "sections": sections,
        "courses": courses,
        "levels": levels,
    })


@login_required
def admin_reports_overview_view(request):
    """
    Renders the Reports and Auditing screen for Teachers and Institution Admins.
    """
    if not (request.user.is_teacher() or request.user.is_institution_admin() or request.user.is_super_admin()):
        messages.error(request, "غير مصرح لك بدخول هذه الصفحة.")
        return redirect("/")

    # Get institution
    institution = None
    if hasattr(request.user, "teacher_profile") and request.user.teacher_profile:
        institution = request.user.teacher_profile.institution
    else:
        institution = Institution.objects.first()

    # If logged-in user is a Teacher, render the specialized Teacher Reports Portal
    if request.user.is_teacher() and not (request.user.is_institution_admin() or request.user.is_super_admin()):
        from datetime import timedelta
        from django.db.models import Q
        today = timezone.now().date()
        period = request.GET.get("period", "all")
        date_param = request.GET.get("date", "").strip()
        course_id = request.GET.get("course_id", "")
        section_id = request.GET.get("section_id", "")
        q_search = request.GET.get("q", "").strip().lower()

        teacher_profile = getattr(request.user, "teacher_profile", None) or TeacherProfile.objects.first()
        from apps.academics.models import Session, Course, ClassSection
        
        # Teacher's courses and sections
        my_course_ids = Session.objects.filter(
            Q(teacher=teacher_profile) | Q(attendance_sessions__created_by=request.user)
        ).values_list("course_id", flat=True).distinct()
        my_courses = Course.objects.filter(id__in=my_course_ids)
        if not my_courses.exists():
            my_courses = Course.objects.filter(department__institution=institution)

        my_section_ids = Session.objects.filter(
            Q(teacher=teacher_profile) | Q(attendance_sessions__created_by=request.user)
        ).values_list("class_section_id", flat=True).distinct()
        my_sections = ClassSection.objects.filter(id__in=my_section_ids)
        if not my_sections.exists():
            my_sections = ClassSection.objects.filter(department__institution=institution)

        # Base query for teacher's sessions (either assigned teacher or created by this user)
        sess_qs = AttendanceSession.objects.filter(
            Q(session__teacher=teacher_profile) | Q(created_by=request.user)
        ).distinct()

        # Date & Period filtering
        selected_date = None
        if date_param:
            try:
                selected_date = datetime.strptime(date_param, "%Y-%m-%d").date()
                sess_qs = sess_qs.filter(date=selected_date)
                period = "custom"
            except ValueError:
                pass
        elif period == "today":
            sess_qs = sess_qs.filter(date=today)
            selected_date = today
        elif period == "yesterday":
            yesterday = today - timedelta(days=1)
            sess_qs = sess_qs.filter(date=yesterday)
            selected_date = yesterday
        elif period == "week":
            sess_qs = sess_qs.filter(date__gte=today - timedelta(days=7))
        elif period == "month":
            sess_qs = sess_qs.filter(date__gte=today - timedelta(days=30))

        # Filter by course
        if course_id and course_id.isdigit():
            sess_qs = sess_qs.filter(session__course_id=int(course_id))

        # Filter by section
        if section_id and section_id.isdigit():
            sess_qs = sess_qs.filter(session__class_section_id=int(section_id))

        # Filter by shift (Morning / Evening)
        shift_filter = request.GET.get("shift", "").strip().upper()
        if shift_filter in ["MORNING", "EVENING"]:
            sess_qs = sess_qs.filter(shift=shift_filter)

        attendance_sessions = sess_qs.select_related("session__course", "session__class_section").order_by("-date", "-start_time")
        total_sessions_held = attendance_sessions.count()

        # Query all records in these sessions
        records_qs = AttendanceRecord.objects.filter(attendance_session__in=attendance_sessions)
        total_records_count = records_qs.count()
        total_present_count = records_qs.filter(status__in=["PRESENT", "LATE"]).count()
        total_absent_count = records_qs.filter(status="ABSENT").count()
        total_late_count = records_qs.filter(status="LATE").count()

        overall_attendance_rate = round((total_present_count / total_records_count * 100), 1) if total_records_count > 0 else 0.0

        # Query students in teacher's sections
        students_qs = StudentProfile.objects.filter(sections__in=my_sections).distinct().select_related("user")
        if section_id and section_id.isdigit():
            students_qs = students_qs.filter(sections__id=int(section_id))
        if shift_filter in ["MORNING", "EVENING"]:
            students_qs = students_qs.filter(study_shift=shift_filter)

        # 1. Build Daily Detailed Roll-Call (if single day or today)
        is_daily_view = period in ["today", "yesterday", "custom"] or bool(date_param)
        daily_roll_call = []
        if is_daily_view and attendance_sessions.exists():
            daily_recs = {r.student_id: r for r in records_qs.select_related("student__user")}
            for std in students_qs.order_by("user__first_name"):
                std_name = std.user.get_full_name() or std.user.username
                if q_search and (q_search not in std_name.lower() and q_search not in std.student_id.lower()):
                    continue
                
                # Check if this student's section had a session on this day
                matched_session = attendance_sessions.filter(session__class_section__in=std.sections.all()).first()
                if not matched_session:
                    continue

                rec = daily_recs.get(std.id)
                status = rec.status if rec else "ABSENT"
                status_display = gettext(rec.get_status_display()) if rec else gettext("غائب")
                checkin_time = rec.timestamp.strftime("%H:%M:%S") if rec and rec.timestamp else "-"
                
                method_display = "-"
                if rec:
                    if rec.method == AttendanceRecord.Methods.OFFLINE_MANUAL:
                        method_display = gettext("📶 بدون إنترنت (فوري)")
                    elif rec.method == AttendanceRecord.Methods.QR:
                        method_display = gettext("📱 كود QR")
                    elif rec.method == AttendanceRecord.Methods.STATIC_QR:
                        method_display = gettext("💳 بطاقة QR")
                    elif rec.method == AttendanceRecord.Methods.OTP:
                        method_display = gettext("🔢 رمز OTP")
                    elif rec.method == AttendanceRecord.Methods.RFID:
                        method_display = gettext("💳 بطاقة RFID")
                    else:
                        method_display = gettext("✍️ يدوي")

                daily_roll_call.append({
                    "profile": std,
                    "name": std_name,
                    "student_id": std.student_id,
                    "phone": std.user.phone or "-",
                    "shift_display": gettext("صباحي") if std.study_shift == "MORNING" else gettext("مسائي"),
                    "section_name": matched_session.session.class_section.name,
                    "course_name": matched_session.session.course.name,
                    "status": status,
                    "status_display": status_display,
                    "checkin_time": checkin_time,
                    "method_display": method_display,
                    "record": rec,
                })

        # 2. Build Per-Student Cumulative Ledger Matrix
        student_ledger = []
        at_risk_count = 0
        regular_count = 0

        for std in students_qs.order_by("user__first_name"):
            std_name = std.user.get_full_name() or std.user.username
            if q_search and (q_search not in std_name.lower() and q_search not in std.student_id.lower()):
                continue

            std_records = records_qs.filter(student=std)
            # Total sessions held for this student's section in the filtered period
            std_held = attendance_sessions.filter(session__class_section__in=std.sections.all()).count()

            p_cnt = std_records.filter(status="PRESENT").count()
            l_cnt = std_records.filter(status="LATE").count()
            a_cnt = std_records.filter(status="ABSENT").count()
            e_cnt = std_records.filter(status="EXCUSED").count()

            effective_pres = p_cnt + l_cnt
            att_rate = round((effective_pres / std_held * 100), 1) if std_held > 0 else (100.0 if period in ["all", "month"] else 0.0)
            abs_rate = round((a_cnt / std_held * 100), 1) if std_held > 0 else 0.0

            # Academic alert status based on Iraqi University regulations
            if abs_rate >= 15.0 and std_held >= 3:
                status_label = "حرمان (رسوب بالغياب)"
                status_badge = "bg-rose-100 text-rose-800 border-rose-200"
                at_risk_count += 1
            elif abs_rate >= 10.0 and std_held >= 3:
                status_label = "إنذار نهائي"
                status_badge = "bg-orange-100 text-orange-800 border-orange-200"
                at_risk_count += 1
            elif abs_rate >= 5.0 and std_held >= 2:
                status_label = "تنبيه أولي"
                status_badge = "bg-amber-100 text-amber-800 border-amber-200"
            else:
                status_label = "طبيعي / منتظم"
                status_badge = "bg-emerald-100 text-emerald-800 border-emerald-200"
                regular_count += 1

            student_ledger.append({
                "profile": std,
                "name": std_name,
                "student_id": std.student_id,
                "sections": list(std.sections.all()),
                "sessions_held": std_held,
                "present_count": p_cnt,
                "late_count": l_cnt,
                "absent_count": a_cnt,
                "excused_count": e_cnt,
                "attendance_rate": att_rate,
                "absence_rate": abs_rate,
                "status_label": status_label,
                "status_badge": status_badge,
            })

        context = {
            "teacher": teacher_profile,
            "institution": institution,
            "my_courses": my_courses,
            "my_sections": my_sections,
            "period": period,
            "date_param": date_param,
            "selected_date": selected_date,
            "is_daily_view": is_daily_view,
            "daily_roll_call": daily_roll_call,
            "shift_filter": shift_filter,
            "selected_course_id": course_id,
            "selected_section_id": section_id,
            "search_query": q_search,
            "attendance_sessions": attendance_sessions,
            "total_sessions_held": total_sessions_held,
            "total_records_count": total_records_count,
            "total_present_count": total_present_count,
            "total_absent_count": total_absent_count,
            "total_late_count": total_late_count,
            "overall_attendance_rate": overall_attendance_rate,
            "student_ledger": student_ledger,
            "total_students_count": len(student_ledger),
            "at_risk_count": at_risk_count,
            "regular_count": regular_count,
        }
        return render(request, "reports/teacher_reports.html", context)

    # Otherwise (Admins), render the system-wide overview
    attendance_sessions = AttendanceSession.objects.filter(
        session__class_section__department__institution=institution
    ).select_related("session__course", "session__class_section", "created_by").order_by("-date", "-start_time")

    audit_logs = AuditLog.objects.all().select_related("user").order_by("-timestamp")[:30]

    context = {
        "institution": institution,
        "attendance_sessions": attendance_sessions,
        "audit_logs": audit_logs,
        "is_teacher": request.user.is_teacher(),
    }
    return render(request, "reports/overview.html", context)


@login_required
def export_teacher_report_excel_view(request):
    """Generates and downloads an enhanced multi-sheet Excel report honoring all filters (period, date, section, course)."""
    if not (request.user.is_teacher() or request.user.is_institution_admin() or request.user.is_super_admin()):
        return HttpResponse("غير مصرح لك", status=403)

    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from io import BytesIO
    from datetime import timedelta
    from django.db.models import Q

    teacher_profile = getattr(request.user, "teacher_profile", None) or TeacherProfile.objects.first()
    from apps.academics.models import Session, ClassSection, Course

    period = request.GET.get("period", "all")
    date_param = request.GET.get("date", "").strip()
    course_id = request.GET.get("course_id", "")
    section_id = request.GET.get("section_id", "")
    shift_filter = request.GET.get("shift", "").strip().upper()

    # Base sessions
    sess_qs = AttendanceSession.objects.filter(
        Q(session__teacher=teacher_profile) | Q(created_by=request.user)
    ).distinct()

    today = timezone.now().date()
    if date_param:
        try:
            d_val = datetime.strptime(date_param, "%Y-%m-%d").date()
            sess_qs = sess_qs.filter(date=d_val)
        except ValueError:
            pass
    elif period == "today":
        sess_qs = sess_qs.filter(date=today)
    elif period == "yesterday":
        sess_qs = sess_qs.filter(date=today - timedelta(days=1))
    elif period == "week":
        sess_qs = sess_qs.filter(date__gte=today - timedelta(days=7))
    elif period == "month":
        sess_qs = sess_qs.filter(date__gte=today - timedelta(days=30))

    if course_id and course_id.isdigit():
        sess_qs = sess_qs.filter(session__course_id=int(course_id))
    if section_id and section_id.isdigit():
        sess_qs = sess_qs.filter(session__class_section_id=int(section_id))
    if shift_filter in ["MORNING", "EVENING"]:
        sess_qs = sess_qs.filter(shift=shift_filter)

    my_section_ids = Session.objects.filter(
        Q(teacher=teacher_profile) | Q(attendance_sessions__created_by=request.user)
    ).values_list("class_section_id", flat=True).distinct()
    my_sections = ClassSection.objects.filter(id__in=my_section_ids)

    students_qs = StudentProfile.objects.filter(sections__in=my_sections).distinct().select_related("user")
    if section_id and section_id.isdigit():
        students_qs = students_qs.filter(sections__id=int(section_id))
    if shift_filter in ["MORNING", "EVENING"]:
        students_qs = students_qs.filter(study_shift=shift_filter)

    records_qs = AttendanceRecord.objects.filter(attendance_session__in=sess_qs).select_related("student__user", "attendance_session__session__course")

    wb = openpyxl.Workbook()
    # ─── Sheet 1: كشف الغياب التراكمي ونسب الحضور ────────────────────────
    ws1 = wb.active
    ws1.title = "كشف الغياب والإنذارات"
    ws1.sheet_view.rightToLeft = True

    # Styling
    title_font = Font(name="Calibri", size=15, bold=True, color="1E293B")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
    center = Alignment(horizontal="center", vertical="center")
    right = Alignment(horizontal="right", vertical="center")

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )

    ws1.merge_cells("A1:L1")
    filter_label = f" (الفترة: {period})" if period != 'all' else ""
    inst_name = teacher_profile.institution.name if teacher_profile and teacher_profile.institution else "جامعة بغداد"
    ws1["A1"] = f"{inst_name} | تقرير الحضور والغياب الأكاديمي للأستاذ: {teacher_profile.user.get_full_name()}{filter_label}"
    ws1["A1"].font = title_font
    ws1["A1"].alignment = center
    ws1.row_dimensions[1].height = 40

    headers1 = [
        "ت", "اسم الطالب الكامل", "الرقم الجامعي", "رقم الهاتف", "نوع الدراسة", "الشعبة", "المحاضرات المنعقدة",
        "حاضر", "متأخر", "غائب", "نسبة الحضور %", "الموقف الأكاديمي"
    ]
    ws1.append(headers1)
    ws1.row_dimensions[2].height = 25

    for col_num in range(1, len(headers1) + 1):
        c = ws1.cell(row=2, column=col_num)
        c.font = header_font
        c.fill = header_fill
        c.alignment = center

    row_idx = 3
    for idx, std in enumerate(students_qs.order_by("user__first_name"), 1):
        std_held = sess_qs.filter(session__class_section__in=std.sections.all()).count()
        std_recs = records_qs.filter(student=std)

        p = std_recs.filter(status="PRESENT").count()
        l = std_recs.filter(status="LATE").count()
        a = std_recs.filter(status="ABSENT").count()

        att_rate = round(((p + l) / std_held * 100), 1) if std_held > 0 else (100.0 if period in ["all", "month"] else 0.0)
        abs_rate = round((a / std_held * 100), 1) if std_held > 0 else 0.0

        if abs_rate >= 15.0 and std_held >= 3:
            status_text = "حرمان (رسوب بالغياب)"
        elif abs_rate >= 10.0 and std_held >= 3:
            status_text = "إنذار نهائي"
        elif abs_rate >= 5.0 and std_held >= 2:
            status_text = "تنبيه أولي"
        else:
            status_text = "طبيعي / منتظم"

        sec_names = ", ".join(std.sections.values_list("name", flat=True)) or "-"
        shift_text = "مسائي" if std.study_shift == "EVENING" else "صباحي"

        row_data = [
            idx, std.user.get_full_name() or std.user.username, std.student_id, std.user.phone or "-",
            shift_text, sec_names, std_held, p, l, a, f"{att_rate}%", status_text
        ]
        ws1.append(row_data)

        for col_num in range(1, len(headers1) + 1):
            c = ws1.cell(row=row_idx, column=col_num)
            c.border = thin_border
            c.alignment = right if col_num == 2 else center

        row_idx += 1

    col_widths1 = {"A": 6, "B": 28, "C": 16, "D": 16, "E": 14, "F": 18, "G": 14, "H": 10, "I": 10, "J": 10, "K": 14, "L": 22}
    for col_letter, width in col_widths1.items():
        ws1.column_dimensions[col_letter].width = width

    # ─── Sheet 2: سجل الحضور التفصيلي بالوقت والطريقة ───────────────────────
    ws2 = wb.create_sheet(title="سجل الحضور التفصيلي بالوقت")
    ws2.sheet_view.rightToLeft = True

    headers2 = [
        "ت", "اسم الطالب", "الرقم الجامعي", "المادة الدراسية", "الشعبة", "التاريخ", "وقت التسجيل", "الحالة", "طريقة التحضير"
    ]
    ws2.append(headers2)
    ws2.row_dimensions[1].height = 25

    header2_fill = PatternFill(start_color="059669", end_color="059669", fill_type="solid")
    for col_num in range(1, len(headers2) + 1):
        c = ws2.cell(row=1, column=col_num)
        c.font = header_font
        c.fill = header2_fill
        c.alignment = center

    row2_idx = 2
    for idx2, rec in enumerate(records_qs.order_by("-attendance_session__date", "-timestamp")[:500], 1):
        method_str = "يدوي"
        if rec.method == AttendanceRecord.Methods.QR: method_str = "📱 كود QR"
        elif rec.method == AttendanceRecord.Methods.OFFLINE_MANUAL: method_str = "📶 بدون إنترنت (فوري)"
        elif rec.method == AttendanceRecord.Methods.OTP: method_str = "🔢 رمز OTP"
        elif rec.method == AttendanceRecord.Methods.RFID: method_str = "💳 بطاقة RFID"

        row2_data = [
            idx2, rec.student.user.get_full_name() or rec.student.user.username,
            rec.student.student_id,
            rec.attendance_session.session.course.name,
            rec.attendance_session.session.class_section.name,
            str(rec.attendance_session.date),
            rec.timestamp.strftime("%H:%M:%S") if rec.timestamp else "-",
            rec.get_status_display(),
            method_str
        ]
        ws2.append(row2_data)
        for col_num in range(1, len(headers2) + 1):
            c = ws2.cell(row=row2_idx, column=col_num)
            c.border = thin_border
            c.alignment = right if col_num == 2 else center
        row2_idx += 1

    col_widths2 = {"A": 6, "B": 26, "C": 16, "D": 22, "E": 16, "F": 14, "G": 14, "H": 12, "I": 20}
    for col_letter, width in col_widths2.items():
        ws2.column_dimensions[col_letter].width = width

    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)

    filename = f"Attendance_Report_{timezone.now().strftime('%Y%m%d_%H%M')}.xlsx"
    response = HttpResponse(stream.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def export_teacher_report_csv_view(request):
    """Downloads a clean, UTF-8 encoded CSV file of the attendance ledger."""
    if not (request.user.is_teacher() or request.user.is_institution_admin() or request.user.is_super_admin()):
        return HttpResponse("غير مصرح لك", status=403)

    import csv
    from django.db.models import Q

    teacher_profile = getattr(request.user, "teacher_profile", None) or TeacherProfile.objects.first()
    from apps.academics.models import Session, ClassSection

    period = request.GET.get("period", "all")
    date_param = request.GET.get("date", "").strip()
    shift_filter = request.GET.get("shift", "").strip().upper()
    section_id = request.GET.get("section_id", "")

    sess_qs = AttendanceSession.objects.filter(
        Q(session__teacher=teacher_profile) | Q(created_by=request.user)
    ).distinct()

    today = timezone.now().date()
    if date_param:
        try:
            d_val = datetime.strptime(date_param, "%Y-%m-%d").date()
            sess_qs = sess_qs.filter(date=d_val)
        except ValueError:
            pass
    elif period == "today":
        sess_qs = sess_qs.filter(date=today)
    elif period == "week":
        sess_qs = sess_qs.filter(date__gte=today - timedelta(days=7))

    if shift_filter in ["MORNING", "EVENING"]:
        sess_qs = sess_qs.filter(shift=shift_filter)

    my_section_ids = Session.objects.filter(
        Q(teacher=teacher_profile) | Q(attendance_sessions__created_by=request.user)
    ).values_list("class_section_id", flat=True).distinct()
    my_sections = ClassSection.objects.filter(id__in=my_section_ids)

    students_qs = StudentProfile.objects.filter(sections__in=my_sections).distinct().select_related("user")
    if section_id and section_id.isdigit():
        students_qs = students_qs.filter(sections__id=int(section_id))
    if shift_filter in ["MORNING", "EVENING"]:
        students_qs = students_qs.filter(study_shift=shift_filter)

    records_qs = AttendanceRecord.objects.filter(attendance_session__in=sess_qs)

    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = f'attachment; filename="Attendance_{timezone.now().strftime("%Y%m%d")}.csv"'
    response.write("\ufeff".encode("utf-8"))  # UTF-8 BOM for Microsoft Excel compatibility

    writer = csv.writer(response)
    writer.writerow(["ت", "اسم الطالب الكامل", "الرقم الجامعي", "رقم الهاتف", "نوع الدراسة", "المحاضرات المنعقدة", "حاضر", "متأخر", "غائب", "نسبة الحضور %"])

    for idx, std in enumerate(students_qs.order_by("user__first_name"), 1):
        std_held = sess_qs.filter(session__class_section__in=std.sections.all()).count()
        std_recs = records_qs.filter(student=std)
        p = std_recs.filter(status="PRESENT").count()
        l = std_recs.filter(status="LATE").count()
        a = std_recs.filter(status="ABSENT").count()
        att_rate = round(((p + l) / std_held * 100), 1) if std_held > 0 else (100.0 if period == 'all' else 0.0)
        shift_text = "مسائي" if std.study_shift == "EVENING" else "صباحي"

        writer.writerow([
            idx, std.user.get_full_name() or std.user.username, std.student_id, std.user.phone or "-",
            shift_text, std_held, p, l, a, f"{att_rate}%"
        ])

    return response


@login_required
def admin_student_detail_view(request, student_id):
    """
    Renders a detailed report of a specific student's attendance history for teachers and admins.
    """
    if not (request.user.is_teacher() or request.user.is_institution_admin() or request.user.is_super_admin()):
        messages.error(request, "غير مصرح لك بدخول هذه الصفحة.")
        return redirect("/")
        
    student = get_object_or_404(StudentProfile.objects.select_related("user", "institution"), id=student_id)
    records = AttendanceRecord.objects.filter(student=student).select_related(
        "attendance_session__session__course", 
        "attendance_session__session__class_section"
    ).order_by("-timestamp")
    
    # Calculate stats
    total = records.count()
    present = records.filter(status="PRESENT").count()
    late = records.filter(status="LATE").count()
    absent = records.filter(status="ABSENT").count()
    excused = records.filter(status="EXCUSED").count()
    
    rate = 0
    if total > 0:
        rate = ((present + late) / total) * 100
        
    context = {
        "student": student,
        "records": records,
        "total": total,
        "present": present,
        "late": late,
        "absent": absent,
        "excused": excused,
        "rate": round(rate, 1)
    }
    return render(request, "reports/student_detail.html", context)


from django.http import JsonResponse
from django.views.decorators.http import require_POST

@login_required
@require_POST
def quick_create_academic_entity_view(request):
    """
    API endpoint allowing teachers and admins to create an Institution, Section, or Course on-the-fly.
    """
    import json
    from apps.academics.models import Institution, Department, ClassSection, Course

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        data = request.POST

    entity_type = data.get("type", "").strip().lower()

    if entity_type == "institution":
        name = data.get("name", "").strip()
        if not name:
            return JsonResponse({"success": False, "error": "يرجى كتابة اسم المؤسسة / الجامعة."}, status=400)

        inst, created = Institution.objects.get_or_create(name=name)
        # Ensure at least one department exists for this institution
        dept, _ = Department.objects.get_or_create(
            institution=inst,
            defaults={"name": "القسم العام", "code": "GEN"}
        )

        return JsonResponse({
            "success": True,
            "entity": {
                "id": inst.id,
                "name": inst.name,
                "department_id": dept.id
            },
            "message": f"تمت إضافة المؤسسة «{inst.name}» بنجاح!"
        })

    elif entity_type == "section":
        name = data.get("name", "").strip()
        level = data.get("level", "").strip() or "المرحلة الأولى"
        shift = data.get("shift", "MORNING").strip().upper()
        if shift not in ["MORNING", "EVENING"]:
            shift = "MORNING"

        inst_id = data.get("institution_id")
        institution = None
        if inst_id:
            institution = Institution.objects.filter(id=inst_id).first()
        if not institution:
            if hasattr(request.user, "teacher_profile") and request.user.teacher_profile and request.user.teacher_profile.institution:
                institution = request.user.teacher_profile.institution
            else:
                institution = Institution.objects.first()

        if not name:
            return JsonResponse({"success": False, "error": "يرجى كتابة اسم الشعبة (مثال: شعبة أ)."}, status=400)

        # Get or create department for institution
        department = None
        if institution:
            department = Department.objects.filter(institution=institution).first()
            if not department:
                department = Department.objects.create(institution=institution, name="القسم العام", code="GEN")
        else:
            department = Department.objects.first()

        sec, created = ClassSection.objects.get_or_create(
            department=department,
            name=name,
            level=level,
            shift=shift,
        )

        # Link to teacher if teacher profile exists
        if hasattr(request.user, "teacher_profile") and request.user.teacher_profile:
            request.user.teacher_profile.sections.add(sec)

        return JsonResponse({
            "success": True,
            "entity": {
                "id": sec.id,
                "name": sec.name,
                "level": sec.level,
                "shift": sec.shift,
                "display": f"{sec.name} - {sec.level} ({sec.get_shift_display()})"
            },
            "message": f"تم إنشاء الشعبة «{sec.name} ({sec.level})» بنجاح!"
        })

    elif entity_type == "course":
        name = data.get("name", "").strip()
        code = data.get("code", "").strip()
        inst_id = data.get("institution_id")

        institution = None
        if inst_id:
            institution = Institution.objects.filter(id=inst_id).first()
        if not institution:
            if hasattr(request.user, "teacher_profile") and request.user.teacher_profile and request.user.teacher_profile.institution:
                institution = request.user.teacher_profile.institution
            else:
                institution = Institution.objects.first()

        if not name:
            return JsonResponse({"success": False, "error": "يرجى إدخال اسم المادة الدراسية."}, status=400)

        department = None
        if institution:
            department = Department.objects.filter(institution=institution).first()
            if not department:
                department = Department.objects.create(institution=institution, name="القسم العام", code="GEN")
        else:
            department = Department.objects.first()

        if not code:
            code = f"CS{Course.objects.count() + 101}"

        course, created = Course.objects.get_or_create(
            department=department,
            name=name,
            defaults={"code": code}
        )

        return JsonResponse({
            "success": True,
            "entity": {
                "id": course.id,
                "name": course.name,
                "code": course.code,
                "display": f"{course.name} ({course.code})"
            },
            "message": f"تم إنشاء المادة «{course.name}» بنجاح!"
        })

    return JsonResponse({"success": False, "error": "نوع العنصر المطلوب غير معروف."}, status=400)


# ─────────────────────────────────────────────────────────────────────────────
# 🗑️  AUDIT LOG MANAGEMENT VIEWS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def delete_audit_log_view(request, log_id):
    """Delete a single audit log entry (admin/super-admin only)."""
    if not (request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك بحذف سجلات التدقيق.")
        return redirect("reports:admin_reports_overview")
    log = get_object_or_404(AuditLog, id=log_id)
    log.delete()
    messages.success(request, "تم حذف سجل العملية بنجاح.")
    return redirect(request.META.get("HTTP_REFERER", "reports:admin_reports_overview"))


@login_required
def bulk_delete_audit_logs_view(request):
    """Bulk-delete selected audit log entries (admin/super-admin only)."""
    if not (request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك بحذف سجلات التدقيق.")
        return redirect("reports:admin_reports_overview")
    if request.method == "POST":
        log_ids = request.POST.getlist("selected_logs")
        if not log_ids:
            raw = request.POST.get("selected_logs_str", "")
            log_ids = [s.strip() for s in raw.split(",") if s.strip()]
        valid_ids = [int(lid) for lid in log_ids if str(lid).isdigit()]
        if valid_ids:
            cnt = AuditLog.objects.filter(id__in=valid_ids).delete()[0]
            messages.success(request, f"تم حذف {cnt} سجل تدقيق بنجاح.")
        else:
            messages.warning(request, "لم يتم تحديد أي سجل للحذف.")
    return redirect(request.META.get("HTTP_REFERER", "reports:admin_reports_overview"))


@login_required
def clear_all_audit_logs_view(request):
    """Delete ALL audit log entries (super-admin only)."""
    if not request.user.is_super_admin():
        messages.error(request, "هذه الصلاحية للمدير الأعلى فقط.")
        return redirect("reports:admin_reports_overview")
    if request.method == "POST":
        cnt = AuditLog.objects.all().delete()[0]
        messages.success(request, f"تم مسح جميع سجلات التدقيق ({cnt} سجل) بنجاح.")
    return redirect("reports:admin_reports_overview")


# ─────────────────────────────────────────────────────────────────────────────
# 🔑  CAPTCHA TOGGLE VIEW
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def toggle_captcha_view(request):
    """Set CAPTCHA enforcement explicitly (super-admin only)."""
    from apps.core.models import AuditLog, SystemSetting
    if not request.user.is_super_admin():
        messages.error(request, "غير مصرح لك.")
        return redirect("dashboard")
    new_val = request.POST.get("enabled")
    if new_val not in {"true", "false"}:
        messages.error(request, "قيمة إعداد التحقق غير صالحة.")
        return redirect("dashboard")

    setting, _ = SystemSetting.objects.get_or_create(
        key="ENABLE_CAPTCHA",
        defaults={
            "value": new_val,
            "description": "تفعيل/تعطيل رمز التحقق البصري (Captcha) في تسجيل الدخول والتسجيل",
        },
    )
    setting.value = new_val
    setting.description = "تفعيل/تعطيل رمز التحقق البصري (Captcha) في تسجيل الدخول والتسجيل"
    setting.save(update_fields=["value", "description", "updated_at"])
    AuditLog.objects.create(
        user=request.user,
        action="تغيير إعداد CAPTCHA",
        ip_address=request.META.get("REMOTE_ADDR") or None,
        details={"enabled": new_val == "true"},
    )
    status_ar = "مفعّل ✅" if new_val == "true" else "معطّل ❌"
    messages.success(request, f"تم تحديث حالة رمز التحقق (Captcha) إلى: {status_ar}")
    return redirect("dashboard")
