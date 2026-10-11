from django.db import migrations


# Its own migration, after 0007 has copied the column into `JobPhoto`, so the
# data copy and the drop never share a transaction (Postgres can refuse an
# ALTER TABLE on a table with deferred FK checks still pending).
class Migration(migrations.Migration):
    dependencies = [
        ('workorders', '0007_jobphoto'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='workorder',
            name='photo',
        ),
    ]
