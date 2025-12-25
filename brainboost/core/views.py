from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import get_object_or_404, render, redirect
from django.db.models import Q
from django.utils import timezone
from django.http import JsonResponse, HttpResponse

from .forms import LessonForm, ProgressEntryForm, LearningMaterialForm, InvoiceForm
from .models import (
    CustomUser,
    Lesson,
    ProgressEntry,
    StudentProfile,
    ParentProfile,
    TutorProfile,
    LearningMaterial,
    Invoice,
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


def contact(request):
    return render(request, "contact.html")


def impressum(request):
    return render(request, "impressum.html")


def agbs(request):
    return render(request, "agbs.html")


@login_required
def dashboard(request):
    _ensure_profile_for_user(request.user)
    context = {}
    if request.user.role == CustomUser.Roles.STUDENT:
        template = "dashboard_student.html"
        if hasattr(request.user, "student_profile"):
            context["tasks"] = LearningMaterial.objects.filter(
                student=request.user.student_profile, kind=LearningMaterial.Kind.TASK
            )
    elif request.user.role == CustomUser.Roles.PARENT:
        template = "dashboard_parent.html"
        if hasattr(request.user, "parent_profile"):
            students = request.user.parent_profile.students.all()
            context["solutions"] = LearningMaterial.objects.filter(
                student__in=students, kind=LearningMaterial.Kind.SOLUTION
            )
    elif request.user.role == CustomUser.Roles.TUTOR:
        template = "dashboard_tutor.html"
        if hasattr(request.user, "tutor_profile"):
            assigned_students = StudentProfile.objects.filter(
                lessons__tutor=request.user.tutor_profile
            )
            new_students_with_links = StudentProfile.objects.filter(
                lessons__isnull=True
            ).filter(
                Q(zoom_link__isnull=False, zoom_link__gt="")
                | Q(zumpad_link__isnull=False, zumpad_link__gt="")
            )
            zoom_students = (
                assigned_students
                | new_students_with_links
            ).select_related("user").distinct()
            context["zoom_students"] = zoom_students
            news_lessons = (
                Lesson.objects.filter(tutor=request.user.tutor_profile)
                .select_related("student__user")
                .order_by("-date", "-time")[:5]
            )
            news_items = []
            for lesson in news_lessons:
                labels = []
                if lesson.reschedule_requested:
                    labels.append("Verschiebung angefragt")
                if lesson.status == Lesson.Status.CANCELLED:
                    labels.append("Termin storniert")
                if not labels:
                    labels.append(f"Status: {lesson.get_status_display()}")
                news_items.append(
                    {
                        "student": lesson.student.user.username,
                        "date": lesson.date,
                        "time": lesson.time,
                        "text": ", ".join(labels),
                    }
                )
            context["news_items"] = news_items
    else:
        template = "dashboard_student.html"
    return render(request, template, context)


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

    if when == "past":
        lessons = Lesson.past_qs().filter(pk__in=base_qs.values_list("pk", flat=True))
    else:
        lessons = Lesson.upcoming_qs().filter(
            pk__in=base_qs.values_list("pk", flat=True)
        )

    cancelable_ids = list(
        base_qs.exclude(status=Lesson.Status.CANCELLED).values_list("id", flat=True)
    )

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
    # Tutor, Student oder Elternteil dürfen stornieren
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
        msg = "Keine kostenlose Stornierung mehr möglich. Bitte kontaktiere den Tutor sofort."
        return JsonResponse({"ok": False, "message": msg}, status=400) if is_ajax else redirect("lesson_list")

    lesson.status = Lesson.Status.CANCELLED
    lesson.cancellation_reason = reason
    lesson.reschedule_requested = False
    lesson.save(update_fields=["status", "cancellation_reason", "reschedule_requested"])
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
        msg = "Terminverlegung weniger als 5 Stunden vor Termin nicht möglich. Bitte kontaktiere den Tutor direkt."
        return JsonResponse({"ok": False, "message": msg}, status=400) if is_ajax else redirect("lesson_list")

    if lesson.reschedule_requested:
        msg = "Terminverlegung wurde bereits angefragt."
        return JsonResponse({"ok": True, "message": msg})

    lesson.reschedule_requested = True
    lesson.save(update_fields=["reschedule_requested"])
    success_msg = "Tutor wurde informiert. Termin ist als Verlegungsanfrage markiert."
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
        lessons__tutor=request.user.tutor_profile
    ).distinct()
    if not allowed_students.exists():
        allowed_students = StudentProfile.objects.all()
    heading = "Aufgabe hochladen" if kind == LearningMaterial.Kind.TASK else "Lösung hochladen"

    if request.method == "POST":
        form = LearningMaterialForm(
            data=request.POST,
            files=request.FILES,
            allowed_students=allowed_students,
        )
        if form.is_valid():
            material = form.save(commit=False)
            material.kind = kind
            material.uploaded_by = request.user.tutor_profile
            material.save()
            return redirect("dashboard")
    else:
        form = LearningMaterialForm(allowed_students=allowed_students)

    return render(
        request,
        "material_upload.html",
        {"form": form, "heading": heading},
    )


@login_required
def invoice_upload(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    allowed_students = (
        StudentProfile.objects.filter(lessons__tutor=request.user.tutor_profile)
        .select_related("user")
        .distinct()
    )
    if not allowed_students.exists():
        allowed_students = StudentProfile.objects.all().select_related("user")

    if request.method == "POST":
        form = InvoiceForm(
            data=request.POST,
            files=request.FILES,
            allowed_students=allowed_students,
        )
        if form.is_valid():
            invoice = form.save(commit=False)
            invoice.uploaded_by = request.user.tutor_profile
            invoice.save()
            return redirect("dashboard")
    else:
        form = InvoiceForm(allowed_students=allowed_students)

    return render(
        request,
        "invoice_upload.html",
        {
            "form": form,
            "heading": "Rechnung hochladen",
            "tutor_invoices": Invoice.objects.filter(uploaded_by=request.user.tutor_profile)
            .select_related("student__user")
            .order_by("-uploaded_at"),
        },
    )


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
        {"form": form, "lesson": lesson},
    )


@login_required
def progress_view(request, student_id=None):
    _ensure_profile_for_user(request.user)
    entries = ProgressEntry.objects.none()
    viewed_student = None
    student_list = StudentProfile.objects.none()

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
        student_list = (
            StudentProfile.objects.filter(
                lessons__tutor=request.user.tutor_profile
            ).distinct()
        )
        if student_id is None:
            entries = ProgressEntry.objects.filter(
                lesson__tutor=request.user.tutor_profile
            )
        else:
            viewed_student = get_object_or_404(student_list, pk=student_id)
            entries = ProgressEntry.objects.filter(lesson__student=viewed_student)

    return render(
        request,
        "progress.html",
        {
            "entries": entries,
            "viewed_student": viewed_student,
            "student_id": student_id,
            "student_list": student_list,
        },
    )


@login_required
def invoice_list(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.PARENT or not hasattr(request.user, "parent_profile"):
        return redirect("dashboard")
    students = request.user.parent_profile.students.all()
    invoices = Invoice.objects.filter(student__in=students).select_related("student__user", "uploaded_by__user")
    return render(
        request,
        "invoice_list.html",
        {"invoices": invoices},
    )
