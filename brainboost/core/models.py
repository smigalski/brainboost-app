import json
import urllib.request
from datetime import datetime, timedelta
from math import radians, sin, cos, sqrt, atan2
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator, FileExtensionValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class CustomUser(AbstractUser):
    class Roles(models.TextChoices):
        STUDENT = "student", "SchülerIn/StudentIn"
        PARENT = "parent", "Parent"
        TUTOR = "tutor", "TutorIn"

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
    phone_number = models.CharField(max_length=50, blank=True)

    def __str__(self) -> str:
        return f"Parent: {self.user.username}"


class StudentProfile(models.Model):
    address = models.CharField(max_length=255, blank=True)
    phone_number = models.CharField(max_length=50, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    zoom_link = models.URLField(blank=True)
    zumpad_link = models.URLField(blank=True)

    class Meta:
        verbose_name = "SchülerIn/StudentIn"
        verbose_name_plural = "SchülerInnen/StudentInnen"

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
    assigned_tutors = models.ManyToManyField(
        "TutorProfile",
        related_name="assigned_students",
        blank=True,
    )

    def __str__(self) -> str:
        return f"StudentIn/SchülerIn: {self.user.username}"

    def save(self, *args, **kwargs):
        should_geocode = False
        if self.address:
            if self.pk:
                try:
                    previous = StudentProfile.objects.get(pk=self.pk)
                    should_geocode = previous.address != self.address or not (self.latitude and self.longitude)
                except StudentProfile.DoesNotExist:
                    should_geocode = True
            else:
                should_geocode = True

        if should_geocode:
            coords = self._geocode_address(self.address)
            if coords:
                self.latitude, self.longitude = coords

        super().save(*args, **kwargs)

    @staticmethod
    def _geocode_address(address: str):
        encoded = quote(address)
        url = f"https://nominatim.openstreetmap.org/search?q={encoded}&format=json&limit=1"
        req = urllib.request.Request(
            url, headers={"User-Agent": "brainboost-app/1.0 (brainboost.nachhilfe@gmail.com)"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.load(resp)
        except Exception:
            return None

        if not data:
            return None
        try:
            return float(data[0]["lat"]), float(data[0]["lon"])
        except (KeyError, ValueError, TypeError):
            return None


class TutorProfile(models.Model):
    address = models.CharField(max_length=255, blank=True)
    phone_number = models.CharField(max_length=50, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    assigned_tutors = models.ManyToManyField(
        "self",
        symmetrical=False,
        related_name="supervising_tutors",
        blank=True,
    )
    class Meta:
        verbose_name = "TutorIn"
        verbose_name_plural = "TutorInnen"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tutor_profile",
    )

    def __str__(self) -> str:
        return f"TutorIn: {self.user.username}"


class Lesson(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "geplant"
        COMPLETED = "completed", "vorbei"
        CANCELLED = "cancelled", "storniert"

    class Ort(models.TextChoices):
        BIB = "library", "Bibliothek Braunschweig"
        BIB_WOB = "library_wob", "Bibliothek Wolfsburg"
        ZUHAUSE_STUDENT = "at home", "Bei SchülerIn/StudentIn"
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
    cancellation_reason = models.TextField(blank=True, default="")
    reschedule_requested = models.BooleanField(default=False)
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
                base_km = self._haversine_km(t_lat, t_lon, s_lat, s_lon)
                return round(base_km * 2 * 1.35, 2)
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

    @property
    def end_datetime(self) -> datetime:
        return self.scheduled_datetime + timedelta(minutes=self.duration_minutes)

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

    @property
    def calendar_title(self) -> str:
        return f"Nachhilfe {self.get_fach_display()} ({self.student.user.username})"

    @property
    def calendar_details(self) -> str:
        return (
            f"TutorIn: {self.tutor.user.username}\\n"
            f"SchülerIn: {self.student.user.username}\\n"
            f"Ort: {self.get_ort_display()}"
        )

    @property
    def calendar_location(self) -> str:
        return self.location_address or self.get_ort_display()

    @property
    def google_calendar_url(self) -> str:
        start = self.scheduled_datetime.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        end = self.end_datetime.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ctz = timezone.get_current_timezone_name()
        return (
            "https://calendar.google.com/calendar/render?action=TEMPLATE"
            f"&text={quote(self.calendar_title)}"
            f"&dates={start}/{end}"
            f"&details={quote(self.calendar_details)}"
            f"&location={quote(self.calendar_location)}"
            f"&ctz={quote(ctz)}"
        )


def material_upload_path(instance, filename):
    kind_folder = instance.kind
    return f"materials/{kind_folder}/student_{instance.student_id}_{filename}"


def tutor_template_upload_path(instance, filename):
    return f"tutor_templates/{instance.uploaded_by_id}_{filename}"


def invoice_upload_path(instance, filename):
    return f"invoices/student_{instance.student_id}_{filename}"


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
    related_task = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="linked_solutions",
        null=True,
        blank=True,
        limit_choices_to={"kind": "task"},
        verbose_name="Zugehoerige Aufgabe",
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
        validators=[MinValueValidator(0), MaxValueValidator(10)],
        verbose_name="Mitarbeit",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Lernfortschrittseintrag"
        verbose_name_plural = "Lernfortschrittseinträge"

    def __str__(self) -> str:
        return f"Lernfortschritt für {self.lesson} - Mitarbeit {self.rating}"


class Invoice(models.Model):
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    uploaded_by = models.ForeignKey(
        TutorProfile,
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    approved_by = models.ForeignKey(
        TutorProfile,
        on_delete=models.SET_NULL,
        related_name="approved_invoices",
        null=True,
        blank=True,
    )
    file = models.FileField(
        upload_to=invoice_upload_path,
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "Rechnung"
        verbose_name_plural = "Rechnungen"

    def __str__(self):
        return f"Rechnung für {self.student} ({self.file.name})"

    @property
    def due_date(self):
        return self.uploaded_at + timedelta(days=7)

    @property
    def is_approved(self):
        return self.approved_at is not None


class TutorTemplate(models.Model):
    uploaded_by = models.ForeignKey(
        TutorProfile,
        on_delete=models.CASCADE,
        related_name="templates",
    )
    file = models.FileField(
        upload_to=tutor_template_upload_path,
        validators=[
            FileExtensionValidator(
                allowed_extensions=["pdf", "png", "jpg", "jpeg", "docx"]
            )
        ],
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "Vorlage"
        verbose_name_plural = "Vorlagen"

    def __str__(self):
        return f"Vorlage von {self.uploaded_by.user.username} ({self.file.name})"
