from django.db import models

class Institution(models.Model):
    name = models.CharField(max_length=150, verbose_name="اسم المؤسسة")
    logo = models.ImageField(upload_to="institutions/", blank=True, null=True, verbose_name="الشعار")
    address = models.TextField(blank=True, null=True, verbose_name="العنوان")
    
    # WiFi / Subnet restriction config for physical attendance
    allowed_wifi_ssid = models.CharField(max_length=100, blank=True, null=True, verbose_name="اسم شبكة الـ WiFi المسموحة (SSID)")
    allowed_wifi_bssid = models.CharField(
        max_length=255, 
        blank=True, 
        null=True, 
        help_text="عناوين MAC لنقاط الاتصال مفرقة بفاصلة (مثال: 00:0a:95:9d:68:16)", 
        verbose_name="BSSID مسموح"
    )
    allowed_ip_subnet = models.CharField(
        max_length=50, 
        blank=True, 
        null=True, 
        help_text="مثال: 192.168.4.0/24 أو 10.0.0.0/16", 
        verbose_name="نطاق الـ IP الداخلي المسموح (Subnet)"
    )
    
    # Optional Geofencing
    latitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True, verbose_name="خط العرض")
    longitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True, verbose_name="خط الطول")
    radius_meters = models.PositiveIntegerField(default=100, help_text="نطاق السماح الجغرافي بالمتر", verbose_name="قطر النطاق الجغرافي")

    class Meta:
        verbose_name = "المؤسسة التعليمية"
        verbose_name_plural = "المؤسسات التعليمية"

    def __str__(self):
        return self.name


class AcademicYear(models.Model):
    name = models.CharField(max_length=20, verbose_name="العام الدراسي")  # e.g., "2025/2026"
    start_date = models.DateField(verbose_name="تاريخ البدء")
    end_date = models.DateField(verbose_name="تاريخ الانتهاء")
    is_active = models.BooleanField(default=False, verbose_name="نشط")

    class Meta:
        verbose_name = "العام الدراسي"
        verbose_name_plural = "الأعوام الدراسية"

    def __str__(self):
        return self.name


class Semester(models.Model):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="semesters", verbose_name="العام الدراسي")
    name = models.CharField(max_length=50, verbose_name="الفصل الدراسي")  # e.g., "الفصل الأول"
    start_date = models.DateField(verbose_name="تاريخ البدء")
    end_date = models.DateField(verbose_name="تاريخ الانتهاء")
    is_active = models.BooleanField(default=False, verbose_name="نشط")

    class Meta:
        verbose_name = "الفصل الدراسي"
        verbose_name_plural = "الفصول الدراسية"

    def __str__(self):
        return f"{self.name} - {self.academic_year.name}"


class Department(models.Model):
    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="departments", verbose_name="المؤسسة")
    name = models.CharField(max_length=100, verbose_name="اسم القسم")
    code = models.CharField(max_length=20, blank=True, null=True, verbose_name="رمز القسم")

    class Meta:
        verbose_name = "القسم"
        verbose_name_plural = "الأقسام"

    def __str__(self):
        return f"{self.name} ({self.institution.name})"


class ClassSection(models.Model):
    class Shifts(models.TextChoices):
        MORNING = "MORNING", "صباحي"
        EVENING = "EVENING", "مسائي"

    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="sections", verbose_name="القسم")
    name = models.CharField(max_length=50, verbose_name="اسم الشعبة/الفصل")  # e.g., "الشعبة أ"
    level = models.CharField(max_length=50, verbose_name="المستوى/الصف")  # e.g., "السنة الأولى"
    shift = models.CharField(
        max_length=10,
        choices=Shifts.choices,
        default=Shifts.MORNING,
        verbose_name="نوع الدراسة (صباحي/مسائي)"
    )

    class Meta:
        verbose_name = "الفصل الدراسي/الشعبة"
        verbose_name_plural = "الفصول الدراسية/الشعب"

    def __str__(self):
        return f"{self.level} - {self.name} ({self.get_shift_display()}) ({self.department.name})"


class Course(models.Model):
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="courses", verbose_name="القسم")
    name = models.CharField(max_length=150, verbose_name="اسم المادة")
    code = models.CharField(max_length=20, verbose_name="رمز المادة")

    class Meta:
        verbose_name = "المادة الدراسية"
        verbose_name_plural = "المواد الدراسية"
        unique_together = ("department", "code")

    def __str__(self):
        return f"{self.name} ({self.code})"


class Session(models.Model):
    class WeekDays(models.IntegerChoices):
        SATURDAY = 0, "السبت"
        SUNDAY = 1, "الأحد"
        MONDAY = 2, "الإثنين"
        TUESDAY = 3, "الثلاثاء"
        WEDNESDAY = 4, "الأربعاء"
        THURSDAY = 5, "الخميس"
        FRIDAY = 6, "الجمعة"

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="sessions", verbose_name="المادة")
    class_section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name="sessions", verbose_name="الشعبة")
    teacher = models.ForeignKey("accounts.TeacherProfile", on_delete=models.CASCADE, related_name="sessions", verbose_name="المعلم")
    day_of_week = models.IntegerField(choices=WeekDays.choices, verbose_name="اليوم")
    start_time = models.TimeField(verbose_name="وقت البدء")
    end_time = models.TimeField(verbose_name="وقت الانتهاء")
    shift = models.CharField(
        max_length=10,
        choices=ClassSection.Shifts.choices,
        default=ClassSection.Shifts.MORNING,
        verbose_name="نوع الدراسة"
    )
    room = models.CharField(max_length=50, blank=True, null=True, verbose_name="القاعة/الغرفة")

    class Meta:
        verbose_name = "الحصة / المحاضرة"
        verbose_name_plural = "الحصص / المحاضرات"

    def __str__(self):
        return f"{self.course.name} - {self.class_section} ({self.get_day_of_week_display()} {self.start_time}-{self.end_time})"
