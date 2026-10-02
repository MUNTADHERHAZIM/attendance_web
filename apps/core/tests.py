from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils.translation import gettext, override
import json


class LanguageSwitchTests(TestCase):
    def test_pwa_manifest_and_offline_page_follow_selected_language(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
        manifest_response = self.client.get(reverse("manifest_json"), HTTP_HOST="localhost")
        manifest = json.loads(manifest_response.content)
        self.assertEqual(manifest["lang"], "en")
        self.assertEqual(manifest["dir"], "ltr")
        self.assertIn("Smart Attendance", manifest["name"])
        self.assertIn("no-store", manifest_response["Cache-Control"])

        offline_response = self.client.get("/offline/?lang=en", HTTP_HOST="localhost")
        self.assertContains(offline_response, 'lang="en" dir="ltr"')
        self.assertContains(offline_response, "Network connection lost")

        arabic_response = self.client.get("/offline/?lang=ar", HTTP_HOST="localhost")
        self.assertContains(arabic_response, 'lang="ar" dir="rtl"')
        self.assertContains(arabic_response, "أنت تعمل في وضع عدم الاتصال")

    def test_language_switch_persists_and_updates_login_direction(self):
        response = self.client.post(
            reverse("set_language"),
            {"language": "en", "next": "/login/"},
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "en")

        response = self.client.get("/login/", HTTP_HOST="localhost")
        content = response.content.decode()
        self.assertContains(response, 'lang="en" dir="ltr"')
        self.assertIn("Welcome back", content)

        response = self.client.post(
            reverse("set_language"),
            {"language": "ar", "next": "/login/"},
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.get("/login/", HTTP_HOST="localhost")
        self.assertContains(response, 'lang="ar" dir="rtl"')
        self.assertContains(response, "مرحباً بعودتك")

    def test_english_translations_for_teacher_pages_and_risk_radar(self):
        translations = {
            "إعداد وبدء جلسة تحضير ذكية": "Set up and start a smart attendance session",
            "المادة الدراسية": "Course",
            "اليوم الأسبوعي للمحاضرة": "Class day of the week",
            "مدة صلاحية التحضير": "Attendance window",
            "بدء المحاضرة وتوليد شاشة الـ QR الذكية فوراً": "Start class and generate the smart QR screen",
            "إدارة طلابي والشعب الدراسية": "Manage my students and sections",
            "حذف الطلاب المحددين": "Delete selected students",
            "حفظ وإلحاق الطالب": "Save and enroll student",
            "تقارير الحضور وسجل الغيابات الأكاديمي": "Attendance reports and academic absence record",
            "تصدير (Excel)": "Export (Excel)",
            "الفترة الزمنية للتقرير:": "Report period:",
            "رادار الخطر الأكاديمي والإنذار المبكر": "Academic risk and early-warning radar",
        }

        with override("en"):
            for source, expected in translations.items():
                with self.subTest(source=source):
                    self.assertEqual(gettext(source), expected)
