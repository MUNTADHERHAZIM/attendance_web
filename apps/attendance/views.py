import uuid
import qrcode
from io import BytesIO
from datetime import time, date, datetime

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Q

import json
import random
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer
from rest_framework import serializers as drf_serializers

from .models import AttendanceSession, AttendanceRecord, SyncQueue
from .serializers import AttendanceSessionSerializer, AttendanceRecordSerializer
from .utils import (
    generate_qr_token,
    verify_qr_token,
    is_ip_in_subnet,
    calculate_haversine_distance,
    normalize_arabic_text,
    find_matching_student,
)
from apps.accounts.permissions import IsTeacher, IsStudent
from apps.accounts.models import StudentProfile, TeacherProfile
from apps.academics.models import Session, Course, ClassSection, Department, Institution
from apps.core.models import AuditLog


# =====================================================================
# HELPERS
# =====================================================================

def queue_for_sync(record, action_type):
    """
    Serializes an AttendanceRecord change and stores it in the local SyncQueue
    for later synchronization with the central cloud server.
    """
    payload = {
        "student_id": record.student.student_id,
        "session_id": record.attendance_session.session.id,
        "date": str(record.attendance_session.date),
        "status": record.status,
        "method": record.method,
        "timestamp": (
            record.timestamp.isoformat()
            if hasattr(record.timestamp, "isoformat")
            else str(timezone.now())
        ),
        "ip_address": record.ip_address,
        "notes": record.notes or "",
    }
    SyncQueue.objects.create(
        record_id=record.id,
        model_name="AttendanceRecord",
        action=action_type,
        payload=payload,
        is_synced=False,
    )


def _get_ip(request):
    """Extracts the real client IP address from the request."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _get_students_for_session(attendance_session):
    """
    ✅ FIX: Returns students enrolled in the specific class section of the session,
    not all students in the institution.
    """
    class_section = attendance_session.session.class_section
    return StudentProfile.objects.filter(
        sections=class_section
    ).select_related("user")


# =====================================================================
# 1. API VIEWS (DRF Endpoints for mobile/PWA)
# =====================================================================

@extend_schema(
    summary="بدء جلسة تحضير",
    description="يفتح المعلم جلسة تحضير جديدة لحصة معينة في تاريخ اليوم.",
    responses={201: AttendanceSessionSerializer},
)
class StartAttendanceSessionView(APIView):
    permission_classes = [IsTeacher]

    def post(self, request):
        session_id = request.data.get("session")
        requires_wifi = request.data.get("requires_wifi", False)
        requires_geofence = request.data.get("requires_geofence", False)
        latitude = request.data.get("latitude")
        longitude = request.data.get("longitude")
        radius_meters = request.data.get("radius_meters", 50)

        session_obj = get_object_or_404(Session, id=session_id)
        now = timezone.now()
        end_time = now + timezone.timedelta(minutes=30)
        generated_otp = f"{random.randint(100000, 999999)}"

        attendance_session, created = AttendanceSession.objects.get_or_create(
            session=session_obj,
            date=now.date(),
            defaults={
                "created_by": request.user,
                "end_time": end_time,
                "requires_wifi": requires_wifi,
                "requires_geofence": requires_geofence,
                "latitude": latitude,
                "longitude": longitude,
                "radius_meters": radius_meters,
                "quick_otp": generated_otp,
                "is_active": True,
            },
        )

        if not created and not attendance_session.is_active:
            attendance_session.is_active = True
            attendance_session.end_time = end_time
            attendance_session.qr_salt = uuid.uuid4()
            if not attendance_session.quick_otp:
                attendance_session.quick_otp = generated_otp
            attendance_session.save(update_fields=["is_active", "end_time", "qr_salt", "quick_otp"])

        AuditLog.objects.create(
            user=request.user,
            action="فتح_جلسة_تحضير",
            ip_address=_get_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT"),
            details={"session_id": session_obj.id, "course": session_obj.course.name},
        )

        serializer = AttendanceSessionSerializer(attendance_session)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication

class SafeJWTOrSessionAuthentication(SessionAuthentication):
    """
    Safely authenticates via JWT Bearer header if valid, 
    otherwise falls back gracefully to standard Django Session Cookie.
    Prevents expired/stale localStorage JWT tokens from breaking teacher session views.
    """
    def authenticate(self, request):
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                jwt_auth = JWTAuthentication()
                res = jwt_auth.authenticate(request)
                if res is not None:
                    return res
            except Exception:
                pass  # Fall back to session cookie
        return super().authenticate(request)


@extend_schema(
    summary="الحصول على رمز QR الديناميكي",
    description="يُعيد رمز QR مؤقت (15 ثانية) لجلسة تحضير نشطة.",
    responses={200: OpenApiResponse(description="رمز QR والمدة الزمنية")},
)
class GetDynamicQRTokenView(APIView):
    authentication_classes = [SafeJWTOrSessionAuthentication]
    permission_classes = [IsTeacher]

    def get(self, request, session_id):
        attendance_session = get_object_or_404(AttendanceSession, id=session_id, is_active=True)
        # If not frozen, rotate salt normally every 15s. If frozen, preserve stable salt!
        if not getattr(attendance_session, "is_frozen_qr", False):
            attendance_session.qr_salt = uuid.uuid4()
            attendance_session.save(update_fields=["qr_salt"])

        token = generate_qr_token(attendance_session)
        expires = 86400 if getattr(attendance_session, "is_frozen_qr", False) else 15
        return Response(
            {
                "token": token,
                "session_id": attendance_session.id,
                "quick_otp": attendance_session.quick_otp,
                "expires_in_seconds": expires,
                "is_frozen": getattr(attendance_session, "is_frozen_qr", False),
            },
            status=status.HTTP_200_OK,
        )


@login_required
def toggle_freeze_qr_view(request, session_id):
    """Allows teacher to toggle freeze/unfreeze QR mode for the session."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        return JsonResponse({"success": False, "error": "غير مصرح لك"}, status=403)

    attendance_session = get_object_or_404(AttendanceSession, id=session_id)
    if request.method == "POST":
        try:
            body = json.loads(request.body.decode("utf-8")) if request.body else {}
            if "is_frozen" in body:
                attendance_session.is_frozen_qr = bool(body["is_frozen"])
            else:
                attendance_session.is_frozen_qr = not attendance_session.is_frozen_qr
        except Exception:
            attendance_session.is_frozen_qr = not attendance_session.is_frozen_qr

        attendance_session.save(update_fields=["is_frozen_qr"])
        token = generate_qr_token(attendance_session)
        msg = "تم تثبيت رمز الـ QR بنجاح (رمز ثابت مستقر طوال المحاضرة)" if attendance_session.is_frozen_qr else "تم تفعيل التدوير الديناميكي كل 15 ثانية"
        return JsonResponse({
            "success": True,
            "is_frozen": attendance_session.is_frozen_qr,
            "token": token,
            "message": msg,
        })
    return JsonResponse({"is_frozen": attendance_session.is_frozen_qr})


@login_required
def printable_qr_sheet_view(request, session_id):
    """Renders a high-resolution, beautiful A4 poster of the lecture QR & OTP for printing before class."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        return HttpResponse("غير مصرح لك", status=403)

    attendance_session = get_object_or_404(AttendanceSession, id=session_id)
    if not attendance_session.is_frozen_qr:
        attendance_session.is_frozen_qr = True
        attendance_session.save(update_fields=["is_frozen_qr"])

    token = generate_qr_token(attendance_session)
    inst = None
    try:
        inst = attendance_session.session.course.department.institution
    except Exception:
        inst = Institution.objects.first()

    context = {
        "attendance_session": attendance_session,
        "token": token,
        "institution": inst,
        "print_date": timezone.now().strftime("%Y-%m-%d"),
    }
    return render(request, "attendance/printable_qr.html", context)


@login_required
def quick_offline_checkin_view(request):
    """Quick 1-click check-in for students with NO INTERNET."""
    if not request.user.is_teacher():
        return JsonResponse({"success": False, "error": "غير مصرح لك"}, status=403)

    if request.method != "POST":
        return JsonResponse({"success": False, "error": "طلب غير صالح"}, status=400)

    session_id = request.POST.get("session_id")
    student_query = request.POST.get("student_query", "").strip()
    profile_id = request.POST.get("student_profile_id")

    att_session = get_object_or_404(AttendanceSession, id=session_id)
    section_students = _get_students_for_session(att_session)

    target_student = None
    if profile_id and str(profile_id).isdigit():
        target_student = section_students.filter(id=int(profile_id)).first()
    elif student_query:
        target_student = section_students.filter(
            Q(student_id__iexact=student_query) |
            Q(user__username__iexact=student_query) |
            Q(user__first_name__icontains=student_query) |
            Q(user__last_name__icontains=student_query)
        ).first()

    if not target_student:
        return JsonResponse({"success": False, "error": "لم يتم العثور على الطالب في هذه الشعبة الدراسية"}, status=404)

    record, created = AttendanceRecord.objects.update_or_create(
        student=target_student,
        attendance_session=att_session,
        defaults={
            "status": AttendanceRecord.Statuses.PRESENT,
            "method": AttendanceRecord.Methods.OFFLINE_MANUAL,
            "modified_by": request.user,
            "notes": "تسجيل فوري مباشر (الطالب لا يملك إنترنت)",
        },
    )

    std_name = target_student.user.get_full_name() or target_student.user.username
    msg = f"تم تسجيل حضور الطالب ({std_name}) بنجاح كحاضر (بدون إنترنت) 📶"
    return JsonResponse({
        "success": True,
        "message": msg,
        "student_id": target_student.student_id,
        "student_name": std_name,
    })


@login_required
def get_hotspot_info_api(request):
    """
    Returns active Local Area Network (LAN/Wi-Fi/Hotspot) IP addresses and port
    for zero-internet offline classroom check-in.
    """
    import socket
    all_ips = []
    primary_ip = None

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(('10.255.255.255', 1))
        primary_ip = s.getsockname()[0]
        s.close()
        if primary_ip and not primary_ip.startswith("127."):
            all_ips.append(primary_ip)
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127.") and ip not in all_ips:
                all_ips.append(ip)
    except Exception:
        pass

    if not primary_ip and all_ips:
        primary_ip = all_ips[0]

    port = str(request.get_port() or "8001")
    if port == "80":
        port = "8001"
    host_display = f"{primary_ip}:{port}" if primary_ip else f"127.0.0.1:{port}"

    return JsonResponse({
        "success": True,
        "primary_ip": primary_ip or "127.0.0.1",
        "all_ips": all_ips,
        "port": port,
        "host_display": host_display,
        "checkin_url": f"http://{host_display}/attendance/checkin/",
        "instructions": [
            "1. قم بتشغيل نقطة الاتصال المحمولة (Mobile Hotspot) من هاتفك أو اللابتوب دون الحاجة لبيانات أو رصيد إنترنت.",
            "2. اطلب من طلاب القاعة الاتصال بشبكة الـ Hotspot الخاصة بك.",
            "3. سيقوم الطلاب بمسح رمز الـ QR أو فتح الرابط المباشر للتسجيل فورياً وبسرعة البرق عبر الشبكة الداخلية."
        ]
    })


@extend_schema(
    summary="تسجيل حضور الطالب عبر QR أو رمز OTP",
    description="يرسل الطالب رمز QR الممسوح أو رمز OTP السريع مع التحقق الجغرافي لمنع الحضور بالنيابة.",
    responses={200: AttendanceRecordSerializer, 400: OpenApiResponse(description="رمز منتهي أو خارج النطاق")},
)
class StudentCheckInView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "checkin"

    def post(self, request):
        # 1. Rate Limiting: prevent spamming
        user_key = request.user.id if request.user.is_authenticated else _get_ip(request)
        cache_key = f"checkin_lock_{user_key}"
        if cache.get(cache_key):
            return Response(
                {"error": "لقد سجّلت حضورك مؤخراً، يرجى الانتظار قليلاً قبل المحاولة مجدداً."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        token = request.data.get("token")
        otp = request.data.get("otp")
        session_id = request.data.get("session_id")
        checkin_method = AttendanceRecord.Methods.QR
        is_new_auto_student = False

        if not token and not otp:
            return Response(
                {"error": "يرجى مسح رمز الـ QR أو إدخال رمز التحضير السريع (OTP)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 2. Token / OTP Verification
        if token:
            attendance_session, error_msg = verify_qr_token(token, max_age=600)
            if not attendance_session:
                return Response({"error": error_msg}, status=status.HTTP_400_BAD_REQUEST)
        else:
            otp_clean = str(otp).strip() if otp else ""
            if not session_id:
                # Find active session matching this OTP automatically
                active_sessions = AttendanceSession.objects.filter(is_active=True, quick_otp=otp_clean)
                if active_sessions.count() == 1:
                    attendance_session = active_sessions.first()
                elif active_sessions.count() > 1:
                    return Response({"error": "توجد أكثر من جلسة نشطة بهذا الرمز. يرجى اختيار الجلسة التابعة لمادتك."}, status=status.HTTP_400_BAD_REQUEST)
                else:
                    return Response({"error": "رمز التحضير السريع (OTP) غير صحيح أو انتهت المحاضرة."}, status=status.HTTP_400_BAD_REQUEST)
            else:
                attendance_session = get_object_or_404(AttendanceSession, id=session_id, is_active=True)
                if not attendance_session.quick_otp or attendance_session.quick_otp != otp_clean:
                    return Response({"error": "رمز التحضير السريع (OTP) غير صحيح أو انتهى."}, status=status.HTTP_400_BAD_REQUEST)
            checkin_method = AttendanceRecord.Methods.OTP

        # 3. Verify session is still active and within allowed lecture time
        if not attendance_session.is_active or attendance_session.end_time < timezone.now():
            return Response(
                {"error": "انتهت جلسة التحضير لهذه المحاضرة وتم إغلاق تسجيل الحضور."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        class_section = attendance_session.session.class_section

        # 4. Student Identification: Logged-in Account OR Guest Student Name/ID Match / Auto-Register New Student
        student_profile = None
        if request.user.is_authenticated and hasattr(request.user, "student_profile"):
            student_profile = request.user.student_profile
            # Verify student is enrolled in this section
            if not student_profile.sections.filter(id=class_section.id).exists():
                student_profile.sections.add(class_section)
        else:
            student_query = request.data.get("student_name") or request.data.get("student_id") or request.data.get("identifier")
            if not student_query or not str(student_query).strip():
                return Response(
                    {
                        "error": "يرجى إدخال اسمك الكامل أو رقمك الجامعي لتثبيت حضورك في كشف الشعبة.",
                        "requires_name": True
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            matched_student, match_err = find_matching_student(student_query, class_section)
            if not matched_student:
                # ✅ Auto-register new guest student if not found in database!
                student_name_clean = str(student_query).strip()
                from apps.accounts.models import User
                new_username = f"std_auto_{uuid.uuid4().hex[:8]}"
                
                new_user = User.objects.create(
                    username=new_username,
                    first_name=student_name_clean,
                    role=User.Roles.STUDENT,
                )
                inst = getattr(class_section.department, "institution", None) or Institution.objects.first()
                new_std_id = f"NEW-{random.randint(10000, 99999)}"
                if student_name_clean.isdigit():
                    new_std_id = student_name_clean
                
                matched_student = StudentProfile.objects.create(
                    user=new_user,
                    student_id=new_std_id,
                    institution=inst,
                    study_shift=attendance_session.shift,
                )
                matched_student.sections.add(class_section)
                is_new_auto_student = True

            student_profile = matched_student

        # Shift / Study Type Verification (منع تضارب دوام الصباحي والمسائي)
        if student_profile and student_profile.study_shift and attendance_session.shift:
            if student_profile.study_shift != attendance_session.shift:
                shift_names = {"MORNING": "الصباحية", "EVENING": "المسائية"}
                std_shift = shift_names.get(student_profile.study_shift, student_profile.study_shift)
                sess_shift = shift_names.get(attendance_session.shift, attendance_session.shift)
                return Response(
                    {
                        "error": f"❌ لا يمكن تسجيل الحضور: أنت مسجل في الدراسة ({std_shift}) بينما هذه الجلسة مخصصة لطلاب الدراسة ({sess_shift}). يمنع تسجيل الحضور بين الدوامين منعاً للتضارب والتزوير."
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        # 5. Anti-Proxy: Duplicate Prevention (Factor 5)
        existing_present = AttendanceRecord.objects.filter(
            student=student_profile,
            attendance_session=attendance_session,
            status__in=[AttendanceRecord.Statuses.PRESENT, AttendanceRecord.Statuses.LATE]
        ).first()
        if existing_present:
            serializer = AttendanceRecordSerializer(existing_present)
            return Response(
                {
                    "message": "تم تسجيل حضورك مسبقاً في هذه الجلسة بنجاح، ولا يمكن تكرار التحضير!",
                    "record": serializer.data,
                    "already_recorded": True
                },
                status=status.HTTP_200_OK
            )

        institution = student_profile.institution
        ip = _get_ip(request)

        # 7. Geolocation / GPS Geofencing Verification (Factor 2)
        if attendance_session.requires_geofence:
            student_lat = request.data.get("latitude")
            student_lon = request.data.get("longitude")

            if not student_lat or not student_lon:
                return Response(
                    {
                        "error": "تتطلب هذه المحاضرة التحقق من موقعك الجغرافي (GPS). يرجى السماح بالوصول للموقع في متصفح هاتفك والمحاولة مجدداً."
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

            target_lat = attendance_session.latitude or (institution.latitude if institution else None)
            target_lon = attendance_session.longitude or (institution.longitude if institution else None)
            allowed_radius = attendance_session.radius_meters or (institution.radius_meters if institution else 50) or 50

            if target_lat and target_lon:
                distance = calculate_haversine_distance(student_lat, student_lon, target_lat, target_lon)
                if distance is not None and distance > allowed_radius:
                    return Response(
                        {
                            "error": f"فشل التحقق من الموقع الجغرافي: أنت خارج نطاق المحاضرة! المسافة المحسوبة ({int(distance)}م) تتجاوز الحد الأقصى المسموح ({int(allowed_radius)}م)."
                        },
                        status=status.HTTP_400_BAD_REQUEST
                    )

        # 8. WiFi / Subnet restriction (if configured)
        if attendance_session.requires_wifi:
            subnet = attendance_session.allowed_ip_subnet or (institution.allowed_ip_subnet if institution else None)
            if subnet and not is_ip_in_subnet(ip, subnet):
                return Response(
                    {
                        "error": (
                            f"يجب الاتصال بشبكة WiFi/Hotspot المحاضرة. "
                            f"عنوان جهازك الحالي ({ip}) غير مسموح به."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # 9. Lecture Time Windows: On-time vs Late (Factor 6)
        now = timezone.now()
        elapsed_minutes = (now - attendance_session.start_time).total_seconds() / 60
        record_status = (
            AttendanceRecord.Statuses.LATE
            if elapsed_minutes > 15
            else AttendanceRecord.Statuses.PRESENT
        )

        record_notes = "✨ طالب جديد (تم التسجيل الذاتي أثناء المحاضرة)" if is_new_auto_student else None

        record, created = AttendanceRecord.objects.update_or_create(
            student=student_profile,
            attendance_session=attendance_session,
            defaults={
                "status": record_status,
                "method": checkin_method,
                "ip_address": ip,
                "device_user_agent": request.META.get("HTTP_USER_AGENT"),
                "modified_by": request.user if request.user.is_authenticated else None,
                "notes": record_notes,
            },
        )

        # Lock this user from checking in again for 30 seconds
        cache.set(cache_key, True, timeout=30)
        queue_for_sync(record, SyncQueue.Actions.CREATE if created else SyncQueue.Actions.UPDATE)

        serializer = AttendanceRecordSerializer(record)
        if is_new_auto_student:
            msg = f"تم إضافتك كطالب جديد وتسجيل حضورك بنجاح ✔ ({student_profile.user.get_full_name()})"
        else:
            msg = (
                "تم تسجيل حضورك بنجاح وفي الوقت المحدد ✔"
                if record_status == AttendanceRecord.Statuses.PRESENT
                else "تم تسجيل حضورك بنجاح (مع احتساب تأخير) ⏰"
            )
        return Response({"message": msg, "record": serializer.data}, status=status.HTTP_200_OK)


@extend_schema(
    summary="تعديل يدوي لسجل الحضور",
    description="يُمكّن المعلم من تغيير حالة حضور طالب يدوياً مع إضافة ملاحظة.",
    responses={200: AttendanceRecordSerializer},
)
class ManualAttendanceRecordUpdateView(APIView):
    permission_classes = [IsTeacher]

    def post(self, request):
        record_id = request.data.get("record_id")
        new_status = request.data.get("status")
        notes = request.data.get("notes", "")

        if not record_id or not new_status:
            return Response(
                {"error": "معرف السجل والحالة الجديدة مطلوبان"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate status value
        valid_statuses = [s.value for s in AttendanceRecord.Statuses]
        if new_status not in valid_statuses:
            return Response(
                {"error": f"حالة غير صالحة. القيم المقبولة: {valid_statuses}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        record = get_object_or_404(AttendanceRecord, id=record_id)
        old_status = record.status

        record.status = new_status
        record.notes = notes
        record.modified_by = request.user
        record.method = AttendanceRecord.Methods.MANUAL
        record.save(update_fields=["status", "notes", "modified_by", "method", "updated_at"])

        AuditLog.objects.create(
            user=request.user,
            action="تعديل_يدوي_للحضور",
            ip_address=_get_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT"),
            details={
                "record_id": record.id,
                "student": f"{record.student.user.get_full_name()} ({record.student.student_id})",
                "course": record.attendance_session.session.course.name,
                "old_status": old_status,
                "new_status": new_status,
                "notes": notes,
            },
        )

        queue_for_sync(record, SyncQueue.Actions.UPDATE)
        serializer = AttendanceRecordSerializer(record)
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    summary="إغلاق جلسة تحضير",
    description="يُغلق المعلم جلسة التحضير النشطة يدوياً.",
    responses={200: OpenApiResponse(description="تم الإغلاق بنجاح")},
)
class CloseAttendanceSessionView(APIView):
    """Allows a teacher to manually close an active attendance session."""
    permission_classes = [IsTeacher]

    def post(self, request, session_id):
        attendance_session = get_object_or_404(
            AttendanceSession, id=session_id, is_active=True
        )
        attendance_session.is_active = False
        attendance_session.save(update_fields=["is_active"])

        AuditLog.objects.create(
            user=request.user,
            action="إغلاق_جلسة_تحضير",
            ip_address=_get_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT"),
            details={
                "attendance_session_id": attendance_session.id,
                "course": attendance_session.session.course.name,
            },
        )
        return Response({"message": "تم إغلاق جلسة التحضير بنجاح."}, status=status.HTTP_200_OK)


# =====================================================================
# 2. WEB TEMPLATE VIEWS
# =====================================================================

@login_required
def start_session_web_view(request):
    """Renders starting web view and processes starting from schedule dashboard."""
    if not request.user.is_teacher():
        messages.error(request, "غير مصرح لك بفتح جلسة حضور.")
        return redirect("dashboard")

    if request.method == "POST":
        session_id = request.POST.get("session_id")
        requires_wifi = "requires_wifi" in request.POST
        requires_geofence = "requires_geofence" in request.POST
        generated_otp = f"{random.randint(100000, 999999)}"

        session_obj = get_object_or_404(Session, id=session_id)
        now = timezone.now()
        end_time = now + timezone.timedelta(minutes=30)

        attendance_session, created = AttendanceSession.objects.get_or_create(
            session=session_obj,
            date=now.date(),
            defaults={
                "created_by": request.user,
                "end_time": end_time,
                "requires_wifi": requires_wifi,
                "requires_geofence": requires_geofence,
                "quick_otp": generated_otp,
                "is_active": True,
            },
        )

        if not created and not attendance_session.is_active:
            attendance_session.is_active = True
            attendance_session.end_time = end_time
            attendance_session.requires_geofence = requires_geofence
            attendance_session.requires_wifi = requires_wifi
            if not attendance_session.quick_otp:
                attendance_session.quick_otp = generated_otp
            attendance_session.qr_salt = uuid.uuid4()
            attendance_session.save(update_fields=["is_active", "end_time", "requires_geofence", "requires_wifi", "qr_salt", "quick_otp"])

        messages.success(request, f"تم تفعيل جلسة تحضير مادة {session_obj.course.name}")
        return redirect("attendance:session_detail_web", session_id=attendance_session.id)

    return redirect("dashboard")


@login_required
def session_detail_web_view(request, session_id):
    """Detailed monitoring screen for teachers. Shows dynamic QR and student list."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك بعرض شاشة التحضير.")
        return redirect("dashboard")

    attendance_session = AttendanceSession.objects.filter(id=session_id).first()
    if not attendance_session:
        # Check if session_id refers to a timetable Session schedule
        sess_obj = Session.objects.filter(id=session_id).first()
        if sess_obj:
            att = AttendanceSession.objects.filter(session=sess_obj).order_by("-id").first()
            if att:
                return redirect("attendance:session_detail_web", session_id=att.id)
            return redirect(f"/attendance/session/create-custom/?session_id={session_id}")
        messages.error(request, "لم يتم العثور على جلسة التحضير المطلوبة.")
        return redirect("attendance:teacher_sessions_web")

    # ✅ FIX: Get only students enrolled in this specific class section
    students_in_section = _get_students_for_session(attendance_session)

    records_map = {
        r.student_id: r
        for r in AttendanceRecord.objects.filter(attendance_session=attendance_session)
    }

    students_list = []
    for std in students_in_section:
        rec = records_map.get(std.id)
        students_list.append(
            {
                "profile": std,
                "record": rec,
                "status": rec.status if rec else "ABSENT",
                "status_display": rec.get_status_display() if rec else "غائب",
            }
        )

    present_cnt = sum(1 for s in students_list if s["status"] in ["PRESENT", "LATE"])
    absent_cnt = sum(1 for s in students_list if s["status"] == "ABSENT")
    late_cnt = sum(1 for s in students_list if s["status"] == "LATE")
    total_cnt = len(students_list)
    rate = round((present_cnt / total_cnt * 100), 1) if total_cnt > 0 else 0
    initial_qr_token = ""
    if attendance_session.is_active:
        if not attendance_session.qr_salt:
            attendance_session.qr_salt = uuid.uuid4()
            attendance_session.save(update_fields=["qr_salt"])
        initial_qr_token = generate_qr_token(attendance_session)

    import socket
    primary_hotspot_ip = None
    host_name = request.get_host().split(":")[0]
    is_cloud_prod = "pythonanywhere" in host_name or not settings.DEBUG or host_name not in ["127.0.0.1", "localhost"]

    if not is_cloud_prod:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.1)
            s.connect(('10.255.255.255', 1))
            primary_hotspot_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass

    if not primary_hotspot_ip or primary_hotspot_ip.startswith("127.") or primary_hotspot_ip.startswith("10.0.4."):
        primary_hotspot_ip = "127.0.0.1"

    server_port = str(request.get_port() or "8001")
    if server_port == "80":
        server_port = "8001"

    context = {
        "attendance_session": attendance_session,
        "students_list": students_list,
        "total_count": total_cnt,
        "present_count": present_cnt,
        "absent_count": absent_cnt,
        "late_count": late_cnt,
        "attendance_rate": rate,
        "initial_qr_token": initial_qr_token,
        "primary_hotspot_ip": primary_hotspot_ip,
        "server_port": server_port,
    }
    return render(request, "attendance/session_detail.html", context)


@login_required
def flexible_session_route_view(request, arg1=None, arg2=None, extra=None):
    """
    Handles compound or multi-argument URLs (e.g. /attendance/session/7/524/)
    without throwing a 404 error, redirecting intelligently to the proper session or schedule.
    """
    if arg1 and AttendanceSession.objects.filter(id=arg1).exists():
        return redirect("attendance:session_detail_web", session_id=arg1)

    if arg2 and AttendanceSession.objects.filter(id=arg2).exists():
        return redirect("attendance:session_detail_web", session_id=arg2)

    if arg1 and Session.objects.filter(id=arg1).exists():
        sess_obj = Session.objects.filter(id=arg1).first()
        att = AttendanceSession.objects.filter(session=sess_obj).order_by("-id").first()
        if att:
            return redirect("attendance:session_detail_web", session_id=att.id)
        return redirect(f"/attendance/session/create-custom/?session_id={arg1}")

    if arg1 and arg2:
        sess_obj = Session.objects.filter(course_id=arg1, class_section_id=arg2).first()
        if sess_obj:
            att = AttendanceSession.objects.filter(session=sess_obj).order_by("-id").first()
            if att:
                return redirect("attendance:session_detail_web", session_id=att.id)
            return redirect(f"/attendance/session/create-custom/?course_id={arg1}&section_id={arg2}")

    return redirect("attendance:teacher_sessions_web")


@login_required
def session_students_list_partial(request, session_id):
    """Partial HTML for HTMX live polling inside the session_detail view."""
    # ✅ FIX: Allow admins and institution admins in addition to teachers
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        return HttpResponse("غير مصرح لك", status=403)

    attendance_session = get_object_or_404(AttendanceSession, id=session_id)

    # ✅ FIX: Use helper to get section-specific students
    students_in_section = _get_students_for_session(attendance_session)

    records_map = {
        r.student_id: r
        for r in AttendanceRecord.objects.filter(attendance_session=attendance_session)
    }

    students_list = []
    for std in students_in_section:
        rec = records_map.get(std.id)
        students_list.append(
            {
                "profile": std,
                "record": rec,
                "status": rec.status if rec else "ABSENT",
                "status_display": rec.get_status_display() if rec else "غائب",
            }
        )

    present_cnt = sum(1 for s in students_list if s["status"] in ["PRESENT", "LATE"])
    absent_cnt = sum(1 for s in students_list if s["status"] == "ABSENT")
    late_cnt = sum(1 for s in students_list if s["status"] == "LATE")
    total_cnt = len(students_list)
    rate = round((present_cnt / total_cnt * 100), 1) if total_cnt > 0 else 0

    context = {
        "attendance_session": attendance_session,
        "students_list": students_list,
        "present_count": present_cnt,
        "absent_count": absent_cnt,
        "late_count": late_cnt,
        "total_count": total_cnt,
        "attendance_rate": rate,
    }
    return render(request, "attendance/partials/students_list.html", context)


@login_required
def manual_update_web_view(request):
    """Handles inline manual updates from the teacher detail screen via HTMX."""
    if not request.user.is_teacher():
        return HttpResponse("غير مصرح لك", status=403)

    if request.method != "POST":
        return HttpResponse("طلب غير صالح", status=400)

    student_id = request.POST.get("student_profile_id")
    session_id = request.POST.get("session_id")
    new_status = request.POST.get("status")

    # Validate status
    valid_statuses = [s.value for s in AttendanceRecord.Statuses]
    if new_status not in valid_statuses:
        return HttpResponse("حالة غير صالحة", status=400)

    student = get_object_or_404(StudentProfile, id=student_id)
    att_session = get_object_or_404(AttendanceSession, id=session_id)

    method_param = request.POST.get("method")
    if method_param == "OFFLINE_MANUAL":
        chosen_method = AttendanceRecord.Methods.OFFLINE_MANUAL
        note = "تسجيل فوري مباشر (الطالب لا يملك إنترنت)"
    else:
        chosen_method = AttendanceRecord.Methods.MANUAL
        note = "تحديث يدوي سريع من شاشة المراقبة"

    record, created = AttendanceRecord.objects.update_or_create(
        student=student,
        attendance_session=att_session,
        defaults={
            "status": new_status,
            "method": chosen_method,
            "modified_by": request.user,
            "notes": note,
        },
    )

    queue_for_sync(record, SyncQueue.Actions.CREATE if created else SyncQueue.Actions.UPDATE)

    AuditLog.objects.create(
        user=request.user,
        action="تحديث_سريع_للحضور",
        ip_address=_get_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT"),
        details={
            "student": student.user.username,
            "course": att_session.session.course.name,
            "status": new_status,
        },
    )

    return session_students_list_partial(request, session_id)


@login_required
def bulk_update_web_view(request):
    """Handles bulk attendance updates (e.g. mark all present) from the teacher detail screen via HTMX."""
    if not request.user.is_teacher():
        return HttpResponse("غير مصرح لك", status=403)

    if request.method != "POST":
        return HttpResponse("طلب غير صالح", status=400)

    session_id = request.POST.get("session_id")
    target_status = request.POST.get("status", "PRESENT")
    att_session = get_object_or_404(AttendanceSession, id=session_id)
    students_in_section = _get_students_for_session(att_session)

    valid_statuses = [s.value for s in AttendanceRecord.Statuses]
    if target_status not in valid_statuses:
        return HttpResponse("حالة غير صالحة", status=400)

    for student in students_in_section:
        record, created = AttendanceRecord.objects.update_or_create(
            student=student,
            attendance_session=att_session,
            defaults={
                "status": target_status,
                "method": AttendanceRecord.Methods.MANUAL,
                "modified_by": request.user,
                "notes": "تحديث جماعي يدوي سريع",
            },
        )
        queue_for_sync(record, SyncQueue.Actions.CREATE if created else SyncQueue.Actions.UPDATE)

    return session_students_list_partial(request, session_id)


def student_checkin_web_view(request):
    """Renders the student-facing PWA camera scanning and guest check-in screen."""
    return render(
        request,
        "attendance/checkin.html",
        {
            "is_logged_in": request.user.is_authenticated,
            "user_student_name": request.user.get_full_name()
            if (request.user.is_authenticated and request.user.is_student())
            else "",
        },
    )


def qr_image_view(request):
    """
    Generates and returns an offline-compatible
    QR code image on-the-fly as a PNG file. Supports custom box size for projector/HD displays.
    """
    token = request.GET.get("token", "")
    try:
        box_size = int(request.GET.get("size", 10))
        box_size = max(6, min(box_size, 24))  # Bounds between 6 and 24
    except (ValueError, TypeError):
        box_size = 10

    # Build absolute URL for student check-in (supports Hotspot LAN host)
    custom_host = request.GET.get("host", "").strip()
    current_host = request.get_host().split(":")[0]
    is_cloud_env = "pythonanywhere" in current_host or current_host not in ["127.0.0.1", "localhost"]

    if custom_host and not is_cloud_env and not custom_host.startswith("10.0.4."):
        checkin_url = f"http://{custom_host}/attendance/checkin/"
    else:
        checkin_url = request.build_absolute_uri("/attendance/checkin/")
    if token:
        checkin_url += f"?token={token}"

    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=box_size,
            border=2,
        )
        qr.add_data(checkin_url)
        qr.make(fit=True)

        img = qr.make_image(fill_color="#0f172a", back_color="#ffffff")
        stream = BytesIO()
        img.save(stream, format="PNG")

        response = HttpResponse(stream.getvalue(), content_type="image/png")
        response["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error generating QR image: {e}")
        # Safe fallback: create a simple QR code
        qr = qrcode.make(checkin_url)
        stream = BytesIO()
        qr.save(stream, format="PNG")
        response = HttpResponse(stream.getvalue(), content_type="image/png")
        response["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response


@login_required
def student_records_web_view(request):
    """Lists the complete attendance history for the logged-in student, or multi-filtered records for admin."""
    if request.user.is_super_admin() or request.user.is_institution_admin() or request.user.is_staff or request.user.is_superuser:
        from apps.academics.models import Institution, ClassSection, Course
        from django.db.models import Q
        from django.utils import timezone
        from datetime import timedelta, datetime

        # Base QuerySet
        qs = AttendanceRecord.objects.all().select_related(
            "student__user",
            "student__institution",
            "attendance_session__session__course__department__institution",
            "attendance_session__session__class_section__department__institution",
            "attendance_session__session__teacher__user",
        )

        # 1. Institution filter
        inst_id = request.GET.get("institution_id", "").strip()
        if inst_id and inst_id.isdigit():
            qs = qs.filter(
                Q(attendance_session__session__course__department__institution_id=int(inst_id)) |
                Q(attendance_session__session__class_section__department__institution_id=int(inst_id)) |
                Q(student__institution_id=int(inst_id))
            )

        # 2. Section filter
        section_id = request.GET.get("section_id", "").strip()
        if section_id and section_id.isdigit():
            qs = qs.filter(attendance_session__session__class_section_id=int(section_id))

        # 3. Course filter
        course_id = request.GET.get("course_id", "").strip()
        if course_id and course_id.isdigit():
            qs = qs.filter(attendance_session__session__course_id=int(course_id))

        # 4. Level / Stage filter
        level = request.GET.get("level", "").strip()
        if level:
            qs = qs.filter(attendance_session__session__class_section__level=level)

        # 5. Shift filter (Morning / Evening)
        shift = request.GET.get("shift", "").strip().upper()
        if shift in ["MORNING", "EVENING"]:
            qs = qs.filter(
                Q(attendance_session__session__class_section__shift=shift) |
                Q(student__study_shift=shift)
            )

        # 6. Status filter (PRESENT, LATE, ABSENT, EXCUSED)
        status_filter = request.GET.get("status", "").strip().upper()
        if status_filter and status_filter != "ALL":
            qs = qs.filter(status=status_filter)

        # 7. Method filter (QR, OFFLINE_MANUAL, RFID, MANUAL, etc.)
        method_filter = request.GET.get("method", "").strip().upper()
        if method_filter and method_filter != "ALL":
            qs = qs.filter(method=method_filter)

        # 8. Search Query (Student Name or Student ID)
        q = request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(student__user__first_name__icontains=q) |
                Q(student__user__last_name__icontains=q) |
                Q(student__user__username__icontains=q) |
                Q(student__student_id__icontains=q)
            )

        # 9. Date & Period Filters
        date_param = request.GET.get("date", "").strip()
        period = request.GET.get("period", "").strip()
        today = timezone.localdate()

        if date_param:
            try:
                parsed_date = datetime.strptime(date_param, "%Y-%m-%d").date()
                qs = qs.filter(attendance_session__date=parsed_date)
            except ValueError:
                pass
        elif period == "today":
            qs = qs.filter(attendance_session__date=today)
        elif period == "yesterday":
            qs = qs.filter(attendance_session__date=today - timedelta(days=1))
        elif period == "week":
            qs = qs.filter(attendance_session__date__gte=today - timedelta(days=7))
        elif period == "month":
            qs = qs.filter(attendance_session__date__gte=today - timedelta(days=30))

        # Order by latest
        ordered_qs = qs.order_by("-attendance_session__date", "-timestamp")
        
        # Calculate stats
        total_records = qs.count()
        present_count = qs.filter(status="PRESENT").count()
        late_count = qs.filter(status="LATE").count()
        absent_count = qs.filter(status="ABSENT").count()
        excused_count = qs.filter(status="EXCUSED").count()
        offline_count = qs.filter(method="OFFLINE_MANUAL").count()
        qr_count = qs.filter(method__in=["QR", "STATIC_QR"]).count()
        
        present_pct = round(((present_count + late_count) / total_records * 100), 1) if total_records > 0 else 0

        # Limit to 200 records for fast browser rendering
        records = ordered_qs[:200]

        # Options for dropdowns
        institutions = Institution.objects.all().order_by("name")
        sections = ClassSection.objects.all().select_related("department__institution").order_by("name")
        courses = Course.objects.all().select_related("department__institution").order_by("name")
        levels = ["المرحلة الأولى", "المرحلة الثانية", "المرحلة الثالثة", "المرحلة الرابعة", "الدراسات العليا"]

        context = {
            "records": records,
            "student": None,
            "is_admin": True,
            # Filter values
            "inst_filter": inst_id,
            "sec_filter": section_id,
            "course_filter": course_id,
            "level_filter": level,
            "shift_filter": shift,
            "status_filter": status_filter,
            "method_filter": method_filter,
            "search_query": q,
            "date_param": date_param,
            "period": period or ("" if date_param else "all"),
            # Stats
            "total_records": total_records,
            "present_count": present_count,
            "late_count": late_count,
            "absent_count": absent_count,
            "excused_count": excused_count,
            "offline_count": offline_count,
            "qr_count": qr_count,
            "present_pct": present_pct,
            # Dropdown data
            "institutions": institutions,
            "sections": sections,
            "courses": courses,
            "levels": levels,
        }
        return render(request, "attendance/student_records.html", context)

    if not request.user.is_student():
        messages.error(request, "هذه الصفحة مخصصة للطلاب فقط.")
        return redirect("dashboard")

    try:
        student_profile = request.user.student_profile
    except StudentProfile.RelatedObjectDoesNotExist:
        messages.error(request, "لا يوجد ملف طالب مرتبط بحسابك.")
        return redirect("dashboard")

    records = AttendanceRecord.objects.filter(student=student_profile).select_related(
        "attendance_session__session__course",
        "attendance_session__session__class_section",
    ).order_by("-timestamp")

    return render(
        request,
        "attendance/student_records.html",
        {"records": records, "student": student_profile},
    )


@login_required
def teacher_sessions_web_view(request):
    """Lists the schedules/timetable sessions for the logged-in teacher or all sessions for admins."""
    if request.user.is_super_admin() or request.user.is_institution_admin():
        sessions = Session.objects.all().select_related(
            "course", "class_section", "teacher__user"
        ).order_by("day_of_week", "start_time")
        return render(
            request,
            "attendance/teacher_sessions.html",
            {"sessions": sessions, "teacher": None, "is_admin": True},
        )

    if not request.user.is_teacher():
        messages.error(request, "هذه الصفحة مخصصة للمعلمين فقط.")
        return redirect("dashboard")

    try:
        teacher_profile = request.user.teacher_profile
    except TeacherProfile.RelatedObjectDoesNotExist:
        messages.error(request, "لا يوجد ملف معلم مرتبط بحسابك.")
        return redirect("dashboard")

    sessions = Session.objects.filter(teacher=teacher_profile).select_related(
        "course", "class_section"
    ).order_by("day_of_week", "start_time")

    return render(
        request,
        "attendance/teacher_sessions.html",
        {"sessions": sessions, "teacher": teacher_profile},
    )


@login_required
def create_custom_session_view(request, timetable_id=None):
    """Allows a teacher or admin to start an ad-hoc/custom attendance session."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "هذه الصفحة مخصصة للمعلمين والإداريين فقط.")
        return redirect("dashboard")

    # If this ID corresponds directly to an active/existing AttendanceSession, redirect to its live monitoring
    if timetable_id:
        att_session = AttendanceSession.objects.filter(id=timetable_id).first()
        if att_session:
            return redirect("attendance:session_detail_web", session_id=att_session.id)

    teacher_profile = getattr(request.user, "teacher_profile", None)
    if teacher_profile is None:
        teacher_profile = TeacherProfile.objects.first()

    if teacher_profile is None:
        messages.error(
            request,
            "لا يوجد ملف معلم مسجل في المؤسسة حالياً لإنشاء الجلسة باسمه.",
        )
        return redirect("dashboard")

    institution = teacher_profile.institution if teacher_profile else Institution.objects.first()

    if request.method == "POST":
        course_id = request.POST.get("course_id", "").strip()
        new_course_name = request.POST.get("new_course_name", "").strip()
        class_section_id = request.POST.get("class_section_id", "").strip()
        new_section_name = request.POST.get("new_section_name", "").strip()
        duration_minutes = int(request.POST.get("duration_minutes", 60))
        room = request.POST.get("room", "قاعة مخصصة").strip()
        topic = request.POST.get("topic", "").strip()
        lecture_type = request.POST.get("lecture_type", "نظري").strip()

        # Security & Verification
        requires_wifi = "requires_wifi" in request.POST
        allowed_wifi_ssid = request.POST.get("allowed_wifi_ssid", "").strip() or None
        allowed_ip_subnet = request.POST.get("allowed_ip_subnet", "").strip() or None
        requires_geofence = "requires_geofence" in request.POST
        latitude = request.POST.get("latitude", "").strip() or None
        longitude = request.POST.get("longitude", "").strip() or None
        radius_meters = int(request.POST.get("radius_meters", 50))

        # Date & Day of Week
        now = timezone.now()
        session_date_str = request.POST.get("session_date", "").strip()
        if session_date_str:
            try:
                session_date = datetime.strptime(session_date_str, "%Y-%m-%d").date()
            except ValueError:
                session_date = now.date()
        else:
            session_date = now.date()

        day_of_week_str = request.POST.get("day_of_week", "").strip()
        if day_of_week_str != "" and day_of_week_str.isdigit():
            day_of_week = int(day_of_week_str)
        else:
            # Map Python weekday (0=Mon, 6=Sun) → WeekDays enum (Sat=0 … Fri=6)
            python_to_enum = {5: 0, 6: 1, 0: 2, 1: 3, 2: 4, 3: 5, 4: 6}
            day_of_week = python_to_enum.get(session_date.weekday(), 2)

        # Start Time
        start_time_str = request.POST.get("start_time", "").strip()
        if start_time_str:
            try:
                parsed_time = datetime.strptime(start_time_str, "%H:%M").time()
            except ValueError:
                parsed_time = now.time()
        else:
            parsed_time = now.time()

        # Combine session_date and parsed_time into timezone-aware datetime
        naive_dt = datetime.combine(session_date, parsed_time)
        try:
            start_dt = timezone.make_aware(naive_dt)
        except Exception:
            start_dt = timezone.now()

        end_dt = start_dt + timezone.timedelta(minutes=duration_minutes)

        # Resolve or create department
        department = institution.departments.first() if institution else Department.objects.first()
        if not department and institution:
            department = Department.objects.create(institution=institution, name="قسم علوم الحاسوب", code="CS")

        # Resolve or create Course
        course = None
        if course_id and course_id.isdigit():
            course = Course.objects.filter(id=int(course_id)).first()

        if not course and new_course_name:
            rand_code = f"CS{random.randint(100, 999)}"
            while Course.objects.filter(code=rand_code).exists():
                rand_code = f"CS{random.randint(100, 999)}"
            course, _ = Course.objects.get_or_create(
                name=new_course_name,
                department=department,
                defaults={"code": rand_code}
            )

        if not course:
            course = Course.objects.filter(department__institution=institution).first()

        if not course:
            messages.error(request, "يرجى تحديد أو إدخال اسم مادة صالحة.")
            return redirect("attendance:create_custom_session")

        # Resolve or create ClassSection
        class_section = None
        if class_section_id and class_section_id.isdigit():
            class_section = ClassSection.objects.filter(id=int(class_section_id)).first()

        if not class_section and new_section_name:
            class_section, _ = ClassSection.objects.get_or_create(
                name=new_section_name,
                department=department,
                defaults={"level": "المستوى العام"}
            )

        if not class_section:
            class_section = ClassSection.objects.filter(department__institution=institution).first()
            if not class_section and department:
                class_section = ClassSection.objects.create(
                    department=department, name="الشعبة العامة أ", level="المرحلة الجامعية"
                )

        shift = request.POST.get("shift", "").strip().upper()
        if shift not in ["MORNING", "EVENING"]:
            shift = class_section.shift if class_section and class_section.shift else "MORNING"

        # Check Academic & Schedule Conflicts (الأستاذ، الطلاب، القاعة، الدوام)
        from apps.academics.conflicts import check_session_conflict

        conflict = check_session_conflict(
            teacher=teacher_profile,
            course=course,
            class_section=class_section,
            day_of_week=day_of_week,
            start_time=parsed_time,
            end_time=end_dt.time(),
            room=room,
            shift=shift,
            session_date=session_date,
        )

        if conflict["has_conflict"] and conflict["severity"] == "error":
            messages.error(request, conflict["message"])
            return redirect("attendance:create_custom_session")

        if conflict["has_conflict"] and conflict["severity"] == "warning":
            messages.warning(request, conflict["message"])

        session_obj, _ = Session.objects.get_or_create(
            course=course,
            class_section=class_section,
            teacher=teacher_profile,
            day_of_week=day_of_week,
            defaults={
                "start_time": parsed_time,
                "end_time": end_dt.time(),
                "room": room,
                "shift": shift,
            },
        )
        if room and session_obj.room != room:
            session_obj.room = room
        if shift and session_obj.shift != shift:
            session_obj.shift = shift
        session_obj.save()

        attendance_session, att_created = AttendanceSession.objects.get_or_create(
            session=session_obj,
            date=session_date,
            defaults={
                "created_by": request.user,
                "start_time": start_dt,
                "end_time": end_dt,
                "requires_wifi": requires_wifi,
                "allowed_wifi_ssid": allowed_wifi_ssid,
                "allowed_ip_subnet": allowed_ip_subnet,
                "requires_geofence": requires_geofence,
                "latitude": latitude,
                "longitude": longitude,
                "radius_meters": radius_meters,
                "topic": topic,
                "lecture_type": lecture_type,
                "shift": shift,
                "is_active": True,
            },
        )

        if not att_created:
            attendance_session.is_active = True
            attendance_session.end_time = end_dt
            attendance_session.requires_wifi = requires_wifi
            attendance_session.allowed_wifi_ssid = allowed_wifi_ssid
            attendance_session.allowed_ip_subnet = allowed_ip_subnet
            attendance_session.requires_geofence = requires_geofence
            if latitude and longitude:
                attendance_session.latitude = latitude
                attendance_session.longitude = longitude
            attendance_session.radius_meters = radius_meters
            if topic:
                attendance_session.topic = topic
            if lecture_type:
                attendance_session.lecture_type = lecture_type
            attendance_session.shift = shift
            attendance_session.qr_salt = uuid.uuid4()
            attendance_session.save()

        shift_display = "صباحي" if shift == "MORNING" else "مسائي"
        AuditLog.objects.create(
            user=request.user,
            action="بدء_تحضير_مخصص",
            ip_address=_get_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT"),
            details={
                "session_id": session_obj.id,
                "course": course.name,
                "section": class_section.name,
                "shift": shift_display,
                "topic": topic,
                "lecture_type": lecture_type,
                "duration_minutes": duration_minutes,
                "requires_wifi": requires_wifi,
                "requires_geofence": requires_geofence,
            },
        )

        messages.success(
            request,
            f"تم بدء جلسة التحضير الذكية لمادة {course.name} ({lecture_type} - {shift_display}) بنجاح!",
        )
        return redirect("attendance:session_detail_web", session_id=attendance_session.id)

    # GET: populate the form with defaults
    courses = Course.objects.filter(department__institution=institution).order_by("name")
    sections = ClassSection.objects.filter(
        department__institution=institution
    ).order_by("shift", "level", "name")

    # Determine default day of week and current dates
    now = timezone.now()
    python_to_enum = {5: 0, 6: 1, 0: 2, 1: 3, 2: 4, 3: 5, 4: 6}
    default_day_enum = python_to_enum.get(now.weekday(), 2)

    return render(
        request,
        "attendance/create_custom_session.html",
        {
            "courses": courses,
            "sections": sections,
            "teacher": teacher_profile,
            "current_date": now.strftime("%Y-%m-%d"),
            "current_time": now.strftime("%H:%M"),
            "default_day_enum": default_day_enum,
            "institution": institution,
        },
    )


@login_required
def quick_create_course_view(request):
    """API endpoint to instantly add a new course directly from the custom combobox."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        return JsonResponse({"success": False, "error": "غير مصرح لك"}, status=403)

    if request.method != "POST":
        return JsonResponse({"success": False, "error": "طلب غير صالح"}, status=400)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        data = request.POST

    name = data.get("name", "").strip()
    code = data.get("code", "").strip()

    if not name:
        return JsonResponse({"success": False, "error": "يرجى كتابة اسم المادة"}, status=400)

    teacher_profile = getattr(request.user, "teacher_profile", None) or TeacherProfile.objects.first()
    institution = teacher_profile.institution if teacher_profile else Institution.objects.first()
    department = institution.departments.first() if institution else Department.objects.first()

    if not department and institution:
        department = Department.objects.create(institution=institution, name="قسم علوم الحاسوب", code="CS")

    if not code:
        rand_code = f"CS{random.randint(100, 999)}"
        while Course.objects.filter(code=rand_code).exists():
            rand_code = f"CS{random.randint(100, 999)}"
        code = rand_code

    course, created = Course.objects.get_or_create(
        name=name,
        department=department,
        defaults={"code": code}
    )

    return JsonResponse({
        "success": True,
        "id": course.id,
        "name": course.name,
        "code": course.code,
        "display": f"{course.name} ({course.code})",
        "created": created,
    })


@login_required
def check_session_conflict_api(request):
    """
    Live API endpoint to evaluate schedule & shift conflicts before submitting the lecture form.
    Checks:
    1. Teacher overlap
    2. Section / Students overlap
    3. Room overlap
    4. Morning vs Evening shift compatibility
    """
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
        except Exception:
            data = request.POST
    else:
        data = request.GET

    teacher_profile = getattr(request.user, "teacher_profile", None) or TeacherProfile.objects.first()
    course_id = data.get("course_id")
    class_section_id = data.get("class_section_id")
    day_of_week = data.get("day_of_week")
    start_time_str = data.get("start_time", "10:00")
    duration_minutes = int(data.get("duration_minutes", 60) or 60)
    shift = str(data.get("shift", "MORNING")).upper()
    room = data.get("room", "")
    session_date_str = data.get("session_date")

    course = Course.objects.filter(id=int(course_id)).first() if course_id and str(course_id).isdigit() else None
    class_section = ClassSection.objects.filter(id=int(class_section_id)).first() if class_section_id and str(class_section_id).isdigit() else None

    session_date = None
    if session_date_str:
        try:
            session_date = datetime.strptime(session_date_str, "%Y-%m-%d").date()
        except ValueError:
            session_date = timezone.now().date()
    else:
        session_date = timezone.now().date()

    parsed_start = None
    parsed_end = None
    if start_time_str:
        try:
            parsed_start = datetime.strptime(start_time_str, "%H:%M").time()
            start_dt = datetime.combine(session_date, parsed_start)
            end_dt = start_dt + timezone.timedelta(minutes=duration_minutes)
            parsed_end = end_dt.time()
        except Exception:
            pass

    # Day of week
    if day_of_week is not None and str(day_of_week).isdigit():
        dow = int(day_of_week)
    else:
        python_to_enum = {5: 0, 6: 1, 0: 2, 1: 3, 2: 4, 3: 5, 4: 6}
        dow = python_to_enum.get(session_date.weekday(), 2)

    from apps.academics.conflicts import check_session_conflict

    result = check_session_conflict(
        teacher=teacher_profile,
        course=course,
        class_section=class_section,
        day_of_week=dow,
        start_time=parsed_start,
        end_time=parsed_end,
        room=room,
        shift=shift,
        session_date=session_date,
    )
    return JsonResponse(result)


# =====================================================================
# 📶 وضع الطوارئ — تسجيل الحضور بدون إنترنت (Offline Emergency Mode)
# =====================================================================

@login_required
def offline_emergency_mode_view(request, session_id):
    """
    صفحة وضع الطوارئ الكاملة للمعلم — تسجيل حضور ورقي رقمي بدون أي إنترنت خارجي.
    تعمل عبر الشبكة المحلية (Hotspot) أو محلياً على جهاز المعلم.
    """
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك بالوصول لهذه الصفحة.")
        return redirect("dashboard")

    attendance_session = get_object_or_404(AttendanceSession, id=session_id)
    students_in_section = _get_students_for_session(attendance_session)

    # Handle quick AJAX/form check-in from the page itself
    if request.method == "POST":
        action = request.POST.get("action", "")
        student_profile_id = request.POST.get("student_profile_id", "")
        new_status = request.POST.get("status", "PRESENT").upper()
        if new_status not in ["PRESENT", "ABSENT", "LATE", "EXCUSED"]:
            new_status = "PRESENT"

        if action == "bulk_present":
            # تحضير الكل دفعة واحدة
            count = 0
            for std in students_in_section:
                AttendanceRecord.objects.update_or_create(
                    student=std,
                    attendance_session=attendance_session,
                    defaults={
                        "status": AttendanceRecord.Statuses.PRESENT,
                        "method": AttendanceRecord.Methods.OFFLINE_MANUAL,
                        "modified_by": request.user,
                        "notes": "تسجيل جماعي — وضع الطوارئ بدون إنترنت",
                    },
                )
                count += 1
            return JsonResponse({"success": True, "message": f"تم تحضير {count} طالب بنجاح ⚡", "count": count})

        elif action == "update_student" and student_profile_id:
            student = get_object_or_404(StudentProfile, id=student_profile_id)
            record, created = AttendanceRecord.objects.update_or_create(
                student=student,
                attendance_session=attendance_session,
                defaults={
                    "status": new_status,
                    "method": AttendanceRecord.Methods.OFFLINE_MANUAL,
                    "modified_by": request.user,
                    "notes": "تسجيل يدوي — وضع الطوارئ",
                },
            )
            status_labels = {
                "PRESENT": "حاضر ✓",
                "ABSENT": "غائب ✕",
                "LATE": "متأخر ⏳",
                "EXCUSED": "غائب بعذر 📝",
            }
            return JsonResponse({
                "success": True,
                "status": new_status,
                "label": status_labels.get(new_status, new_status),
                "student_id": student.id,
            })

        elif action == "search_student":
            query = request.POST.get("query", "").strip()
            matched = students_in_section.filter(
                Q(user__first_name__icontains=query) |
                Q(user__last_name__icontains=query) |
                Q(student_id__icontains=query) |
                Q(user__username__icontains=query)
            )
            records_map = {
                r.student_id: r
                for r in AttendanceRecord.objects.filter(attendance_session=attendance_session)
            }
            results = []
            for std in matched[:10]:
                rec = records_map.get(std.id)
                results.append({
                    "id": std.id,
                    "name": std.user.get_full_name() or std.user.username,
                    "student_id": std.student_id,
                    "status": rec.status if rec else "ABSENT",
                    "status_label": rec.get_status_display() if rec else "غائب",
                })
            return JsonResponse({"success": True, "results": results})

        return JsonResponse({"success": False, "error": "طلب غير معروف"}, status=400)

    # GET — بناء قائمة الطلاب مع سجلاتهم
    records_map = {
        r.student_id: r
        for r in AttendanceRecord.objects.filter(attendance_session=attendance_session)
    }
    students_list = []
    for std in students_in_section:
        rec = records_map.get(std.id)
        students_list.append({
            "profile": std,
            "record": rec,
            "status": rec.status if rec else "ABSENT",
            "status_display": rec.get_status_display() if rec else "غائب",
        })

    present_cnt = sum(1 for s in students_list if s["status"] in ["PRESENT", "LATE"])
    absent_cnt = sum(1 for s in students_list if s["status"] == "ABSENT")
    late_cnt = sum(1 for s in students_list if s["status"] == "LATE")
    total_cnt = len(students_list)
    rate = round((present_cnt / total_cnt * 100), 1) if total_cnt > 0 else 0

    # Detect local IPs for hotspot
    import socket
    local_ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(('10.255.255.255', 1))
        primary_ip = s.getsockname()[0]
        s.close()
        if primary_ip and not primary_ip.startswith("127."):
            local_ips.append(primary_ip)
    except Exception:
        pass
    server_port = str(request.get_port() or "8001")

    context = {
        "attendance_session": attendance_session,
        "students_list": students_list,
        "present_count": present_cnt,
        "absent_count": absent_cnt,
        "late_count": late_cnt,
        "total_count": total_cnt,
        "attendance_rate": rate,
        "local_ips": local_ips,
        "primary_ip": local_ips[0] if local_ips else "127.0.0.1",
        "server_port": server_port,
    }
    return render(request, "attendance/offline_emergency.html", context)

