from .common import *


@login_required
def lesson_list(request):
    _ensure_profile_for_user(request.user)
    when = request.GET.get("when", "upcoming")
    today = timezone.localdate()
    try:
        week_offset = int(request.GET.get("offset", 0))
    except (TypeError, ValueError):
        week_offset = 0

    if _is_learning_profile_user(request.user) and hasattr(request.user, "student_profile"):
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

    _auto_complete_past_lessons(base_qs)

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
        ).order_by("date", "time")

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
    progress_lesson_ids = list(
        ProgressEntry.objects.filter(lesson__in=base_qs)
        .values_list("lesson_id", flat=True)
        .distinct()
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
            "progress_lesson_ids": progress_lesson_ids,
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
        form = LessonForm(
            data=request.POST,
            tutor_profile=request.user.tutor_profile,
            is_edit=False,
        )
        if form.is_valid():
            lesson = form.save(commit=False)
            lesson.tutor = request.user.tutor_profile
            recurrence_dates = _build_recurrence_dates(form.cleaned_data)
            lessons_to_create = [lesson]
            for lesson_date in recurrence_dates:
                lessons_to_create.append(
                    Lesson(
                        date=lesson_date,
                        time=lesson.time,
                        ort=lesson.ort,
                        duration_minutes=lesson.duration_minutes,
                        student=lesson.student,
                        tutor=lesson.tutor,
                        fach=lesson.fach,
                        fach_2=lesson.fach_2,
                        fach_3=lesson.fach_3,
                        status=lesson.status,
                    )
                )
            for lesson_item in lessons_to_create:
                _assign_location_and_distance(lesson_item)
                lesson_item.save()
            if recurrence_dates:
                notify_lesson_series_created(
                    request,
                    lessons_to_create,
                    repeat_interval_weeks=form.cleaned_data["repeat_interval_weeks"],
                )
                messages.success(request, f"{len(lessons_to_create)} Termine wurden angelegt.")
            else:
                notify_lesson_created(request, lesson)
            return redirect("lesson_list")
    else:
        form = LessonForm(tutor_profile=request.user.tutor_profile, is_edit=False)
    return render(request, "lesson_form.html", {"form": form})


@login_required
def lesson_cancel(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if not can_cancel_lesson(request.user, lesson):
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
    is_chargeable = time_until_lesson < timedelta(hours=CANCELLATION_FREE_HOURS)

    lesson.status = Lesson.Status.CANCELLED
    lesson.cancellation_reason = reason
    lesson.cancelled_at = now
    lesson.cancellation_chargeable = is_chargeable
    lesson.reschedule_requested = False
    lesson.save(
        update_fields=[
            "status",
            "cancellation_reason",
            "cancelled_at",
            "cancellation_chargeable",
            "reschedule_requested",
        ]
    )
    notify_lesson_cancelled(
        request,
        lesson,
        actor_label=_actor_label(request.user),
        reason=reason,
        include_tutor=request.user.role != CustomUser.Roles.TUTOR,
    )
    success_message = "Stornierungsanfrage wurde gespeichert."
    if is_chargeable:
        success_message = (
            "Stornierung wurde gespeichert. Da weniger als 5 Stunden vor Termin storniert wurde, "
            "ist der Termin kostenpflichtig und erscheint auf der Rechnung."
        )
    if is_ajax:
        return JsonResponse({"ok": True, "message": success_message})
    return redirect("lesson_list")


@login_required
def lesson_reschedule_request(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

    if not can_request_lesson_reschedule(request.user, lesson):
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
def lesson_google_calendar(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    if not can_view_lesson(request.user, lesson):
        return redirect("lesson_list")
    return redirect(_lesson_google_calendar_url_for_user(request.user, lesson))


@login_required
def lesson_ics(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    if not can_view_lesson(request.user, lesson):
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
    if not can_manage_lesson(request.user, lesson):
        return redirect("lesson_list")
    next_url = request.GET.get("next") or request.POST.get("next") or reverse("lesson_list")
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = reverse("lesson_list")

    if request.method == "POST":
        was_cancelled_before = lesson.status == Lesson.Status.CANCELLED
        form = LessonForm(
            data=request.POST,
            instance=lesson,
            tutor_profile=request.user.tutor_profile,
            is_edit=True,
            allowed_students=None,
        )
        if form.is_valid():
            updated = form.save(commit=False)
            if updated.status == Lesson.Status.CANCELLED:
                if not was_cancelled_before or updated.cancelled_at is None:
                    cancelled_at = timezone.now()
                    updated.cancelled_at = cancelled_at
                    updated.cancellation_chargeable = _is_chargeable_cancellation(
                        updated, cancelled_at
                    )
            else:
                updated.cancelled_at = None
                updated.cancellation_chargeable = False
            _assign_location_and_distance(updated)
            updated.save()
            notify_lesson_changed(request, updated)
            return redirect(next_url)
    else:
        form = LessonForm(
            instance=lesson,
            tutor_profile=request.user.tutor_profile,
            is_edit=True,
            allowed_students=None,
        )

    return render(
        request,
        "lesson_form.html",
        {
            "form": form,
            "is_edit": True,
            "lesson": lesson,
            "cancel_url": next_url,
            "next_url": next_url,
        },
    )


@login_required
def lesson_delete(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    if not can_manage_lesson(request.user, lesson):
        return redirect("lesson_list")

    if request.method == "POST":
        next_url = request.POST.get("next") or reverse("lesson_list")
        if not url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            next_url = reverse("lesson_list")
        delete_scope = request.POST.get("delete_scope", "single")
        lessons_to_delete = Lesson.objects.filter(pk=lesson.pk)
        if delete_scope == "same_weekday_time":
            lessons_to_delete = Lesson.objects.filter(
                tutor=lesson.tutor,
                student=lesson.student,
                date__week_day=lesson.date.isoweekday() % 7 + 1,
                time=lesson.time,
            )
        elif delete_scope == "future_student":
            lessons_to_delete = Lesson.objects.filter(
                tutor=lesson.tutor,
                student=lesson.student,
            ).filter(
                Q(date__gt=lesson.date)
                | Q(date=lesson.date, time__gte=lesson.time)
            )
        deleted_count, _ = lessons_to_delete.delete()
        if deleted_count == 1:
            messages.success(request, "Termin wurde gelöscht.")
        else:
            messages.success(request, f"{deleted_count} Termine wurden gelöscht.")
        return redirect(next_url)
    return redirect("lesson_edit", lesson_id=lesson_id)


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
            "rating_fields": [form[name] for name in ("rating", "rating_fach_2", "rating_fach_3") if name in form.fields],
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
            "rating_fields": [form[name] for name in ("rating", "rating_fach_2", "rating_fach_3") if name in form.fields],
        },
    )


@login_required
def progress_delete(request, entry_id):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(
        request.user, "tutor_profile"
    ):
        return redirect("progress")

    if request.method != "POST":
        return redirect("progress")

    tutor_profile = request.user.tutor_profile
    entry = get_object_or_404(
        ProgressEntry.objects.select_related("lesson__student__user", "lesson__tutor"),
        pk=entry_id,
        lesson__tutor=tutor_profile,
    )
    next_url = request.POST.get("next") or reverse(
        "progress_student", args=[entry.lesson.student_id]
    )
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = reverse("progress_student", args=[entry.lesson.student_id])

    entry.delete()
    messages.success(request, "Lernfortschrittseintrag wurde gelöscht.")
    return redirect(next_url)


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

    if _is_learning_profile_user(request.user) and hasattr(request.user, "student_profile"):
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
                lesson__student__in=student_list
            )
        else:
            viewed_student = get_object_or_404(student_list, pk=student_id)
            entries = ProgressEntry.objects.filter(
                lesson__student=viewed_student,
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

    entries = entries.select_related("lesson__student__user", "lesson__tutor__user").order_by(
        "-lesson__date",
        "-lesson__time",
        "-created_at",
    )

    show_progress_chart = request.user.role in {
        CustomUser.Roles.STUDENT,
        CustomUser.Roles.INDEPENDENT_STUDENT,
        CustomUser.Roles.PARENT,
        CustomUser.Roles.TUTOR,
    }
    tutor_chart_requires_student_selection = (
        request.user.role == CustomUser.Roles.TUTOR
        and viewed_student is None
        and not selected_student
    )
    chart_entries = (
        ProgressEntry.objects.none() if tutor_chart_requires_student_selection else entries
    )
    progress_chart_data = (
        _build_progress_chart_data(
            chart_entries,
            include_student_name=(
                request.user.role == CustomUser.Roles.PARENT and viewed_student is None
            ),
        )
        if show_progress_chart
        else {"labels": [], "date_keys": [], "detail_labels": [], "datasets": []}
    )

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
            "show_progress_chart": show_progress_chart,
            "tutor_chart_requires_student_selection": tutor_chart_requires_student_selection,
            "progress_chart_data": progress_chart_data,
            "current_tutor_profile_id": (
                request.user.tutor_profile.id
                if request.user.role == CustomUser.Roles.TUTOR
                and hasattr(request.user, "tutor_profile")
                else None
            ),
        },
    )
