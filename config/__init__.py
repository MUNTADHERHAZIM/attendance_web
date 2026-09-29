# استيراد Celery بشكل مشروط — يمنع ImportError إذا لم يكن Celery مثبتاً
# (مثلاً في بيئات الاختبار أو بعض إعدادات النشر)
try:
    from .celery import app as celery_app
    __all__ = ("celery_app",)
except ImportError:
    pass
