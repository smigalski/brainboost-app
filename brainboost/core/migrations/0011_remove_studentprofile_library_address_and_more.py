from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_alter_lesson_ort'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='studentprofile',
            name='library_address',
        ),
        migrations.RemoveField(
            model_name='studentprofile',
            name='library_latitude',
        ),
        migrations.RemoveField(
            model_name='studentprofile',
            name='library_longitude',
        ),
    ]
