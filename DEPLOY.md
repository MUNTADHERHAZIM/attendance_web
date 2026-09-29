# دليل النشر الشامل على PythonAnywhere

## ملخص سريع

| العنصر | القيمة |
|--------|--------|
| الخادم | PythonAnywhere (WSGI) |
| إعدادات الإنتاج | `config/settings_pythonanywhere.py` |
| ملف WSGI | `pythonanywhere_wsgi.py` |
| المتطلبات | `requirements-pythonanywhere.txt` |
| قاعدة البيانات | SQLite (الافتراضي) أو MySQL (مدفوع) |

---

## الخطوة 1 — تحضير الملفات محلياً

### 1.1 تأكد من عدم وجود ملفات حساسة في Git
```bash
# تأكد أن .gitignore يشمل هذه الملفات
echo ".env" >> .gitignore
echo ".env.production" >> .gitignore
echo "db.sqlite3" >> .gitignore
echo "logs/" >> .gitignore
```

### 1.2 جمع الملفات الثابتة
```bash
python manage.py collectstatic --noinput --settings=config.settings_pythonanywhere
```

---

## الخطوة 2 — رفع المشروع على PythonAnywhere

### الطريقة أ: عبر Git (موصى بها)
```bash
# على PythonAnywhere Bash console:
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git ~/attendance
cd ~/attendance
```

### الطريقة ب: رفع يدوي عبر ZIP
1. اضغط المشروع كـ ZIP (مع استثناء `.venv/`, `__pycache__/`, `db.sqlite3`)
2. ارفعه عبر **Files** في لوحة تحكم PythonAnywhere
3. فك الضغط: `unzip project.zip -d ~/attendance`

---

## الخطوة 3 — إعداد البيئة الافتراضية

```bash
# على PythonAnywhere Bash console:
cd ~/attendance

# إنشاء virtualenv
python3.12 -m venv .venv
source .venv/bin/activate

# تثبيت المتطلبات
pip install -r requirements-pythonanywhere.txt
```

---

## الخطوة 4 — إعداد ملف البيئة (.env)

```bash
# انسخ قالب الإنتاج
cp .env.production .env

# عدّل القيم (استخدم nano أو محرر PythonAnywhere)
nano .env
```

**القيم التي يجب تغييرها:**
```env
SECRET_KEY=<مفتاح جديد قوي — لا تستخدم المفتاح الافتراضي>
ALLOWED_HOSTS=yourusername.pythonanywhere.com
DATABASE_URL=sqlite:////home/yourusername/attendance/db.sqlite3
```

> لتوليد مفتاح سري جديد:
> ```bash
> python -c "import secrets; print(secrets.token_urlsafe(50))"
> ```

---

## الخطوة 5 — تشغيل Migrations

```bash
source .venv/bin/activate
python manage.py migrate --settings=config.settings_pythonanywhere
python manage.py createsuperuser --settings=config.settings_pythonanywhere
```

---

## الخطوة 6 — إعداد تطبيق الويب على PythonAnywhere

### 6.1 إنشاء Web App
1. اذهب إلى لوحة تحكم PythonAnywhere → **Web**
2. اضغط **Add a new web app**
3. اختر **Manual configuration** (وليس Django)
4. اختر **Python 3.12**

### 6.2 ضبط إعدادات الكود
في قسم **Code**:
| الحقل | القيمة |
|-------|--------|
| Source code | `/home/yourusername/attendance` |
| Working directory | `/home/yourusername/attendance` |
| WSGI configuration file | *(انظر 6.3)* |

### 6.3 ضبط ملف WSGI
1. اضغط على رابط ملف WSGI في لوحة التحكم
2. **امسح المحتوى الموجود بالكامل**
3. الصق محتوى `pythonanywhere_wsgi.py` من مشروعك
4. غيّر `yourusername` إلى اسمك الفعلي
5. احفظ

### 6.4 ضبط Virtualenv
في قسم **Virtualenv**:
```
/home/yourusername/attendance/.venv
```

---

## الخطوة 7 — إعداد الملفات الثابتة

في قسم **Static files** بلوحة التحكم، أضف:

| URL | Directory |
|-----|-----------|
| `/static/` | `/home/yourusername/attendance/staticfiles` |
| `/media/` | `/home/yourusername/attendance/media` |

```bash
# تأكد من جمع الملفات الثابتة
python manage.py collectstatic --noinput --settings=config.settings_pythonanywhere
```

---

## الخطوة 8 — إعادة التشغيل والتحقق

1. اضغط زر **Reload** الأخضر في لوحة تحكم Web
2. افتح الموقع: `https://yourusername.pythonanywhere.com`
3. تحقق من الأخطاء في: **Log files** → `error.log`

### فحص سريع للحالة
```bash
# تحقق من الإعدادات
python manage.py check --settings=config.settings_pythonanywhere --deploy

# تحقق من المهاجرات
python manage.py showmigrations --settings=config.settings_pythonanywhere
```

---

## استكشاف الأخطاء الشائعة

### خطأ: `ModuleNotFoundError: No module named 'config'`
**السبب:** مسار المشروع غير صحيح في ملف WSGI
**الحل:** تأكد أن `PROJECT_PATH` في `pythonanywhere_wsgi.py` يُشير إلى المجلد الصحيح

### خطأ: `DisallowedHost`
**السبب:** اسم النطاق غير مضاف في `ALLOWED_HOSTS`
**الحل:** أضف `yourusername.pythonanywhere.com` في ملف `.env`

### خطأ: Redirect Loop (Too Many Redirects)
**السبب:** `SECURE_SSL_REDIRECT=True` مع Reverse Proxy
**الحل:** هذا مُصلح بالفعل في `settings_pythonanywhere.py` (القيمة `False`)

### خطأ: الملفات الثابتة (CSS/JS) لا تُحمَّل
**السبب:** لم يُضبط مسار static files في لوحة التحكم
**الحل:** أضف `/static/` → `/home/yourusername/attendance/staticfiles` (الخطوة 7)

### خطأ: `ImproperlyConfigured: CELERY_BROKER_URL`
**السبب:** استخدام settings.py الأساسي بدلاً من settings_pythonanywhere.py
**الحل:** تأكد أن WSGI يُحمِّل `config.settings_pythonanywhere`

---

## ميزات تعمل / لا تعمل على PythonAnywhere المجاني

| الميزة | الحالة | ملاحظة |
|--------|--------|--------|
| Django WSGI | ✅ يعمل | الوضع الأساسي |
| قاعدة بيانات SQLite | ✅ يعمل | موصى به للبداية |
| الملفات الثابتة | ✅ يعمل | بعد ضبط Static Files |
| رفع الملفات (Media) | ✅ يعمل | بعد ضبط Media Files |
| إرسال البريد | ✅ يعمل | مع بيانات Gmail صحيحة |
| توليد QR Code | ✅ يعمل | |
| تصدير Excel/PDF | ✅ يعمل | |
| Django Admin | ✅ يعمل | |
| JWT Authentication | ✅ يعمل | |
| Celery Tasks | ⚡ Eager | تُنفَّذ فورياً بدون عامل |
| Celery Beat (دوري) | ❌ معطل | يحتاج Always-On Task مدفوع |
| WebSocket (Django Channels) | ⚠️ محدود | InMemory فقط، عامل واحد |
| Redis | ❌ غير متاح | الخطة المجانية |
| MySQL | 💰 مدفوع | متاح بمقابل مادي |

---

## تحديث التطبيق (Deployment Updates)

```bash
# على PythonAnywhere Bash console:
cd ~/attendance
git pull origin main

# تثبيت حزم جديدة (إذا وجدت)
source .venv/bin/activate
pip install -r requirements-pythonanywhere.txt

# تطبيق migrations جديدة (إذا وجدت)
python manage.py migrate --settings=config.settings_pythonanywhere

# تحديث الملفات الثابتة (إذا تغيرت)
python manage.py collectstatic --noinput --settings=config.settings_pythonanywhere

# إعادة تشغيل التطبيق عبر API أو لوحة التحكم
touch /var/www/yourusername_pythonanywhere_com_wsgi.py
```
