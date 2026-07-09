from .common import *


@login_required
def holiday_surveys(request):
    _ensure_profile_for_user(request.user)
    if request.user.role == CustomUser.Roles.TUTOR and hasattr(request.user, "tutor_profile"):
        tutor_profile = request.user.tutor_profile
        assigned_students = list(_assigned_students_qs(tutor_profile))

        if request.method == "POST":
            form = HolidaySurveyForm(request.POST)
            if form.is_valid():
                survey = form.save(commit=False)
                survey.tutor = tutor_profile
                survey.save()
                created_count = 0
                for student in assigned_students:
                    response = HolidaySurveyResponse.objects.create(
                        survey=survey,
                        student=student,
                    )
                    notify_holiday_survey_created(request, response)
                    created_count += 1
                messages.success(
                    request,
                    f"Umfrage wurde erstellt und an {created_count} SchülerInnen/Eltern versendet.",
                )
                return redirect("holiday_surveys")
        else:
            form = HolidaySurveyForm(initial={"question": "Nachhilfe in den kommenden Ferien?"})

        surveys = HolidaySurvey.objects.filter(tutor=tutor_profile).prefetch_related(
            "responses__student__user",
            "responses__parent__user",
        )
        for survey in surveys:
            responses = list(survey.responses.all())
            survey.yes_responses = [response for response in responses if response.answer == HolidaySurveyResponse.Answer.YES]
            survey.no_responses = [response for response in responses if response.answer == HolidaySurveyResponse.Answer.NO]
            survey.open_responses = [response for response in responses if not response.answer]

        return render(
            request,
            "holiday_surveys_tutor.html",
            {
                "form": form,
                "surveys": surveys,
            },
        )

    if request.user.role == CustomUser.Roles.PARENT and hasattr(request.user, "parent_profile"):
        parent_profile = request.user.parent_profile
        responses = HolidaySurveyResponse.objects.filter(
            student__in=parent_profile.students.all(),
        ).select_related(
            "student__user",
            "survey__tutor__user",
            "parent__user",
        ).order_by("-survey__created_at", "student__user__last_name")

        if request.method == "POST":
            response = get_object_or_404(
                responses,
                pk=request.POST.get("response_id"),
            )
            form = HolidaySurveyAnswerForm(request.POST, instance=response)
            if form.is_valid():
                updated = form.save(commit=False)
                updated.parent = parent_profile
                updated.answered_at = timezone.now()
                updated.save()
                messages.success(request, "Deine Antwort wurde gespeichert.")
                return redirect("holiday_surveys")
        response_items = [
            {
                "response": response,
                "form": HolidaySurveyAnswerForm(instance=response),
            }
            for response in responses
        ]
        return render(
            request,
            "holiday_surveys_parent.html",
            {
                "response_items": response_items,
            },
        )

    if _is_independent_student_user(request.user) and hasattr(request.user, "student_profile"):
        student_profile = request.user.student_profile
        responses = HolidaySurveyResponse.objects.filter(
            student=student_profile,
        ).select_related(
            "student__user",
            "survey__tutor__user",
            "parent__user",
        ).order_by("-survey__created_at")

        if request.method == "POST":
            response = get_object_or_404(
                responses,
                pk=request.POST.get("response_id"),
            )
            form = HolidaySurveyAnswerForm(request.POST, instance=response)
            if form.is_valid():
                updated = form.save(commit=False)
                updated.parent = None
                updated.answered_at = timezone.now()
                updated.save()
                messages.success(request, "Deine Antwort wurde gespeichert.")
                return redirect("holiday_surveys")
        response_items = [
            {
                "response": response,
                "form": HolidaySurveyAnswerForm(instance=response),
            }
            for response in responses
        ]
        return render(
            request,
            "holiday_surveys_parent.html",
            {
                "response_items": response_items,
                "is_independent_student": True,
            },
        )

    return redirect("dashboard")


@login_required
def profile_view(request):
    _ensure_profile_for_user(request.user)

    form_class = {
        CustomUser.Roles.PARENT: ParentProfileForm,
        CustomUser.Roles.STUDENT: StudentProfileForm,
        CustomUser.Roles.INDEPENDENT_STUDENT: StudentProfileForm,
        CustomUser.Roles.TUTOR: TutorProfileForm,
    }.get(request.user.role, ParentProfileForm)

    uses_address_autocomplete = (
        _is_learning_profile_user(request.user)
        or request.user.role == CustomUser.Roles.TUTOR
    )
    form_kwargs = {"user": request.user}
    if request.user.role == CustomUser.Roles.TUTOR:
        form_kwargs["can_manage_tutor_status"] = _has_admin_access(request.user)

    if request.method == "POST":
        form = form_class(request.POST, request.FILES, **form_kwargs)
        if form.is_valid():
            user = form.save()
            if getattr(form, "email_change_requested", False):
                try:
                    _send_email_change_confirmation(request, user)
                except Exception:
                    logger.exception("E-Mail Änderungsbestätigung konnte nicht versendet werden.")
                    messages.error(
                        request,
                        "Die neue E-Mail wurde vorgemerkt, aber die Bestätigungsmail konnte nicht versendet werden.",
                    )
                else:
                    messages.success(
                        request,
                        "Wir haben dir eine Bestätigungsmail an die neue E-Mail-Adresse gesendet. Die Änderung wird erst nach Bestätigung aktiv.",
                    )
            if request.user.role == CustomUser.Roles.TUTOR and hasattr(request.user, "tutor_profile"):
                _sync_tutor_tax_number_status(request.user.tutor_profile)
            if not getattr(form, "email_change_requested", False):
                messages.success(request, "Dein Profil wurde aktualisiert.")
            return redirect("profile")
    else:
        form = form_class(**form_kwargs)

    return render(
        request,
        "profile.html",
        {
            "form": form,
            "uses_address_autocomplete": uses_address_autocomplete,
            "is_tutor_profile_form": request.user.role == CustomUser.Roles.TUTOR,
        },
    )


def profile_email_confirm(request, token):
    try:
        payload = signing.loads(
            token,
            salt=EMAIL_CHANGE_TOKEN_SALT,
            max_age=EMAIL_CHANGE_TOKEN_MAX_AGE,
        )
    except signing.SignatureExpired:
        messages.error(request, "Der Bestätigungslink für die E-Mail-Adresse ist abgelaufen.")
        return redirect("login")
    except signing.BadSignature:
        messages.error(request, "Der Bestätigungslink für die E-Mail-Adresse ist ungültig.")
        return redirect("login")

    user = get_object_or_404(CustomUser, pk=payload.get("user_id"))
    new_email = (payload.get("email") or "").strip()
    if not new_email or user.pending_email.lower() != new_email.lower():
        messages.error(request, "Diese E-Mail-Änderung ist nicht mehr aktiv.")
        return redirect("login")
    if CustomUser.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
        messages.error(request, "Diese E-Mail-Adresse wird bereits verwendet.")
        return redirect("login")

    user.email = new_email
    user.pending_email = ""
    user.pending_email_requested_at = None
    user.save(update_fields=["email", "pending_email", "pending_email_requested_at"])
    messages.success(request, "Deine neue E-Mail-Adresse wurde bestätigt.")
    return redirect("profile" if request.user.is_authenticated else "login")


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
        {
            "students": students,
            "can_resend_password_mail": _has_admin_access(request.user),
        },
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
        {
            "tutors": tutors,
            "can_resend_password_mail": _has_admin_access(request.user),
        },
    )


@login_required
def resend_set_password_email(request, user_id):
    _ensure_profile_for_user(request.user)
    if not _has_admin_access(request.user):
        messages.error(request, "Du darfst keine Passwort-Mails erneut versenden.")
        return redirect("dashboard")
    if request.method != "POST":
        return redirect("dashboard")

    next_url = request.POST.get("next") or reverse("dashboard")
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse("dashboard")

    user = get_object_or_404(CustomUser, pk=user_id)
    try:
        _send_set_password_email(request, user)
    except ValueError:
        messages.error(
            request,
            "Für diesen Nutzer ist keine E-Mail-Adresse hinterlegt.",
        )
    except Exception as exc:
        logger.exception("Erneuter Versand der Passwort-Mail fehlgeschlagen")
        messages.error(
            request,
            "Die Passwort-Mail konnte nicht erneut versendet werden. "
            f"Fehler: {exc.__class__.__name__} ({exc})",
        )
    else:
        messages.success(
            request,
            f"Die Passwort-Mail wurde erneut an {user.get_full_name() or user.username} versendet.",
        )
    return redirect(next_url)


@login_required
def parent_create(request):
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(
        request.user, "tutor_profile"
    ):
        return redirect("dashboard")
    if not _tutor_can_create_accounts(request.user.tutor_profile):
        messages.error(request, "Konten können erst angelegt werden, wenn dein TutorInnen-Status aktiv ist.")
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
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(
        request.user, "tutor_profile"
    ):
        return redirect("dashboard")
    if not _tutor_can_create_accounts(request.user.tutor_profile):
        messages.error(request, "SchülerInnen können erst angelegt werden, wenn dein TutorInnen-Status aktiv ist.")
        return redirect("dashboard")
    if request.method == "POST":
        form = StudentCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            user.student_profile.assigned_tutors.add(request.user.tutor_profile)
            user.student_profile.created_by_tutor = request.user.tutor_profile
            user.student_profile.save(update_fields=["created_by_tutor"])
            account_label = (
                "StudentIn"
                if user.role == CustomUser.Roles.INDEPENDENT_STUDENT
                else "SchülerIn"
            )
            if not user.email:
                messages.success(
                    request,
                    f"{account_label} wurde als Platzhalter ohne WebApp-Zugang angelegt.",
                )
            else:
                try:
                    _send_set_password_email(request, user)
                except Exception as exc:
                    logger.exception("E-Mail Versand fehlgeschlagen (%s)", account_label)
                    messages.error(
                        request,
                        f"{account_label} wurde angelegt, die Bestätigungs-Mail konnte jedoch nicht gesendet werden. "
                        f"Fehler: {exc.__class__.__name__} ({exc})",
                    )
                else:
                    messages.success(
                        request,
                        f"{account_label} wurde angelegt. Eine Bestätigungs-Mail wurde versendet.",
                    )
            return redirect("dashboard")
    else:
        form = StudentCreateForm()
    return render(request, "student_create.html", {"form": form})


@login_required
def independent_student_create(request):
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(
        request.user, "tutor_profile"
    ):
        return redirect("dashboard")
    if not _tutor_can_create_accounts(request.user.tutor_profile):
        messages.error(request, "StudentInnen können erst angelegt werden, wenn dein TutorInnen-Status aktiv ist.")
        return redirect("dashboard")
    if request.method == "POST":
        form = IndependentStudentCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            user.student_profile.assigned_tutors.add(request.user.tutor_profile)
            user.student_profile.created_by_tutor = request.user.tutor_profile
            user.student_profile.save(update_fields=["created_by_tutor"])
            if not user.email:
                messages.success(
                    request,
                    "StudentIn wurde als Platzhalter ohne WebApp-Zugang angelegt.",
                )
            else:
                try:
                    _send_set_password_email(request, user)
                except Exception as exc:
                    logger.exception("E-Mail Versand fehlgeschlagen (StudentIn)")
                    messages.error(
                        request,
                        "StudentIn wurde angelegt, die Bestätigungs-Mail konnte jedoch nicht gesendet werden. "
                        f"Fehler: {exc.__class__.__name__} ({exc})",
                    )
                else:
                    messages.success(
                        request,
                        "StudentIn wurde angelegt. Eine Bestätigungs-Mail wurde versendet.",
                    )
            return redirect("dashboard")
    else:
        form = IndependentStudentCreateForm()
    return render(
        request,
        "student_create.html",
        {
            "form": form,
            "heading": "StudentIn anlegen",
            "description": (
                "Lege eine selbständige StudentIn ohne Elternkonto an. "
                "E-Mail und Passwort koennen fuer Platzhalter leer bleiben."
            ),
        },
    )
