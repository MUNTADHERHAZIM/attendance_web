import uuid

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import logout
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.views.decorators.http import require_POST
from .models import StudentProfile, TeacherProfile, GuardianProfile
from .serializers import (
    CustomTokenObtainPairSerializer, 
    UserSerializer,
    StudentProfileSerializer,
    TeacherProfileSerializer,
    GuardianProfileSerializer
)

User = get_user_model()

def is_valid_iraqi_phone(phone_str):
    """
    Validates that a phone number is an Iraqi mobile number of exactly 11 digits
    starting with '07' (e.g., 077..., 078..., 075...).
    Also normalizes +9647... into 07... if provided.
    """
    import re
    clean = re.sub(r'[\s\-\(\)\+]', '', str(phone_str or ''))
    if clean.startswith('9647'):
        clean = '0' + clean[3:]
    elif clean.startswith('009647'):
        clean = '0' + clean[5:]
    is_valid = bool(re.match(r'^07\d{9}$', clean))
    return is_valid, clean


def _manageable_sections(user):
    from apps.academics.models import ClassSection

    queryset = ClassSection.objects.all()
    if user.is_super_admin():
        return queryset

    profile = getattr(user, "teacher_profile", None)
    institution_id = getattr(profile, "institution_id", None)
    department_id = getattr(profile, "department_id", None)
    if not institution_id and user.is_institution_admin():
        profile = getattr(user, "student_profile", None)
        institution_id = getattr(profile, "institution_id", None)

    if not institution_id:
        return queryset.none()
    queryset = queryset.filter(department__institution_id=institution_id)
    if user.is_teacher() and department_id:
        queryset = queryset.filter(department_id=department_id)
    return queryset


def _manageable_users(user):
    from django.db.models import Q

    queryset = User.objects.all()
    if user.is_super_admin():
        return queryset
    institution_id = None
    for profile_name in ("teacher_profile", "student_profile"):
        profile = getattr(user, profile_name, None)
        institution_id = getattr(profile, "institution_id", None)
        if institution_id:
            break
    if not institution_id:
        return queryset.none()
    return queryset.filter(
        Q(student_profile__institution_id=institution_id)
        | Q(teacher_profile__institution_id=institution_id)
    )


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class UserProfileView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        user_serializer = UserSerializer(user)
        data = {
            "user": user_serializer.data,
            "profile": None
        }

        # Attach role-specific profile details if they exist
        if user.is_student() and hasattr(user, "student_profile"):
            data["profile"] = StudentProfileSerializer(user.student_profile).data
        elif user.is_teacher() and hasattr(user, "teacher_profile"):
            data["profile"] = TeacherProfileSerializer(user.teacher_profile).data
        elif user.is_guardian() and hasattr(user, "guardian_profile"):
            data["profile"] = GuardianProfileSerializer(user.guardian_profile).data

        return Response(data, status=status.HTTP_200_OK)


# =====================================================================
# WEB TEMPLATE VIEWS
# =====================================================================

@login_required
def profile_web_view(request):
    """
    Renders and processes the User Profile HTML page.
    Allows viewing/updating personal info, academic data (institution, university, student ID, shift, etc.) and changing password.
    """
    from django.contrib import messages
    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.forms import PasswordChangeForm
    from apps.academics.models import Institution, ClassSection
    from datetime import datetime

    user = request.user
    pwd_form = PasswordChangeForm(user)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update_info":
            first_name = request.POST.get("first_name", "").strip()
            last_name = request.POST.get("last_name", "").strip()
            email = request.POST.get("email", "").strip()
            phone = request.POST.get("phone", "").strip()

            if phone:
                is_phone_ok, clean_phone = is_valid_iraqi_phone(phone)
                if not is_phone_ok:
                    messages.error(request, "رقم الهاتف غير صحيح! يجب أن يكون رقماً عراقياً مكوّناً من 11 رقماً ويبدأ بـ 07 (مثال: 07724978301).")
                    return redirect("accounts:profile_web")
                phone = clean_phone

            user.first_name = first_name
            user.last_name = last_name
            user.email = email
            user.phone = phone

            if "avatar" in request.FILES:
                user.avatar = request.FILES["avatar"]

            user.save()

            # Handle Role-Specific Academic Profile Details (Student / Teacher)
            institution_id = request.POST.get("institution_id", "").strip()
            new_institution_name = request.POST.get("new_institution_name", "").strip()

            # Resolve or create institution (Custom typed university takes absolute priority)
            target_institution = None
            can_reassign_institution = request.user.is_super_admin()
            if can_reassign_institution and new_institution_name:
                target_institution, _ = Institution.objects.get_or_create(
                    name=new_institution_name,
                    defaults={"address": "جمهورية العراق"}
                )
            elif can_reassign_institution and institution_id and institution_id.isdigit():
                target_institution = Institution.objects.filter(id=int(institution_id)).first()

            if user.is_student():
                student_profile = getattr(user, "student_profile", None)
                if not student_profile:
                    if not target_institution:
                        target_institution = Institution.objects.first() or Institution.objects.create(name="جامعتي")
                    student_profile = StudentProfile.objects.create(
                        user=user,
                        student_id=f"STD-{user.id}",
                        institution=target_institution
                    )

                student_id = request.POST.get("student_id", "").strip()
                study_shift = request.POST.get("study_shift", "").strip().upper()
                birth_date_str = request.POST.get("birth_date", "").strip()
                section_ids = request.POST.getlist("sections")

                if student_id and student_id != student_profile.student_id:
                    if not StudentProfile.objects.filter(student_id=student_id).exclude(id=student_profile.id).exists():
                        student_profile.student_id = student_id
                    else:
                        messages.warning(request, "الرقم الجامعي مستخدم من قبل طالب آخر، تم الإبقاء على رقمك الحالي.")

                if target_institution:
                    student_profile.institution = target_institution

                if study_shift in ["MORNING", "EVENING"]:
                    student_profile.study_shift = study_shift

                if birth_date_str:
                    try:
                        student_profile.birth_date = datetime.strptime(birth_date_str, "%Y-%m-%d").date()
                    except ValueError:
                        pass

                student_profile.save()

                if section_ids and can_reassign_institution:
                    valid_sections = ClassSection.objects.filter(
                        id__in=[s for s in section_ids if s.isdigit()],
                        department__institution_id=student_profile.institution_id,
                    )
                    student_profile.sections.set(valid_sections)

            elif user.is_teacher():
                teacher_profile = getattr(user, "teacher_profile", None)
                if not teacher_profile:
                    if not target_institution:
                        target_institution = Institution.objects.first() or Institution.objects.create(name="جامعتي")
                    teacher_profile = TeacherProfile.objects.create(
                        user=user,
                        teacher_id=f"TCH-{user.id}",
                        institution=target_institution
                    )

                teacher_id = request.POST.get("teacher_id", "").strip()
                specialization = request.POST.get("specialization", "").strip()

                if teacher_id and teacher_id != teacher_profile.teacher_id:
                    if not TeacherProfile.objects.filter(teacher_id=teacher_id).exclude(id=teacher_profile.id).exists():
                        teacher_profile.teacher_id = teacher_id

                if specialization:
                    teacher_profile.specialization = specialization

                if target_institution:
                    teacher_profile.institution = target_institution

                teacher_profile.save()

            messages.success(request, "تم تحديث البيانات الشخصية والأكاديمية بنجاح! ✨")
            return redirect("accounts:profile_web")

        elif action == "change_password":
            pwd_form = PasswordChangeForm(user, request.POST)
            if pwd_form.is_valid():
                pwd_user = pwd_form.save()
                update_session_auth_hash(request, pwd_user)
                messages.success(request, "تم تغيير كلمة المرور بنجاح.")
                return redirect("accounts:profile_web")
            else:
                messages.error(request, "يرجى التحقق من صحة بيانات كلمة المرور.")

    profile = user.get_profile()
    from apps.academics.models import Institution, ClassSection
    if user.is_super_admin():
        institutions = Institution.objects.all().order_by("name")
        all_sections = ClassSection.objects.all().order_by("shift", "level", "name")
    else:
        profile_institution_id = getattr(profile, "institution_id", None)
        institutions = Institution.objects.filter(
            id=profile_institution_id
        ).order_by("name")
        all_sections = ClassSection.objects.filter(
            department__institution_id=profile_institution_id
        ).order_by("shift", "level", "name")

    birth_date_val = ""
    if profile and hasattr(profile, "birth_date") and profile.birth_date:
        birth_date_val = profile.birth_date.strftime("%Y-%m-%d")

    context = {
        "user": user,
        "profile": profile,
        "pwd_form": pwd_form,
        "institutions": institutions,
        "all_sections": all_sections,
        "birth_date_val": birth_date_val,
    }
    return render(request, "accounts/profile.html", context)


def logout_view(request):
    """
    Safely logs out the user (supporting GET request) and redirects to the login screen.
    """
    logout(request)
    return redirect("login")


class CheckTeacherCodeView(APIView):
    """
    Validates or retrieves the academic verification code dynamically configured by Admin.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from apps.core.models import SystemSetting
        code = str(request.data.get("code", "")).strip().upper()
        current = SystemSetting.get_teacher_code().upper()
        return Response({"valid": bool(code and code == current)})


class RegisterView(APIView):
    """
    Creates a new user and role-specific profile (Student or Teacher)
    and immediately logs them in.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from django.contrib.auth import login as auth_login
        from django.db import transaction
        from apps.accounts.models import StudentProfile, TeacherProfile
        from apps.academics.models import Institution
        from apps.core.models import SystemSetting
        from apps.core.captcha import consume_captcha

        username = str(request.data.get("name") or request.data.get("username") or "").strip()
        email = str(request.data.get("email") or "").strip()
        phone = str(request.data.get("phone") or "").strip()
        role = str(request.data.get("role") or "STUDENT").strip().upper()
        password = str(request.data.get("password") or "").strip()
        teacher_code = str(request.data.get("teacherCode") or request.data.get("teacher_code") or "").strip().upper()

        if not consume_captcha(request, "register", request.data.get("captcha")):
            return Response(
                {"error": "رمز التحقق غير صحيح أو انتهت صلاحيته. حدّث الرمز وحاول مجدداً."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not username:
            return Response({"error": "يرجى إدخال اسم المستخدم."}, status=status.HTTP_400_BAD_REQUEST)
        if not password or len(password) < 6:
            return Response({"error": "يجب أن تتكون كلمة المرور من 6 أحرف على الأقل."}, status=status.HTTP_400_BAD_REQUEST)

        # Check existing username
        if User.objects.filter(username__iexact=username).exists():
            return Response({"error": f"اسم المستخدم '{username}' مسجل مسبقاً. يرجى اختيار اسم مستخدم آخر."}, status=status.HTTP_400_BAD_REQUEST)

        # Check existing email
        if email and User.objects.filter(email__iexact=email).exists():
            return Response({"error": "البريد الإلكتروني مسجل مسبقاً في النظام."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate Iraqi Phone Number (exactly 11 digits starting with 07)
        if not phone:
            return Response({"error": "يرجى إدخال رقم الهاتف."}, status=status.HTTP_400_BAD_REQUEST)

        is_phone_ok, clean_phone = is_valid_iraqi_phone(phone)
        if not is_phone_ok:
            return Response({
                "error": "رقم الهاتف غير صحيح! يجب أن يكون رقماً عراقياً مكوّناً من 11 رقماً ويبدأ بـ 07 (مثال: 07724978301)."
            }, status=status.HTTP_400_BAD_REQUEST)
        phone = clean_phone

        # Validate Teacher Code
        if role == User.Roles.TEACHER:
            valid_code = SystemSetting.get_teacher_code().upper()
            if not teacher_code or teacher_code != valid_code:
                return Response({"error": "كود التحقق الأكاديمي للكادر التعليمي غير صحيح."}, status=status.HTTP_400_BAD_REQUEST)
        else:
            role = User.Roles.STUDENT

        try:
            with transaction.atomic():
                user = User(
                    username=username,
                    email=email,
                    phone=phone,
                    role=role
                )
                user.set_password(password)
                user.save()

                institution_name = request.data.get("institution_name", "").strip()
                department_name = request.data.get("department_name", "").strip()
                specialization = request.data.get("specialization", "").strip() or "عضو هيئة التدريس"

                if institution_name:
                    institution, _ = Institution.objects.get_or_create(
                        name=institution_name,
                        defaults={"address": "جمهورية العراق"}
                    )
                else:
                    institution = Institution.objects.first()
                    if not institution:
                        institution = Institution.objects.create(
                            name="جامعة بغداد - كلية علوم الحاسوب وتكنولوجيا المعلومات",
                            address="بغداد، جمهورية العراق"
                        )

                dept = None
                if department_name:
                    dept, _ = Department.objects.get_or_create(
                        institution=institution,
                        name=department_name,
                        defaults={"code": department_name[:3].upper()}
                    )

                if role == User.Roles.TEACHER:
                    t_count = TeacherProfile.objects.count() + 1
                    TeacherProfile.objects.create(
                        user=user,
                        teacher_id=f"T{1000 + t_count}",
                        institution=institution,
                        department=dept,
                        specialization=specialization
                    )
                else:
                    s_count = StudentProfile.objects.count() + 1
                    StudentProfile.objects.create(
                        user=user,
                        student_id=f"S{2000 + s_count}",
                        institution=institution
                    )

                # Log the user in to create a valid Django session
                auth_login(request, user, backend="apps.accounts.backends.EmailOrUsernameBackend")

            return Response({
                "success": True,
                "message": "تم إنشاء الحساب وتسجيل الدخول بنجاح!",
                "username": user.username,
                "role": user.role,
                "redirect_url": "/"
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({"error": f"تعذر إنشاء الحساب: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ─── Teacher Student Management Views ─────────────────────────────────

@login_required
def teacher_students_view(request):
    """
    Independent Student Management screen for Teachers.
    Allows viewing all students in the teacher's sections, searching, adding, and importing students.
    """
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك بدخول هذه الصفحة.")
        return redirect("/")

    from apps.academics.models import ClassSection, Session
    from apps.attendance.models import AttendanceRecord
    from django.db.models import Q

    teacher_profile = getattr(request.user, "teacher_profile", None)
    if request.user.is_teacher() and not teacher_profile:
        messages.error(request, "لا يوجد ملف أستاذ مرتبط بحسابك.")
        return redirect("dashboard")

    if request.user.is_teacher():
        section_ids = Session.objects.filter(
            teacher=teacher_profile
        ).values_list("class_section_id", flat=True).distinct()
        my_sections = ClassSection.objects.filter(id__in=section_ids)
        if not my_sections.exists():
            my_sections = _manageable_sections(request.user)
    else:
        my_sections = _manageable_sections(request.user)

    # Filter by section
    selected_section_id = request.GET.get("section_id", "")
    shift_filter = request.GET.get("shift", "").strip().upper()
    students_qs = StudentProfile.objects.filter(sections__in=my_sections).distinct().select_related("user")

    if selected_section_id and selected_section_id.isdigit():
        students_qs = students_qs.filter(sections__id=int(selected_section_id))

    if shift_filter in ["MORNING", "EVENING"]:
        students_qs = students_qs.filter(study_shift=shift_filter)

    # Search filter
    q = request.GET.get("q", "").strip()
    if q:
        students_qs = students_qs.filter(
            Q(user__first_name__icontains=q) |
            Q(user__last_name__icontains=q) |
            Q(user__username__icontains=q) |
            Q(student_id__icontains=q) |
            Q(user__phone__icontains=q)
        )

    # Shift counts
    morning_count = StudentProfile.objects.filter(sections__in=my_sections, study_shift="MORNING").distinct().count()
    evening_count = StudentProfile.objects.filter(sections__in=my_sections, study_shift="EVENING").distinct().count()

    # Build student cards with attendance stats
    students_list = []
    for std in students_qs.order_by("user__first_name"):
        records = AttendanceRecord.objects.filter(student=std)
        tot = records.count()
        pres = records.filter(status__in=["PRESENT", "LATE"]).count()
        absent = records.filter(status="ABSENT").count()
        rate = round((pres / tot * 100), 1) if tot > 0 else 100.0
        students_list.append({
            "profile": std,
            "total_records": tot,
            "present_count": pres,
            "absent_count": absent,
            "rate": rate,
            "sections": list(std.sections.all()),
            "shift_display": "صباحي" if std.study_shift == "MORNING" else "مسائي",
        })

    context = {
        "teacher": teacher_profile,
        "my_sections": my_sections,
        "selected_section_id": selected_section_id,
        "shift_filter": shift_filter,
        "morning_count": morning_count,
        "evening_count": evening_count,
        "students_list": students_list,
        "total_count": len(students_list),
        "search_query": q,
    }
    return render(request, "accounts/teacher_students.html", context)


@login_required
def teacher_add_student_view(request):
    """Allows a teacher to quickly add a new student and enroll them into a class section."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
            return JsonResponse({"success": False, "error": "غير مصرح لك"}, status=403)
        messages.error(request, "غير مصرح لك.")
        return redirect("/")

    from apps.academics.models import ClassSection
    from django.http import JsonResponse

    if request.method != "POST":
        return redirect("accounts:teacher_students")

    first_name = request.POST.get("first_name", "").strip()
    last_name = request.POST.get("last_name", "").strip()
    student_id = request.POST.get("student_id", "").strip()
    phone = request.POST.get("phone", "").strip()
    email = request.POST.get("email", "").strip()
    section_id = request.POST.get("section_id", "").strip()
    study_shift = request.POST.get("study_shift", "").strip().upper()
    rfid_card = request.POST.get("rfid_card", "").strip() or None

    new_sec_name = request.POST.get("new_section_name", "").strip()
    if not first_name or not student_id or (not section_id and not new_sec_name):
        msg = "يرجى تعبئة الحقول المطلوبة (الاسم الكامل، الرقم الجامعي، والشعبة)."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # Validate phone if provided
    if phone:
        valid_phone, clean_phone = is_valid_iraqi_phone(phone)
        if not valid_phone:
            msg = "رقم الهاتف يجب أن يكون رقم هاتف عراقي مكون من 11 رقماً ويبدأ بـ 07"
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": False, "error": msg}, status=400)
            messages.error(request, msg)
            return redirect(request.META.get("HTTP_REFERER", "/"))
        phone = clean_phone

    # Resolve or create section
    teacher_profile = getattr(request.user, "teacher_profile", None)
    if not teacher_profile and not request.user.is_super_admin():
        messages.error(request, "لا يوجد ملف أكاديمي مرتبط بحسابك لتحديد المؤسسة.")
        return redirect("dashboard")
    if new_sec_name:
        from apps.academics.models import Department
        new_sec_level = request.POST.get("new_section_level", "المرحلة الأولى").strip()
        new_sec_shift = request.POST.get("new_section_shift", study_shift).strip().upper()
        if new_sec_shift not in ["MORNING", "EVENING"]:
            new_sec_shift = "MORNING"

        dept = teacher_profile.department if teacher_profile else None
        if not dept and teacher_profile and teacher_profile.institution:
            dept = Department.objects.filter(institution=teacher_profile.institution).first()
            if not dept:
                dept = Department.objects.create(
                    institution=teacher_profile.institution,
                    name=teacher_profile.specialization or "القسم الأكاديمي",
                    code="CS"
                )
            teacher_profile.department = dept
            teacher_profile.save(update_fields=["department"])
        elif not dept:
            dept = Department.objects.first()

        section, _ = ClassSection.objects.get_or_create(
            department=dept,
            name=new_sec_name,
            level=new_sec_level,
            defaults={"shift": new_sec_shift}
        )
    elif section_id and section_id.isdigit():
        section = get_object_or_404(
            _manageable_sections(request.user), id=int(section_id)
        )
    else:
        msg = "يرجى اختيار شعبة دراسية صالحة أو إدخال اسم شعبة جديدة."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect(request.META.get("HTTP_REFERER", "/"))

    institution = section.department.institution

    # If study shift not explicitly specified, inherit from section
    if study_shift not in ["MORNING", "EVENING"]:
        study_shift = section.shift or "MORNING"
    if study_shift != section.shift:
        msg = "نوع دوام الطالب يجب أن يطابق نوع دوام الشعبة."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))

    # Check if student_id already taken
    existing_sp = StudentProfile.objects.filter(student_id=student_id).first()
    if existing_sp:
        if existing_sp.institution_id != institution.id:
            msg = "لا يمكن إلحاق طالب من مؤسسة أخرى بهذه الشعبة."
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": False, "error": msg}, status=403)
            messages.error(request, msg)
            return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))
        if existing_sp.study_shift != section.shift:
            msg = "نوع دوام الطالب لا يطابق نوع دوام الشعبة."
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": False, "error": msg}, status=400)
            messages.error(request, msg)
            return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))
        existing_sp.sections.add(section)
        msg = f"تم إلحاق الطالب ({existing_sp.user.get_full_name()}) بالشعبة {section.name} بنجاح!"
        messages.success(request, msg)
        return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))

    # Create new User
    username = f"std_{student_id}"
    if not email:
        email = f"{username}@uobaghdad.edu.iq"

    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "email": email,
            "role": User.Roles.STUDENT,
        }
    )
    if created:
        user.set_unusable_password()
        user.save()
    elif user.role != User.Roles.STUDENT:
        msg = "اسم حساب الطالب مستخدم لحساب غير طلابي."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": msg}, status=409)
        messages.error(request, msg)
        return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))

    sp, sp_created = StudentProfile.objects.get_or_create(
        user=user,
        defaults={
            "student_id": student_id,
            "institution": institution,
            "study_shift": study_shift,
            "rfid_card": rfid_card,
        }
    )
    if sp.institution_id != institution.id or sp.study_shift != section.shift:
        msg = "حساب الطالب موجود في مؤسسة أخرى أو بنوع دوام مختلف."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": msg}, status=409)
        messages.error(request, msg)
        return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))
    sp.sections.add(section)
    if not sp_created and sp.study_shift != study_shift:
        sp.study_shift = study_shift
        sp.save()

    shift_title = "صباحي" if study_shift == "MORNING" else "مسائي"
    msg = f"تمت إضافة الطالب ({first_name} {last_name}) [دوام {shift_title}] وربطه بالشعبة {section.name} بنجاح!"
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"success": True, "message": msg, "student_id": student_id, "name": f"{first_name} {last_name}"})

    messages.success(request, msg)
    return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))


@login_required
def teacher_import_students_view(request):
    """Allows uploading Excel / CSV file of students directly into teacher's section."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك.")
        return redirect("/")

    from apps.academics.models import ClassSection
    import pandas as pd

    if request.method != "POST":
        messages.error(request, "يرجى إرسال نموذج الاستيراد.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    import_mode = request.POST.get("import_mode", "file").strip().lower()
    excel_file = request.FILES.get("excel_file")
    if import_mode != "paste" and not excel_file:
        messages.error(request, "يرجى اختيار ملف Excel أو CSV صالح.")
        return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))

    pasted_names = None
    if import_mode == "paste":
        raw_names = request.POST.get("student_names", "")
        if len(raw_names) > 50000:
            messages.error(request, "قائمة الأسماء كبيرة جداً؛ الحد الأقصى 500 طالب في كل عملية.")
            return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))
        pasted_names = [line.strip() for line in raw_names.splitlines() if line.strip()]
        if len(pasted_names) > 500:
            messages.error(request, "الحد الأقصى هو 500 طالب في كل عملية استيراد.")
            return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))
        if not pasted_names:
            messages.error(request, "الصق اسم طالب واحداً على الأقل، كل اسم في سطر مستقل.")
            return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))

    section_id = request.POST.get("section_id", "").strip()
    new_sec_name = request.POST.get("new_section_name", "").strip()
    teacher_profile = getattr(request.user, "teacher_profile", None)
    if not teacher_profile and not request.user.is_super_admin():
        messages.error(request, "لا يوجد ملف أكاديمي مرتبط بحسابك لتحديد المؤسسة.")
        return redirect("dashboard")

    if new_sec_name:
        from apps.academics.models import Department
        new_sec_level = request.POST.get("new_section_level", "المرحلة الأولى").strip()
        new_sec_shift = request.POST.get("new_section_shift", "MORNING").strip().upper()
        if new_sec_shift not in ["MORNING", "EVENING"]:
            new_sec_shift = "MORNING"

        dept = teacher_profile.department if teacher_profile else None
        if not dept and teacher_profile and teacher_profile.institution:
            dept = Department.objects.filter(institution=teacher_profile.institution).first()
            if not dept:
                dept = Department.objects.create(
                    institution=teacher_profile.institution,
                    name=teacher_profile.specialization or "القسم الأكاديمي",
                    code="CS"
                )
            teacher_profile.department = dept
            teacher_profile.save(update_fields=["department"])
        elif not dept:
            dept = Department.objects.first()

        section, _ = ClassSection.objects.get_or_create(
            department=dept,
            name=new_sec_name,
            level=new_sec_level,
            defaults={"shift": new_sec_shift}
        )
    elif section_id and section_id.isdigit():
        section = get_object_or_404(
            _manageable_sections(request.user), id=int(section_id)
        )
    else:
        messages.error(request, "يرجى اختيار شعبة دراسية صالحة أو إدخال بيانات الشعبة الجديدة المراد إنشاؤها.")
        return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))

    institution = section.department.institution

    if import_mode == "paste":
        from apps.attendance.utils import normalize_arabic_text

        existing_names = {
            normalize_arabic_text(
                student.user.get_full_name() or student.user.username
            )
            for student in section.students.select_related("user").all()
        }
        seen_names = set()
        added_count = 0
        skipped_count = 0
        invalid_count = 0

        with transaction.atomic():
            for full_name in pasted_names:
                normalized_name = normalize_arabic_text(full_name)
                if not normalized_name or normalized_name in seen_names:
                    skipped_count += 1
                    continue
                seen_names.add(normalized_name)

                parts = full_name.split(maxsplit=1)
                first_name = parts[0]
                last_name = parts[1] if len(parts) > 1 else ""
                if len(first_name) > 150 or len(last_name) > 150:
                    invalid_count += 1
                    continue
                if normalized_name in existing_names:
                    skipped_count += 1
                    continue

                unique_key = uuid.uuid4().hex
                username = f"std_paste_{unique_key}"
                user = User(
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    role=User.Roles.STUDENT,
                )
                user.set_unusable_password()
                user.save()
                student = StudentProfile.objects.create(
                    user=user,
                    student_id=f"AUTO-{unique_key.upper()}",
                    institution=institution,
                    study_shift=section.shift,
                )
                student.sections.add(section)
                existing_names.add(normalized_name)
                added_count += 1

        if added_count:
            messages.success(
                request,
                f"تم إنشاء وإلحاق {added_count} طالباً بالشعبة ({section.name}). "
                f"أُنشئت لهم أرقام جامعية تلقائية، وكلمات المرور غير مفعّلة.",
            )
        if skipped_count or invalid_count:
            messages.warning(
                request,
                f"تم تجاوز {skipped_count} اسماً مكرراً، و{invalid_count} اسماً أطول من الحد المسموح."
            )
        if not added_count and not skipped_count and invalid_count:
            messages.error(request, "لم تُضف أي أسماء؛ تحقق من طول الأسماء وأعد المحاولة.")
        return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))

    try:
        if excel_file.name.endswith(".csv"):
            df = pd.read_csv(excel_file)
        else:
            df = pd.read_excel(excel_file)
    except Exception as e:
        messages.error(request, f"تعذر قراءة الملف: {str(e)}")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    # Normalize column names
    col_map = {}
    for col in df.columns:
        c_clean = str(col).strip().lower()
        if "اسم" in c_clean or "name" in c_clean:
            col_map["name"] = col
        elif "رقم" in c_clean or "id" in c_clean or "جامعي" in c_clean:
            col_map["id"] = col
        elif "هاتف" in c_clean or "phone" in c_clean or "موبايل" in c_clean:
            col_map["phone"] = col
        elif "بريد" in c_clean or "email" in c_clean:
            col_map["email"] = col

    if "name" not in col_map or "id" not in col_map:
        messages.error(request, "يجب أن يحتوي الملف على عمود للاسم الكامل وعمود للرقم الجامعي.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    added_count = 0
    for _, row in df.iterrows():
        raw_name = str(row[col_map["name"]]).strip()
        raw_id = str(row[col_map["id"]]).strip()
        if not raw_name or not raw_id or raw_id == "nan":
            continue

        raw_phone = str(row.get(col_map.get("phone", ""), "")).strip() if "phone" in col_map else ""
        if raw_phone == "nan": raw_phone = ""
        _, clean_phone = is_valid_iraqi_phone(raw_phone) if raw_phone else (True, "")

        parts = raw_name.split(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

        uname = f"std_{raw_id}"
        u, _ = User.objects.get_or_create(
            username=uname,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
                "phone": clean_phone,
                "email": f"{uname}@uobaghdad.edu.iq",
                "role": User.Roles.STUDENT,
            }
        )
        if u.role != User.Roles.STUDENT:
            continue
        sp, _ = StudentProfile.objects.get_or_create(
            user=u,
            defaults={
                "student_id": raw_id,
                "institution": institution,
                "study_shift": section.shift,
            }
        )
        if sp.institution_id != institution.id or sp.study_shift != section.shift:
            continue
        sp.sections.add(section)
        added_count += 1

    messages.success(request, f"تم استيراد {added_count} طالباً وإلحاقهم بالشعبة ({section.name}) بنجاح!")
    return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))


@login_required
def teacher_add_section_view(request):
    """Allows a teacher to create a new class section belonging strictly to their university/department."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "غير مصرح لك"}, status=403)
        messages.error(request, "غير مصرح لك.")
        return redirect("/")

    if request.method != "POST":
        return redirect("dashboard")

    from apps.academics.models import ClassSection, Department, Session, Course
    from datetime import time
    teacher_profile = getattr(request.user, "teacher_profile", None)
    if not teacher_profile:
        messages.error(request, "لم يتم العثور على ملف الأستاذ.")
        return redirect("dashboard")

    name = request.POST.get("name", "").strip()
    level = request.POST.get("level", "").strip() or "المرحلة الأولى"
    shift = request.POST.get("shift", "MORNING").strip().upper()
    if shift not in ["MORNING", "EVENING"]:
        shift = "MORNING"

    if not name:
        msg = "يرجى كتابة اسم الشعبة أو المجموعة (مثال: شعبة أ أو Group A)."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect(request.META.get("HTTP_REFERER", "dashboard"))

    # Resolve department
    dept = teacher_profile.department
    if not dept:
        dept = Department.objects.filter(institution=teacher_profile.institution).first()
        if not dept:
            dept = Department.objects.create(
                institution=teacher_profile.institution,
                name=teacher_profile.specialization or "القسم الأكاديمي",
                code="CS"
            )
        teacher_profile.department = dept
        teacher_profile.save(update_fields=["department"])

    section = ClassSection.objects.create(
        department=dept,
        name=name,
        level=level,
        shift=shift
    )

    # Optional: Link to an existing course
    course_id = request.POST.get("course_id", "").strip()
    if course_id and course_id.isdigit():
        course = Course.objects.filter(id=int(course_id), department__institution=teacher_profile.institution).first()
        if course:
            Session.objects.create(
                course=course,
                class_section=section,
                teacher=teacher_profile,
                day_of_week=0,
                start_time=time(9, 0),
                end_time=time(10, 30),
                shift=shift,
                room="القاعة الدراسية"
            )

    shift_title = "صباحي" if shift == "MORNING" else "مسائي"
    msg = f"تم إنشاء الشعبة الدراسية الجديدة ({level} - {name}) [دراسة {shift_title}] بنجاح!"
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"success": True, "message": msg, "section_id": section.id, "name": str(section)})

    messages.success(request, msg)
    return redirect(request.META.get("HTTP_REFERER", "dashboard"))


@login_required
def teacher_add_course_view(request):
    """Allows a teacher to add a new course/subject belonging strictly to their university/department."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "غير مصرح لك"}, status=403)
        messages.error(request, "غير مصرح لك.")
        return redirect("/")

    if request.method != "POST":
        return redirect("dashboard")

    from apps.academics.models import Course, Department, Session, ClassSection
    from datetime import time
    teacher_profile = getattr(request.user, "teacher_profile", None)
    if not teacher_profile:
        messages.error(request, "لم يتم العثور على ملف الأستاذ.")
        return redirect("dashboard")

    name = request.POST.get("name", "").strip()
    code = request.POST.get("code", "").strip()
    section_id = request.POST.get("section_id", "").strip()

    if not name:
        msg = "يرجى كتابة اسم المادة الدراسية (مثال: الذكاء الاصطناعي)."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect(request.META.get("HTTP_REFERER", "dashboard"))

    dept = teacher_profile.department
    if not dept:
        dept = Department.objects.filter(institution=teacher_profile.institution).first()
        if not dept:
            dept = Department.objects.create(
                institution=teacher_profile.institution,
                name=teacher_profile.specialization or "القسم الأكاديمي",
                code="GEN"
            )
        teacher_profile.department = dept
        teacher_profile.save(update_fields=["department"])

    if not code:
        existing_cnt = Course.objects.filter(department=dept).count() + 1
        code = f"{dept.code or 'CRS'}{100 + existing_cnt}"

    course, created = Course.objects.get_or_create(
        department=dept,
        code=code,
        defaults={"name": name}
    )
    if not created:
        course.name = name
        course.save(update_fields=["name"])

    # If linked to a section
    if section_id and section_id.isdigit():
        sec = ClassSection.objects.filter(id=int(section_id), department__institution=teacher_profile.institution).first()
        if sec:
            Session.objects.get_or_create(
                course=course,
                class_section=sec,
                teacher=teacher_profile,
                defaults={
                    "day_of_week": 0,
                    "start_time": time(9, 0),
                    "end_time": time(10, 30),
                    "shift": sec.shift,
                    "room": "القاعة الدراسية"
                }
            )

    msg = f"تمت إضافة المادة الدراسية ({name} - {code}) إلى سجلك الأكاديمي بنجاح!"
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"success": True, "message": msg, "course_id": course.id, "name": course.name, "code": course.code})

    messages.success(request, msg)
    return redirect(request.META.get("HTTP_REFERER", "dashboard"))


@login_required
def teacher_update_profile_view(request):
    """Allows a teacher to personalize their university name, college, department, and academic rank."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك.")
        return redirect("/")

    if request.method != "POST":
        return redirect("dashboard")

    from apps.academics.models import Institution, Department
    teacher_profile = getattr(request.user, "teacher_profile", None)
    if not teacher_profile:
        messages.error(request, "لم يتم العثور على ملف الأستاذ.")
        return redirect("dashboard")

    first_name = request.POST.get("first_name", "").strip()
    institution_name = request.POST.get("institution_name", "").strip()
    department_name = request.POST.get("department_name", "").strip()
    specialization = request.POST.get("specialization", "").strip()
    phone = request.POST.get("phone", "").strip()

    if first_name:
        request.user.first_name = first_name
    if phone:
        valid_phone, clean_phone = is_valid_iraqi_phone(phone)
        if valid_phone:
            request.user.phone = clean_phone
    request.user.save()

    if institution_name and request.user.is_super_admin():
        inst, _ = Institution.objects.get_or_create(
            name=institution_name,
            defaults={"address": "جمهورية العراق"}
        )
        teacher_profile.institution = inst

    if department_name:
        dept, _ = Department.objects.get_or_create(
            institution=teacher_profile.institution,
            name=department_name,
            defaults={"code": department_name[:3].upper()}
        )
        teacher_profile.department = dept

    if specialization:
        teacher_profile.specialization = specialization

    teacher_profile.save()

    messages.success(request, f"تم تحديث بيانات ملفك الأكاديمي بنجاح: ({teacher_profile.institution.name}) - ({teacher_profile.department.name if teacher_profile.department else ''})!")
    return redirect(request.META.get("HTTP_REFERER", "dashboard"))


@login_required
def admin_users_directory_view(request):
    """
    Comprehensive multi-criteria filtered directory of all accounts, teachers, and students.
    Only accessible by Super Admins and Institution Admins.
    """
    if not (request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك بدخول هذه الصفحة.")
        return redirect("/")

    from apps.academics.models import Institution, ClassSection, Department
    from django.db.models import Q

    # Handle quick POST actions (Toggle Active, Reset Password, Delete)
    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        target_user_id = request.POST.get("target_user_id", "").strip()
        target_user = (
            _manageable_users(request.user).filter(id=target_user_id).first()
            if target_user_id.isdigit()
            else None
        )

        if target_user:
            if action == "toggle_active":
                target_user.is_active = not target_user.is_active
                target_user.save(update_fields=["is_active"])
                status_str = "تفعيل" if target_user.is_active else "تعطيل"
                messages.success(request, f"تم {status_str} حساب المستخدم ({target_user.get_full_name() or target_user.username}) بنجاح.")
            elif action == "reset_password":
                new_pwd = request.POST.get("new_password", "").strip() or "12345678"
                target_user.set_password(new_pwd)
                target_user.save()
                messages.success(request, f"تمت إعادة تعيين كلمة المرور للمستخدم ({target_user.get_full_name() or target_user.username}) بنجاح إلى: {new_pwd}")
            elif action == "delete_user":
                if target_user == request.user:
                    messages.error(request, "لا يمكنك حذف حسابك الحالي!")
                else:
                    uname = target_user.get_full_name() or target_user.username
                    target_user.delete()
                    messages.success(request, f"تم حذف حساب المستخدم ({uname}) نهائياً.")
            return redirect(request.get_full_path())

    # Get Filter Parameters
    q = request.GET.get("q", "").strip().lower()
    role_filter = request.GET.get("role", "ALL").strip().upper()
    inst_filter = request.GET.get("institution_id", "").strip()
    level_filter = request.GET.get("level", "").strip()
    sec_filter = request.GET.get("section_id", "").strip()
    shift_filter = request.GET.get("shift", "ALL").strip().upper()
    status_filter = request.GET.get("status", "ALL").strip().upper()

    # Base Query
    users_qs = _manageable_users(request.user).select_related(
        "student_profile__institution", 
        "teacher_profile__institution", 
        "teacher_profile__department"
    ).prefetch_related(
        "student_profile__sections__department"
    ).order_by("-date_joined")

    # Apply Role Filter
    if role_filter == "TEACHER":
        users_qs = users_qs.filter(role=User.Roles.TEACHER)
    elif role_filter == "STUDENT":
        users_qs = users_qs.filter(role=User.Roles.STUDENT)
    elif role_filter == "ADMIN":
        users_qs = users_qs.filter(role__in=[User.Roles.SUPER_ADMIN, User.Roles.INSTITUTION_ADMIN])

    # Apply Institution Filter
    if inst_filter and inst_filter.isdigit():
        inst_id = int(inst_filter)
        users_qs = users_qs.filter(
            Q(student_profile__institution_id=inst_id) | 
            Q(teacher_profile__institution_id=inst_id)
        )

    # Apply Academic Level / Stage Filter
    if level_filter:
        users_qs = users_qs.filter(student_profile__sections__level=level_filter)

    # Apply Section Filter
    if sec_filter and sec_filter.isdigit():
        sec_id = int(sec_filter)
        users_qs = users_qs.filter(student_profile__sections__id=sec_id)

    # Apply Shift Filter
    if shift_filter in ["MORNING", "EVENING"]:
        users_qs = users_qs.filter(student_profile__study_shift=shift_filter)

    # Apply Status Filter
    if status_filter == "ACTIVE":
        users_qs = users_qs.filter(is_active=True)
    elif status_filter == "INACTIVE":
        users_qs = users_qs.filter(is_active=False)

    # Apply Text Search
    if q:
        users_qs = users_qs.filter(
            Q(first_name__icontains=q) | 
            Q(last_name__icontains=q) | 
            Q(username__icontains=q) | 
            Q(email__icontains=q) | 
            Q(phone__icontains=q) | 
            Q(student_profile__student_id__icontains=q) |
            Q(teacher_profile__teacher_id__icontains=q)
        )

    users_qs = users_qs.distinct()

    # Calculate global statistics
    all_users = _manageable_users(request.user)
    stats = {
        "total_users": all_users.count(),
        "total_teachers": all_users.filter(role=User.Roles.TEACHER).count(),
        "total_students": all_users.filter(role=User.Roles.STUDENT).count(),
        "total_active": all_users.filter(is_active=True).count(),
        "total_inactive": all_users.filter(is_active=False).count(),
        "filtered_count": users_qs.count(),
    }

    # Meta choices for filter dropdowns
    if request.user.is_super_admin():
        institutions = Institution.objects.all()
    else:
        institution_id = getattr(
            getattr(request.user, "teacher_profile", None), "institution_id", None
        ) or getattr(
            getattr(request.user, "student_profile", None), "institution_id", None
        )
        institutions = Institution.objects.filter(id=institution_id)
    sections = ClassSection.objects.filter(
        department__institution__in=institutions
    ).select_related("department")
    levels = ["المرحلة الأولى", "المرحلة الثانية", "المرحلة الثالثة", "المرحلة الرابعة"]

    context = {
        "users": users_qs[:150],
        "stats": stats,
        "institutions": institutions,
        "sections": sections,
        "levels": levels,
        "q": q,
        "role_filter": role_filter,
        "inst_filter": inst_filter,
        "level_filter": level_filter,
        "sec_filter": sec_filter,
        "shift_filter": shift_filter,
        "status_filter": status_filter,
    }
    return render(request, "accounts/admin_directory.html", context)


@login_required
@require_POST
def teacher_delete_course_view(request, course_id):
    """Allows deleting a course/subject."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك.")
        return redirect("dashboard")
    from apps.academics.models import Course
    course = get_object_or_404(
        Course.objects.filter(department__in=_manageable_sections(request.user).values("department_id")),
        id=course_id,
    )
    if request.user.is_teacher() and course.sessions.exclude(
        teacher__user=request.user
    ).exists():
        messages.error(request, "لا يمكن حذف مادة مرتبطة بمحاضرات أساتذة آخرين.")
        return redirect(request.META.get("HTTP_REFERER", "dashboard"))
    cname = course.name
    course.delete()
    messages.success(request, f"تم حذف المادة الدراسية ({cname}) بنجاح.")
    return redirect(request.META.get("HTTP_REFERER", "dashboard"))


@login_required
@require_POST
def teacher_delete_section_view(request, section_id):
    """Allows deleting a class section."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك.")
        return redirect("dashboard")
    from apps.academics.models import ClassSection
    section = get_object_or_404(_manageable_sections(request.user), id=section_id)
    if request.user.is_teacher():
        from apps.academics.models import Session
        if Session.objects.filter(class_section=section).exclude(
            teacher__user=request.user
        ).exists():
            messages.error(request, "لا يمكن حذف شعبة يستخدمها أساتذة آخرون.")
            return redirect(request.META.get("HTTP_REFERER", "dashboard"))
    sname = f"{section.level} - {section.name}"
    section.delete()
    messages.success(request, f"تم حذف الشعبة الدراسية ({sname}) بنجاح.")
    return redirect(request.META.get("HTTP_REFERER", "dashboard"))


@login_required
@require_POST
def teacher_delete_student_view(request, student_id):
    """Allows deleting a single student and their user record."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك.")
        return redirect("accounts:teacher_students")
    std = get_object_or_404(
        StudentProfile.objects.filter(sections__in=_manageable_sections(request.user)).distinct(),
        id=student_id,
    )
    name = std.user.get_full_name() or std.user.username
    if request.user.is_super_admin():
        std.user.delete()
        messages.success(request, f"تم حذف الطالب ({name}) وسجلاته بنجاح.")
    else:
        managed_sections = list(
            std.sections.filter(id__in=_manageable_sections(request.user))
        )
        std.sections.remove(*managed_sections)
        if not std.sections.exists():
            std.user.delete()
            messages.success(request, f"تم حذف الطالب ({name}) لعدم ارتباطه بأي شعبة أخرى.")
        else:
            messages.success(request, f"تم إلغاء تسجيل الطالب ({name}) من الشعب التي تديرها فقط.")
    return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))


@login_required
@require_POST
def teacher_bulk_delete_students_view(request):
    """Allows bulk deleting selected students."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك.")
        return redirect("accounts:teacher_students")
    if request.method == "POST":
        student_ids = request.POST.getlist("selected_students")
        if not student_ids:
            raw_str = request.POST.get("selected_students_str", "")
            student_ids = [s.strip() for s in raw_str.split(",") if s.strip()]
        
        valid_ids = [int(sid) for sid in student_ids if sid.isdigit()]
        if valid_ids:
            students = StudentProfile.objects.filter(
                id__in=valid_ids,
                sections__in=_manageable_sections(request.user),
            ).distinct()
            count = students.count()
            if request.user.is_super_admin():
                User.objects.filter(student_profile__in=students).delete()
            else:
                for student in students:
                    managed_sections = list(
                        student.sections.filter(id__in=_manageable_sections(request.user))
                    )
                    student.sections.remove(*managed_sections)
                    if not student.sections.exists():
                        student.user.delete()
            messages.success(request, f"تم حذف {count} طالب بنجاح.")
        else:
            messages.warning(request, "لم يتم تحديد أي طالب للحذف.")
    return redirect(request.META.get("HTTP_REFERER", "accounts:teacher_students"))
