from .common import *


def landing_page(request):
    form = EmailOrUsernameAuthenticationForm(request)
    return render(
        request,
        "landing.html",
        {
            "form": form,
            "faq_items": _faq_items_for_target("landing"),
            "google_reviews": _get_google_reviews_summary(),
        },
    )


def contact(request):
    initial = _lead_initial_from_query(request)
    if request.method == "POST":
        post_data = request.POST.copy()
        for key, value in initial.items():
            if key in {"role"}:
                continue
            if value and not post_data.get(key):
                post_data[key] = value
        form = LeadForm(post_data, initial=initial)
        if form.is_valid():
            lead = form.save(commit=False)
            if lead.role == Lead.Role.TUTOR:
                lead.subject = lead.teaching_subjects
                lead.grade = lead.teaching_grades
            for key, value in _lead_attribution_from_request(request).items():
                if value:
                    setattr(lead, key, value)
            lead.save()
            if lead.role == Lead.Role.TUTOR:
                try:
                    user = _create_tutor_from_lead(lead, mark_lead_won=False)
                    _send_set_password_email(request, user)
                except Exception:
                    logger.exception(
                        "TutorInnen-Bewerbung wurde gespeichert, aber die Registrierungsmail konnte nicht versendet werden."
                    )
            notify_lead_created(lead)
            if lead.role == Lead.Role.TUTOR:
                request.session["lead_tracking_pending"] = "tutor"
                return redirect("lead_thanks_tutor")
            request.session["lead_tracking_pending"] = "tutoring"
            return redirect("lead_thanks_tutoring")
    else:
        form = LeadForm(initial=initial)
    return render(request, "contact.html", {"form": form})


def lead_thanks_tutoring(request):
    tracking_pending = request.session.pop("lead_tracking_pending", "") == "tutoring"
    return render(
        request,
        "lead_thanks.html",
        {
            "title": "Danke für deine Anfrage",
            "message": "Wir melden uns zeitnah bei dir und besprechen die passenden nächsten Schritte.",
            "meta_lead_content_name": "Nachhilfe Anfrage" if tracking_pending else "",
            "meta_lead_content_category": "parents_students" if tracking_pending else "",
        },
    )


def lead_thanks_tutor(request):
    tracking_pending = request.session.pop("lead_tracking_pending", "") == "tutor"
    return render(
        request,
        "lead_thanks.html",
        {
            "title": "Danke für deine Bewerbung",
            "message": "Wir haben deine Bewerbung erhalten. Du bekommst per E-Mail den Link zum Passwortsetzen und kannst danach deinen Status im BrainBoost-Dashboard verfolgen.",
            "meta_lead_content_name": "Tutor Bewerbung" if tracking_pending else "",
            "meta_lead_content_category": "tutors" if tracking_pending else "",
        },
    )


def nachhilfe_anfrage(request):
    return render(request, "landing_alle.html")


def landing_eltern(request):
    return render(request, "landing_eltern.html")


def landing_schuelerinnen(request):
    return render(request, "landing_schuelerinnen.html")


def tutor_werden(request):
    return render(request, "landing_tutor.html")


def tutorin_werden(request):
    return redirect("tutor_werden")


def brainboost_feedback(request):
    valid_roles = {choice[0] for choice in BrainBoostFeedback.Audience.choices}
    valid_sources = {choice[0] for choice in BrainBoostFeedback.Source.choices}

    initial_role = request.GET.get("role", "").strip()
    initial_source = request.GET.get("source", BrainBoostFeedback.Source.DIRECT).strip()
    if initial_role not in valid_roles:
        initial_role = BrainBoostFeedback.Audience.OTHER
    if initial_source not in valid_sources:
        initial_source = BrainBoostFeedback.Source.DIRECT

    if request.method == "POST":
        form = BrainBoostFeedbackForm(request.POST)
        source = request.POST.get("source", BrainBoostFeedback.Source.DIRECT).strip()
        if source not in valid_sources:
            source = BrainBoostFeedback.Source.DIRECT
        if form.is_valid():
            feedback = form.save(commit=False)
            feedback.source = source
            feedback.save()
            messages.success(request, "Danke! Dein Feedback wurde anonym gespeichert.")
            return redirect(
                f"{reverse('brainboost_feedback')}?submitted=1&role={feedback.audience}&source={feedback.source}"
            )
    else:
        form = BrainBoostFeedbackForm(initial={"audience": initial_role})

    submitted = request.GET.get("submitted") == "1"
    return render(
        request,
        "brainboost_feedback.html",
        {
            "form": form,
            "submitted": submitted,
            "source": initial_source,
            "headline": "BrainBoost möchte die beste Nachhilfeplattform Deutschlands werden! Was ist dafür nötig?",
        },
    )


def impressum(request):
    return render(request, "impressum.html")


def agbs(request):
    return render(request, "agbs.html")


def pricing(request):
    return render(request, "pricing.html")


@login_required
def lead_dashboard(request):
    _ensure_profile_for_user(request.user)
    if not _has_admin_access(request.user):
        return redirect("dashboard")

    start_date = _valid_iso_date(request.GET.get("start_date", ""))
    end_date = _valid_iso_date(request.GET.get("end_date", ""))
    leads = _filter_leads_by_period(
        Lead.objects.select_related("converted_tutor__user"),
        start_date,
        end_date,
    )

    period_rows = (
        leads.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("-day")
    )
    open_leads = leads.filter(
        status__in=[Lead.Status.NEW, Lead.Status.CONTACTED],
        follow_up_done=False,
    ).order_by("follow_up_date", "-created_at")[:30]

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "total_leads": leads.count(),
        "new_leads_count": leads.filter(status=Lead.Status.NEW).count(),
        "open_leads_count": leads.filter(
            status__in=[Lead.Status.NEW, Lead.Status.CONTACTED],
            follow_up_done=False,
        ).count(),
        "role_rows": _lead_group_counts(leads, "role", Lead.Role.choices),
        "status_rows": _lead_group_counts(leads, "status", Lead.Status.choices),
        "subject_rows": _lead_group_counts(leads, "subject"),
        "grade_rows": _lead_group_counts(leads, "grade"),
        "campaign_rows": _lead_campaign_stats(leads),
        "period_rows": period_rows,
        "open_leads": open_leads,
        "recent_leads": leads.order_by("-created_at")[:30],
        "lead_status_choices": Lead.Status.choices,
    }
    return render(request, "lead_dashboard.html", context)


def _safe_admin_next_url(request) -> str:
    next_url = request.POST.get("next") or request.GET.get("next") or reverse("lead_dashboard")
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return reverse("lead_dashboard")
    return next_url


@login_required
def lead_mark_contacted(request, lead_id):
    _ensure_profile_for_user(request.user)
    if not _has_admin_access(request.user):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=403)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "method_not_allowed"}, status=405)

    lead = get_object_or_404(Lead, pk=lead_id)
    if lead.status == Lead.Status.NEW:
        lead.status = Lead.Status.CONTACTED
        if not lead.contacted_at:
            lead.contacted_at = timezone.now()
        lead.save(update_fields=["status", "contacted_at", "updated_at"])
    elif lead.status == Lead.Status.CONTACTED and not lead.contacted_at:
        lead.contacted_at = timezone.now()
        lead.save(update_fields=["contacted_at", "updated_at"])

    return JsonResponse(
        {
            "ok": True,
            "status": lead.status,
            "status_label": lead.get_status_display(),
            "contacted_at": lead.contacted_at.isoformat() if lead.contacted_at else "",
        }
    )


@login_required
def lead_update_status(request, lead_id):
    _ensure_profile_for_user(request.user)
    if not _has_admin_access(request.user):
        messages.error(request, "Du darfst Lead-Status nicht ändern.")
        return redirect("dashboard")
    next_url = _safe_admin_next_url(request)
    if request.method != "POST":
        return redirect(next_url)

    lead = get_object_or_404(Lead, pk=lead_id)
    next_status = (request.POST.get("status") or "").strip()
    valid_statuses = {choice[0] for choice in Lead.Status.choices}
    if next_status not in valid_statuses:
        messages.error(request, "Ungültiger Lead-Status.")
        return redirect(next_url)

    update_fields = ["status", "updated_at"]
    lead.status = next_status
    if next_status == Lead.Status.CONTACTED and not lead.contacted_at:
        lead.contacted_at = timezone.now()
        update_fields.append("contacted_at")
    lead.save(update_fields=update_fields)
    messages.success(request, f"Status von {lead.name} wurde auf {lead.get_status_display()} gesetzt.")
    return redirect(next_url)


@login_required
def lead_delete(request, lead_id):
    _ensure_profile_for_user(request.user)
    if not _has_admin_access(request.user):
        messages.error(request, "Du darfst Leads nicht entfernen.")
        return redirect("dashboard")
    next_url = _safe_admin_next_url(request)
    if request.method != "POST":
        return redirect(next_url)

    lead = get_object_or_404(Lead, pk=lead_id)
    lead_name = lead.name
    lead.delete()
    messages.success(request, f"Lead {lead_name} wurde entfernt.")
    return redirect(next_url)


@login_required
def lead_convert_to_tutor(request, lead_id):
    _ensure_profile_for_user(request.user)
    if not _has_admin_access(request.user):
        messages.error(request, "Du darfst TutorInnen-Leads nicht umwandeln.")
        return redirect("dashboard")
    if request.method != "POST":
        return redirect("lead_dashboard")

    next_url = _safe_admin_next_url(request)

    lead = get_object_or_404(Lead.objects.select_related("converted_tutor__user"), pk=lead_id)
    if lead.role != Lead.Role.TUTOR:
        messages.error(request, "Nur TutorInnen-Leads können in TutorInnen umgewandelt werden.")
        return redirect(next_url)

    try:
        user = _create_tutor_from_lead(lead)
        _send_set_password_email(request, user)
    except ValueError:
        messages.error(
            request,
            "Für diesen TutorInnen-Lead ist keine E-Mail-Adresse hinterlegt.",
        )
    except SMTPAuthenticationError:
        logger.exception("SMTP-Anmeldung beim Versand der TutorInnen-Mail fehlgeschlagen.")
        messages.error(
            request,
            "TutorIn wurde angelegt, aber die Mail konnte nicht versendet werden: "
            "SMTP-Anmeldung fehlgeschlagen. Bitte prüfe EMAIL_HOST_USER und EMAIL_HOST_PASSWORD "
            "in der Produktionsumgebung und sende die Mail danach erneut.",
        )
    except Exception as exc:
        logger.exception("TutorInnen-Lead konnte nicht umgewandelt oder benachrichtigt werden.")
        messages.error(
            request,
            "TutorIn wurde angelegt, aber die Mail konnte nicht versendet werden. "
            "Bitte prüfe die Mail-Konfiguration und sende die Mail danach erneut.",
        )
    else:
        action_label = "erneut versendet" if lead.converted_tutor_id else "versendet"
        messages.success(
            request,
            f"{lead.name} wurde als TutorIn angelegt. Die Mail zum Passwortsetzen und Profilausfüllen wurde {action_label}.",
        )
    return redirect(next_url)


@login_required
def lead_export_csv(request):
    _ensure_profile_for_user(request.user)
    if not _has_admin_access(request.user):
        return redirect("dashboard")

    start_date = _valid_iso_date(request.GET.get("start_date", ""))
    end_date = _valid_iso_date(request.GET.get("end_date", ""))
    leads = _filter_leads_by_period(Lead.objects.all(), start_date, end_date)
    return _write_leads_csv(leads)


@login_required
def campaign_link_builder(request):
    _ensure_profile_for_user(request.user)
    if not _has_admin_access(request.user):
        return redirect("dashboard")

    initial = {
        "base_url": request.build_absolute_uri(reverse("nachhilfe_anfrage")),
        "utm_source": "meta",
        "utm_medium": "paid_social",
    }
    form = CampaignLinkBuilderForm(request.GET or None, initial=initial)
    generated_url = ""
    if form.is_valid():
        params = {
            "utm_source": form.cleaned_data["utm_source"].strip(),
            "utm_medium": form.cleaned_data["utm_medium"].strip(),
            "utm_campaign": form.cleaned_data["utm_campaign"].strip(),
            "utm_content": form.cleaned_data["utm_content"].strip(),
            "utm_term": form.cleaned_data["utm_term"].strip(),
            "role": form.cleaned_data["role"].strip(),
        }
        generated_url = _build_campaign_url(form.cleaned_data["base_url"], params)
    elif not request.GET:
        form = CampaignLinkBuilderForm(initial=initial)

    return render(
        request,
        "campaign_link_builder.html",
        {"form": form, "generated_url": generated_url},
    )


@login_required
def meta_ads_guide(request):
    _ensure_profile_for_user(request.user)
    if not _has_admin_access(request.user):
        return redirect("dashboard")
    return render(request, "meta_ads_guide.html")
