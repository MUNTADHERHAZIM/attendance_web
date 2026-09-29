#!/bin/bash
# =============================================================
# سكريبت النشر التلقائي على PythonAnywhere
# الاستخدام: bash deploy_pythonanywhere.sh
# =============================================================
# شغّل هذا السكريبت من Bash Console على PythonAnywhere
# بعد رفع/clone المشروع في ~/attendance
# =============================================================

set -e  # وقف السكريبت فوراً عند أي خطأ

# ─── اكتشاف مسار المشروع تلقائياً ────────────────────────
# يستخدم مجلد السكريبت نفسه — يعمل بغض النظر عن اسم المجلد
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"
MANAGE="$PYTHON $PROJECT_DIR/manage.py"
SETTINGS="config.settings_pythonanywhere"

echo "=================================================="
echo "  نشر نظام الحضور والغياب على PythonAnywhere"
echo "=================================================="
echo ""

# ─── 1. الانتقال إلى مجلد المشروع ─────────────────────────
echo "[1/7] الانتقال إلى مجلد المشروع..."
cd "$PROJECT_DIR"

# ─── 2. إنشاء/تحديث البيئة الافتراضية ─────────────────────
echo "[2/7] إعداد البيئة الافتراضية (venv)..."
# إذا كان venv موجوداً لكن pip مفقود (تالف) → احذفه وأعد إنشاءه
if [ -d "$VENV_DIR" ] && [ ! -f "$PIP" ]; then
    echo "      ⚠️  venv تالف (pip مفقود) — يتم إعادة الإنشاء..."
    rm -rf "$VENV_DIR"
fi

if [ ! -d "$VENV_DIR" ]; then
    python3.12 -m venv --system-site-packages "$VENV_DIR"
    echo "      ✅ تم إنشاء venv خفيف وسريع (مستفيد من مكتبات النظام)"
else
    echo "      ℹ️  venv موجود وصالح"
fi

# ─── 3. تثبيت/تحديث المتطلبات ──────────────────────────────
echo "[3/7] تثبيت المتطلبات الضرورية فقط..."
$PIP install -r requirements-pythonanywhere.txt --no-cache-dir --quiet
echo "      ✅ تم تثبيت جميع الحزم"

# ─── 4. التحقق من ملف .env ─────────────────────────────────
echo "[4/7] التحقق من ملف البيئة (.env)..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "      ⚠️  ملف .env غير موجود!"
    echo "      ⚠️  انسخ .env.production إلى .env وعدّل القيم:"
    echo "          cp .env.production .env && nano .env"
    exit 1
fi

# تحقق من أن SECRET_KEY تم تغييره
if grep -q "isstAQ9OQxdyQap\|pigIUJGS4Ra8" "$PROJECT_DIR/.env"; then
    echo "      ❌ خطأ: SECRET_KEY لم يتغير! يجب توليد مفتاح جديد:"
    echo "         python -c \"import secrets; print(secrets.token_urlsafe(50))\""
    exit 1
fi
echo "      ✅ ملف .env موجود ومُهيَّأ"

# ─── 5. تطبيق Migrations ────────────────────────────────────
echo "[5/7] تطبيق Migrations..."
$MANAGE migrate --settings="$SETTINGS" --noinput
echo "      ✅ تم تطبيق جميع Migrations"

# ─── 6. جمع الملفات الثابتة ─────────────────────────────────
echo "[6/7] جمع الملفات الثابتة (collectstatic)..."
$MANAGE collectstatic --settings="$SETTINGS" --noinput --clear
echo "      ✅ تم جمع الملفات الثابتة"

# ─── 7. فحص الإعدادات ───────────────────────────────────────
echo "[7/7] فحص إعدادات الإنتاج..."
$MANAGE check --settings="$SETTINGS" 2>&1 | grep -v "drf_spectacular" || true
echo "      ✅ الفحص مكتمل"

# ─── ملاحظة ختامية ──────────────────────────────────────────
echo ""
echo "=================================================="
echo "  ✅ النشر جاهز — أعِد تشغيل Web App الآن!"
echo "=================================================="
echo ""
echo "الخطوات الأخيرة في لوحة تحكم PythonAnywhere:"
echo "  1. Web → Reload"
echo "  2. تأكد من Static Files:"
echo "     /static/ → $PROJECT_DIR/staticfiles"
echo "     /media/  → $PROJECT_DIR/media"
echo "  3. افتح الموقع وتحقق من Error Log"
echo ""
