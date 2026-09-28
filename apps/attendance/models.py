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
        unique_together = ("session", "date")

    def __str__(self):
        return f"جلسة {self.session} بتاريخ {self.date}"


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
