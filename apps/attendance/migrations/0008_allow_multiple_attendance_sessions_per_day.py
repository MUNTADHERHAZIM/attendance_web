from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0007_offlineattendancesubmission"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="attendancesession",
            unique_together=set(),
        ),
    ]
