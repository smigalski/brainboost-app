from .common import *


@login_required
def dashboard(request):
    _ensure_profile_for_user(request.user)
    context = {"show_faq_target_filters": _has_faq_admin_access(request.user)}
    if _is_learning_profile_user(request.user):
        template = "dashboard_student.html"
        if hasattr(request.user, "student_profile"):
            student_profile = request.user.student_profile
            _auto_complete_past_lessons(Lesson.objects.filter(student=student_profile))
            context["news_items"] = _student_news_items(student_profile)
            context["faq_items"] = _faq_items_for_target("student")
            context["faq_submission_form"] = FAQSubmissionForm(
                initial={"show_for_students": True}
            )
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
            student_progress_entries = (
                ProgressEntry.objects.filter(lesson__student=student_profile)
                .select_related("lesson__tutor__user")
            )
            context["progress_entries"] = student_progress_entries.order_by(
                "-lesson__date", "-lesson__time", "-created_at"
            )[:3]
            context["show_progress_chart"] = True
            context["progress_chart_data"] = _build_progress_chart_data(student_progress_entries)
            context["assigned_tutors"] = student_profile.assigned_tutors.select_related(
                "user"
            ).distinct()
            context["materials"] = (
                LearningMaterial.objects.filter(student=student_profile)
                .select_related("student__user", "uploaded_by__user", "related_task")
                .order_by("-uploaded_at")
            )
            context["student_online_bbb_link"] = student_profile.zoom_link
            context["student_online_zumpad_link"] = student_profile.zumpad_link
            context["is_independent_student"] = _is_independent_student_user(request.user)
            if _is_independent_student_user(request.user):
                context["open_invoice_count"] = Invoice.objects.filter(
                    student=student_profile,
                    approved_at__isnull=False,
                ).exclude(payment_status=Invoice.PaymentStatus.PAID).count()
                context["open_holiday_survey_count"] = HolidaySurveyResponse.objects.filter(
                    student=student_profile,
                    answer="",
                ).count()
    elif request.user.role == CustomUser.Roles.PARENT:
        template = "dashboard_parent.html"
        if hasattr(request.user, "parent_profile"):
            _auto_complete_past_lessons(
                Lesson.objects.filter(student__in=request.user.parent_profile.students.all())
            )
            context["news_items"] = _parent_news_items(request.user.parent_profile)
            context["faq_items"] = _faq_items_for_target("parent")
            context["faq_submission_form"] = FAQSubmissionForm(
                initial={"show_for_parents": True}
            )
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
            parent_progress_entries = (
                ProgressEntry.objects.filter(lesson__student__in=students)
                .select_related("lesson__student__user", "lesson__tutor__user")
            )
            context["progress_entries"] = parent_progress_entries.order_by(
                "-lesson__date", "-lesson__time", "-created_at"
            )[:3]
            context["show_progress_chart"] = True
            context["progress_chart_data"] = _build_progress_chart_data(
                parent_progress_entries,
                include_student_name=True,
            )
            context["materials"] = (
                LearningMaterial.objects.filter(student__in=students)
                .select_related("student__user", "uploaded_by__user", "related_task")
                .order_by("-uploaded_at")
            )
            context["online_students"] = (
                students.filter(Q(zoom_link__gt="") | Q(zumpad_link__gt=""))
                .select_related("user")
                .order_by("user__first_name", "user__last_name", "user__username")
            )
    elif request.user.role == CustomUser.Roles.TUTOR:
        template = "dashboard_tutor.html"
        if hasattr(request.user, "tutor_profile"):
            tutor_profile = request.user.tutor_profile
            _auto_complete_past_lessons(
                Lesson.objects.filter(tutor=tutor_profile)
            )
            _sync_tutor_onboarding_status(tutor_profile)
            _sync_tutor_tax_number_status(tutor_profile)
            assigned_students = _assigned_students_qs(tutor_profile)
            assigned_tutors = _assigned_tutors_qs(tutor_profile)
            can_create_accounts = _tutor_can_create_accounts(tutor_profile)

            context["is_admin_tutor"] = request.user.is_superuser
            context["tutor_profile"] = tutor_profile
            context["tutor_status_label"] = tutor_profile.get_status_display()
            context["tutor_can_create_accounts"] = can_create_accounts
            context["tutor_onboarding_steps"] = _tutor_onboarding_steps(tutor_profile)
            context["can_send_broadcast_email"] = _has_admin_access(request.user)
            context["can_manage_faq"] = _has_faq_admin_access(request.user)
            context["has_parent_profiles"] = ParentProfile.objects.exists()
            context["news_items"] = _tutor_news_items(tutor_profile)
            context["faq_items"] = _faq_items_for_target("tutor")
            bbb_students = assigned_students
            context["bbb_students"] = bbb_students
            context["assigned_student_count"] = assigned_students.count()
            context["assigned_tutor_count"] = assigned_tutors.count()
            context["upcoming_lessons"] = (
                Lesson.upcoming_qs()
                .filter(tutor=tutor_profile)
                .select_related("student__user")
                .order_by("date", "time")[:5]
            )
            context["latest_holiday_survey"] = (
                HolidaySurvey.objects.filter(tutor=tutor_profile)
                .prefetch_related("responses__student__user")
                .first()
            )
            context["pending_faq_count"] = FAQItem.objects.filter(is_published=False).count()
            missing_bank_fields = _missing_tutor_bank_field_labels(tutor_profile)
            context["missing_tutor_bank_fields"] = missing_bank_fields
            if (
                tutor_profile.status
                in {
                    TutorProfile.Status.ACCEPTED,
                    TutorProfile.Status.ONBOARDING,
                    TutorProfile.Status.ACTIVE,
                }
                and missing_bank_fields
                and not request.session.get("bank_data_reminder_shown", False)
            ):
                context["show_bank_data_popup"] = True
                context["missing_tutor_bank_fields_text"] = ", ".join(missing_bank_fields)
                request.session["bank_data_reminder_shown"] = True
            if (
                _tutor_tax_number_reminder_due(tutor_profile)
                and not request.session.get("tax_number_reminder_shown", False)
            ):
                context["show_tax_number_popup"] = True
                request.session["tax_number_reminder_shown"] = True
    else:
        template = "dashboard_student.html"
    return render(request, template, context)


@login_required
def broadcast_email_send(request):
    _ensure_profile_for_user(request.user)
    if not _has_admin_access(request.user):
        messages.error(request, "Nur AdministratorInnen dürfen Rundmails versenden.")
        return redirect("dashboard")
    if request.method == "GET":
        return render(request, "broadcast_email.html", {"form": BroadcastEmailForm()})
    if request.method != "POST":
        return redirect("dashboard")

    form = BroadcastEmailForm(request.POST)
    if not form.is_valid():
        return render(request, "broadcast_email.html", {"form": form})

    audience = form.cleaned_data["audience"]
    subject = form.cleaned_data["subject"].strip()
    body = form.cleaned_data["message"].strip()
    recipients = _broadcast_recipient_emails(audience)
    if not recipients:
        messages.error(request, "Keine EmpfängerInnen mit hinterlegter E-Mail gefunden.")
        return render(request, "broadcast_email.html", {"form": form})

    sent_count, failed_count = _send_broadcast_emails(subject, body, recipients)
    if sent_count and not failed_count:
        messages.success(request, f"Rundmail erfolgreich an {sent_count} EmpfängerInnen versendet.")
    elif sent_count and failed_count:
        messages.warning(
            request,
            f"Rundmail teilweise versendet: {sent_count} erfolgreich, {failed_count} fehlgeschlagen.",
        )
    else:
        messages.error(request, "Rundmail konnte nicht versendet werden.")
    return redirect("broadcast_email_send")


@login_required
def tutor_student_assignment(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")
    _sync_temporary_tutor_assignments()

    tutor_profile = request.user.tutor_profile
    is_admin_tutor = _has_admin_access(request.user)
    requested_source_tutor = tutor_profile
    raw_source_tutor_id = request.GET.get("source_tutor")
    if request.method == "POST":
        raw_source_tutor_id = request.POST.get("source_tutor")
    if is_admin_tutor:
        if raw_source_tutor_id:
            try:
                requested_source_tutor = TutorProfile.objects.get(pk=int(raw_source_tutor_id))
            except (TutorProfile.DoesNotExist, TypeError, ValueError):
                requested_source_tutor = tutor_profile

    redirect_url = _tutor_student_assignment_url_with_source(
        current_tutor=tutor_profile,
        source_tutor=requested_source_tutor if is_admin_tutor else None,
    )
    if request.method == "POST":
        form = TutorStudentAssignmentForm(
            request.POST,
            current_tutor=tutor_profile,
            is_admin_tutor=is_admin_tutor,
            source_tutor=requested_source_tutor,
        )
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            return redirect(redirect_url)

        source_tutor = form.cleaned_data["effective_source_tutor"]
        target_tutor = form.cleaned_data["target_tutor"]
        reason = form.cleaned_data["reason"]
        selected_students = list(form.cleaned_data["student_ids"])
        end_mode = form.cleaned_data.get("temporary_end_mode") or ""
        temporary_lessons = form.cleaned_data.get("temporary_lessons")
        temporary_end_date = form.cleaned_data.get("temporary_end_date")

        for student in selected_students:
            target_was_preassigned = student.assigned_tutors.filter(pk=target_tutor.pk).exists()
            student.assigned_tutors.add(target_tutor)
            if reason == TutorStudentAssignmentForm.REASON_SUBSTITUTION:
                active_same_pair = TemporaryTutorAssignment.objects.filter(
                    is_active=True,
                    student=student,
                    source_tutor=source_tutor,
                    target_tutor=target_tutor,
                )
                for assignment in active_same_pair:
                    _close_temporary_assignment(
                        assignment,
                        TemporaryTutorAssignment.EndReason.SUPERSEDED,
                        remove_target_assignment=False,
                    )
                TemporaryTutorAssignment.objects.create(
                    source_tutor=source_tutor,
                    target_tutor=target_tutor,
                    student=student,
                    created_by=request.user,
                    end_mode=end_mode,
                    max_lessons=temporary_lessons
                    if end_mode == TutorStudentAssignmentForm.END_MODE_LESSONS
                    else None,
                    ends_on=temporary_end_date
                    if end_mode == TutorStudentAssignmentForm.END_MODE_DATE
                    else None,
                    target_was_preassigned=target_was_preassigned,
                )
        if reason == TutorStudentAssignmentForm.REASON_HANDOVER:
            for student in selected_students:
                student.assigned_tutors.remove(source_tutor)
                active_same_pair = TemporaryTutorAssignment.objects.filter(
                    is_active=True,
                    student=student,
                    source_tutor=source_tutor,
                    target_tutor=target_tutor,
                )
                for assignment in active_same_pair:
                    _close_temporary_assignment(
                        assignment,
                        TemporaryTutorAssignment.EndReason.HANDOVER,
                        remove_target_assignment=False,
                    )

        source_name = _display_name(source_tutor.user)
        target_name = _display_name(target_tutor.user)
        if reason == TutorStudentAssignmentForm.REASON_HANDOVER:
            messages.success(
                request,
                f"{len(selected_students)} SchülerInnen wurden von {source_name} an {target_name} abgegeben.",
            )
        else:
            if end_mode == TutorStudentAssignmentForm.END_MODE_LESSONS:
                end_hint = f" (automatische Rücknahme nach {temporary_lessons} Terminen)"
            else:
                end_hint = f" (automatische Rücknahme bis {temporary_end_date.strftime('%d.%m.%Y')})"
            messages.success(
                request,
                f"{len(selected_students)} SchülerInnen wurden {target_name} zur Vertretung zugewiesen{end_hint}.",
            )
        return redirect(redirect_url)

    form = TutorStudentAssignmentForm(
        current_tutor=tutor_profile,
        is_admin_tutor=is_admin_tutor,
        source_tutor=requested_source_tutor,
    )
    context = {
        "tutor_student_assignment_form": form,
        "assignment_available_students": form.fields["student_ids"].queryset,
        "assignment_source_tutor_id": requested_source_tutor.pk,
        "can_assign_students_from_other_tutors": is_admin_tutor,
    }
    if is_admin_tutor:
        context["assignment_source_tutors"] = TutorProfile.objects.select_related("user").order_by(
            "user__first_name", "user__last_name", "user__username"
        )
    return render(request, "tutor_student_assignment.html", context)


@login_required
def faq_submit(request):
    _ensure_profile_for_user(request.user)
    if request.user.role not in (
        CustomUser.Roles.PARENT,
        CustomUser.Roles.STUDENT,
        CustomUser.Roles.INDEPENDENT_STUDENT,
    ):
        return redirect("dashboard")
    if request.method != "POST":
        return redirect("dashboard")

    form_data = request.POST.copy()
    if not _has_faq_admin_access(request.user):
        form_data["audience_all"] = ""
        form_data["show_on_landing"] = ""
        if request.user.role == CustomUser.Roles.PARENT:
            form_data["show_for_parents"] = "on"
            form_data["show_for_students"] = ""
            form_data["show_for_tutors"] = ""
        else:
            form_data["show_for_students"] = "on"
            form_data["show_for_parents"] = ""
            form_data["show_for_tutors"] = ""
    form = FAQSubmissionForm(form_data)
    if form.is_valid():
        faq_item = form.save(commit=False)
        faq_item.created_by = request.user
        faq_item.is_published = False
        faq_item.answer = ""
        faq_item.save()
        messages.success(request, "Deine Frage wurde an die AdministratorInnen weitergeleitet.")
    else:
        messages.error(request, "Die Frage konnte nicht gespeichert werden. Bitte prüfe deine Eingabe.")
    return redirect("dashboard")


@login_required
def faq_admin(request):
    _ensure_profile_for_user(request.user)
    if not _has_faq_admin_access(request.user):
        return redirect("dashboard")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            create_form = FAQItemForm(request.POST)
            if create_form.is_valid():
                faq_item = create_form.save(commit=False)
                faq_item.created_by = request.user
                faq_item.is_published = True
                faq_item.save()
                messages.success(request, "FAQ wurde gespeichert.")
                return redirect(f"{reverse('faq_admin')}#faq-item-{faq_item.id}")
        elif action == "publish":
            faq_item = get_object_or_404(FAQItem, pk=request.POST.get("faq_id"), is_published=False)
            publish_form = FAQItemForm(request.POST, instance=faq_item)
            if publish_form.is_valid():
                updated = publish_form.save(commit=False)
                updated.created_by = faq_item.created_by or request.user
                updated.is_published = True
                updated.save()
                messages.success(request, "Frage wurde beantwortet und zur FAQ hinzugefügt.")
                return redirect(f"{reverse('faq_admin')}#faq-item-{updated.id}")
        elif action == "update":
            faq_item = get_object_or_404(FAQItem, pk=request.POST.get("faq_id"), is_published=True)
            update_form = FAQItemForm(request.POST, instance=faq_item)
            if update_form.is_valid():
                updated = update_form.save()
                messages.success(request, "FAQ wurde aktualisiert.")
                return redirect(f"{reverse('faq_admin')}#faq-item-{updated.id}")

    create_form = FAQItemForm(initial={"audience_all": True})
    published_items = FAQItem.objects.filter(is_published=True).order_by("question")
    pending_items = FAQItem.objects.filter(is_published=False).order_by("-created_at")
    pending_forms = [(item, FAQItemForm(instance=item)) for item in pending_items]
    published_forms = [(item, FAQItemForm(instance=item)) for item in published_items]
    return render(
        request,
        "faq_admin.html",
        {
            "create_form": create_form,
            "pending_forms": pending_forms,
            "published_forms": published_forms,
        },
    )


@login_required
def admin_tasks(request):
    _ensure_profile_for_user(request.user)
    if not _has_admin_access(request.user):
        return redirect("dashboard")

    selected_tab = request.GET.get("tab", "tasks")
    if selected_tab not in {"ideas", "tasks", "kanban", "leads"}:
        selected_tab = "tasks"

    create_form = AdminTaskCreateForm()
    idea_create_form = AdminIdeaCreateForm()
    vision_create_form = AdminIdeaCreateForm(
        initial={"category": AdminIdea.Category.VISION}
    )
    improvement_create_form = AdminIdeaCreateForm(
        initial={"category": AdminIdea.Category.IMPROVEMENT}
    )

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "create":
            create_form = AdminTaskCreateForm(request.POST)
            if create_form.is_valid():
                task = create_form.save(commit=False)
                task.status = AdminTask.Status.TODO
                task.created_by = request.user
                source_idea_id = request.POST.get("source_idea_id")
                source_idea = None
                if source_idea_id:
                    source_idea = AdminIdea.objects.filter(pk=source_idea_id).first()
                    if source_idea and source_idea.image:
                        task.image = source_idea.image.name
                task.save()
                if source_idea:
                    source_idea.delete()
                messages.success(request, "Aufgabe wurde hinzugefügt.")
                return_tab = request.POST.get("return_tab")
                if return_tab not in {"ideas", "tasks", "kanban"}:
                    return_tab = "tasks"
                return redirect(f"{reverse('admin_tasks')}?tab={return_tab}#task-{task.id}")
            messages.error(request, "Aufgabe konnte nicht gespeichert werden. Bitte Eingaben prüfen.")
            selected_tab = request.POST.get("return_tab") if request.POST.get("return_tab") in {"ideas", "tasks"} else "tasks"
        elif action == "update":
            task = get_object_or_404(AdminTask, pk=request.POST.get("task_id"))
            update_form = AdminTaskUpdateForm(request.POST, instance=task)
            if update_form.is_valid():
                update_form.save()
                messages.success(request, "Aufgabe wurde aktualisiert.")
            else:
                messages.error(request, "Aufgabe konnte nicht aktualisiert werden.")
            return redirect(f"{reverse('admin_tasks')}?tab=tasks#task-{task.id}")
        elif action == "delete":
            task = get_object_or_404(AdminTask, pk=request.POST.get("task_id"))
            task.delete()
            messages.success(request, "Aufgabe wurde gelöscht.")
            return redirect(f"{reverse('admin_tasks')}?tab=tasks")
        elif action == "complete":
            task = get_object_or_404(AdminTask, pk=request.POST.get("task_id"))
            task.status = AdminTask.Status.DONE
            task.save(update_fields=["status", "updated_at"])
            messages.success(request, "Aufgabe wurde als erledigt markiert.")
            return redirect(f"{reverse('admin_tasks')}?tab=tasks")
        elif action == "create_idea":
            idea_create_form = AdminIdeaCreateForm(request.POST, request.FILES)
            selected_tab = "ideas"
            if idea_create_form.is_valid():
                idea = idea_create_form.save(commit=False)
                idea.created_by = request.user
                if idea.category != AdminIdea.Category.IMPROVEMENT:
                    idea.image = None
                idea.save()
                messages.success(request, "Idee wurde hinzugefügt.")
                return redirect(f"{reverse('admin_tasks')}?tab=ideas#idea-{idea.id}")
            messages.error(request, "Idee konnte nicht gespeichert werden. Bitte Eingabe prüfen.")
            category = request.POST.get("category")
            if category == AdminIdea.Category.VISION:
                vision_create_form = idea_create_form
            else:
                improvement_create_form = idea_create_form
        elif action == "update_idea":
            idea = get_object_or_404(AdminIdea, pk=request.POST.get("idea_id"))
            idea_update_form = AdminIdeaUpdateForm(request.POST, instance=idea)
            if idea_update_form.is_valid():
                idea_update_form.save()
                messages.success(request, "Idee wurde aktualisiert.")
            else:
                messages.error(request, "Idee konnte nicht aktualisiert werden.")
            return redirect(f"{reverse('admin_tasks')}?tab=ideas#idea-{idea.id}")

    tasks = list(
        AdminTask.objects.select_related("owner")
        .order_by("status", "created_at", "id")
    )
    today = timezone.localdate()
    all_task_rows = [_admin_task_view_data(task, today) for task in tasks]
    task_rows = [
        item for item in all_task_rows if item["task"].status != AdminTask.Status.DONE
    ]
    todo_task_rows = [
        item for item in all_task_rows if item["task"].status == AdminTask.Status.TODO
    ]
    ideas = list(
        AdminIdea.objects.select_related("created_by").order_by("category", "-created_at")
    )

    kanban_columns = [
        {
            "status": AdminTask.Status.TODO,
            "label": dict(AdminTask.Status.choices)[AdminTask.Status.TODO],
            "tasks": [item for item in all_task_rows if item["task"].status == AdminTask.Status.TODO],
        },
        {
            "status": AdminTask.Status.DOING,
            "label": dict(AdminTask.Status.choices)[AdminTask.Status.DOING],
            "tasks": [item for item in all_task_rows if item["task"].status == AdminTask.Status.DOING],
        },
        {
            "status": AdminTask.Status.DONE,
            "label": dict(AdminTask.Status.choices)[AdminTask.Status.DONE],
            "tasks": [item for item in all_task_rows if item["task"].status == AdminTask.Status.DONE],
        },
    ]

    return render(
        request,
        "admin_tasks.html",
        {
            "selected_tab": selected_tab,
            "create_form": create_form,
            "vision_create_form": vision_create_form,
            "improvement_create_form": improvement_create_form,
            "vision_ideas": [
                idea for idea in ideas if idea.category == AdminIdea.Category.VISION
            ],
            "improvement_ideas": [
                idea for idea in ideas if idea.category == AdminIdea.Category.IMPROVEMENT
            ],
            "task_rows": task_rows,
            "todo_task_rows": todo_task_rows,
            "kanban_columns": kanban_columns,
            "admins": _admin_users_queryset(),
            "importance_choices": AdminTask.Importance.choices,
            "days_by_importance": _admin_task_days_by_importance(),
            "status_choices": AdminTask.Status.choices,
        },
    )


@login_required
def admin_task_status_update(request, task_id: int):
    if not _has_admin_access(request.user):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=403)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "method_not_allowed"}, status=405)

    task = get_object_or_404(AdminTask, pk=task_id)
    next_status = (request.POST.get("status") or "").strip()
    if not next_status:
        try:
            payload = json.loads(request.body.decode("utf-8"))
            next_status = str(payload.get("status", "")).strip()
        except (json.JSONDecodeError, UnicodeDecodeError):
            next_status = ""

    valid_statuses = {choice[0] for choice in AdminTask.Status.choices}
    if next_status not in valid_statuses:
        return JsonResponse({"ok": False, "error": "invalid_status"}, status=400)

    allowed_moves = {
        AdminTask.Status.TODO: {AdminTask.Status.DOING},
        AdminTask.Status.DOING: {AdminTask.Status.TODO, AdminTask.Status.DONE},
        AdminTask.Status.DONE: {AdminTask.Status.TODO, AdminTask.Status.DOING},
    }
    if next_status != task.status and next_status not in allowed_moves.get(task.status, set()):
        return JsonResponse({"ok": False, "error": "move_not_allowed"}, status=400)

    task.status = next_status
    task.save(update_fields=["status", "updated_at"])
    today = timezone.localdate()
    task_data = _admin_task_view_data(task, today)
    return JsonResponse(
        {
            "ok": True,
            "task_id": task.id,
            "status": task.status,
            "status_label": task.get_status_display(),
            "deadline_label": task_data["deadline_label"],
        }
    )
