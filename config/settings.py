import os
from pathlib import Path
import environ
from datetime import timedelta
from django.core.exceptions import ImproperlyConfigured

# Setup environ
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["127.0.0.1", "localhost"]),
)

BASE_DIR = Path(__file__).resolve().parent.parent

# Read .env file
environ.Env.read_env(os.path.join(BASE_DIR, ".env"))

# SECURITY — SECRET_KEY must always be set in .env, no insecure default
SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
ALLOWED_HOSTS = [host.strip() for host in ALLOWED_HOSTS if host.strip() and host.strip() != "*"]

# Auto-detect local network IP addresses for Hotspot / Zero-Internet mode
import socket

def get_local_ip_addresses():
    """Detect all active LAN/Wi-Fi/Hotspot IPv4 addresses on this machine."""
    ips = ["127.0.0.1", "localhost"]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(('10.255.255.255', 1))
        primary = s.getsockname()[0]
        s.close()
        if primary and not primary.startswith("127."):
            ips.append(primary)
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips

_LOCAL_IP_ADDRESSES = get_local_ip_addresses()
if DEBUG:
    ALLOWED_HOSTS = list(dict.fromkeys(ALLOWED_HOSTS + _LOCAL_IP_ADDRESSES))
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS must contain explicit host names or IP addresses; '*' is not allowed."
    )

CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:8001",
    "http://localhost:8001",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]
for _host in ALLOWED_HOSTS:
    if _host not in {"127.0.0.1", "localhost"} and not _host.startswith("127."):
        CSRF_TRUSTED_ORIGINS.append(f"https://{_host}")
for _ip in _LOCAL_IP_ADDRESSES:
    CSRF_TRUSTED_ORIGINS.extend([
        f"http://{_ip}:8001",
        f"http://{_ip}:8000",
        f"http://{_ip}",
    ])

# Custom CSRF Failure View
CSRF_FAILURE_VIEW = "apps.core.views.custom_csrf_failure_view"

# Application definition
INSTALLED_APPS = []

try:
    import daphne  # noqa: F401
    INSTALLED_APPS.append("daphne")
except ImportError:
    pass

INSTALLED_APPS.extend([
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Third-party apps
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    "django_celery_results",
    "django_celery_beat",
    "pwa",

    # Custom apps
    "apps.core",
    "apps.accounts",
    "apps.academics",
    "apps.attendance",
    "apps.notifications",
    "apps.reports",
])

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.AuditLogMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.system_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR}/db.sqlite3")
}

# Custom User Model
AUTH_USER_MODEL = "accounts.User"

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "ar"
LANGUAGES = [
    ("ar", "العربية"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Asia/Baghdad"
USE_I18N = True
USE_TZ = True
LANGUAGE_COOKIE_NAME = "django_language"
LANGUAGE_COOKIE_AGE = 60 * 60 * 24 * 365

# Static and Media files
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Auth redirects
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# =====================================================================
# CACHING — LocMemCache in dev (no Redis required), Redis in production
# =====================================================================
_REDIS_URL = env("REDIS_URL", default="")

if _REDIS_URL and not DEBUG:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _REDIS_URL,
            "OPTIONS": {
                "socket_connect_timeout": 2,  # fail fast
                "socket_timeout": 2,
            },
        }
    }
else:
    # Fast, zero-dependency cache for local development
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "attendance-dev",
        }
    }

# =====================================================================
# Authentication Backends (supports login by username OR email)
# =====================================================================
AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.EmailOrUsernameBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# =====================================================================
# Django REST Framework
# =====================================================================
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "20/minute",
        "user": "120/minute",
        "checkin": "120/minute",
    },
    "EXCEPTION_HANDLER": "apps.core.exceptions.custom_exception_handler",
}

# =====================================================================
# DRF Spectacular (OpenAPI docs)
# =====================================================================
SPECTACULAR_SETTINGS = {
    "TITLE": "نظام إدارة الحضور والغياب API",
    "DESCRIPTION": "توثيق واجهة برمجة التطبيقات (API) لنظام الحضور والغياب التعليمي أوفلاين/أونلاين",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

# =====================================================================
# Simple JWT
# =====================================================================
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=2),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# =====================================================================
# Channel Layers — InMemory in dev, Redis in production
# =====================================================================
if _REDIS_URL and not DEBUG:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [_REDIS_URL],
            },
        },
    }
else:
    # In-process channel layer — works without Redis (no WebSocket broadcast between workers)
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    }

# =====================================================================
# Celery
# =====================================================================
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "default"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes hard limit

# Celery Beat — Periodic Tasks Schedule
CELERY_BEAT_SCHEDULE = {
    "sync-offline-records-every-5-minutes": {
        "task": "apps.attendance.tasks.sync_offline_records",
        "schedule": 300,  # every 5 minutes
    },
    "close-expired-sessions-every-minute": {
        "task": "apps.attendance.tasks.close_expired_sessions",
        "schedule": 60,  # every minute
    },
    "send-weekly-report-every-sunday": {
        "task": "apps.notifications.tasks.send_weekly_report_task",
        "schedule": timedelta(days=7),
    },
}

# =====================================================================
# PWA Configuration (django-pwa & Standard Web App Manifest)
# =====================================================================
PWA_SERVICE_WORKER_PATH = os.path.join(BASE_DIR, "static", "sw.js")
PWA_APP_NAME = "نظام الحضور الذكي | Smart Attendance"
PWA_APP_SHORT_NAME = "الحضور الذكي"
PWA_APP_DESCRIPTION = "نظام الحضور والغياب الذكي والمستقل عن الإنترنت - مسح سريع، تقارير فورية، ومزامنة تلقائية"
PWA_APP_THEME_COLOR = "#4f46e5"
PWA_APP_BACKGROUND_COLOR = "#0f172a"
PWA_APP_DISPLAY = "standalone"
PWA_APP_SCOPE = "/"
PWA_APP_START_URL = "/"
PWA_APP_ORIENTATION = "portrait-primary"
PWA_APP_DIR = "rtl"
PWA_APP_LANG = "ar-SA"
PWA_APP_STATUS_BAR_COLOR = "black-translucent"

PWA_APP_ICONS = [
    {"src": "/static/images/pwa/icon-72x72.png", "sizes": "72x72", "type": "image/png", "purpose": "any"},
    {"src": "/static/images/pwa/icon-96x96.png", "sizes": "96x96", "type": "image/png", "purpose": "any"},
    {"src": "/static/images/pwa/icon-128x128.png", "sizes": "128x128", "type": "image/png", "purpose": "any"},
    {"src": "/static/images/pwa/icon-144x144.png", "sizes": "144x144", "type": "image/png", "purpose": "any"},
    {"src": "/static/images/pwa/icon-152x152.png", "sizes": "152x152", "type": "image/png", "purpose": "any"},
    {"src": "/static/images/pwa/icon-192x192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
    {"src": "/static/images/pwa/icon-384x384.png", "sizes": "384x384", "type": "image/png", "purpose": "any"},
    {"src": "/static/images/pwa/icon-512x512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
    {"src": "/static/images/pwa/icon-maskable-192x192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
    {"src": "/static/images/pwa/icon-maskable-512x512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    {"src": "/static/images/logo_192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/static/images/logo_512.png", "sizes": "512x512", "type": "image/png"},
]

PWA_APP_ICONS_APPLE = [
    {"src": "/static/images/pwa/apple-touch-icon.png", "sizes": "180x180"},
    {"src": "/static/images/pwa/icon-152x152.png", "sizes": "152x152"},
    {"src": "/static/images/logo_192.png", "sizes": "192x192"},
]

PWA_APP_SHORTCUTS = [
    {
        "name": "مسح رمز QR للتحضير",
        "short_name": "تحضير QR",
        "url": "/attendance/checkin/",
        "icons": [{"src": "/static/images/pwa/icon-96x96.png", "sizes": "96x96", "type": "image/png"}]
    },
    {
        "name": "محاضراتي وجلسات اليوم",
        "short_name": "المحاضرات",
        "url": "/attendance/teacher/sessions/",
        "icons": [{"src": "/static/images/pwa/icon-96x96.png", "sizes": "96x96", "type": "image/png"}]
    },
    {
        "name": "سجل الحضور والغياب",
        "short_name": "سجلي",
        "url": "/attendance/student/records/",
        "icons": [{"src": "/static/images/pwa/icon-96x96.png", "sizes": "96x96", "type": "image/png"}]
    },
    {
        "name": "لوحة التحكم الرئيسية",
        "short_name": "الرئيسية",
        "url": "/",
        "icons": [{"src": "/static/images/pwa/icon-96x96.png", "sizes": "96x96", "type": "image/png"}]
    }
]

# =====================================================================
# Email
# =====================================================================
EMAIL_HOST = env("EMAIL_HOST", default="smtp.gmail.com")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("EMAIL_HOST_USER", default="noreply@attendance.local")

# =====================================================================
# Security Headers (Production-Ready)
# =====================================================================
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# =====================================================================
# Logging
# =====================================================================
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
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
    },
}
