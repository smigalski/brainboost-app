from django.db import migrations, models
import django.db.models.deletion
import core.models
from django.core.validators import FileExtensionValidator


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_lesson_subject'),
    ]

    operations = [
        migrations.CreateModel(
            name='LearningMaterial',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('task', 'Aufgabe'), ('solution', 'Lösung')], max_length=20)),
                ('file', models.FileField(upload_to=core.models.material_upload_path, validators=[FileExtensionValidator(allowed_extensions=['pdf', 'png', 'jpg', 'jpeg', 'docx'])])),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='materials', to='core.studentprofile')),
                ('uploaded_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='materials', to='core.tutorprofile')),
            ],
            options={
                'verbose_name': 'Material',
                'verbose_name_plural': 'Materialien',
                'ordering': ['-uploaded_at'],
            },
        ),
    ]
