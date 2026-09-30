#!/bin/bash
cd ~/attendance_web

# حفظ احتياطي لقاعدة البيانات
if [ -f "db.sqlite3" ]; then
    cp db.sqlite3 /tmp/db_backup_$(date +%Y%m%d_%H%M%S).sqlite3
fi

# منع git من حذف db.sqlite3
git update-index --skip-worktree db.sqlite3 2>/dev/null || true
git pull origin main

# استعادة قاعدة البيانات إذا اختفت
if [ ! -f "db.sqlite3" ] || [ ! -s "db.sqlite3" ]; then
    LATEST=$(ls -t /tmp/db_backup_*.sqlite3 2>/dev/null | head -1)
    if [ -n "$LATEST" ]; then cp "$LATEST" db.sqlite3; fi
fi

python manage.py migrate --settings=config.settings_pythonanywhere
python manage.py collectstatic --noinput --settings=config.settings_pythonanywhere
echo "Done! Press Reload in PythonAnywhere Web tab."
