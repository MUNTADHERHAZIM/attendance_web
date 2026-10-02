from django.db import models
from django.conf import settings
import uuid

class AttendanceSession(models.Model):
    session = models.ForeignKey("academics.Session", on_delete=models.CASCADE, related_name="attendance_sessions", verbose_name="الحصة/المحاضرة")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, verbose_name="مُنشئ الجلسة")
    date = models.DateField(verbose_name="التاريخ")
    qr_salt = models.UUIDField(default=uuid.uuid4, verbose_name="ملح التشفير للـ QR")
    start_time = models.DateTimeField(auto_now_add=True, verbose_name="وقت البدء")
    end_time = models.DateTimeField(verbose_name="وقت الانتهاء")
    is_active = models.BooleanField(default=True, verbose_name="نشطة")
    requires_wifi = models.BooleanField(default=False, verbose_name="تطلب التحقق من WiFi")
    requires_geofence = models.BooleanField(default=False, verbose_name="تطلب التحقق من الموقع الجغرافي")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True, verbose_name="خط عرض المحاضرة")
    longitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True, verbose_name="خط طول المحاضرة")
    radius_meters = models.PositiveIntegerField(default=50, verbose_name="نطاق السماح الجغرافي بالمتر")
    quick_otp = models.CharField(max_length=6, blank=True, null=True, verbose_name="رمز التحضير السريع (OTP)")
    allowed_ip_subnet = models.CharField(max_length=50, blank=True, null=True, verbose_name="نطاق الـ IP المؤقت / Hotspot Subnet")
    allowed_wifi_ssid = models.CharField(max_length=100, blank=True, null=True, verbose_name="اسم الـ WiFi المؤقت / Hotspot SSID")
    topic = models.CharField(max_length=200, blank=True, null=True, verbose_name="موضوع أو عنوان المحاضرة")
    lecture_type = models.CharField(max_length=50, blank=True, default="نظري", verbose_name="نوع المحاضرة")
    is_frozen_qr = models.BooleanField(default=False, verbose_name="تثبيت كود الـ QR (رمز ثابت)")
    shift = models.CharField(
        max_length=10,
        choices=[("MORNING", "صباحي"), ("EVENING", "مسائي")],
        default="MORNING",
        verbose_name="نوع الدراسة (صباحي/مسائي)"
    )

    class Meta:
        verbose_name = "جلسة تحضير"
        verbose_name_plural = "جلسات التحضير"

    def __str__(self):
        return f"جلسة {self.session} بتاريخ {self.date}"

    @property
    def present_count(self):
        return self.records.filter(status__in=[AttendanceRecord.Statuses.PRESENT, AttendanceRecord.Statuses.LATE]).count()

    @property
    def absent_count(self):
        return self.records.filter(status=AttendanceRecord.Statuses.ABSENT).count()

    @property
    def late_count(self):
        return self.records.filter(status=AttendanceRecord.Statuses.LATE).count()

    @property
    def total_count(self):
        sec_count = 0
        if self.session and self.session.class_section:
            sec_count = self.session.class_section.students.count()
        rec_count = self.records.count()
        return max(sec_count, rec_count)

    @property
    def attendance_rate(self):
        tot = self.total_count
        if tot == 0:
            return 0.0
        return round((self.present_count / tot * 100), 1)


class AttendanceRecord(models.Model):
    class Statuses(models.TextChoices):
        PRESENT = "PRESENT", "حاضر"
        ABSENT = "ABSENT", "غائب"
        LATE = "LATE", "متأخر"
        EXCUSED = "EXCUSED", "غائب بعذر"

    class Methods(models.TextChoices):
        QR = "QR", "رمز QR الديناميكي"
        STATIC_QR = "STATIC_QR", "بطاقة الطالب (QR ثابت)"
        WIFI = "WIFI", "شبكة WiFi"
        RFID = "RFID", "بطاقة RFID / NFC"
        MANUAL = "MANUAL", "يدوي (من المعلم)"
        OTP = "OTP", "رمز التحضير السريع (OTP)"
        OFFLINE_MANUAL = "OFFLINE_MANUAL", "بدون إنترنت (تسجيل فوري)"

    student = models.ForeignKey("accounts.StudentProfile", on_delete=models.CASCADE, related_name="attendance_records", verbose_name="الطالب")
    attendance_session = models.ForeignKey(AttendanceSession, on_delete=models.CASCADE, related_name="records", verbose_name="جلسة التحضير")
    status = models.CharField(max_length=20, choices=Statuses.choices, default=Statuses.ABSENT, verbose_name="الحالة")
    method = models.CharField(max_length=20, choices=Methods.choices, default=Methods.MANUAL, verbose_name="طريقة التحضير")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="طابع وقت الحضور")
    ip_address = models.GenericIPAddressField(blank=True, null=True, verbose_name="عنوان الـ IP للجهاز")
    device_user_agent = models.TextField(blank=True, null=True, verbose_name="معلومات المتصفح/الجهاز")
    
    # Audit trail for modifications
    updated_at = models.DateTimeField(auto_now=True, verbose_name="تاريخ آخر تعديل")
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name="modified_records",
        verbose_name="عدله المستخدم"
    )
    notes = models.TextField(blank=True, null=True, verbose_name="ملاحظات / سبب التعديل اليدوي")

    class Meta:
        verbose_name = "سجل حضور الطالب"
        verbose_name_plural = "سجلات حضور الطلاب"
        unique_together = ("student", "attendance_session")

    def __str__(self):
        return f"{self.student} - {self.attendance_session.session.course.name} ({self.get_status_display()})"


class OfflineAttendanceSubmission(models.Model):
    class Statuses(models.TextChoices):
        PENDING = "PENDING", "بانتظار المراجعة"
        APPROVED = "APPROVED", "تمت الموافقة"
        REJECTED = "REJECTED", "مرفوض"

    attendance_session = models.ForeignKey(
        AttendanceSession,
        on_delete=models.CASCADE,
        related_name="offline_submissions",
        verbose_name="جلسة التحضير",
    )
    student_name = models.CharField(max_length=150, verbose_name="اسم الطالب")
    device_id = models.CharField(max_length=128, blank=True, verbose_name="معرف الجهاز")
    queue_id = models.CharField(max_length=64, unique=True, verbose_name="معرف طلب الجهاز")
    token_error = models.CharField(max_length=255, verbose_name="سبب تعذر التحقق")
    status = models.CharField(
        max_length=10,
        choices=Statuses.choices,
        default=Statuses.PENDING,
        db_index=True,
        verbose_name="حالة المراجعة",
    )
    student = models.ForeignKey(
        "accounts.StudentProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="offline_attendance_submissions",
        verbose_name="ملف الطالب المعتمد",
    )
    received_at = models.DateTimeField(auto_now_add=True, verbose_name="وقت استلام الخادم")
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name="وقت المراجعة")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_offline_attendance_submissions",
        verbose_name="راجع الطلب",
    )

    class Meta:
        verbose_name = "طلب حضور غير متصل للمراجعة"
        verbose_name_plural = "طلبات الحضور غير المتصلة للمراجعة"
        ordering = ["received_at"]

    def __str__(self):
        return f"{self.student_name} - {self.attendance_session} ({self.get_status_display()})"


class SyncQueue(models.Model):
    class Actions(models.TextChoices):
        CREATE = "CREATE", "إضافة"
        UPDATE = "UPDATE", "تعديل"
        DELETE = "DELETE", "حذف"

    record_id = models.PositiveIntegerField(verbose_name="معرف السجل")
    model_name = models.CharField(max_length=50, default="AttendanceRecord", verbose_name="اسم النموذج")
    action = models.CharField(max_length=10, choices=Actions.choices, verbose_name="العملية")
    payload = models.JSONField(verbose_name="محتوى البيانات (JSON)")
    is_synced = models.BooleanField(default=False, verbose_name="تمت المزامنة")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء في الطابور")
    synced_at = models.DateTimeField(blank=True, null=True, verbose_name="تاريخ المزامنة للسحابة")

    class Meta:
        verbose_name = "سجل المزامنة"
        verbose_name_plural = "سجلات طابور المزامنة"
        ordering = ["created_at"]

    def __str__(self):
        return f"مزامنة {self.model_name} #{self.record_id} ({self.get_action_display()}) - {self.is_synced}"
