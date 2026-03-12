from datetime import timedelta

import logging
from urllib.parse import urlencode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import get_object_or_404, render, redirect
from django.db.models import Count, Q
from django.template.loader import render_to_string
from django.utils.formats import date_format
from django.utils import timezone
from django.http import JsonResponse, HttpResponse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode, url_has_allowed_host_and_scheme
from django.urls import reverse

from .forms import (
    LessonForm,
    ProgressEntryForm,
    LearningMaterialForm,
    InvoiceForm,
    ParentCreateForm,
    StudentCreateForm,
    TutorTemplateForm,
    TutorCreateForm,
    ParentProfileForm,
    StudentProfileForm,
    TutorProfileForm,
)
from .notifications import (
    notify_invoice_uploaded,
    notify_lesson_cancelled,
    notify_lesson_changed,
    notify_lesson_created,
    notify_lesson_reschedule_requested,
    notify_material_uploaded,
)
from .models import (
    CustomUser,
    Lesson,
    ProgressEntry,
    StudentProfile,
    ParentProfile,
    TutorProfile,
    LearningMaterial,
    Invoice,
    TutorTemplate,
)


def _ensure_profile_for_user(user: CustomUser):
    """Create missing profile objects on-the-fly to keep views simple."""
    if user.role == CustomUser.Roles.STUDENT and not hasattr(user, "student_profile"):
        StudentProfile.objects.create(user=user)
    elif user.role == CustomUser.Roles.PARENT and not hasattr(user, "parent_profile"):
        ParentProfile.objects.create(user=user)
    elif user.role == CustomUser.Roles.TUTOR and not hasattr(user, "tutor_profile"):
        TutorProfile.objects.create(user=user)


def _haversine_km(lat1, lon1, lat2, lon2):
    """Compute distance between two lat/lon points in km."""
    from math import radians, sin, cos, sqrt, atan2

    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(
        dlon / 2
    ) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return round(r * c, 2)


def landing_page(request):
    form = AuthenticationForm(request)
    return render(request, "landing.html", {"form": form})


def _assign_location_and_distance(lesson: Lesson):
    """Set address and distance on lesson based on location choice."""
    address = ""
    distance = None
    if lesson.ort == Lesson.Ort.ZUHAUSE_STUDENT:
        student = lesson.student
        address = student.address
        lat = student.latitude
        lon = student.longitude
        tutor_lat = getattr(lesson.tutor, "latitude", None)
        tutor_lon = getattr(lesson.tutor, "longitude", None)
        if None not in (lat, lon, tutor_lat, tutor_lon):
            base_km = _haversine_km(tutor_lat, tutor_lon, lat, lon)
            distance = round(base_km * 2 * 1.35, 2)
    elif lesson.ort == Lesson.Ort.BIB:
        address = "Bibliothek Braunschweig"
    elif lesson.ort == Lesson.Ort.BIB_WOB:
        address = "Bibliothek Wolfsburg"
        distance = 70.0

    lesson.location_address = address or ""
    lesson.distance_km = distance


def _actor_label(user: CustomUser) -> str:
    if user.role == CustomUser.Roles.TUTOR:
        return f"TutorIn {user.username}"
    if user.role == CustomUser.Roles.PARENT:
        return f"Elternteil {user.username}"
    if user.role == CustomUser.Roles.STUDENT:
        return f"SchülerIn {user.username}"
    return user.username


def _display_name(user: CustomUser) -> str:
    full_name = user.get_full_name().strip()
    return full_name or user.username


def _rating_label(rating) -> str:
    if rating is None:
        return "ohne Mitarbeitsbewertung"
    return f"Mitarbeit {rating}/10"


def _limit_news_items(items: list[dict], limit: int = 3) -> list[dict]:
    return sorted(items, key=lambda item: item["timestamp"], reverse=True)[:limit]


def _lesson_news_items(lessons) -> list[dict]:
    items = []
    for lesson in lessons:
        labels = []
        if lesson.reschedule_requested:
            labels.append("Terminverlegung angefragt")
        if lesson.status == Lesson.Status.CANCELLED:
            labels.append("Termin storniert")
        elif lesson.status == Lesson.Status.COMPLETED:
            labels.append("Termin abgeschlossen")
        elif lesson.status == Lesson.Status.PLANNED:
            labels.append("Termin geplant")
        items.append(
            {
                "timestamp": lesson.scheduled_datetime,
                "title": f"Termin: {_display_name(lesson.student.user)}",
                "text": f"{date_format(lesson.date, 'l, d.m.Y')} um {lesson.time.strftime('%H:%M')} · {', '.join(labels)}",
            }
        )
    return items


def _student_news_items(student_profile: StudentProfile) -> list[dict]:
    items = []
    lesson_items = Lesson.objects.filter(student=student_profile).select_related("student__user").order_by("-date", "-time")[:4]
    items.extend(_lesson_news_items(lesson_items))

    progress_entries = (
        ProgressEntry.objects.filter(lesson__student=student_profile)
        .select_related("lesson__tutor__user")
        .order_by("-created_at")[:4]
    )
    for entry in progress_entries:
        items.append(
            {
                "timestamp": entry.created_at,
                "title": "Neuer Lernfortschritt",
                "text": f"{_display_name(entry.lesson.tutor.user)} hat einen Eintrag mit {_rating_label(entry.rating)} hinterlegt.",
            }
        )

    materials = (
        LearningMaterial.objects.filter(student=student_profile, kind=LearningMaterial.Kind.SOLUTION)
        .select_related("uploaded_by__user")
        .order_by("-uploaded_at")[:4]
    )
    for material in materials:
        items.append(
            {
                "timestamp": material.uploaded_at,
                "title": "Neue Musterlösung",
                "text": f"Neue Musterlösung von {_display_name(material.uploaded_by.user)} wurde hochgeladen.",
            }
        )
    return _limit_news_items(items)


def _parent_news_items(parent_profile: ParentProfile) -> list[dict]:
    items = []
    students = parent_profile.students.all()
    lesson_items = (
        Lesson.objects.filter(student__in=students)
        .select_related("student__user")
        .order_by("-date", "-time")[:4]
    )
    items.extend(_lesson_news_items(lesson_items))

    progress_entries = (
        ProgressEntry.objects.filter(lesson__student__in=students)
        .select_related("lesson__student__user", "lesson__tutor__user")
        .order_by("-created_at")[:4]
    )
    for entry in progress_entries:
        items.append(
            {
                "timestamp": entry.created_at,
                "title": f"Lernfortschritt: {_display_name(entry.lesson.student.user)}",
                "text": f"{_display_name(entry.lesson.tutor.user)} hat einen neuen Eintrag mit {_rating_label(entry.rating)} erstellt.",
            }
        )

    invoices = (
        Invoice.objects.filter(student__in=students)
        .select_related("student__user")
        .order_by("-uploaded_at")[:4]
    )
    for invoice in invoices:
        items.append(
            {
                "timestamp": invoice.uploaded_at,
                "title": f"Neue Rechnung: {_display_name(invoice.student.user)}",
                "text": f"Eine neue Rechnung wurde am {invoice.uploaded_at.strftime('%d.%m.%Y %H:%M')} hochgeladen.",
            }
        )

    materials = (
        LearningMaterial.objects.filter(student__in=students, kind=LearningMaterial.Kind.SOLUTION)
        .select_related("student__user")
        .order_by("-uploaded_at")[:4]
    )
    for material in materials:
        items.append(
            {
                "timestamp": material.uploaded_at,
                "title": f"Neue Musterlösung: {_display_name(material.student.user)}",
                "text": "Es wurde eine neue Musterlösung hochgeladen.",
            }
        )
    return _limit_news_items(items)


def _tutor_news_items(tutor_profile: TutorProfile) -> list[dict]:
    items = []
    assigned_students = _assigned_students_qs(tutor_profile)
    subordinate_tutors = _assigned_tutors_qs(tutor_profile)

    lesson_items = (
        Lesson.objects.filter(tutor=tutor_profile)
        .select_related("student__user")
        .order_by("-date", "-time")[:4]
    )
    items.extend(_lesson_news_items(lesson_items))

    progress_entries = (
        ProgressEntry.objects.filter(lesson__tutor=tutor_profile)
        .select_related("lesson__student__user")
        .order_by("-created_at")[:4]
    )
    for entry in progress_entries:
        items.append(
            {
                "timestamp": entry.created_at,
                "title": f"Lernfortschritt gespeichert: {_display_name(entry.lesson.student.user)}",
                "text": f"{_rating_label(entry.rating)} wurde eingetragen.",
            }
        )

    materials = (
        LearningMaterial.objects.filter(Q(student__in=assigned_students) | Q(uploaded_by=tutor_profile))
        .select_related("student__user")
        .order_by("-uploaded_at")[:4]
    )
    for material in materials:
        items.append(
            {
                "timestamp": material.uploaded_at,
                "title": f"Neues Material: {_display_name(material.student.user)}",
                "text": f"{material.get_kind_display()} wurde hochgeladen.",
            }
        )

    invoices = (
        Invoice.objects.filter(Q(uploaded_by=tutor_profile) | Q(uploaded_by__in=subordinate_tutors))
        .select_related("student__user", "uploaded_by__user")
        .order_by("-uploaded_at")[:4]
    )
    for invoice in invoices:
        items.append(
            {
                "timestamp": invoice.uploaded_at,
                "title": f"Neue Rechnung: {_display_name(invoice.student.user)}",
                "text": f"Hochgeladen von {_display_name(invoice.uploaded_by.user)}.",
            }
        )
    return _limit_news_items(items)


def contact(request):
    return render(request, "contact.html")


def impressum(request):
    return render(request, "impressum.html")


def agbs(request):
    return render(request, "agbs.html")


def pricing(request):
    return render(request, "pricing.html")

logger = logging.getLogger(__name__)


def _send_set_password_email(request, user: CustomUser) -> None:
    if not user.email:
        raise ValueError("missing_email")
    if not getattr(settings, "EMAIL_HOST_USER", "") or not getattr(
        settings, "EMAIL_HOST_PASSWORD", ""
    ):
        raise RuntimeError("smtp_config_missing")
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    reset_path = reverse(
        "password_reset_confirm",
        kwargs={"uidb64": uid, "token": token},
    )
    reset_url = request.build_absolute_uri(reset_path)
    context = {
        "user": user,
        "set_password_url": reset_url,
    }
    subject = "BrainBoost: Bestätigung & Passwort setzen"
    text_body = render_to_string("emails/registration_confirmation.txt", context)
    html_body = render_to_string("emails/registration_confirmation.html", context)
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@brainboost.local")
    message = EmailMultiAlternatives(subject, text_body, from_email, [user.email])
    message.attach_alternative(html_body, "text/html")
    sent = message.send()
    if sent == 0:
        raise RuntimeError("email_send_failed")


def _assigned_students_qs(tutor_profile: TutorProfile):
    return (
        StudentProfile.objects.filter(assigned_tutors=tutor_profile)
        .select_related("user")
        .distinct()
    )


def _assigned_tutors_qs(tutor_profile: TutorProfile):
    return tutor_profile.assigned_tutors.select_related("user").distinct()


@login_required
def dashboard(request):
    _ensure_profile_for_user(request.user)
    context = {}
    if request.user.role == CustomUser.Roles.STUDENT:
        template = "dashboard_student.html"
        if hasattr(request.user, "student_profile"):
            student_profile = request.user.student_profile
            context["news_items"] = _student_news_items(student_profile)
            context["upcoming_lessons"] = (
                Lesson.upcoming_qs()
                .filter(student=student_profile)
                .select_related("tutor__user")
                .order_by("date", "time")[:5]
            )
            context["latest_progress_entry"] = (
                ProgressEntry.objects.filter(lesson__student=student_profile)
                .select_related("lesson__tutor__user")
                .order_by("-created_at")
                .first()
            )
            context["assigned_tutors"] = student_profile.assigned_tutors.select_related(
                "user"
            ).distinct()
    elif request.user.role == CustomUser.Roles.PARENT:
        template = "dashboard_parent.html"
        if hasattr(request.user, "parent_profile"):
            context["news_items"] = _parent_news_items(request.user.parent_profile)
            students = request.user.parent_profile.students.all()
            context["upcoming_lessons"] = (
                Lesson.upcoming_qs()
                .filter(student__in=students)
                .select_related("student__user", "tutor__user")
                .order_by("date", "time")[:5]
            )
            context["assigned_tutors"] = (
                TutorProfile.objects.filter(assigned_students__in=students)
                .select_related("user")
                .distinct()
            )
            context["solutions"] = LearningMaterial.objects.filter(
                student__in=students, kind=LearningMaterial.Kind.SOLUTION
            ).select_related("student__user", "related_task")
    elif request.user.role == CustomUser.Roles.TUTOR:
        template = "dashboard_tutor.html"
        if hasattr(request.user, "tutor_profile"):
            assigned_students = _assigned_students_qs(request.user.tutor_profile)
            assigned_tutors = _assigned_tutors_qs(request.user.tutor_profile)
            context["is_admin_tutor"] = request.user.is_superuser
            context["has_parent_profiles"] = ParentProfile.objects.exists()
            context["news_items"] = _tutor_news_items(request.user.tutor_profile)
            bbb_students = assigned_students
            context["bbb_students"] = bbb_students
            context["assigned_student_count"] = assigned_students.count()
            context["assigned_tutor_count"] = assigned_tutors.count()
            context["upcoming_lessons"] = (
                Lesson.upcoming_qs()
                .filter(tutor=request.user.tutor_profile)
                .select_related("student__user")
                .order_by("date", "time")[:5]
            )
    else:
        template = "dashboard_student.html"
    return render(request, template, context)


@login_required
def profile_view(request):
    _ensure_profile_for_user(request.user)

    form_class = {
        CustomUser.Roles.PARENT: ParentProfileForm,
        CustomUser.Roles.STUDENT: StudentProfileForm,
        CustomUser.Roles.TUTOR: TutorProfileForm,
    }.get(request.user.role, ParentProfileForm)

    uses_address_autocomplete = request.user.role in {
        CustomUser.Roles.STUDENT,
        CustomUser.Roles.TUTOR,
    }

    if request.method == "POST":
        form = form_class(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Dein Profil wurde aktualisiert.")
            return redirect("profile")
    else:
        form = form_class(user=request.user)

    return render(
        request,
        "profile.html",
        {
            "form": form,
            "uses_address_autocomplete": uses_address_autocomplete,
        },
    )


@login_required
def assigned_student_list(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    tutor_profile = request.user.tutor_profile
    today = timezone.localdate()
    now_time = timezone.localtime().time()
    students = (
        _assigned_students_qs(tutor_profile)
        .select_related("user")
        .prefetch_related("parents__user")
        .annotate(
            past_lesson_count=Count(
                "lessons",
                filter=Q(lessons__tutor=tutor_profile)
                & (Q(lessons__date__lt=today) | Q(lessons__date=today, lessons__time__lt=now_time)),
                distinct=True,
            )
        )
        .distinct()
        .order_by("user__username")
    )

    return render(
        request,
        "assigned_student_list.html",
        {"students": students},
    )


@login_required
def assigned_tutor_list(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    tutor_profile = request.user.tutor_profile
    tutors = (
        _assigned_tutors_qs(tutor_profile)
        .annotate(
            assigned_student_count=Count("assigned_students", distinct=True),
            assigned_tutor_count=Count("assigned_tutors", distinct=True),
        )
        .order_by("user__username")
    )

    return render(
        request,
        "assigned_tutor_list.html",
        {"tutors": tutors},
    )


@login_required
def parent_create(request):
    if request.user.role != CustomUser.Roles.TUTOR:
        return redirect("dashboard")
    if request.method == "POST":
        form = ParentCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            if not user.email:
                messages.success(
                    request,
                    "Elternteil wurde als Platzhalter ohne WebApp-Zugang angelegt.",
                )
            else:
                try:
                    _send_set_password_email(request, user)
                except Exception as exc:
                    logger.exception("E-Mail Versand fehlgeschlagen (Elternteil)")
                    messages.error(
                        request,
                        "Elternteil wurde angelegt, die Bestätigungs-Mail konnte jedoch nicht gesendet werden. "
                        f"Fehler: {exc.__class__.__name__} ({exc})",
                    )
                else:
                    messages.success(
                        request,
                        "Elternteil wurde angelegt. Eine Bestätigungs-Mail wurde versendet.",
                    )
            return redirect("dashboard")
    else:
        form = ParentCreateForm()
    return render(request, "parent_create.html", {"form": form})


@login_required
def tutor_create(request):
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(
        request.user, "tutor_profile"
    ):
        return redirect("dashboard")
    if not request.user.is_superuser:
        messages.error(
            request,
            "TutorInnen koennen nur von AdministratorInnen angelegt werden.",
        )
        return redirect("dashboard")
    if request.method == "POST":
        form = TutorCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            request.user.tutor_profile.assigned_tutors.add(user.tutor_profile)
            try:
                _send_set_password_email(request, user)
            except ValueError:
                messages.error(
                    request,
                    "TutorIn wurde angelegt, aber es wurde keine E-Mail-Adresse angegeben.",
                )
            except Exception as exc:
                logger.exception("E-Mail Versand fehlgeschlagen (Tutor)")
                messages.error(
                    request,
                    "TutorIn wurde angelegt, die Bestätigungs-Mail konnte jedoch nicht gesendet werden. "
                    f"Fehler: {exc.__class__.__name__} ({exc})",
                )
            else:
                messages.success(
                    request,
                    "TutorIn wurde angelegt. Eine Bestätigungs-Mail wurde versendet.",
                )
            return redirect("dashboard")
    else:
        form = TutorCreateForm()
    return render(request, "tutor_create.html", {"form": form})


@login_required
def student_create(request):
    if request.user.role != CustomUser.Roles.TUTOR:
        return redirect("dashboard")
    if not ParentProfile.objects.exists():
        messages.error(
            request,
            "Lege zuerst ein Elternteil an, bevor du eine SchülerIn anlegst.",
        )
        return redirect("dashboard")
    if request.method == "POST":
        form = StudentCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            user.student_profile.assigned_tutors.add(request.user.tutor_profile)
            if not user.email:
                messages.success(
                    request,
                    "SchülerIn wurde als Platzhalter ohne WebApp-Zugang angelegt.",
                )
            else:
                try:
                    _send_set_password_email(request, user)
                except Exception as exc:
                    logger.exception("E-Mail Versand fehlgeschlagen (Schüler)")
                    messages.error(
                        request,
                        "SchülerIn wurde angelegt, die Bestätigungs-Mail konnte jedoch nicht gesendet werden. "
                        f"Fehler: {exc.__class__.__name__} ({exc})",
                    )
                else:
                    messages.success(
                        request,
                        "SchülerIn wurde angelegt. Eine Bestätigungs-Mail wurde versendet.",
                    )
            return redirect("dashboard")
    else:
        form = StudentCreateForm()
    return render(request, "student_create.html", {"form": form})


@login_required
def lesson_list(request):
    _ensure_profile_for_user(request.user)
    when = request.GET.get("when", "upcoming")
    today = timezone.localdate()
    try:
        week_offset = int(request.GET.get("offset", 0))
    except (TypeError, ValueError):
        week_offset = 0

    if request.user.role == CustomUser.Roles.STUDENT and hasattr(
        request.user, "student_profile"
    ):
        base_qs = Lesson.objects.filter(student=request.user.student_profile)
    elif request.user.role == CustomUser.Roles.PARENT and hasattr(
        request.user, "parent_profile"
    ):
        base_qs = Lesson.objects.filter(
            student__in=request.user.parent_profile.students.all()
        )
    elif request.user.role == CustomUser.Roles.TUTOR and hasattr(
        request.user, "tutor_profile"
    ):
        base_qs = Lesson.objects.filter(tutor=request.user.tutor_profile)
    else:
        base_qs = Lesson.objects.none()

    editable_ids: list[int] = []
    if request.user.role == CustomUser.Roles.TUTOR:
        editable_ids = list(base_qs.values_list("id", flat=True))

    period = request.GET.get("period", "").strip()
    student_id = request.GET.get("student", "").strip()
    weekday = request.GET.get("weekday", "").strip()
    duration = request.GET.get("duration", "").strip()
    ort = request.GET.get("ort", "").strip()

    filtered_qs = base_qs
    if period:
        try:
            period_year, period_month = period.split("-", 1)
        except ValueError:
            period_year, period_month = "", ""
        if period_year and period_month:
            filtered_qs = filtered_qs.filter(
                date__year=period_year,
                date__month=period_month,
            )
    if student_id:
        filtered_qs = filtered_qs.filter(student_id=student_id)
    if weekday:
        filtered_qs = filtered_qs.filter(date__week_day=weekday)
    if duration:
        filtered_qs = filtered_qs.filter(duration_minutes=duration)
    if ort:
        filtered_qs = filtered_qs.filter(ort=ort)

    if when == "past":
        lessons = Lesson.past_qs().filter(
            pk__in=filtered_qs.values_list("pk", flat=True)
        ).order_by("-date", "-time")
    else:
        lessons = Lesson.upcoming_qs().filter(
            pk__in=filtered_qs.values_list("pk", flat=True)
        ).order_by("-date", "-time")

    filter_pairs = [
        ("period", period),
        ("student", student_id),
        ("weekday", weekday),
        ("duration", duration),
        ("ort", ort),
    ]
    active_filter_pairs = [(key, value) for key, value in filter_pairs if value]
    filter_query = urlencode(active_filter_pairs)
    when_query = urlencode([("when", when), *active_filter_pairs])

    cancelable_ids = list(
        base_qs.exclude(status=Lesson.Status.CANCELLED).values_list("id", flat=True)
    )

    period_options = [
        {
            "value": lesson_date.strftime("%Y-%m"),
            "label": date_format(lesson_date, "F Y"),
        }
        for lesson_date in sorted(
            base_qs.dates("date", "month"),
            reverse=True,
        )
    ]
    student_options = (
        StudentProfile.objects.filter(id__in=base_qs.values_list("student_id", flat=True))
        .select_related("user")
        .order_by("user__first_name", "user__last_name")
    )
    duration_options = [
        str(value)
        for value in sorted(
        {
            value
            for value in base_qs.values_list("duration_minutes", flat=True).distinct()
            if value is not None
        }
        )
    ]
    ort_values = set(base_qs.values_list("ort", flat=True).distinct())
    ort_options = [choice for choice in Lesson.Ort.choices if choice[0] in ort_values]
    weekday_options = [
        ("2", "Montag"),
        ("3", "Dienstag"),
        ("4", "Mittwoch"),
        ("5", "Donnerstag"),
        ("6", "Freitag"),
        ("7", "Samstag"),
        ("1", "Sonntag"),
    ]

    # Build a simple week calendar (current week Monday-Sunday)
    week_start = today - timedelta(days=today.weekday()) + timedelta(days=7 * week_offset)
    week_end = week_start + timedelta(days=6)
    week_lessons_qs = (
        base_qs.filter(date__range=(week_start, week_end))
        .order_by("date", "time")
        .select_related("student__user", "tutor__user")
    )
    week_days = []
    for i in range(7):
        day_date = week_start + timezone.timedelta(days=i)
        day_lessons = [l for l in week_lessons_qs if l.date == day_date]
        week_days.append({"date": day_date, "lessons": day_lessons})

    return render(
        request,
        "lesson_list.html",
        {
            "lessons": lessons,
            "when": when,
            "week_days": week_days,
            "week_start": week_start,
            "week_end": week_end,
            "week_offset": week_offset,
            "filter_query": filter_query,
            "when_query": when_query,
            "selected_period": period,
            "selected_student": student_id,
            "selected_weekday": weekday,
            "selected_duration": duration,
            "selected_ort": ort,
            "period_options": period_options,
            "student_options": student_options,
            "weekday_options": weekday_options,
            "duration_options": duration_options,
            "ort_options": ort_options,
            "cancelable_ids": cancelable_ids,
            "editable_ids": editable_ids,
        },
    )


@login_required
def lesson_create(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(
        request.user, "tutor_profile"
    ):
        return redirect("lesson_list")

    if request.method == "POST":
        form = LessonForm(data=request.POST, tutor_profile=request.user.tutor_profile)
        if form.is_valid():
            lesson = form.save(commit=False)
            lesson.tutor = request.user.tutor_profile
            _assign_location_and_distance(lesson)
            lesson.save()
            notify_lesson_created(request, lesson)
            return redirect("lesson_list")
    else:
        form = LessonForm(tutor_profile=request.user.tutor_profile)
    return render(request, "lesson_form.html", {"form": form})


@login_required
def lesson_cancel(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    allowed = False
    # TutorIn, StudentIn oder Elternteil dürfen stornieren
    if hasattr(request.user, "tutor_profile") and lesson.tutor == request.user.tutor_profile:
        allowed = True
    if hasattr(request.user, "student_profile") and lesson.student == request.user.student_profile:
        allowed = True
    if hasattr(request.user, "parent_profile") and request.user.parent_profile.students.filter(id=lesson.student_id).exists():
        allowed = True
    if not allowed:
        return JsonResponse({"ok": False, "message": "Keine Berechtigung."}, status=403) if is_ajax else redirect("lesson_list")

    if request.method != "POST":
        return redirect("lesson_list")

    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        msg = "Bitte gib einen Stornierungsgrund an."
        return JsonResponse({"ok": False, "message": msg}, status=400) if is_ajax else redirect("lesson_list")

    if lesson.status == Lesson.Status.CANCELLED:
        msg = "Termin ist bereits storniert."
        return JsonResponse({"ok": False, "message": msg}, status=400) if is_ajax else redirect("lesson_list")

    now = timezone.now()
    time_until_lesson = lesson.scheduled_datetime - now
    if time_until_lesson < timedelta(hours=5):
        msg = "Keine kostenlose Stornierung mehr möglich. Bitte kontaktiere die TutorIn sofort."
        return JsonResponse({"ok": False, "message": msg}, status=400) if is_ajax else redirect("lesson_list")

    lesson.status = Lesson.Status.CANCELLED
    lesson.cancellation_reason = reason
    lesson.reschedule_requested = False
    lesson.save(update_fields=["status", "cancellation_reason", "reschedule_requested"])
    notify_lesson_cancelled(
        request,
        lesson,
        actor_label=_actor_label(request.user),
        reason=reason,
        include_tutor=request.user.role != CustomUser.Roles.TUTOR,
    )
    if is_ajax:
        return JsonResponse({"ok": True, "message": "Stornierungsanfrage wurde gespeichert."})
    return redirect("lesson_list")


@login_required
def lesson_reschedule_request(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

    allowed = False
    if hasattr(request.user, "student_profile") and lesson.student == request.user.student_profile:
        allowed = True
    if hasattr(request.user, "parent_profile") and request.user.parent_profile.students.filter(id=lesson.student_id).exists():
        allowed = True
    if not allowed:
        return JsonResponse({"ok": False, "message": "Keine Berechtigung."}, status=403) if is_ajax else redirect("lesson_list")

    if request.method != "POST":
        return redirect("lesson_list")

    if lesson.status == Lesson.Status.CANCELLED:
        msg = "Stornierte Termine können nicht verlegt werden."
        return JsonResponse({"ok": False, "message": msg}, status=400) if is_ajax else redirect("lesson_list")

    now = timezone.now()
    time_until_lesson = lesson.scheduled_datetime - now
    if time_until_lesson < timedelta(hours=5):
        msg = "Terminverlegung weniger als 5 Stunden vor Termin nicht möglich. Bitte kontaktiere die TutorIn direkt."
        return JsonResponse({"ok": False, "message": msg}, status=400) if is_ajax else redirect("lesson_list")

    if lesson.reschedule_requested:
        msg = "Terminverlegung wurde bereits angefragt."
        return JsonResponse({"ok": True, "message": msg})

    lesson.reschedule_requested = True
    lesson.save(update_fields=["reschedule_requested"])
    notify_lesson_reschedule_requested(
        request,
        lesson,
        actor_label=_actor_label(request.user),
        include_tutor=True,
    )
    success_msg = "TutorIn wurde informiert. Termin ist als Verlegungsanfrage markiert."
    return JsonResponse({"ok": True, "message": success_msg}) if is_ajax else redirect("lesson_list")


@login_required
def lesson_ics(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    allowed = False
    if hasattr(request.user, "tutor_profile") and lesson.tutor == request.user.tutor_profile:
        allowed = True
    if hasattr(request.user, "student_profile") and lesson.student == request.user.student_profile:
        allowed = True
    if hasattr(request.user, "parent_profile") and request.user.parent_profile.students.filter(id=lesson.student_id).exists():
        allowed = True
    if not allowed:
        return redirect("lesson_list")

    start = lesson.scheduled_datetime.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    end = lesson.end_datetime.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    now_stamp = timezone.now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    summary = lesson.calendar_title
    description = lesson.calendar_details
    location = lesson.calendar_location
    ics_content = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//BrainBoost//DE",
            "BEGIN:VEVENT",
            f"UID:lesson-{lesson.id}@brainboost",
            f"DTSTAMP:{now_stamp}",
            f"DTSTART:{start}",
            f"DTEND:{end}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{description}",
            f"LOCATION:{location}",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )
    response = HttpResponse(ics_content, content_type="text/calendar")
    response["Content-Disposition"] = f'attachment; filename="termin-{lesson.id}.ics"'
    return response


@login_required
def lesson_edit(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    is_tutor = hasattr(request.user, "tutor_profile") and lesson.tutor == request.user.tutor_profile
    if not is_tutor:
        return redirect("lesson_list")

    if request.method == "POST":
        form = LessonForm(
            data=request.POST,
            instance=lesson,
            tutor_profile=request.user.tutor_profile if is_tutor else None,
            allowed_students=None,
        )
        if form.is_valid():
            updated = form.save(commit=False)
            _assign_location_and_distance(updated)
            updated.save()
            notify_lesson_changed(request, updated)
            return redirect("lesson_list")
    else:
        form = LessonForm(
            instance=lesson,
            tutor_profile=request.user.tutor_profile if is_tutor else None,
            allowed_students=None,
        )

    return render(request, "lesson_form.html", {"form": form, "is_edit": True, "lesson": lesson})


@login_required
def lesson_delete(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    is_tutor = hasattr(request.user, "tutor_profile") and lesson.tutor == request.user.tutor_profile
    if not is_tutor:
        return redirect("lesson_list")

    if request.method == "POST":
        lesson.delete()
        return redirect("lesson_list")
    return redirect("lesson_edit", lesson_id=lesson_id)


@login_required
def material_upload(request, kind: str):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    if kind not in (LearningMaterial.Kind.TASK, LearningMaterial.Kind.SOLUTION):
        return redirect("dashboard")

    allowed_students = StudentProfile.objects.filter(
        assigned_tutors=request.user.tutor_profile
    ).distinct()
    heading = "Aufgabe hochladen" if kind == LearningMaterial.Kind.TASK else "Musterlösung hochladen"

    if request.method == "POST":
        form = LearningMaterialForm(
            data=request.POST,
            files=request.FILES,
            allowed_students=allowed_students,
            kind=kind,
            tutor_profile=request.user.tutor_profile,
        )
        if form.is_valid():
            material = form.save(commit=False)
            material.kind = kind
            material.uploaded_by = request.user.tutor_profile
            material.save()
            notify_material_uploaded(request, material)
            return redirect("dashboard")
    else:
        form = LearningMaterialForm(
            allowed_students=allowed_students,
            kind=kind,
            tutor_profile=request.user.tutor_profile,
        )

    return render(
        request,
        "material_upload.html",
        {"form": form, "heading": heading},
    )


@login_required
def tutor_solution_list(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    tutor_profile = request.user.tutor_profile
    assigned_students = _assigned_students_qs(tutor_profile)
    solutions = (
        LearningMaterial.objects.filter(kind=LearningMaterial.Kind.SOLUTION)
        .filter(Q(student__in=assigned_students) | Q(uploaded_by=tutor_profile))
        .select_related("student__user", "uploaded_by__user", "related_task")
        .distinct()
    )

    return render(
        request,
        "tutor_solution_list.html",
        {"solutions": solutions},
    )


@login_required
def tutor_template_list(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    is_admin_tutor = request.user.is_staff or request.user.is_superuser

    if request.method == "POST":
        if not is_admin_tutor:
            return redirect("tutor_template_list")
        form = TutorTemplateForm(request.POST, request.FILES)
        if form.is_valid():
            template = form.save(commit=False)
            template.uploaded_by = request.user.tutor_profile
            template.save()
            messages.success(request, "Vorlage wurde hochgeladen.")
            return redirect("tutor_template_list")
    else:
        form = TutorTemplateForm()

    templates = TutorTemplate.objects.select_related("uploaded_by__user")
    return render(
        request,
        "tutor_template_list.html",
        {
            "templates": templates,
            "form": form,
            "is_admin_tutor": is_admin_tutor,
        },
    )


@login_required
def invoice_upload(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    tutor_profile = request.user.tutor_profile
    allowed_students = _assigned_students_qs(tutor_profile)
    subordinate_tutors = _assigned_tutors_qs(tutor_profile)

    if request.method == "POST":
        form = InvoiceForm(
            data=request.POST,
            files=request.FILES,
            allowed_students=allowed_students,
        )
        if form.is_valid():
            invoice = form.save(commit=False)
            invoice.uploaded_by = tutor_profile
            invoice.save()
            if not tutor_profile.supervising_tutors.exists():
                invoice.approved_by = tutor_profile
                invoice.approved_at = timezone.now()
                invoice.save(update_fields=["approved_by", "approved_at"])
                notify_invoice_uploaded(request, invoice)
                messages.success(request, "Rechnung wurde hochgeladen und direkt freigegeben.")
            else:
                messages.success(request, "Rechnung wurde hochgeladen und wartet auf Freigabe.")
            return redirect("invoice_upload")
    else:
        form = InvoiceForm(allowed_students=allowed_students)

    own_invoices = (
        Invoice.objects.filter(uploaded_by=tutor_profile)
        .select_related("student__user", "approved_by__user")
        .order_by("-uploaded_at")
    )
    subordinate_invoices = (
        Invoice.objects.filter(uploaded_by__in=subordinate_tutors)
        .select_related("student__user", "uploaded_by__user", "approved_by__user")
        .order_by("-uploaded_at")
    )

    return render(
        request,
        "invoice_upload.html",
        {
            "form": form,
            "heading": "Rechnungen",
            "tutor_invoices": own_invoices,
            "subordinate_invoices": subordinate_invoices,
            "has_subordinate_tutors": subordinate_tutors.exists(),
        },
    )


@login_required
def invoice_approve(request, invoice_id):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    if request.method != "POST":
        return redirect("invoice_upload")

    tutor_profile = request.user.tutor_profile
    invoice = get_object_or_404(
        Invoice.objects.select_related("student__user", "uploaded_by__user", "approved_by__user"),
        pk=invoice_id,
    )

    if not tutor_profile.assigned_tutors.filter(pk=invoice.uploaded_by_id).exists():
        messages.error(request, "Du darfst diese Rechnung nicht freigeben.")
        return redirect("invoice_upload")

    if invoice.is_approved:
        messages.info(request, "Diese Rechnung wurde bereits freigegeben.")
        return redirect("invoice_upload")

    invoice.approved_by = tutor_profile
    invoice.approved_at = timezone.now()
    invoice.save(update_fields=["approved_by", "approved_at"])
    notify_invoice_uploaded(request, invoice)
    messages.success(request, "Rechnung wurde freigegeben.")
    return redirect("invoice_upload")


@login_required
def progress_create(request, lesson_id=None):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(
        request.user, "tutor_profile"
    ):
        return redirect("progress")

    tutor_profile = request.user.tutor_profile
    initial = {}
    if lesson_id:
        lesson = get_object_or_404(
            tutor_profile.lessons.select_related("student__user"), pk=lesson_id
        )
        initial["lesson"] = lesson
    else:
        lesson = None

    if request.method == "POST":
        form = ProgressEntryForm(
            data=request.POST, tutor_profile=tutor_profile, initial=initial
        )
        if form.is_valid():
            progress = form.save()
            return redirect(
                "progress_student", student_id=form.cleaned_data["lesson"].student.id
            )
    else:
        form = ProgressEntryForm(
            tutor_profile=tutor_profile,
            initial=initial,
        )

    return render(
        request,
        "progress_form.html",
        {
            "form": form,
            "lesson": lesson,
            "is_edit": False,
            "cancel_url": reverse("progress"),
        },
    )


@login_required
def progress_edit(request, entry_id):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(
        request.user, "tutor_profile"
    ):
        return redirect("progress")

    tutor_profile = request.user.tutor_profile
    entry = get_object_or_404(
        ProgressEntry.objects.select_related("lesson__student__user", "lesson__tutor"),
        pk=entry_id,
        lesson__tutor=tutor_profile,
    )
    next_url = request.GET.get("next") or request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = reverse("progress_student", args=[entry.lesson.student_id])

    if request.method == "POST":
        form = ProgressEntryForm(
            data=request.POST,
            instance=entry,
            tutor_profile=tutor_profile,
        )
        if form.is_valid():
            form.save()
            return redirect(next_url)
    else:
        form = ProgressEntryForm(instance=entry, tutor_profile=tutor_profile)

    return render(
        request,
        "progress_form.html",
        {
            "form": form,
            "lesson": entry.lesson,
            "is_edit": True,
            "cancel_url": next_url,
            "next_url": next_url,
        },
    )


@login_required
def progress_view(request, student_id=None):
    _ensure_profile_for_user(request.user)
    entries = ProgressEntry.objects.none()
    viewed_student = None
    student_list = StudentProfile.objects.none()
    period = request.GET.get("period", "").strip()
    selected_student = request.GET.get("student", "").strip()
    weekday = request.GET.get("weekday", "").strip()
    duration = request.GET.get("duration", "").strip()
    ort = request.GET.get("ort", "").strip()

    if request.user.role == CustomUser.Roles.STUDENT and hasattr(
        request.user, "student_profile"
    ):
        viewed_student = request.user.student_profile
        entries = ProgressEntry.objects.filter(
            lesson__student=request.user.student_profile
        )
    elif request.user.role == CustomUser.Roles.PARENT and hasattr(
        request.user, "parent_profile"
    ):
        students = request.user.parent_profile.students.all()
        student_list = students
        if student_id:
            viewed_student = get_object_or_404(students, pk=student_id)
            entries = ProgressEntry.objects.filter(lesson__student=viewed_student)
        else:
            entries = ProgressEntry.objects.filter(lesson__student__in=students)
    elif request.user.role == CustomUser.Roles.TUTOR and hasattr(
        request.user, "tutor_profile"
    ):
        student_list = _assigned_students_qs(request.user.tutor_profile)
        if student_id is None:
            entries = ProgressEntry.objects.filter(
                lesson__tutor=request.user.tutor_profile
            )
        else:
            viewed_student = get_object_or_404(student_list, pk=student_id)
            entries = ProgressEntry.objects.filter(
                lesson__student=viewed_student,
                lesson__tutor=request.user.tutor_profile,
            )

        if period:
            try:
                period_year, period_month = period.split("-", 1)
            except ValueError:
                period_year, period_month = "", ""
            if period_year and period_month:
                entries = entries.filter(
                    lesson__date__year=period_year,
                    lesson__date__month=period_month,
                )
        if selected_student and student_id is None:
            entries = entries.filter(lesson__student_id=selected_student)
        if weekday:
            entries = entries.filter(lesson__date__week_day=weekday)
        if duration:
            entries = entries.filter(lesson__duration_minutes=duration)
        if ort:
            entries = entries.filter(lesson__ort=ort)

    period_options = [
        {
            "value": lesson_date.strftime("%Y-%m"),
            "label": date_format(lesson_date, "F Y"),
        }
        for lesson_date in sorted(
            Lesson.objects.filter(id__in=entries.values_list("lesson_id", flat=True))
            .dates("date", "month"),
            reverse=True,
        )
    ]
    duration_options = [
        str(value)
        for value in sorted(
            {
                value
                for value in entries.values_list("lesson__duration_minutes", flat=True).distinct()
                if value is not None
            }
        )
    ]
    ort_values = set(entries.values_list("lesson__ort", flat=True).distinct())
    ort_options = [choice for choice in Lesson.Ort.choices if choice[0] in ort_values]
    weekday_options = [
        ("2", "Montag"),
        ("3", "Dienstag"),
        ("4", "Mittwoch"),
        ("5", "Donnerstag"),
        ("6", "Freitag"),
        ("7", "Samstag"),
        ("1", "Sonntag"),
    ]

    return render(
        request,
        "progress.html",
        {
            "entries": entries,
            "viewed_student": viewed_student,
            "student_id": student_id,
            "student_list": student_list,
            "period_options": period_options,
            "selected_period": period,
            "selected_student": selected_student,
            "selected_weekday": weekday,
            "selected_duration": duration,
            "selected_ort": ort,
            "duration_options": duration_options,
            "ort_options": ort_options,
            "weekday_options": weekday_options,
        },
    )


@login_required
def invoice_list(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.PARENT or not hasattr(request.user, "parent_profile"):
        return redirect("dashboard")
    students = request.user.parent_profile.students.all()
    invoices = Invoice.objects.filter(
        student__in=students,
        approved_at__isnull=False,
    ).select_related("student__user", "uploaded_by__user", "approved_by__user")
    return render(
        request,
        "invoice_list.html",
        {"invoices": invoices},
    )
