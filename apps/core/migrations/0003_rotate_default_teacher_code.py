import secrets

from django.db import migrations


def rotate_known_default_codes(apps, schema_editor):
    SystemSetting = apps.get_model("core", "SystemSetting")
    SystemSetting.objects.filter(
        key="TEACHER_VERIFICATION_CODE",
        value__in=["EDU2026", "TEACHER2026", "FACULTY"],
    ).update(value=secrets.token_urlsafe(18).upper())


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_systemsetting"),
    ]

    operations = [
        migrations.RunPython(
            rotate_known_default_codes,
            migrations.RunPython.noop,
        ),
    ]
