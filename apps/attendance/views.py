import uuid
import qrcode
import secrets
from io import BytesIO
from datetime import time, date, datetime

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.db import transaction
from django.db.models import Q

import json
import random
from urllib.parse import parse_qs, unquote, urlparse
from django.utils.translation import gettext as _
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import SimpleRateThrottle
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer
from rest_framework import serializers as drf_serializers

from .models import (
    AttendanceSession,
    AttendanceRecord,
    OfflineAttendanceSubmission,
    SyncQueue,
)
from .serializers import AttendanceSessionSerializer, AttendanceRecordSerializer
from .utils import (
    DEFAULT_QR_TOKEN_TTL,
    MAX_DYNAMIC_QR_TOKEN_TTL,
    FROZEN_QR_TOKEN_TTL,
    generate_qr_token,
    verify_qr_token,
    is_ip_in_subnet,
    calculate_haversine_distance,
    normalize_arabic_text,
    find_matching_student,
)
from apps.accounts.permissions import IsTeacher, IsStudent
from apps.accounts.models import StudentProfile, TeacherProfile, User
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


def _ensure_attendance_otp(attendance_session):
    if not attendance_session.quick_otp or not attendance_session.quick_otp.isdigit():
        attendance_session.quick_otp = str(secrets.randbelow(900000) + 100000)
        attendance_session.save(update_fields=["quick_otp"])
    return attendance_session.quick_otp


def _unverified_qr_session_id(token):
    """Extracts a session candidate for human review only; never authenticates the QR."""
    raw_token = str(token or "").strip()
    if "token=" in raw_token:
        raw_token = parse_qs(urlparse(raw_token).query).get("token", [raw_token])[0]
    raw_token = unquote(raw_token)
    candidate = raw_token.split(":", 1)[0]
    return int(candidate) if candidate.isdigit() else None


def _store_unverified_offline_submission(request, attendance_session, reason):
    if request.data.get("offline_checkin") is not True or not attendance_session:
        return None

    student_name = str(request.data.get("student_name") or "").strip()
    if not student_name or len(student_name) > 150:
        return None

    raw_queue_id = str(request.data.get("queue_id") or "").strip()
    if not raw_queue_id or len(raw_queue_id) > 64 or not raw_queue_id.isascii():
        queue_id = f"q_{uuid.uuid4().hex[:16]}"
    else:
        cleaned_id = "".join(c for c in raw_queue_id if c.isalnum() or c in "_-")[:64]
        queue_id = cleaned_id or f"q_{uuid.uuid4().hex[:16]}"

    submission, created = OfflineAttendanceSubmission.objects.get_or_create(
        queue_id=queue_id,
        defaults={
            "attendance_session": attendance_session,
            "student_name": student_name,
            "device_id": str(request.data.get("device_id") or "")[:128],
            "token_error": str(reason or _("رمز الحضور بانتظار مراجعة الأستاذ")),
        },
    )
    if (
        submission.attendance_session_id != attendance_session.id
        or submission.student_name != student_name
    ):
        return Response(
            {"error": _("معرف طلب المزامنة مستخدم لطلب مختلف.")},
            status=status.HTTP_409_CONFLICT,
        )
    return Response(
        {
            "stored_for_review": True,
            "review_status": submission.status,
            "message": _(
                "وصل الطلب إلى قائمة مراجعة الأستاذ، لكنه لم يُعتمد حضوراً "
                "لأن رمز الحضور لم يمكن التحقق منه."
            ),
        },
        status=status.HTTP_200_OK,
    )


class StudentCheckInRateThrottle(SimpleRateThrottle):
    scope = "checkin"

    def get_cache_key(self, request, view):
        if request.user.is_authenticated:
            identity = f"user_{request.user.pk}"
        else:
            identity = f"ip_{self.get_ident(request)}"
        return self.cache_format % {"scope": self.scope, "ident": identity}


def _get_students_for_session(attendance_session):
    """
    ✅ Returns only students who have actually recorded attendance in this session.
    Enrolled students who have not yet checked in will NOT appear in the session list.
    """
    recorded_pks = list(
        AttendanceRecord.objects.filter(attendance_session=attendance_session).values_list("student_id", flat=True)
    )
    return StudentProfile.objects.filter(pk__in=recorded_pks).select_related("user").order_by("-id")



def _get_shared_device_map(attendance_session):
    """
    Identifies students who checked in from the same physical device or browser in this session.
    Groups by extracted device UUID or (IP + User-Agent).
    Returns dict: student_id -> list of other student names sharing this device.
    """
    records = AttendanceRecord.objects.filter(
        attendance_session=attendance_session
    ).select_related("student__user")

    device_groups = {}
    for r in records:
        if not r.student or not r.student.user:
            continue
        student_name = r.student.user.get_full_name() or r.student.user.username
        ua = r.device_user_agent or ""
        dev_key = None

        if "[DEV:" in ua:
            try:
                dev_key = "dev_" + ua.split("[DEV:")[1].split("]")[0].strip()
            except Exception:
                dev_key = None

        if not dev_key and r.ip_address and r.ip_address not in ["127.0.0.1", "localhost", "::1"]:
            clean_ua = ua[:120].strip()
            dev_key = f"ip_{r.ip_address}_{clean_ua}"

        if dev_key:
            device_groups.setdefault(dev_key, []).append((r.student_id, student_name))

    shared_map = {}
    for dev_key, group in device_groups.items():
        if len(group) > 1:
            for std_id, std_name in group:
                others = [name for s_id, name in group if s_id != std_id]
                if others:
                    shared_map[std_id] = others

    return shared_map


def _user_institution_id(user):
    for profile_name in ("teacher_profile", "student_profile"):
        profile = getattr(user, profile_name, None)
        if profile and profile.institution_id:
            return profile.institution_id
    return None


def _manageable_timetable_sessions(user):
    queryset = Session.objects.all()
    if user.is_super_admin():
        return queryset
    if user.is_teacher():
        return queryset.filter(teacher__user_id=user.pk)
    if user.is_institution_admin():
        institution_id = _user_institution_id(user)
        if institution_id:
            return queryset.filter(course__department__institution_id=institution_id)
    return queryset.none()


def _manageable_attendance_sessions(user):
    queryset = AttendanceSession.objects.all()
    if user.is_super_admin():
        return queryset
    if user.is_teacher():
        return queryset.filter(session__teacher__user_id=user.pk)
    if user.is_institution_admin():
        institution_id = _user_institution_id(user)
        if institution_id:
            return queryset.filter(
                session__course__department__institution_id=institution_id
            )
    return queryset.none()


def _manageable_attendance_session(user, session_id):
    return get_object_or_404(
        _manageable_attendance_sessions(user).select_related(
            "session__teacher__user",
            "session__course__department__institution",
            "session__class_section",
        ),
        id=session_id,
    )


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

        session_obj = get_object_or_404(
            _manageable_timetable_sessions(request.user), id=session_id
        )
        now = timezone.now()
        end_time = now + timezone.timedelta(minutes=30)
        generated_otp = f"{random.randint(100000, 999999)}"

        attendance_session = AttendanceSession.objects.create(
            session=session_obj,
            date=now.date(),
            created_by=request.user,
            end_time=end_time,
            requires_wifi=requires_wifi,
            requires_geofence=requires_geofence,
            latitude=latitude,
            longitude=longitude,
            radius_meters=radius_meters,
            quick_otp=generated_otp,
            is_active=True,
        )

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
    description="يُعيد رمز QR مؤقت لجلسة تحضير نشطة مع التحكم في سرعة التدوير.",
    responses={200: OpenApiResponse(description="رمز QR والمدة الزمنية")},
)
class GetDynamicQRTokenView(APIView):
    authentication_classes = [SafeJWTOrSessionAuthentication]
    permission_classes = [IsTeacher]

    def get(self, request, session_id):
        attendance_session = get_object_or_404(
            _manageable_attendance_sessions(request.user),
            id=session_id,
            is_active=True,
            end_time__gt=timezone.now(),
        )
        # Check custom requested speed from query param (e.g. 15, 30, 60, 120)
        speed_param = request.GET.get("speed")
        try:
            speed_val = int(speed_param) if speed_param else 30
            speed_val = max(10, min(speed_val, MAX_DYNAMIC_QR_TOKEN_TTL))
        except (ValueError, TypeError):
            speed_val = 30

        # Keep an OTP available from the initial server-rendered teacher screen.
        _ensure_attendance_otp(attendance_session)

        token_ttl = (
            FROZEN_QR_TOKEN_TTL
            if attendance_session.is_frozen_qr
            else speed_val
        )
        token = generate_qr_token(attendance_session, ttl_seconds=token_ttl)
        expires = token_ttl
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

    attendance_session = _manageable_attendance_session(request.user, session_id)
    if request.method == "POST":
        try:
            body = json.loads(request.body.decode("utf-8")) if request.body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse(
                {"success": False, "error": "بيانات الطلب غير صالحة"},
                status=400,
            )
        if not isinstance(body, dict):
            return JsonResponse(
                {"success": False, "error": "يجب إرسال بيانات بصيغة JSON object"},
                status=400,
            )
        if "is_frozen" in body:
            if not isinstance(body["is_frozen"], bool):
                return JsonResponse(
                    {"success": False, "error": "قيمة تثبيت QR غير صالحة"},
                    status=400,
                )
            new_frozen_state = body["is_frozen"]
        else:
            new_frozen_state = not attendance_session.is_frozen_qr

        update_fields = ["is_frozen_qr"]
        if new_frozen_state != attendance_session.is_frozen_qr:
            attendance_session.qr_salt = uuid.uuid4()
            update_fields.append("qr_salt")
        attendance_session.is_frozen_qr = new_frozen_state
        attendance_session.save(update_fields=update_fields)
        token_ttl = (
            FROZEN_QR_TOKEN_TTL
            if attendance_session.is_frozen_qr
            else DEFAULT_QR_TOKEN_TTL
        )
        token = generate_qr_token(attendance_session, ttl_seconds=token_ttl)
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

    attendance_session = _manageable_attendance_session(request.user, session_id)
    if not attendance_session.is_frozen_qr:
        attendance_session.is_frozen_qr = True
        attendance_session.qr_salt = uuid.uuid4()
        attendance_session.save(update_fields=["is_frozen_qr", "qr_salt"])

    token = generate_qr_token(
        attendance_session, ttl_seconds=FROZEN_QR_TOKEN_TTL
    )
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

    att_session = _manageable_attendance_session(request.user, session_id)
    class_section = att_session.session.class_section
    section_students = StudentProfile.objects.filter(
        Q(sections=class_section) |
        Q(pk__in=AttendanceRecord.objects.filter(attendance_session=att_session).values_list("student_id", flat=True))
    ).distinct()

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
    throttle_classes = [StudentCheckInRateThrottle]

    def post(self, request):
        token = request.data.get("token")
        otp = request.data.get("otp")
        session_id = request.data.get("session_id")
        offline_checkin = request.data.get("offline_checkin") is True
        checkin_method = AttendanceRecord.Methods.QR
        if not token and not otp:
            return Response(
                {"error": _("يرجى مسح رمز الـ QR أو إدخال رمز التحضير السريع (OTP).")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 2. Token / OTP Verification
        if token:
            attendance_session, error_msg = verify_qr_token(
                token,
                allow_expired=offline_checkin,
                allow_rotated_salt=offline_checkin,
            )
            if not attendance_session:
                raw_candidate_session_id = session_id or _unverified_qr_session_id(token)
                try:
                    candidate_session_id = int(raw_candidate_session_id)
                except (TypeError, ValueError, OverflowError):
                    candidate_session_id = 0
                if (
                    offline_checkin
                    and 0 < candidate_session_id <= 9223372036854775807
                ):
                    candidate_session = AttendanceSession.objects.filter(
                        id=candidate_session_id
                    ).first()
                    if candidate_session:
                        review_response = _store_unverified_offline_submission(
                            request, candidate_session, error_msg or _("توقيع الرمز غير صالح")
                        )
                        if review_response:
                            return review_response
                return Response({"error": error_msg or _("رمز التحضير غير صالح")}, status=status.HTTP_400_BAD_REQUEST)
        else:
            otp_clean = str(otp).strip() if otp else ""
            if not session_id:
                return Response(
                    {"error": _("معرّف جلسة التحضير مطلوب مع رمز OTP.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            else:
                try:
                    session_id = int(session_id)
                except (TypeError, ValueError, OverflowError):
                    return Response(
                        {"error": _("معرّف جلسة التحضير غير صالح.")},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                attendance_session = get_object_or_404(AttendanceSession, id=session_id)
                if not attendance_session.quick_otp or attendance_session.quick_otp != otp_clean:
                    review_response = _store_unverified_offline_submission(
                        request,
                        attendance_session,
                        _("رمز التحضير السريع OTP غير صحيح أو انتهى."),
                    )
                    if review_response:
                        return review_response
                    return Response({"error": _("رمز التحضير السريع (OTP) غير صحيح أو انتهى.")}, status=status.HTTP_400_BAD_REQUEST)
            checkin_method = AttendanceRecord.Methods.OTP

        # 3. Never extend an expired session as a side effect of a student check-in.
        if not attendance_session.is_active:
            return Response(
                {
                    "error": _("أوقف الأستاذ استقبال الحضور لهذه المحاضرة. احتفظ بطلبك واطلب منه فتح الجلسة لمزامنته."),
                    "session_closed": True,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if attendance_session.end_time <= timezone.now():
            end_label = timezone.localtime(attendance_session.end_time).strftime("%H:%M")
            return Response(
                {
                    "error": _("انتهت فترة الحضور عند الساعة %s. احتفظ بطلبك واطلب من الأستاذ تمديد الفترة لمزامنته.") % end_label,
                    "session_expired": True,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        class_section = attendance_session.session.class_section

        # 4. Existing student, listed guest, or provisional guest awaiting teacher review.
        student_profile = None
        is_new_student = False
        requires_teacher_review = offline_checkin
        provisional_name = None
        force_guest_checkin = request.data.get("guest_checkin") is True
        if (
            request.user.is_authenticated
            and hasattr(request.user, "student_profile")
            and not force_guest_checkin
        ):
            student_profile = request.user.student_profile
            if student_profile.institution_id != class_section.department.institution_id:
                return Response(
                    {"error": _("حسابك تابع لمؤسسة أخرى ولا يمكن تسجيله في هذه الجلسة.")},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if not student_profile.sections.filter(id=class_section.id).exists():
                requires_teacher_review = True
        else:
            student_query = request.data.get("student_name") or request.data.get("student_id") or request.data.get("identifier")
            if not student_query or not str(student_query).strip():
                return Response(
                    {
                        "error": _("يرجى إدخال اسمك الكامل أو رقمك الجامعي لتثبيت حضورك في كشف الشعبة."),
                        "requires_name": True
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            student_query = str(student_query).strip()
            if len(student_query) > 150:
                return Response(
                    {"error": _("يجب ألا يتجاوز الاسم أو الرقم الجامعي 150 حرفاً.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            provisional_records = AttendanceRecord.objects.filter(
                attendance_session=attendance_session,
                student__student_id__startswith="NEW-",
            ).select_related("student__user")
            normalized_query = normalize_arabic_text(student_query)
            student_profile = next(
                (
                    item.student
                    for item in provisional_records
                    if normalize_arabic_text(
                        item.student.user.get_full_name() or item.student.user.username
                    ) == normalized_query
                ),
                None,
            )
            if student_profile:
                is_new_student = True
                requires_teacher_review = True
            else:
                matched_student, match_err = find_matching_student(student_query, class_section)
                if matched_student:
                    student_profile = matched_student
                elif match_err and "يوجد" in match_err:
                    return Response(
                        {"error": match_err},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                else:
                    provisional_name = student_query
                    is_new_student = True
                    requires_teacher_review = True

        # Allow an unlisted student to check in, but flag their record for review.
        if (
            not requires_teacher_review
            and student_profile
            and student_profile.study_shift
            and attendance_session.shift
        ):
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
        existing_present = (
            AttendanceRecord.objects.filter(
                student=student_profile,
                attendance_session=attendance_session,
                status__in=[AttendanceRecord.Statuses.PRESENT, AttendanceRecord.Statuses.LATE],
            ).first()
            if student_profile
            else None
        )
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

        institution = (
            student_profile.institution
            if student_profile
            else class_section.department.institution
        )
        ip = _get_ip(request)

        # 7. Geolocation / GPS Geofencing Verification (Factor 2)
        # ─────────────────────────────────────────────────────────────────
        # OTP is an explicit fallback; client-supplied GPS quality never bypasses the fence.
        GPS_OTP_BYPASS_METHODS = {
            AttendanceRecord.Methods.OTP,
            AttendanceRecord.Methods.WIFI,
            AttendanceRecord.Methods.OFFLINE_MANUAL,
        }
        GPS_ACCURACY_BYPASS_THRESHOLD = 150  # metres — typical indoor GPS error

        if attendance_session.requires_geofence and checkin_method not in GPS_OTP_BYPASS_METHODS:
            student_lat = request.data.get("latitude")
            student_lon = request.data.get("longitude")
            gps_accuracy = request.data.get("gps_accuracy")  # metres, sent by client

            try:
                student_lat_float = float(student_lat)
                student_lon_float = float(student_lon)
                gps_accuracy_float = float(gps_accuracy)
            except (TypeError, ValueError, OverflowError):
                return Response(
                    {
                        "error": _(
                            "تعذر التحقق من إحداثيات GPS ودقتها. فعّل الموقع وحاول مجدداً "
                            "أو استخدم رمز OTP الذي يقدمه الأستاذ."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
            import math
            if (
                not math.isfinite(student_lat_float)
                or not math.isfinite(student_lon_float)
                or not math.isfinite(gps_accuracy_float)
                or not -90 <= student_lat_float <= 90
                or not -180 <= student_lon_float <= 180
                or not 0 < gps_accuracy_float <= GPS_ACCURACY_BYPASS_THRESHOLD
            ):
                return Response(
                    {
                        "error": _(
                            "إشارة GPS غير دقيقة بما يكفي للتحقق. حاول مجدداً في مكان مفتوح "
                            "أو استخدم رمز OTP الذي يقدمه الأستاذ."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            target_lat = attendance_session.latitude or (institution.latitude if institution else None)
            target_lon = attendance_session.longitude or (institution.longitude if institution else None)
            allowed_radius = attendance_session.radius_meters or (institution.radius_meters if institution else 200) or 200
            if target_lat is None or target_lon is None:
                return Response(
                    {"error": _("لم يحدد الأستاذ موقعاً صالحاً لهذه الجلسة.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            distance = calculate_haversine_distance(
                student_lat_float,
                student_lon_float,
                target_lat,
                target_lon,
            )
            if distance is None or distance > allowed_radius:
                distance_text = str(int(distance)) if distance is not None else _("غير معروفة")
                return Response(
                    {
                        "error": (
                            _("أنت خارج نطاق المحاضرة. المسافة المحسوبة: %sم؛ النطاق المسموح: %sم.")
                            % (distance_text, int(allowed_radius))
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # 8. WiFi / Subnet restriction (if configured)
        if attendance_session.requires_wifi:
            subnet = attendance_session.allowed_ip_subnet or (institution.allowed_ip_subnet if institution else None)
            if subnet and not is_ip_in_subnet(ip, subnet):
                return Response(
                    {
                        "error": (
                            _("يجب الاتصال بشبكة WiFi/Hotspot المحاضرة. عنوان جهازك الحالي (%s) غير مسموح به.")
                            % ip
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

        device_id = request.data.get("device_id") or request.headers.get("X-Device-Id")
        raw_ua = request.META.get("HTTP_USER_AGENT", "")
        if device_id:
            device_user_agent = f"[DEV:{device_id}] {raw_ua}"
        else:
            device_user_agent = raw_ua

        record_notes = None
        if is_new_student:
            record_notes = "طالب جديد غير موجود في قائمة الشعبة - يتطلب مراجعة الأستاذ."
            if offline_checkin:
                record_notes += " أُرسل الطلب بعد انقطاع الاتصال."
        elif requires_teacher_review:
            record_notes = (
                "طلب حضور أُرسل بعد انقطاع الاتصال - يتطلب مراجعة الأستاذ."
                if offline_checkin
                else "طالب غير مسجل في هذه الشعبة - يتطلب مراجعة الأستاذ."
            )

        with transaction.atomic():
            if provisional_name is not None:
                parts = provisional_name.split(maxsplit=1)
                unique_id = uuid.uuid4().hex.upper()
                guest_user = User.objects.create(
                    username=f"guest_{unique_id.lower()}",
                    first_name=parts[0],
                    last_name=parts[1] if len(parts) > 1 else "",
                    role=User.Roles.STUDENT,
                )
                guest_user.set_unusable_password()
                guest_user.save(update_fields=["password"])
                student_profile = StudentProfile.objects.create(
                    user=guest_user,
                    student_id=f"NEW-{unique_id}",
                    institution=class_section.department.institution,
                    study_shift=attendance_session.shift or class_section.shift,
                )

            record, created = AttendanceRecord.objects.update_or_create(
                student=student_profile,
                attendance_session=attendance_session,
                defaults={
                    "status": record_status,
                    "method": checkin_method,
                    "ip_address": ip,
                    "device_user_agent": device_user_agent,
                    "modified_by": (
                        request.user
                        if request.user.is_authenticated and not force_guest_checkin
                        else None
                    ),
                    "notes": record_notes,
                },
            )

        queue_for_sync(record, SyncQueue.Actions.CREATE if created else SyncQueue.Actions.UPDATE)

        if requires_teacher_review:
            AuditLog.objects.create(
                user=(
                    request.user
                    if request.user.is_authenticated and not force_guest_checkin
                    else None
                ),
                action="تسجيل حضور طالب يتطلب مراجعة الأستاذ",
                details={
                    "attendance_session_id": attendance_session.id,
                    "student_profile_id": student_profile.id,
                    "student_name": student_profile.user.get_full_name(),
                    "is_new_student": is_new_student,
                    "device_id": device_id or "",
                },
            )

        serializer = AttendanceRecordSerializer(record)
        if is_new_student:
            msg = "تم تسجيل حضورك كطالب جديد بانتظار مراجعة الأستاذ. يرجى التأكد من إضافة اسمك إلى القائمة."
        elif requires_teacher_review:
            msg = (
                "وصل طلبك بعد انقطاع الاتصال ووُضع بانتظار مراجعة الأستاذ."
                if offline_checkin
                else "تم تسجيل الحضور وإبلاغ الأستاذ بأن حسابك غير مسجل في هذه الشعبة."
            )
        else:
            msg = (
                "تم تسجيل حضورك بنجاح وفي الوقت المحدد ✔"
                if record_status == AttendanceRecord.Statuses.PRESENT
                else "تم تسجيل حضورك بنجاح (مع احتساب تأخير) ⏰"
            )
        return Response(
            {
                "message": msg,
                "record": serializer.data,
                "is_new_student": is_new_student,
                "requires_teacher_review": requires_teacher_review,
            },
            status=status.HTTP_200_OK,
        )


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

        record = get_object_or_404(
            AttendanceRecord.objects.filter(
                attendance_session__in=_manageable_attendance_sessions(request.user)
            ),
            id=record_id,
        )
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
            _manageable_attendance_sessions(request.user),
            id=session_id,
            is_active=True,
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


@login_required
@require_POST
def control_attendance_session_view(request, session_id):
    """Teacher control for closing, reopening, or extending the attendance window."""
    if not (
        request.user.is_teacher()
        or request.user.is_super_admin()
        or request.user.is_institution_admin()
    ):
        return JsonResponse({"success": False, "error": "غير مصرح لك"}, status=403)

    attendance_session = _manageable_attendance_session(request.user, session_id)
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"success": False, "error": "بيانات الطلب غير صالحة"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"success": False, "error": "صيغة الطلب غير صالحة"}, status=400)

    action = payload.get("action")
    if action not in {"close", "reopen", "extend"}:
        return JsonResponse({"success": False, "error": "الإجراء المطلوب غير معروف"}, status=400)

    if action in {"reopen", "extend"}:
        try:
            minutes = int(payload.get("minutes", 0))
        except (TypeError, ValueError):
            minutes = 0
        if not 5 <= minutes <= 240:
            return JsonResponse(
                {"success": False, "error": "اختر مدة بين 5 و240 دقيقة."},
                status=400,
            )
        now = timezone.now()
        if action == "extend":
            base_end = max(attendance_session.end_time, now)
            attendance_session.end_time = base_end + timezone.timedelta(minutes=minutes)
            attendance_session.is_active = True
        else:
            attendance_session.end_time = now + timezone.timedelta(minutes=minutes)
            attendance_session.is_active = True
            attendance_session.start_time = now
            attendance_session.qr_salt = uuid.uuid4()
            if not attendance_session.quick_otp:
                attendance_session.quick_otp = f"{random.randint(100000, 999999)}"
    else:
        attendance_session.is_active = False
        attendance_session.qr_salt = uuid.uuid4()

    fields = ["is_active", "end_time", "qr_salt", "quick_otp", "start_time"]
    attendance_session.save(update_fields=fields)
    AuditLog.objects.create(
        user=request.user,
        action={
            "close": "إغلاق_جلسة_تحضير",
            "reopen": "إعادة_فتح_جلسة_تحضير",
            "extend": "تمديد_فترة_الحضور",
        }[action],
        ip_address=_get_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT"),
        details={
            "attendance_session_id": attendance_session.id,
            "action": action,
            "minutes": payload.get("minutes"),
            "end_time": attendance_session.end_time.isoformat(),
        },
    )
    message = {
        "close": "أُغلق استقبال الحضور. الطلبات الجديدة سترفض.",
        "reopen": "أُعيد فتح استقبال الحضور.",
        "extend": "تم تمديد فترة استقبال الحضور.",
    }[action]
    return JsonResponse({
        "success": True,
        "message": message,
        "is_active": attendance_session.is_active,
        "end_time": attendance_session.end_time.isoformat(),
        "server_now": timezone.now().isoformat(),
    })


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

        session_obj = get_object_or_404(
            _manageable_timetable_sessions(request.user), id=session_id
        )
        now = timezone.now()
        end_time = now + timezone.timedelta(minutes=30)

        attendance_session = AttendanceSession.objects.create(
            session=session_obj,
            date=now.date(),
            created_by=request.user,
            end_time=end_time,
            requires_wifi=requires_wifi,
            requires_geofence=requires_geofence,
            quick_otp=generated_otp,
            is_active=True,
        )

        messages.success(request, f"تم تفعيل جلسة تحضير مادة {session_obj.course.name}")
        return redirect("attendance:session_detail_web", session_id=attendance_session.id)

    return redirect("dashboard")


@login_required
def session_detail_web_view(request, session_id):
    """Detailed monitoring screen for teachers. Shows dynamic QR and student list."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك بعرض شاشة التحضير.")
        return redirect("dashboard")

    attendance_session = _manageable_attendance_sessions(request.user).filter(
        id=session_id
    ).first()
    if not attendance_session:
        # Check if session_id refers to a timetable Session schedule
        sess_obj = _manageable_timetable_sessions(request.user).filter(
            id=session_id
        ).first()
        if sess_obj:
            att = _manageable_attendance_sessions(request.user).filter(
                session=sess_obj
            ).order_by("-id").first()
            if att:
                return redirect("attendance:session_detail_web", session_id=att.id)
            return redirect(f"/attendance/session/create-custom/?session_id={session_id}")
        messages.error(request, "لم يتم العثور على جلسة التحضير المطلوبة.")
        return redirect("attendance:teacher_sessions_web")

    # ✅ FIX: Get students in section + any newly checked-in students
    students_in_section = _get_students_for_session(attendance_session)
    shared_device_map = _get_shared_device_map(attendance_session)
    enrolled_ids = set(
        attendance_session.session.class_section.students.values_list("id", flat=True)
    )

    records_map = {
        r.student_id: r
        for r in AttendanceRecord.objects.filter(attendance_session=attendance_session)
    }

    students_list = []
    for std in students_in_section:
        rec = records_map.get(std.id)
        shared_with = shared_device_map.get(std.id, [])
        students_list.append(
            {
                "profile": std,
                "record": rec,
                "status": rec.status if rec else "ABSENT",
                "status_display": rec.get_status_display() if rec else "غائب",
                "is_shared_device": bool(shared_with),
                "shared_with_names": shared_with,
                "is_enrolled": std.id in enrolled_ids,
            }
        )

    present_cnt = sum(1 for s in students_list if s["status"] in ["PRESENT", "LATE"])
    absent_cnt = sum(1 for s in students_list if s["status"] == "ABSENT")
    late_cnt = sum(1 for s in students_list if s["status"] == "LATE")
    total_cnt = len(students_list)
    rate = round((present_cnt / total_cnt * 100), 1) if total_cnt > 0 else 0
    review_required_count = sum(
        1
        for item in students_list
        if (
            item["profile"].student_id.startswith("NEW-")
            and not item["is_enrolled"]
        )
        or (
            item["record"]
            and item["record"].notes
            and "يتطلب مراجعة الأستاذ" in item["record"].notes
        )
    )
    initial_qr_token = ""
    if attendance_session.is_active and attendance_session.end_time > timezone.now():
        _ensure_attendance_otp(attendance_session)
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
        "review_required_count": review_required_count,
        "initial_qr_token": initial_qr_token,
        "primary_hotspot_ip": primary_hotspot_ip,
        "server_port": server_port,
        "review_submissions": attendance_session.offline_submissions.filter(
            status=OfflineAttendanceSubmission.Statuses.PENDING
        ).order_by("received_at"),
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

    attendance_session = _manageable_attendance_session(request.user, session_id)

    # ✅ FIX: Use helper to get section-specific students + new check-ins
    students_in_section = _get_students_for_session(attendance_session)
    shared_device_map = _get_shared_device_map(attendance_session)
    enrolled_ids = set(
        attendance_session.session.class_section.students.values_list("id", flat=True)
    )

    records_map = {
        r.student_id: r
        for r in AttendanceRecord.objects.filter(attendance_session=attendance_session)
    }

    students_list = []
    for std in students_in_section:
        rec = records_map.get(std.id)
        shared_with = shared_device_map.get(std.id, [])
        students_list.append(
            {
                "profile": std,
                "record": rec,
                "status": rec.status if rec else "ABSENT",
                "status_display": rec.get_status_display() if rec else "غائب",
                "is_shared_device": bool(shared_with),
                "shared_with_names": shared_with,
                "is_enrolled": std.id in enrolled_ids,
            }
        )

    present_cnt = sum(1 for s in students_list if s["status"] in ["PRESENT", "LATE"])
    absent_cnt = sum(1 for s in students_list if s["status"] == "ABSENT")
    late_cnt = sum(1 for s in students_list if s["status"] == "LATE")
    total_cnt = len(students_list)
    rate = round((present_cnt / total_cnt * 100), 1) if total_cnt > 0 else 0
    review_required_count = sum(
        1
        for item in students_list
        if (
            item["profile"].student_id.startswith("NEW-")
            and not item["is_enrolled"]
        )
        or (
            item["record"]
            and item["record"].notes
            and "يتطلب مراجعة الأستاذ" in item["record"].notes
        )
    )

    context = {
        "attendance_session": attendance_session,
        "students_list": students_list,
        "present_count": present_cnt,
        "absent_count": absent_cnt,
        "late_count": late_cnt,
        "total_count": total_cnt,
        "attendance_rate": rate,
        "review_required_count": review_required_count,
        "review_submissions": attendance_session.offline_submissions.filter(
            status=OfflineAttendanceSubmission.Statuses.PENDING
        ).order_by("received_at"),
    }
    return render(request, "attendance/partials/students_list.html", context)


@login_required
@require_POST
def review_offline_submission_view(request, session_id, submission_id):
    if not (
        request.user.is_teacher()
        or request.user.is_super_admin()
        or request.user.is_institution_admin()
    ):
        return JsonResponse({"success": False, "error": "غير مصرح لك"}, status=403)

    attendance_session = _manageable_attendance_session(request.user, session_id)
    action = request.POST.get("action")
    if action not in {"approve", "reject"}:
        return JsonResponse({"success": False, "error": "إجراء المراجعة غير صالح."}, status=400)

    with transaction.atomic():
        try:
            submission = OfflineAttendanceSubmission.objects.select_for_update().get(
                id=submission_id,
                attendance_session=attendance_session,
            )
        except OfflineAttendanceSubmission.DoesNotExist:
            return JsonResponse(
                {"success": False, "error": "طلب المراجعة غير موجود."},
                status=404,
            )
        if submission.status != OfflineAttendanceSubmission.Statuses.PENDING:
            return JsonResponse(
                {"success": False, "error": "تمت مراجعة هذا الطلب مسبقاً."},
                status=409,
            )

        if action == "reject":
            submission.status = OfflineAttendanceSubmission.Statuses.REJECTED
            submission.reviewed_by = request.user
            submission.reviewed_at = timezone.now()
            submission.save(update_fields=["status", "reviewed_by", "reviewed_at"])
            AuditLog.objects.create(
                user=request.user,
                action="رفض_طلب_حضور_غير_متصل",
                ip_address=_get_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT"),
                details={
                    "attendance_session_id": attendance_session.id,
                    "submission_id": submission.id,
                    "student_name": submission.student_name,
                },
            )
            return JsonResponse(
                {"success": True, "message": "تم رفض الطلب وإزالته من قائمة المراجعة."}
            )

        class_section = attendance_session.session.class_section
        provisional_match = next(
            (
                record.student
                for record in AttendanceRecord.objects.filter(
                    attendance_session=attendance_session,
                    student__student_id__startswith="NEW-",
                ).select_related("student__user")
                if normalize_arabic_text(
                    record.student.user.get_full_name()
                    or record.student.user.username
                )
                == normalize_arabic_text(submission.student_name)
            ),
            None,
        )
        student_profile = provisional_match
        if not student_profile:
            student_profile, match_error = find_matching_student(
                submission.student_name, class_section
            )
            if match_error and "يوجد" in match_error:
                return JsonResponse(
                    {"success": False, "error": match_error}, status=409
                )

        if not student_profile:
            name_parts = submission.student_name.split(maxsplit=1)
            guest_user = User.objects.create(
                username=f"guest_{uuid.uuid4().hex.lower()}",
                first_name=name_parts[0],
                last_name=name_parts[1] if len(name_parts) > 1 else "",
                role=User.Roles.STUDENT,
            )
            guest_user.set_unusable_password()
            guest_user.save(update_fields=["password"])
            student_profile = StudentProfile.objects.create(
                user=guest_user,
                student_id=f"NEW-{uuid.uuid4().hex.upper()}",
                institution=class_section.department.institution,
                study_shift=attendance_session.shift or class_section.shift,
            )

        should_enroll = request.POST.get("enroll") == "true"
        if should_enroll:
            student_profile.sections.add(class_section)

        record, created = AttendanceRecord.objects.update_or_create(
            student=student_profile,
            attendance_session=attendance_session,
            defaults={
                "status": AttendanceRecord.Statuses.PRESENT,
                "method": AttendanceRecord.Methods.MANUAL,
                "modified_by": request.user,
                "notes": (
                    "اعتماد يدوي من الأستاذ لطلب حضور تعذر التحقق من رمز الحضور. "
                    "وقت الاستلام المسجل هو وقت وصول الطلب إلى الخادم."
                ),
            },
        )
        submission.status = OfflineAttendanceSubmission.Statuses.APPROVED
        submission.student = student_profile
        submission.reviewed_by = request.user
        submission.reviewed_at = timezone.now()
        submission.save(
            update_fields=["status", "student", "reviewed_by", "reviewed_at"]
        )
        queue_for_sync(
            record,
            SyncQueue.Actions.CREATE if created else SyncQueue.Actions.UPDATE,
        )
        AuditLog.objects.create(
            user=request.user,
            action="اعتماد_طلب_حضور_غير_متصل",
            ip_address=_get_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT"),
            details={
                "attendance_session_id": attendance_session.id,
                "submission_id": submission.id,
                "student_profile_id": student_profile.id,
                "student_name": submission.student_name,
                "enrolled_in_section": should_enroll,
            },
        )
    return JsonResponse(
        {
            "success": True,
            "message": (
                "تم اعتماد الحضور يدويًا وإضافة الطالب إلى الشعبة."
                if should_enroll
                else "تم اعتماد الحضور يدويًا."
            ),
        }
    )


@login_required
@require_POST
def toggle_provisional_student_enrollment_view(request, session_id, student_id):
    if not (
        request.user.is_teacher()
        or request.user.is_super_admin()
        or request.user.is_institution_admin()
    ):
        return JsonResponse({"success": False, "error": "غير مصرح لك"}, status=403)

    attendance_session = _manageable_attendance_session(request.user, session_id)
    student_profile = StudentProfile.objects.filter(
        id=student_id,
        student_id__startswith="NEW-",
        institution=attendance_session.session.class_section.department.institution,
    ).first()
    if not student_profile:
        return JsonResponse({"success": False, "error": "الطالب المؤقت غير موجود في هذه الجلسة."}, status=404)

    section = attendance_session.session.class_section
    if student_profile.sections.filter(id=section.id).exists():
        student_profile.sections.remove(section)
        enrolled = False
    else:
        student_profile.sections.add(section)
        enrolled = True
        record = AttendanceRecord.objects.filter(
            student=student_profile,
            attendance_session=attendance_session,
        ).first()
        if record and record.notes:
            record.notes = "راجع الأستاذ الطالب واعتمده وأضيف إلى قائمة الشعبة."
            record.modified_by = request.user
            record.save(update_fields=["notes", "modified_by", "updated_at"])
            queue_for_sync(record, SyncQueue.Actions.UPDATE)

    AuditLog.objects.create(
        user=request.user,
        action="تعديل_عضوية_طالب_مؤقت_في_الشعبة",
        ip_address=_get_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT"),
        details={
            "attendance_session_id": attendance_session.id,
            "student_profile_id": student_profile.id,
            "enrolled": enrolled,
        },
    )
    return JsonResponse(
        {
            "success": True,
            "enrolled": enrolled,
            "message": "تم تحديث عضوية الطالب في الشعبة.",
        }
    )


@login_required
@require_POST
def bulk_manage_session_students_view(request, session_id):
    if not (
        request.user.is_teacher()
        or request.user.is_super_admin()
        or request.user.is_institution_admin()
    ):
        return JsonResponse({"success": False, "error": "غير مصرح لك"}, status=403)

    attendance_session = _manageable_attendance_session(request.user, session_id)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"success": False, "error": "صيغة الطلب غير صالحة."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"success": False, "error": "صيغة الطلب غير صالحة."}, status=400)

    student_ids = payload.get("student_ids")
    action = payload.get("action")
    if (
        not isinstance(student_ids, list)
        or not student_ids
        or len(student_ids) > 500
        or not all(isinstance(item, int) and item > 0 for item in student_ids)
    ):
        return JsonResponse({"success": False, "error": "حدد طالباً واحداً على الأقل."}, status=400)
    if action not in {"present", "absent", "late", "enroll", "remove"}:
        return JsonResponse({"success": False, "error": "الإجراء الجماعي غير صالح."}, status=400)

    section = attendance_session.session.class_section
    students = list(StudentProfile.objects.filter(id__in=set(student_ids)))
    if not students:
        return JsonResponse({"success": False, "error": "لم يتم العثور على طلاب محددين في هذه الجلسة."}, status=404)

    skipped_count = 0
    if action in {"enroll", "remove"}:
        eligible_students = [
            student for student in students if student.student_id.startswith("NEW-")
        ]
        if not eligible_students:
            return JsonResponse(
                {"success": False, "error": "حدد طالباً جديداً واحداً على الأقل لتعديل عضويته."},
                status=400,
            )
        skipped_count = len(students) - len(eligible_students)
        changed = 0
        with transaction.atomic():
            for student in eligible_students:
                is_enrolled = student.sections.filter(id=section.id).exists()
                if action == "enroll" and not is_enrolled:
                    student.sections.add(section)
                    changed += 1
                elif action == "remove" and is_enrolled:
                    student.sections.remove(section)
                    changed += 1
                    continue
                if action == "enroll":
                    record = AttendanceRecord.objects.filter(
                        student=student,
                        attendance_session=attendance_session,
                    ).first()
                    if record and record.notes:
                        record.notes = "راجع الأستاذ الطالب واعتمده وأضيف إلى قائمة الشعبة."
                        record.modified_by = request.user
                        record.save(update_fields=["notes", "modified_by", "updated_at"])
                        queue_for_sync(record, SyncQueue.Actions.UPDATE)
        audit_action = "إضافة_جماعية_للطلاب_الجدد_إلى_الشعبة" if action == "enroll" else "إزالة_جماعية_للطلاب_الجدد_من_الشعبة"
    else:
        status_by_action = {
            "present": AttendanceRecord.Statuses.PRESENT,
            "absent": AttendanceRecord.Statuses.ABSENT,
            "late": AttendanceRecord.Statuses.LATE,
        }
        changed = 0
        with transaction.atomic():
            for student in students:
                record, created = AttendanceRecord.objects.update_or_create(
                    student=student,
                    attendance_session=attendance_session,
                    defaults={
                        "status": status_by_action[action],
                        "method": AttendanceRecord.Methods.MANUAL,
                        "modified_by": request.user,
                        "notes": "تحديث جماعي يدوي من شاشة الجلسة.",
                    },
                )
                queue_for_sync(
                    record,
                    SyncQueue.Actions.CREATE if created else SyncQueue.Actions.UPDATE,
                )
                changed += 1
        audit_action = "تحديث_جماعي_لحالة_الحضور"

    AuditLog.objects.create(
        user=request.user,
        action=audit_action,
        ip_address=_get_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT"),
        details={
            "attendance_session_id": attendance_session.id,
            "action": action,
            "selected_count": len(students),
            "changed_count": changed,
            "skipped_count": skipped_count,
            "student_profile_ids": [student.id for student in students],
        },
    )
    return JsonResponse(
        {
            "success": True,
            "changed_count": changed,
            "selected_count": len(students),
            "skipped_count": skipped_count,
            "message": f"اكتمل الإجراء الجماعي: تم تحديث {changed} من أصل {len(students)} طالباً.",
        }
    )


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

    att_session = _manageable_attendance_session(request.user, session_id)
    student = get_object_or_404(StudentProfile, id=student_id)

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
    att_session = _manageable_attendance_session(request.user, session_id)
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
    import urllib.parse

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
    query_params = []
    if token:
        query_params.append(f"token={urllib.parse.quote(token, safe='')}")
    session_id = request.GET.get("session_id", "")
    if session_id.isdigit():
        query_params.append(f"session_id={session_id}")
    if query_params:
        checkin_url += "?" + "&".join(query_params)

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
    if request.user.is_super_admin() or request.user.is_institution_admin():
        from apps.academics.models import Institution, ClassSection, Course
        from django.db.models import Q
        from django.utils import timezone
        from datetime import timedelta, datetime

        # Base QuerySet
        qs = AttendanceRecord.objects.filter(
            attendance_session__in=_manageable_attendance_sessions(request.user)
        ).select_related(
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
        if request.user.is_super_admin():
            institutions = Institution.objects.all().order_by("name")
        else:
            institution_id = _user_institution_id(request.user)
            institutions = Institution.objects.filter(id=institution_id).order_by("name")
        sections = ClassSection.objects.filter(
            department__institution__in=institutions
        ).select_related("department__institution").order_by("name")
        courses = Course.objects.filter(
            department__institution__in=institutions
        ).select_related("department__institution").order_by("name")
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
        sessions = _manageable_timetable_sessions(request.user).select_related(
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
        messages.error(
            request,
            "يجب ربط حسابك بملف أستاذ قبل إنشاء جلسة حضور.",
        )
        return redirect("dashboard")

    institution = teacher_profile.institution

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
            course = Course.objects.filter(
                id=int(course_id),
                department__institution=institution,
            ).first()

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
            class_section = ClassSection.objects.filter(
                id=int(class_section_id),
                department__institution=institution,
            ).first()

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

        if conflict["has_conflict"]:
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

        is_scheduled_only = request.POST.get("action") == "schedule" or "schedule_only" in request.POST
        target_is_active = not is_scheduled_only

        attendance_session = AttendanceSession.objects.create(
            session=session_obj,
            date=session_date,
            created_by=request.user,
            end_time=end_dt,
            requires_wifi=requires_wifi,
            allowed_wifi_ssid=allowed_wifi_ssid,
            allowed_ip_subnet=allowed_ip_subnet,
            requires_geofence=requires_geofence,
            latitude=latitude,
            longitude=longitude,
            radius_meters=radius_meters,
            topic=topic,
            lecture_type=lecture_type,
            shift=shift,
            is_active=target_is_active,
        )
        attendance_session.start_time = start_dt
        attendance_session.save(update_fields=["start_time"])

        shift_display = "صباحي" if shift == "MORNING" else "مسائي"
        AuditLog.objects.create(
            user=request.user,
            action="جدولة_محاضرة" if is_scheduled_only else "بدء_تحضير_مخصص",
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
                "is_scheduled": is_scheduled_only,
            },
        )

        if is_scheduled_only:
            messages.success(
                request,
                f"تمت جدولة محاضرة {course.name} ({lecture_type} - {shift_display}) بنجاح لتاريخ {session_date}! ستجدها في قائمة المحاضرات المجدولة لتفعيلها بضغطة زر واحدة.",
            )
            return redirect("dashboard")

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
def edit_session_view(request, session_id):
    """Allows a teacher or admin to edit lecture details (course, section, shift, time, date, room, topic, OTP, etc.)."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "هذه الصفحة مخصصة للمعلمين والإداريين فقط.")
        return redirect("dashboard")

    att_session = _manageable_attendance_session(request.user, session_id)
    session_obj = att_session.session
    teacher_profile = getattr(request.user, "teacher_profile", None)
    institution = (
        teacher_profile.institution 
        if teacher_profile and teacher_profile.institution 
        else Institution.objects.first()
    )

    if request.method == "POST":
        course_name = request.POST.get("course_name", "").strip()
        course_code = request.POST.get("course_code", "").strip()
        section_name = request.POST.get("section_name", "").strip()
        section_level = request.POST.get("section_level", "").strip()
        shift = request.POST.get("shift", "MORNING").strip().upper()
        room = request.POST.get("room", "").strip()
        topic = request.POST.get("topic", "").strip()
        lecture_type = request.POST.get("lecture_type", "نظري").strip()
        session_date_str = request.POST.get("session_date", "").strip()
        start_time_str = request.POST.get("start_time", "").strip()
        end_time_str = request.POST.get("end_time", "").strip()
        is_active = "is_active" in request.POST
        is_frozen_qr = "is_frozen_qr" in request.POST
        quick_otp = request.POST.get("quick_otp", "").strip()

        # 1. Update Course
        if course_name:
            session_obj.course.name = course_name
            if course_code:
                session_obj.course.code = course_code
            session_obj.course.save()

        # 2. Update Section
        if section_name:
            session_obj.class_section.name = section_name
        if section_level:
            session_obj.class_section.level = section_level
        if shift:
            session_obj.class_section.shift = shift
        session_obj.class_section.save()

        # 3. Update Academic Session
        if room:
            session_obj.room = room
        if shift:
            session_obj.shift = shift
        
        if start_time_str:
            try:
                session_obj.start_time = datetime.strptime(start_time_str, "%H:%M").time()
            except ValueError:
                pass
        if end_time_str:
            try:
                session_obj.end_time = datetime.strptime(end_time_str, "%H:%M").time()
            except ValueError:
                pass
        session_obj.save()

        # 4. Update Attendance Session
        if session_date_str:
            try:
                att_session.date = datetime.strptime(session_date_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        if start_time_str:
            try:
                st = datetime.strptime(start_time_str, "%H:%M").time()
                att_session.start_time = timezone.make_aware(datetime.combine(att_session.date, st))
            except Exception:
                pass

        if end_time_str:
            try:
                et = datetime.strptime(end_time_str, "%H:%M").time()
                st_time = att_session.start_time.time() if att_session.start_time else time(0, 0)
                if et <= st_time:
                    end_date = att_session.date + timezone.timedelta(days=1)
                else:
                    end_date = att_session.date
                att_session.end_time = timezone.make_aware(datetime.combine(end_date, et))
            except Exception:
                pass
        elif att_session.start_time:
            att_session.end_time = att_session.start_time + timezone.timedelta(minutes=120)

        att_session.shift = shift
        att_session.topic = topic
        att_session.lecture_type = lecture_type
        att_session.is_active = is_active
        att_session.is_frozen_qr = is_frozen_qr

        # Ensure quick_otp is valid 6 digits
        if not quick_otp or len(quick_otp) != 6 or not quick_otp.isdigit():
            if not att_session.quick_otp:
                quick_otp = f"{random.randint(100000, 999999)}"
            else:
                quick_otp = att_session.quick_otp
        att_session.quick_otp = quick_otp

        att_session.save()

        AuditLog.objects.create(
            user=request.user,
            action="تعديل_بيانات_المحاضرة",
            ip_address=_get_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT"),
            details={
                "session_id": att_session.id,
                "course": session_obj.course.name,
                "section": session_obj.class_section.name,
                "shift": shift,
                "topic": topic,
            },
        )

        messages.success(request, f"تم حفظ تعديلات المحاضرة ({session_obj.course.name}) بنجاح! ✨")
        return redirect("attendance:session_detail_web", session_id=att_session.id)

    # Auto-ensure quick_otp exists for GET
    if not att_session.quick_otp:
        att_session.quick_otp = f"{random.randint(100000, 999999)}"
        att_session.save(update_fields=["quick_otp"])

    # Departments, courses, sections
    departments = institution.departments.all() if institution else Department.objects.all()
    courses = Course.objects.filter(department__in=departments) if departments.exists() else Course.objects.all()
    sections = ClassSection.objects.filter(department__in=departments) if departments.exists() else ClassSection.objects.all()

    # Format start and end times
    start_time_val = ""
    end_time_val = ""
    if att_session.start_time:
        start_time_val = att_session.start_time.strftime("%H:%M")
    elif session_obj.start_time:
        start_time_val = session_obj.start_time.strftime("%H:%M")

    if att_session.end_time:
        end_time_val = att_session.end_time.strftime("%H:%M")
    elif session_obj.end_time:
        end_time_val = session_obj.end_time.strftime("%H:%M")

    date_val = att_session.date.strftime("%Y-%m-%d") if att_session.date else ""

    context = {
        "att_session": att_session,
        "session_obj": session_obj,
        "courses": courses,
        "sections": sections,
        "current_start_time": start_time_val,
        "current_end_time": end_time_val,
        "current_date": date_val,
        "institution": institution,
    }
    return render(request, "attendance/edit_session.html", context)


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

    teacher_profile = getattr(request.user, "teacher_profile", None)
    if teacher_profile is None:
        return JsonResponse(
            {"success": False, "error": "لا يوجد ملف أستاذ مرتبط بحسابك."},
            status=403,
        )
    institution = teacher_profile.institution
    department = teacher_profile.department or institution.departments.first()

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

    teacher_profile = getattr(request.user, "teacher_profile", None)
    if teacher_profile is None:
        return JsonResponse(
            {"has_conflict": False, "error": "لا يوجد ملف أستاذ مرتبط بحسابك."},
            status=403,
        )
    course_id = data.get("course_id")
    class_section_id = data.get("class_section_id")
    day_of_week = data.get("day_of_week")
    start_time_str = data.get("start_time", "10:00")
    duration_minutes = int(data.get("duration_minutes", 60) or 60)
    shift = str(data.get("shift", "MORNING")).upper()
    room = data.get("room", "")
    session_date_str = data.get("session_date")

    course_scope = Course.objects.filter(
        department__institution_id=teacher_profile.institution_id
    )
    section_scope = ClassSection.objects.filter(
        department__institution_id=teacher_profile.institution_id
    )
    if teacher_profile.department_id:
        course_scope = course_scope.filter(department_id=teacher_profile.department_id)
        section_scope = section_scope.filter(department_id=teacher_profile.department_id)
    course = (
        course_scope.filter(id=int(course_id)).first()
        if course_id and str(course_id).isdigit()
        else None
    )
    class_section = (
        section_scope.filter(id=int(class_section_id)).first()
        if class_section_id and str(class_section_id).isdigit()
        else None
    )

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

    attendance_session = _manageable_attendance_session(request.user, session_id)
    checkin_token = ""
    if attendance_session.is_active and attendance_session.end_time > timezone.now():
        _ensure_attendance_otp(attendance_session)
        if not attendance_session.qr_salt:
            attendance_session.qr_salt = uuid.uuid4()
            attendance_session.save(update_fields=["qr_salt"])
        checkin_token = generate_qr_token(attendance_session)
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
            student = get_object_or_404(
                students_in_section, id=student_profile_id
            )
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
    review_required_count = sum(
        1
        for item in students_list
        if item["profile"].student_id.startswith("NEW-")
        or (
            item["record"]
            and item["record"].notes
            and "يتطلب مراجعة الأستاذ" in item["record"].notes
        )
    )

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
        "checkin_token": checkin_token,
        "review_required_count": review_required_count,
    }
    return render(request, "attendance/offline_emergency.html", context)


@login_required
@require_POST
def quick_start_scheduled_session_view(request, session_id):
    """
    1-Click Quick Launch for scheduled sessions or recurring timetable sessions.
    Activates the session immediately with QR code & fresh OTP without re-entering details.
    """
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك بتشغيل جلسات التحضير.")
        return redirect("dashboard")

    # Check if session_id refers to an AttendanceSession
    att_session = _manageable_attendance_sessions(request.user).filter(
        id=session_id
    ).first()
    now = timezone.now()
    generated_otp = f"{random.randint(100000, 999999)}"

    if att_session:
        # Check permissions
        if request.user.is_teacher() and att_session.session.teacher.user != request.user and att_session.created_by != request.user:
            messages.error(request, "غير مصرح لك بتشغيل هذه الجلسة.")
            return redirect("dashboard")

        if att_session.is_active and att_session.end_time > now:
            return redirect("attendance:session_detail_web", session_id=att_session.id)

        has_previous_activity = (
            att_session.records.exists() or att_session.offline_submissions.exists()
        )
        if has_previous_activity:
            att_session = AttendanceSession.objects.create(
                session=att_session.session,
                created_by=request.user,
                date=now.date(),
                end_time=now + timezone.timedelta(minutes=45),
                requires_wifi=att_session.requires_wifi,
                requires_geofence=att_session.requires_geofence,
                latitude=att_session.latitude,
                longitude=att_session.longitude,
                radius_meters=att_session.radius_meters,
                quick_otp=generated_otp,
                allowed_ip_subnet=att_session.allowed_ip_subnet,
                allowed_wifi_ssid=att_session.allowed_wifi_ssid,
                topic=att_session.topic,
                lecture_type=att_session.lecture_type,
                shift=att_session.shift,
                is_active=True,
            )
            att_session.start_time = now
            att_session.save(update_fields=["start_time"])
        else:
            att_session.is_active = True
            att_session.date = now.date()
            att_session.start_time = now
            att_session.end_time = now + timezone.timedelta(minutes=45)
            att_session.qr_salt = uuid.uuid4()
            att_session.quick_otp = generated_otp
            att_session.save()

        messages.success(request, f"⚡ تم تفعيل وبدء جلسة مادة ({att_session.session.course.name}) بنجاح!")
        return redirect("attendance:session_detail_web", session_id=att_session.id)
    
    # Try timetable Session
    timetable_session = get_object_or_404(
        _manageable_timetable_sessions(request.user), id=session_id
    )
    if request.user.is_teacher() and timetable_session.teacher.user != request.user:
        messages.error(request, "غير مصرح لك بتشغيل هذه الحصة.")
        return redirect("dashboard")

    # If an active session already exists for this timetable session right now, redirect directly to it
    existing_today = AttendanceSession.objects.filter(
        session=timetable_session,
        is_active=True,
        end_time__gt=now
    ).order_by("-id").first()
    if existing_today:
        return redirect("attendance:session_detail_web", session_id=existing_today.id)

    # Give every launch a separate attendance record set.
    att_session = AttendanceSession.objects.create(
        session=timetable_session,
        date=now.date(),
        created_by=request.user,
        end_time=now + timezone.timedelta(minutes=45),
        quick_otp=generated_otp,
        is_active=True,
        shift=timetable_session.shift,
    )
    att_session.start_time = now
    att_session.save(update_fields=["start_time"])

    messages.success(request, f"⚡ تم تفعيل وبدء جلسة تحضير مادة ({timetable_session.course.name}) بنجاح!")
    return redirect("attendance:session_detail_web", session_id=att_session.id)


@login_required
@require_POST
def cancel_scheduled_session_view(request, session_id):
    """Cancels a scheduled future/inactive attendance session."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك.")
        return redirect("dashboard")

    att_session = _manageable_attendance_session(request.user, session_id)
    if request.user.is_teacher() and att_session.session.teacher.user != request.user and att_session.created_by != request.user:
        messages.error(request, "غير مصرح لك بإلغاء هذه الجلسة.")
        return redirect("dashboard")

    course_name = att_session.session.course.name
    date_str = str(att_session.date)
    att_session.delete()

    messages.success(request, f"تم إلغاء المحاضرة المجدولة لمادة ({course_name}) بتاريخ {date_str} بنجاح.")
    return redirect(request.META.get("HTTP_REFERER", "dashboard"))


@login_required
@require_POST
def delete_attendance_session_view(request, session_id):
    """Deletes any attendance session and its records."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك.")
        return redirect("dashboard")

    att_session = _manageable_attendance_session(request.user, session_id)

    course_name = att_session.session.course.name if att_session.session and att_session.session.course else "المحاضرة"
    date_str = str(att_session.date)
    att_session.delete()

    messages.success(request, f"تم حذف جلسة التحضير لمادة ({course_name}) بتاريخ {date_str} بنجاح.")
    return redirect(request.META.get("HTTP_REFERER", "dashboard"))


@login_required
@require_POST
def bulk_delete_sessions_view(request):
    """Bulk deletes selected attendance sessions."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك.")
        return redirect("dashboard")
    if request.method == "POST":
        session_ids = request.POST.getlist("selected_sessions")
        if not session_ids:
            raw = request.POST.get("selected_sessions_str", "")
            session_ids = [s.strip() for s in raw.split(",") if s.strip()]
        valid_ids = [int(sid) for sid in session_ids if sid.isdigit()]
        if valid_ids:
            qs = _manageable_attendance_sessions(request.user).filter(
                id__in=valid_ids
            )
            cnt = qs.count()
            qs.delete()
            messages.success(request, f"تم حذف {cnt} جلسة تحضير بنجاح.")
        else:
            messages.warning(request, "لم يتم تحديد أي جلسة للحذف.")
    return redirect(request.META.get("HTTP_REFERER", "dashboard"))


@login_required
@require_POST
def delete_timetable_session_view(request, session_id):
    """Deletes a weekly timetable Session schedule."""
    if not (request.user.is_teacher() or request.user.is_super_admin() or request.user.is_institution_admin()):
        messages.error(request, "غير مصرح لك.")
        return redirect("dashboard")

    from apps.academics.models import Session
    sess = get_object_or_404(
        _manageable_timetable_sessions(request.user), id=session_id
    )
    cname = f"{sess.course.name} - شعبة {sess.class_section.name} ({sess.get_day_of_week_display()})"
    sess.delete()
    messages.success(request, f"تم حذف الحصة الأسبوعية ({cname}) بنجاح.")
    return redirect(request.META.get("HTTP_REFERER", "dashboard"))
