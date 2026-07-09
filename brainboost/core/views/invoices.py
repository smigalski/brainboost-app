from .common import *


@login_required
def invoice_upload(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    tutor_profile = request.user.tutor_profile
    _auto_complete_past_lessons(Lesson.objects.filter(tutor=tutor_profile))
    allowed_students = _assigned_students_qs(tutor_profile)
    subordinate_tutors = _assigned_tutors_qs(tutor_profile)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "generate":
            generate_form = InvoiceGenerateForm(
                data=request.POST,
                allowed_students=allowed_students,
            )
            form = InvoiceForm(allowed_students=allowed_students)
            if generate_form.is_valid():
                student = generate_form.cleaned_data["student"]
                period_start = generate_form.cleaned_data["period"]
                lessons = list(
                    Lesson.objects.filter(
                        tutor=tutor_profile,
                        student=student,
                        date__year=period_start.year,
                        date__month=period_start.month,
                    )
                    .filter(
                        Q(status=Lesson.Status.COMPLETED)
                        | Q(
                            status=Lesson.Status.CANCELLED,
                            cancellation_chargeable=True,
                        )
                    )
                    .order_by("date", "time")
                )
                if not lessons:
                    generate_form.add_error(
                        "period",
                        "Für diesen Monat gibt es keine abrechenbaren Termine.",
                    )
                else:
                    invoice = None
                    try:
                        for _attempt in range(5):
                            next_invoice_number = _next_invoice_number()
                            invoice_context = _build_invoice_pdf_context(
                                tutor_profile=tutor_profile,
                                student=student,
                                period_start=period_start,
                                lessons=lessons,
                                discount_type=generate_form.cleaned_data["discount_type"],
                                discount_value=generate_form.cleaned_data["discount_value"],
                                invoice_number=_format_invoice_number(next_invoice_number),
                            )
                            pdf_bytes = _generate_invoice_pdf(
                                request=request,
                                tutor_profile=tutor_profile,
                                student=student,
                                period_start=period_start,
                                lessons=lessons,
                                invoice_context=invoice_context,
                            )
                            invoice = Invoice(
                                student=student,
                                uploaded_by=tutor_profile,
                                invoice_number=next_invoice_number,
                                sent_at=timezone.now(),
                                billing_year=period_start.year,
                                billing_month=period_start.month,
                                discount_type=invoice_context["discount_type"],
                                discount_value=invoice_context["discount_value"],
                                discount_amount=invoice_context["discount_amount"],
                                amount_total=invoice_context["total_amount"],
                            )
                            filename = _invoice_filename(invoice, next_invoice_number)
                            invoice.file.save(filename, ContentFile(pdf_bytes), save=False)
                            try:
                                invoice.save()
                            except IntegrityError:
                                if invoice.file:
                                    invoice.file.storage.delete(invoice.file.name)
                                invoice = None
                                continue
                            break
                        if invoice is None:
                            raise RuntimeError("Es konnte keine eindeutige Rechnungsnummer vergeben werden.")
                    except ValueError as exc:
                        generate_form.add_error("discount_value", str(exc))
                    except RuntimeError as exc:
                        generate_form.add_error(None, str(exc))
                    if invoice is not None:
                        if not tutor_profile.supervising_tutors.exists():
                            invoice.approved_by = tutor_profile
                            invoice.approved_at = timezone.now()
                            invoice.save(update_fields=["approved_by", "approved_at"])
                            _notify_independent_student_invoice_uploaded(request, invoice)
                            messages.success(
                                request,
                                _invoice_release_message(invoice, "generiert und direkt freigegeben"),
                            )
                        else:
                            notify_invoice_pending_approval(request, invoice)
                            messages.success(
                                request,
                                "Rechnung wurde generiert und wartet auf Freigabe.",
                            )
                        return redirect("invoice_upload")
        else:
            form = InvoiceForm(
                data=request.POST,
                files=request.FILES,
                allowed_students=allowed_students,
            )
            generate_form = InvoiceGenerateForm(allowed_students=allowed_students)
            if form.is_valid():
                invoice = form.save(commit=False)
                invoice.uploaded_by = tutor_profile
                invoice.save()
                if not tutor_profile.supervising_tutors.exists():
                    invoice.approved_by = tutor_profile
                    invoice.approved_at = timezone.now()
                    invoice.save(update_fields=["approved_by", "approved_at"])
                    _notify_independent_student_invoice_uploaded(request, invoice)
                    messages.success(
                        request,
                        _invoice_release_message(invoice, "hochgeladen und direkt freigegeben"),
                    )
                else:
                    notify_invoice_pending_approval(request, invoice)
                    messages.success(request, "Rechnung wurde hochgeladen und wartet auf Freigabe.")
                return redirect("invoice_upload")
    else:
        form = InvoiceForm(allowed_students=allowed_students)
        generate_form = InvoiceGenerateForm(allowed_students=allowed_students)

    own_invoices = (
        Invoice.objects.filter(uploaded_by=tutor_profile)
        .select_related("student__user", "uploaded_by__user", "approved_by__user")
        .prefetch_related("student__parents__user")
        .order_by("-uploaded_at")
    )
    subordinate_invoices = (
        Invoice.objects.filter(uploaded_by__in=subordinate_tutors)
        .select_related("student__user", "uploaded_by__user", "approved_by__user")
        .prefetch_related("student__parents__user")
        .order_by("-uploaded_at")
    )
    own_invoice_context = _invoice_filter_context(list(own_invoices), request)
    own_invoices = own_invoice_context["invoices"]
    subordinate_invoice_context = _invoice_filter_context(
        list(subordinate_invoices),
        request,
        person_field="tutor",
        query_prefix="subordinate_",
    )
    subordinate_invoices = subordinate_invoice_context["invoices"]
    for invoice in own_invoices:
        invoice.parent_notification_links = (
            _invoice_parent_notification_links(request, invoice) if invoice.is_approved else []
        )
        invoice.student_notification_links = (
            _invoice_student_notification_links(invoice) if invoice.is_approved else []
        )
        invoice.notification_links = invoice.parent_notification_links + invoice.student_notification_links
    for invoice in subordinate_invoices:
        invoice.parent_notification_links = (
            _invoice_parent_notification_links(request, invoice) if invoice.is_approved else []
        )
        invoice.student_notification_links = (
            _invoice_student_notification_links(invoice) if invoice.is_approved else []
        )
        invoice.notification_links = invoice.parent_notification_links + invoice.student_notification_links

    return render(
        request,
        "invoice_upload.html",
        {
            "form": form,
            "generate_form": generate_form,
            "heading": "Rechnungen",
            "tutor_invoices": own_invoices,
            "invoice_filters": own_invoice_context["filters"],
            "invoice_student_options": own_invoice_context["person_options"],
            "invoice_month_options": own_invoice_context["month_options"],
            "invoice_year_options": own_invoice_context["year_options"],
            "subordinate_invoices": subordinate_invoices,
            "subordinate_invoice_filters": subordinate_invoice_context["filters"],
            "subordinate_invoice_tutor_options": subordinate_invoice_context["person_options"],
            "subordinate_invoice_month_options": subordinate_invoice_context["month_options"],
            "subordinate_invoice_year_options": subordinate_invoice_context["year_options"],
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

    if not can_approve_invoice(request.user, invoice):
        messages.error(request, "Du darfst diese Rechnung nicht freigeben.")
        return redirect("invoice_upload")

    if invoice.is_approved:
        messages.info(request, "Diese Rechnung wurde bereits freigegeben.")
        return redirect("invoice_upload")

    invoice.approved_by = tutor_profile
    invoice.approved_at = timezone.now()
    invoice.save(update_fields=["approved_by", "approved_at"])
    _notify_independent_student_invoice_uploaded(request, invoice)
    messages.success(request, _invoice_release_message(invoice, "freigegeben"))
    return redirect("invoice_upload")


@login_required
def invoice_notify_parent(request, invoice_id, parent_id):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    invoice = get_object_or_404(
        Invoice.objects.select_related("student__user", "uploaded_by__user", "approved_by__user"),
        pk=invoice_id,
    )
    if not can_manage_invoice(request.user, invoice):
        messages.error(request, "Du darfst diese Rechnung nicht an Eltern versenden.")
        return redirect("invoice_upload")
    if not invoice.is_approved:
        messages.error(request, "Die Rechnung muss erst freigegeben werden.")
        return redirect("invoice_upload")

    parent = get_object_or_404(invoice.student.parents.select_related("user"), pk=parent_id)
    _finalize_invoice_number_and_filename(invoice)
    notify_invoice_parent(request, invoice, parent)

    number = _normalize_whatsapp_number(parent.phone_number)
    if not number:
        messages.info(request, "Für dieses Elternteil ist keine WhatsApp-Nummer hinterlegt. Die Mail wurde versendet.")
        return redirect("invoice_upload")

    message = _invoice_whatsapp_message(request, invoice, parent)
    return redirect(f"https://wa.me/{number}?text={quote(message)}")


@login_required
def invoice_notify_student(request, invoice_id):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    invoice = get_object_or_404(
        Invoice.objects.select_related("student__user", "uploaded_by__user", "approved_by__user")
        .prefetch_related("student__parents__user"),
        pk=invoice_id,
    )
    if not can_manage_invoice(request.user, invoice):
        messages.error(request, "Du darfst diese Rechnung nicht an SchülerInnen/StudentInnen versenden.")
        return redirect("invoice_upload")
    if not invoice.is_approved:
        messages.error(request, "Die Rechnung muss erst freigegeben werden.")
        return redirect("invoice_upload")
    if not _invoice_student_pays_directly(invoice):
        messages.error(request, "Diese Rechnung wird über Eltern verwaltet.")
        return redirect("invoice_upload")

    _finalize_invoice_number_and_filename(invoice)
    notify_invoice_student(request, invoice)

    number = _normalize_whatsapp_number(invoice.student.phone_number)
    if not number:
        messages.info(request, "Für diese SchülerIn/StudentIn ist keine WhatsApp-Nummer hinterlegt. Die Mail wurde versendet, falls eine E-Mail-Adresse vorhanden ist.")
        return redirect("invoice_upload")

    message = _invoice_student_whatsapp_message(request, invoice)
    return redirect(f"https://wa.me/{number}?text={quote(message)}")


@login_required
def invoice_delete(request, invoice_id):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    if request.method != "POST":
        return redirect("invoice_upload")

    invoice = get_object_or_404(
        Invoice.objects.select_related("student__user", "uploaded_by__user"),
        pk=invoice_id,
    )
    if not can_delete_invoice(request.user, invoice):
        messages.error(request, "Du darfst diese Rechnung nicht löschen.")
        return redirect("invoice_upload")

    invoice.file.delete(save=False)
    invoice.delete()
    messages.success(request, "Rechnung wurde gelöscht.")
    return redirect("invoice_upload")


@login_required
def invoice_select_payment(request, invoice_id, method):
    _ensure_profile_for_user(request.user)
    is_parent = request.user.role == CustomUser.Roles.PARENT and hasattr(
        request.user, "parent_profile"
    )
    is_independent_student = _is_independent_student_user(request.user) and hasattr(
        request.user, "student_profile"
    )
    if not (is_parent or is_independent_student):
        return redirect("invoice_list")
    if request.method != "POST":
        return redirect("invoice_list")

    if method not in {Invoice.PaymentMethod.CASH, Invoice.PaymentMethod.BANK_TRANSFER}:
        messages.error(request, "Diese Zahlungsart ist nicht verfügbar.")
        return redirect("invoice_list")

    parent_profile = request.user.parent_profile if is_parent else None
    invoice_qs = payer_invoice_qs(request.user).select_related(
        "student__user",
        "uploaded_by__user",
    ).filter(pk=invoice_id)
    invoice = get_object_or_404(invoice_qs)
    if invoice.payment_status == Invoice.PaymentStatus.PAID:
        messages.info(request, "Diese Rechnung ist bereits als bezahlt markiert.")
        return redirect("invoice_list")

    _mark_invoice_payment_selected(request, invoice, parent_profile, method)
    messages.success(
        request,
        f"Die Zahlungsart {invoice.get_payment_method_display()} wurde gespeichert. TutorIn wurde informiert.",
    )
    return redirect("invoice_list")


@login_required
def invoice_checkout(request, invoice_id):
    _ensure_profile_for_user(request.user)
    is_parent = request.user.role == CustomUser.Roles.PARENT and hasattr(
        request.user, "parent_profile"
    )
    is_independent_student = _is_independent_student_user(request.user) and hasattr(
        request.user, "student_profile"
    )
    if not (is_parent or is_independent_student):
        return redirect("invoice_list")
    if request.method != "POST":
        return redirect("invoice_list")

    parent_profile = request.user.parent_profile if is_parent else None
    invoice_qs = payer_invoice_qs(request.user).select_related(
        "student__user",
        "uploaded_by__user",
    ).filter(pk=invoice_id)
    invoice = get_object_or_404(invoice_qs)
    if not invoice.can_pay_online:
        messages.error(request, "Für diese Rechnung ist aktuell keine Online-Zahlung verfügbar.")
        return redirect("invoice_list")

    try:
        stripe = _stripe_client()
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("invoice_list")

    unit_amount = int((invoice.amount_total * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    success_url = request.build_absolute_uri(reverse("invoice_list")) + "?payment=success"
    cancel_url = request.build_absolute_uri(reverse("invoice_list")) + "?payment=cancelled"
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        customer_email=request.user.email or None,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "invoice_id": str(invoice.id),
            "parent_id": str(parent_profile.id) if parent_profile else "",
            "student_id": str(invoice.student_id),
        },
        line_items=[
            {
                "price_data": {
                    "currency": invoice.currency.lower(),
                    "unit_amount": unit_amount,
                    "product_data": {
                        "name": f"Rechnung für {invoice.student.user.get_full_name() or invoice.student.user.username}",
                        "description": f"BrainBoost Nachhilfe · Fällig bis {invoice.due_date.strftime('%d.%m.%Y')}",
                    },
                },
                "quantity": 1,
            }
        ],
    )
    if (
        invoice.payment_status == Invoice.PaymentStatus.OPEN
        or invoice.payment_method != Invoice.PaymentMethod.ONLINE
        or (
            is_parent
            and invoice.payment_requested_by_id != parent_profile.id
        )
        or (
            is_independent_student
            and invoice.payment_requested_by_id is not None
        )
    ):
        _mark_invoice_payment_selected(
            request,
            invoice,
            parent_profile,
            Invoice.PaymentMethod.ONLINE,
            notify_tutor=False,
        )
    invoice.stripe_checkout_session_id = session.id
    invoice.save(update_fields=["stripe_checkout_session_id"])
    return redirect(session.url)


@csrf_exempt
def stripe_webhook(request):
    try:
        stripe = _stripe_client()
    except RuntimeError:
        return HttpResponse(status=500)

    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "").strip()
    if not webhook_secret:
        logger.error("Stripe webhook rejected because STRIPE_WEBHOOK_SECRET is not configured.")
        return HttpResponse(status=500)

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception:
        return HttpResponse(status=400)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        invoice_id = session.get("metadata", {}).get("invoice_id")
        if invoice_id:
            invoice = Invoice.objects.filter(pk=invoice_id).first()
            if invoice and invoice.payment_status != Invoice.PaymentStatus.PAID:
                invoice.payment_status = Invoice.PaymentStatus.PAID
                invoice.payment_method = Invoice.PaymentMethod.ONLINE
                invoice.paid_at = timezone.now()
                invoice.stripe_checkout_session_id = session.get("id", "") or invoice.stripe_checkout_session_id
                payment_intent = session.get("payment_intent")
                if isinstance(payment_intent, str):
                    invoice.stripe_payment_intent_id = payment_intent
                invoice.save(
                    update_fields=[
                        "payment_status",
                        "payment_method",
                        "paid_at",
                        "stripe_checkout_session_id",
                        "stripe_payment_intent_id",
                    ]
                )
                notify_invoice_payment_received_tutor(
                    request,
                    invoice,
                    invoice.payment_requested_by,
                )

    return HttpResponse(status=200)


@login_required
def invoice_confirm_payment(request, invoice_id):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(
        request.user, "tutor_profile"
    ):
        return redirect("dashboard")
    if request.method != "POST":
        return redirect("invoice_upload")

    tutor_profile = request.user.tutor_profile
    invoice = get_object_or_404(
        Invoice.objects.select_related(
            "student__user",
            "uploaded_by__user",
            "payment_requested_by__user",
        ),
        pk=invoice_id,
    )
    if not can_manage_invoice(request.user, invoice):
        messages.error(request, "Du darfst diesen Zahlungseingang nicht bestätigen.")
        return redirect("invoice_upload")
    if not invoice.can_confirm_receipt:
        messages.info(request, "Für diese Rechnung gibt es aktuell nichts zu bestätigen.")
        return redirect("invoice_upload")

    invoice.payment_status = Invoice.PaymentStatus.PAID
    invoice.paid_at = timezone.now()
    invoice.save(update_fields=["payment_status", "paid_at"])

    notify_invoice_payment_confirmed(request, invoice, invoice.payment_requested_by)

    messages.success(
        request,
        f"Zahlung per {invoice.get_payment_method_display()} wurde als eingegangen bestätigt.",
    )
    return redirect("invoice_upload")


@login_required
def invoice_list(request):
    _ensure_profile_for_user(request.user)
    is_parent = request.user.role == CustomUser.Roles.PARENT and hasattr(
        request.user, "parent_profile"
    )
    is_independent_student = _is_independent_student_user(request.user) and hasattr(
        request.user, "student_profile"
    )
    if not (is_parent or is_independent_student):
        return redirect("dashboard")
    payment_status = request.GET.get("payment")
    if payment_status == "success":
        messages.success(request, "Die Online-Zahlung wurde erfolgreich abgeschlossen. Die Rechnung wurde aktualisiert.")
    elif payment_status == "cancelled":
        messages.info(request, "Die Online-Zahlung wurde abgebrochen.")
    invoices = payer_invoice_qs(request.user).select_related(
        "student__user",
        "uploaded_by__user",
        "approved_by__user",
    )
    return render(
        request,
        "invoice_list.html",
        {
            "invoices": invoices,
            "payment_status_paid": Invoice.PaymentStatus.PAID,
            "is_independent_student": is_independent_student,
        },
    )
