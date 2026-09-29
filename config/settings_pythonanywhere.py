"""
=======================================================================
إعدادات الإنتاج الخاصة بـ PythonAnywhere — نسخة محسَّنة للأداء
=======================================================================
"""

import os
import logging.handlers
from .settings import *  # noqa: F401, F403

# =====================================================================
# 1. وضع الإنتاج
# =====================================================================
DEBUG = False

# =====================================================================
# 2. INSTALLED_APPS — إزالة daphne وإضافة whitenoise
# =====================================================================
INSTALLED_APPS = [app for app in INSTALLED_APPS if app != "daphne"]  # noqa: F405
if "whitenoise.runserver_nostatic" not in INSTALLED_APPS:
    INSTALLED_APPS = ["whitenoise.runserver_nostatic"] + INSTALLED_APPS  # noqa: F405

# =====================================================================
# 3. Middleware — WhiteNoise بعد SecurityMiddleware مباشرةً
# =====================================================================
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.AuditLogMiddleware",
]

# =====================================================================
# 4. WhiteNoise — ضغط وcache للملفات الثابتة بأمان عالي
# =====================================================================
STATICFILES_STORAGE = "whitenoise.storage.CompressedStaticFilesStorage"
WHITENOISE_MANIFEST_STRICT = False
WHITENOISE_MAX_AGE = 31536000
WHITENOISE_SKIP_COMPRESS_EXTENSIONS = [
    "jpg", "jpeg", "png", "gif", "webp", "ico",
    "woff", "woff2", "ttf", "eot",
]

# =====================================================================
# 5. Templates — Cached Loader (يُخزِّن HTML في الذاكرة)
# =====================================================================
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],  # noqa: F405
        "APP_DIRS": False,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
            "loaders": [
                (
                    "django.template.loaders.cached.Loader",
                    [
                        "django.template.loaders.filesystem.Loader",
                        "django.template.loaders.app_directories.Loader",
                    ],
                )
            ],
        },
    },
]

# =====================================================================
# 6. Sessions
# =====================================================================
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 86400 * 7
SESSION_SAVE_EVERY_REQUEST = False

# =====================================================================
# 7. قاعدة البيانات — SQLite متوافقة 100% مع نظام ملفات PythonAnywhere (NFS)
# =====================================================================
DATABASES["default"]["OPTIONS"] = {  # noqa: F405
    "timeout": 60,
    "init_command": (
        "PRAGMA journal_mode=DELETE;"
        "PRAGMA synchronous=NORMAL;"
        "PRAGMA temp_store=MEMORY;"
    ),
    "check_same_thread": False,
}
CONN_MAX_AGE = 0

# =====================================================================
# 8. Cache — LocMemCache محسَّن
# =====================================================================
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "attendance-production",
        "TIMEOUT": 300,
        "OPTIONS": {"MAX_ENTRIES": 1000},
    }
}

# =====================================================================
# 9. Celery — Eager mode (بدون worker)
# =====================================================================
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BEAT_SCHEDULE = {}

# =====================================================================
# 10. Channel Layers — InMemory
# =====================================================================
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# =====================================================================
# 11. الامان — مُكيَّف لـ PythonAnywhere Reverse Proxy
# =====================================================================
SECURE_SSL_REDIRECT = False
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SILENCED_SYSTEM_CHECKS = ["security.W008", "security.W009"]

# =====================================================================
# 12. Logging — ملف دوار
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
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": _LOG_FILE,
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "formatter": "verbose",
            "encoding": "utf-8",
        },
    },
    "root": {"handlers": ["console", "file"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["console", "file"], "level": "WARNING", "propagate": False},
        "apps": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
    },
}
