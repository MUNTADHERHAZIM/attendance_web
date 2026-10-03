from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.translation import gettext_lazy as _
import uuid


class User(AbstractUser):
    class Roles(models.TextChoices):
        SUPER_ADMIN = "SUPER_ADMIN", _("مدير النظام")
        INSTITUTION_ADMIN = "INSTITUTION_ADMIN", _("مدير المؤسسة")
        TEACHER = "TEACHER", _("معلم / مشرف")
        STUDENT = "STUDENT", _("طالب")

    role = models.CharField(
        max_length=20,
        choices=Roles.choices,
        default=Roles.SUPER_ADMIN,
        verbose_name=_("الدور")
    )
    phone = models.CharField(max_length=20, blank=True, null=True, verbose_name="رقم الهاتف")
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True, verbose_name="الصورة الشخصية")

    class Meta:
        verbose_name = "المستخدم"
        verbose_name_plural = "المستخدمون"

    def __str__(self):
        return self.get_full_name() or self.username

    def is_super_admin(self):
        return self.role == self.Roles.SUPER_ADMIN or self.is_superuser

    def is_institution_admin(self):
        return self.role in [self.Roles.INSTITUTION_ADMIN, self.Roles.SUPER_ADMIN] or self.is_superuser

    def is_teacher(self):
        return self.role == self.Roles.TEACHER

    def is_student(self):
        return self.role == self.Roles.STUDENT

    def is_guardian(self):
        return False

    def get_profile(self):
        """Returns the role-specific profile object, or None if it doesn't exist."""
        if self.is_student():
            return getattr(self, "student_profile", None)
        elif self.is_teacher():
            return getattr(self, "teacher_profile", None)
        return None


class GuardianProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="guardian_profile")
    address = models.TextField(blank=True, null=True, verbose_name="العنوان")

    class Meta:
        verbose_name = "ملف ولي الأمر"
        verbose_name_plural = "ملفات أولياء الأمور"

    def __str__(self):
        return f"ولي أمر: {self.user.get_full_name() or self.user.username}"


class StudentProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="student_profile")
    student_id = models.CharField(max_length=50, unique=True, verbose_name="الرقم الجامعي/المدرسي")
    institution = models.ForeignKey(
        "academics.Institution",
        on_delete=models.CASCADE,
        related_name="students",
        verbose_name="المؤسسة"
    )
    # ✅ FIX: M2M relationship to link students to their class sections
    sections = models.ManyToManyField(
        "academics.ClassSection",
        related_name="students",
        blank=True,
        verbose_name="الشعب الدراسية المسجلة"
    )
    guardian = models.ForeignKey(
        GuardianProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
        verbose_name="ولي الأمر"
    )
    birth_date = models.DateField(null=True, blank=True, verbose_name="تاريخ الميلاد")
    study_shift = models.CharField(
        max_length=10,
        choices=[("MORNING", "صباحي"), ("EVENING", "مسائي")],
        default="MORNING",
        verbose_name="نوع الدراسة (صباحي/مسائي)"
    )
    rfid_card = models.CharField(
        max_length=50, unique=True, blank=True, null=True, verbose_name="بطاقة RFID/NFC"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ملف الطالب"
        verbose_name_plural = "ملفات الطلاب"

    def __str__(self):
        return f"طالب: {self.user.get_full_name() or self.user.username} ({self.student_id})"

    @property
    def absence_rate(self):
        """Calculates the overall absence rate percentage for this student."""
        total = self.attendance_records.count()
        if total == 0:
            return 0.0
        absences = self.attendance_records.filter(status="ABSENT").count()
        return round((absences / total) * 100, 1)


class TeacherProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="teacher_profile")
    teacher_id = models.CharField(max_length=50, unique=True, verbose_name="الرقم الوظيفي")
    institution = models.ForeignKey(
        "academics.Institution",
        on_delete=models.CASCADE,
        related_name="teachers",
        verbose_name="المؤسسة"
    )
    department = models.ForeignKey(
        "academics.Department",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teachers",
        verbose_name="القسم الأكاديمي"
    )
    specialization = models.CharField(max_length=100, blank=True, null=True, verbose_name="التخصص")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ملف المعلم"
        verbose_name_plural = "ملفات المعلمين"

    def __str__(self):
        return f"معلم: {self.user.get_full_name() or self.user.username}"
