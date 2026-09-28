from django.db import models
from django.conf import settings

class AuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="المستخدم")
    action = models.CharField(max_length=255, verbose_name="العملية")
    ip_address = models.GenericIPAddressField(blank=True, null=True, verbose_name="عنوان الـ IP")
    user_agent = models.TextField(blank=True, null=True, verbose_name="معلومات المتصفح/الجهاز")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="طابع وقت العملية")
    details = models.JSONField(blank=True, null=True, verbose_name="تفاصيل إضافية")

    class Meta:
        verbose_name = "سجل العمليات والتدقيق"
        verbose_name_plural = "سجلات العمليات والتدقيق"
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.user} - {self.action} ({self.timestamp})"


class SystemSetting(models.Model):
    key = models.CharField(max_length=100, unique=True, verbose_name="رمز الإعداد")
    value = models.TextField(verbose_name="قيمة الإعداد")
    description = models.CharField(max_length=255, blank=True, verbose_name="وصف الإعداد")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="تاريخ آخر تعديل")

    class Meta:
        verbose_name = "إعداد نظام (كود الأستاذ)"
        verbose_name_plural = "إعدادات النظام وأكواد التحقق"

    def __str__(self):
        return f"{self.description or self.key}: {self.value}"

    @classmethod
    def get_teacher_code(cls):
        obj, _ = cls.objects.get_or_create(
            key="TEACHER_VERIFICATION_CODE",
            defaults={
                "value": "EDU2026",
                "description": "كود التحقق الأكاديمي لتسجيل الكادر التعليمي (يمكن للادمن تغييره في أي وقت)"
            }
        )
        return obj.value.strip()
