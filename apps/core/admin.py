from django.contrib import admin
from django.contrib import messages
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.urls import path, reverse
from .models import AuditLog, SystemSetting


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("user", "action", "ip_address", "timestamp")
    search_fields = ("user__username", "action", "ip_address")
    list_filter = ("action", "timestamp")


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ("description", "key", "value", "updated_at")
    search_fields = ("key", "value", "description")
    list_editable = ("value",)
    change_form_template = "admin/core/systemsetting/change_form.html"

    def get_urls(self):
        custom_urls = [
            path(
                "captcha-toggle/",
                self.admin_site.admin_view(self.toggle_captcha),
                name="core_systemsetting_captcha_toggle",
            ),
        ]
        return custom_urls + super().get_urls()

    def toggle_captcha(self, request):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not request.user.is_superuser:
            self.message_user(request, "هذه العملية متاحة للمدير الأعلى فقط.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:core_systemsetting_changelist"))

        setting, _ = SystemSetting.objects.get_or_create(
            key="ENABLE_CAPTCHA",
            defaults={
                "value": "true",
                "description": "تفعيل/تعطيل رمز التحقق البصري (Captcha)",
            },
        )
        setting.value = "false" if setting.value.strip().lower() == "true" else "true"
        setting.save(update_fields=["value", "updated_at"])
        state = "مفعّل" if setting.value == "true" else "معطّل"
        self.message_user(request, f"تم {state} التحقق CAPTCHA بنجاح.")
        return HttpResponseRedirect(
            reverse("admin:core_systemsetting_change", args=[setting.pk])
        )
