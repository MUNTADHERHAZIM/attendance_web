from django.db import models
from django.conf import settings


class Notification(models.Model):
    class Types(models.TextChoices):
        ABSENCE = "ABSENCE", "إشعار غياب"
        LATE = "LATE", "إشعار تأخر"
        SESSION_OPEN = "SESSION_OPEN", "فتح جلسة تحضير"
        SESSION_CLOSE = "SESSION_CLOSE", "إغلاق جلسة تحضير"
        SYSTEM = "SYSTEM", "إشعار نظام"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name="المستلم",
    )
    notification_type = models.CharField(
        max_length=20,
        choices=Types.choices,
        default=Types.SYSTEM,
        verbose_name="نوع الإشعار",
    )
    title = models.CharField(max_length=200, verbose_name="العنوان")
    message = models.TextField(verbose_name="نص الإشعار")
    is_read = models.BooleanField(default=False, verbose_name="مقروء")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")
    read_at = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ القراءة")

    class Meta:
        verbose_name = "إشعار"
        verbose_name_plural = "الإشعارات"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read"]),
        ]

    def __str__(self):
        return f"[{self.get_notification_type_display()}] → {self.recipient}: {self.title}"

    def mark_as_read(self):
        from django.utils import timezone
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at"])
