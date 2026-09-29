"""
=======================================================================
إعدادات الإنتاج الخاصة بـ PythonAnywhere
=======================================================================
الاستخدام:
    يُحمَّل هذا الملف تلقائياً بدلاً من settings.py عند ضبط:
        DJANGO_SETTINGS_MODULE=config.settings_pythonanywhere

    أو عبر ملف WSGI على PythonAnywhere (انظر pythonanywhere_wsgi.py).

الاستراتيجية:
    - يرث كل إعدادات settings.py الأساسية
    - يُعيد تعريف فقط ما يختلف في بيئة PythonAnywhere
    - يُعطِّل Daphne/Celery/Redis التي لا تعمل في الخطة المجانية
    - يُصلح مشكلة redirect loop مع SSL
=======================================================================
"""

import os
import logging.handlers
from .settings import *  # noqa: F401, F403

# =====================================================================
# 1. وضع الإنتاج الأساسي
# =====================================================================
DEBUG = False

# ─── يُقرأ من ملف .env على PythonAnywhere ────────────────────────────
# مثال: ALLOWED_HOSTS=yourusername.pythonanywhere.com
# تأكد من ضبطه في .env قبل النشر.

# =====================================================================
# 2. إزالة Daphne من INSTALLED_APPS (WSGI فقط على PythonAnywhere)
# =====================================================================
# PythonAnywhere يدعم WSGI فقط — Daphne (ASGI) غير مدعوم في الخطة المجانية.
# إزالته تمنع Django من محاولة تشغيل خادم ASGI وتفادي ImportError.
INSTALLED_APPS = [app for app in INSTALLED_APPS if app != "daphne"]  # noqa: F405

# =====================================================================
# 3. تعطيل Celery والمهام الدورية (لا يدعمها PythonAnywhere مجاناً)
# =====================================================================
# نُعيِّن broker إلى الذاكرة لمنع أي خطأ عند الاستيراد،
# مع تنفيذ المهام بشكل متزامن فوري (eager) بدلاً من الإرسال لعامل.
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = True       # تنفيذ فوري بدلاً من الإرسال للعامل
CELERY_TASK_EAGER_PROPAGATES = True   # إعادة رفع الاستثناءات في وضع eager
CELERY_BEAT_SCHEDULE = {}             # تعطيل جميع المهام الدورية

# =====================================================================
# 4. Channel Layers — InMemory (لا Redis متاح)
# =====================================================================
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# =====================================================================
# 5. التخزين المؤقت — LocMemCache (لا Redis)
# =====================================================================
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "attendance-production",
    }
}

# =====================================================================
# 6. إعدادات الأمان المُكيَّفة لـ PythonAnywhere
# =====================================================================
# PythonAnywhere يُنهي SSL عبر Reverse Proxy —
# تفعيل SECURE_SSL_REDIRECT يسبب redirect loop لأن الطلبات
# الداخلية تصل بـ HTTP وليس HTTPS.
# نستخدم SECURE_PROXY_SSL_HEADER بدلاً منه ونُسكت التحذير W008 بوعي تام.
SECURE_SSL_REDIRECT = False
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True   # يُصلح تحذير security.W021
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# تجاهل تحذير W008 بوعي (SECURE_SSL_REDIRECT معطل عمداً بسبب Proxy)
# تجاهل تحذير W009 بوعي (SECRET_KEY يُقرأ من .env — تأكد من تغييره)
SILENCED_SYSTEM_CHECKS = ["security.W008", "security.W009"]

# =====================================================================
# 7. Logging مُحسَّن للإنتاج مع ملف دوّار
# =====================================================================
_LOGS_DIR = os.path.join(BASE_DIR, "logs")  # noqa: F405
os.makedirs(_LOGS_DIR, exist_ok=True)
_LOG_FILE = os.path.join(_LOGS_DIR, "django.log")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] [{levelname}] {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": _LOG_FILE,
            "maxBytes": 5 * 1024 * 1024,  # 5 MB
            "backupCount": 3,
            "formatter": "verbose",
            "encoding": "utf-8",
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": "WARNING",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

# =====================================================================
# 8. قاعدة البيانات
# =====================================================================
# SQLite هو الافتراضي — يُقرأ المسار من DATABASE_URL في .env
# لاستخدام MySQL (مدفوع على PythonAnywhere) علّق السطر أعلاه وفعّل:
#
# DATABASES = {
#     "default": {
#         "ENGINE": "django.db.backends.mysql",
#         "NAME": "yourusername$attendance_db",
#         "USER": "yourusername",
#         "PASSWORD": "your_mysql_password",
#         "HOST": "yourusername.mysql.pythonanywhere-services.com",
#         "OPTIONS": {
#             "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
#             "charset": "utf8mb4",
#         },
#     }
# }
