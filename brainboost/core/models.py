from datetime import datetime
from math import radians, sin, cos, sqrt, atan2

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator, FileExtensionValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class CustomUser(AbstractUser):
    class Roles(models.TextChoices):
        STUDENT = "student", "Schüler/Student"
        PARENT = "parent", "Parent"
        TUTOR = "tutor", "Tutor"

    role = models.CharField(
        max_length=20,
        choices=Roles.choices,
        default=Roles.STUDENT,
    )

    def __str__(self) -> str:
        return f"{self.username} ({self.get_role_display()})"


class ParentProfile(models.Model):
    class Meta:
        verbose_name = "Elternteil"
        verbose_name_plural = "Eltern"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="parent_profile",
    )

    def __str__(self) -> str:
        return f"Parent: {self.user.username}"


class StudentProfile(models.Model):
    address = models.CharField(max_length=255, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    zoom_link = models.URLField(blank=True)

    class Meta:
        verbose_name = "Schüler/Student"
        verbose_name_plural = "Schüler/Studenten"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_profile",
    )
    parents = models.ManyToManyField(
        ParentProfile,
        related_name="students",
        blank=True,
    )

    def __str__(self) -> str:
        return f"Student/Schüler: {self.user.username}"


class TutorProfile(models.Model):
    address = models.CharField(max_length=255, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    class Meta:
        verbose_name = "Tutor"
        verbose_name_plural = "Tutoren"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tutor_profile",
    )

    def __str__(self) -> str:
        return f"Tutor: {self.user.username}"


class Lesson(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "geplant"
        COMPLETED = "completed", "vorbei"
        CANCELLED = "cancelled", "storniert"

    class Ort(models.TextChoices):
        BIB = "library", "Bibliothek Braunschweig"
        BIB_WOB = "library_wob", "Bibliothek Wolfsburg"
        ZUHAUSE_STUDENT = "at home", "Beim Schüler/Studenten"
        ZUHAUSE_BRAIN = "at brainboost", "Bei mir"
        ONLINE = "online", "online"

    date = models.DateField()
    time = models.TimeField()
    ort = models.CharField(
        max_length=20,
        choices=Ort.choices,
        default=Ort.ZUHAUSE_STUDENT,
    )
    duration_minutes = models.PositiveIntegerField()
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="lessons",
    )
    tutor = models.ForeignKey(
        TutorProfile,
        on_delete=models.CASCADE,
        related_name="lessons",
    )
    fach = models.CharField(
        max_length=50,
        choices=[
            ("mathe", "Mathe"),
            ("deutsch", "Deutsch"),
            ("englisch", "Englisch"),
            ("naturwissenschaften", "Naturwissenschaften"),
            ("franzoesisch", "Französisch"),
            ("spanisch", "Spanisch"),
            ("musik", "Musik"),
        ],
        default="mathe",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLANNED,
    )
    location_address = models.CharField(max_length=255, blank=True)
    distance_km = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["date", "time"]
        verbose_name = "Termin"
        verbose_name_plural = "Termine"

    def __str__(self) -> str:
        formatted_date = self.date.strftime("%d.%m.%Y")
        formatted_time = self.time.strftime("%H:%M")
        return (
            f"Lesson for {self.student.user.username} with "
            f"{self.tutor.user.username} on {formatted_date} at {formatted_time}"
        )

    @property
    def computed_distance_km(self):
        """Return stored distance or compute on the fly for Zuhause/Bibliothek Wolfsburg."""
        if self.distance_km:
            return float(self.distance_km)

        # Fixed distance for Wolfsburg
        if self.ort == self.Ort.BIB_WOB:
            return 70.0

        if self.ort == self.Ort.ZUHAUSE_STUDENT:
            s_lat, s_lon = self.student.latitude, self.student.longitude
            t_lat, t_lon = (
                getattr(self.tutor, "latitude", None),
                getattr(self.tutor, "longitude", None),
            )
            if None not in (s_lat, s_lon, t_lat, t_lon):
                # Add 18% on top of the straight-line distance as a simple real-world buffer.
                return round(self._haversine_km(t_lat, t_lon, s_lat, s_lon) * 1.18, 2)
        return None

    @staticmethod
    def _haversine_km(lat1, lon1, lat2, lon2):
        """Distance between two lat/lon points in km."""
        r = 6371.0
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(
            dlon / 2
        ) ** 2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return round(r * c, 2)

    @property
    def scheduled_datetime(self) -> datetime:
        return timezone.make_aware(datetime.combine(self.date, self.time))

    @classmethod
    def upcoming_qs(cls):
        today = timezone.localdate()
        now_time = timezone.localtime().time()
        return cls.objects.filter(
            Q(date__gt=today) | Q(date=today, time__gte=now_time)
        )

    @classmethod
    def past_qs(cls):
        today = timezone.localdate()
        now_time = timezone.localtime().time()
        return cls.objects.filter(
            Q(date__lt=today) | Q(date=today, time__lt=now_time)
        )


def material_upload_path(instance, filename):
    kind_folder = instance.kind
    return f"materials/{kind_folder}/student_{instance.student_id}_{filename}"


class LearningMaterial(models.Model):
    class Kind(models.TextChoices):
        TASK = "task", "Aufgabe"
        SOLUTION = "solution", "Lösung"

    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="materials",
    )
    uploaded_by = models.ForeignKey(
        TutorProfile,
        on_delete=models.CASCADE,
        related_name="materials",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    file = models.FileField(
        upload_to=material_upload_path,
        validators=[
            FileExtensionValidator(
                allowed_extensions=["pdf", "png", "jpg", "jpeg", "docx"]
            )
        ],
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "Material"
        verbose_name_plural = "Materialien"

    def __str__(self):
        return f"{self.get_kind_display()} für {self.student} ({self.file.name})"


class ProgressEntry(models.Model):
    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.CASCADE,
        related_name="progress_entries",
    )
    comment = models.TextField(blank=True)
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(10)]
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Fortschrittseintrag"
        verbose_name_plural = "Fortschrittseinträge"

    def __str__(self) -> str:
        return f"Progress for {self.lesson} - Rating {self.rating}"
