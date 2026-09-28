"""
=======================================================================
ملف WSGI الخاص بـ PythonAnywhere
=======================================================================
النسخ إلى PythonAnywhere:
    1. انسخ محتوى هذا الملف إلى مربع "WSGI configuration file"
       في لوحة تحكم PythonAnywhere → Web → Code
    2. غيّر جميع القيم المحاطة بـ < > بقيمك الفعلية.
    3. اضغط "Reload" لإعادة تشغيل التطبيق.
=======================================================================
"""

import sys
import os

# ─── 1. مسار المشروع ──────────────────────────────────────────────────
# غيّر "yourusername" إلى اسم مستخدمك على PythonAnywhere
# غيّر "attendance" إلى اسم مجلد مشروعك (إذا كان مختلفاً)
PROJECT_PATH = "/home/yourusername/attendance"

if PROJECT_PATH not in sys.path:
    sys.path.insert(0, PROJECT_PATH)

# ─── 2. ملف البيئة (.env) ────────────────────────────────────────────
# تأكد من وجود ملف .env في مجلد المشروع يحتوي على:
#   SECRET_KEY, DEBUG=False, ALLOWED_HOSTS, DATABASE_URL
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings_pythonanywhere")

# ─── 3. تفعيل البيئة الافتراضية (virtualenv) ─────────────────────────
# PythonAnywhere ينشئ virtualenv تلقائياً في:
#   /home/yourusername/.virtualenvs/attendance_env/
# إذا كنت تستخدم venv يدوياً، عدّل المسار أدناه:
VENV_PATH = "/home/yourusername/.virtualenvs/attendance_env/lib/python3.12/site-packages"
if os.path.exists(VENV_PATH) and VENV_PATH not in sys.path:
    sys.path.insert(0, VENV_PATH)

# ─── 4. تشغيل تطبيق Django WSGI ─────────────────────────────────────
from django.core.wsgi import get_wsgi_application  # noqa: E402
application = get_wsgi_application()
