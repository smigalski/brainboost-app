from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..agreement_services import (
    audit,
    finalize_agreement,
    finalize_learning_agreement,
    issue_confirmation_token,
    issue_tutor_confirmation_token,
    send_final_agreement_email,
    send_confirmation_email,
    send_tutor_confirmation_email,
    send_tutor_review_email,
    token_is_valid,
    tutor_token_is_valid,
)
from ..forms import AgreementAcceptanceForm, AgreementTutorAcceptanceForm
from ..models import Agreement
from .common import _stripe_client


class AgreementAwareLoginView(LoginView):
    template_name = "login.html"

    def get_success_url(self):
        open_agreement = Agreement.objects.filter(
            Q(participant=self.request.user) | Q(tutor__user=self.request.user),
            status__in=[
                Agreement.Status.DRAFT,
                Agreement.Status.PENDING_PARTICIPANT,
                Agreement.Status.PENDING_EMAIL,
                Agreement.Status.PENDING_TUTOR,
                Agreement.Status.PENDING_TUTOR_EMAIL,
            ],
        ).order_by("created_at").first()
        if open_agreement:
            return reverse("agreement_detail", kwargs={"agreement_id": open_agreement.pk})
        return super().get_success_url()


def _has_brainboost_access(user) -> bool:
    return user.is_staff or user.is_superuser


def _agreement_queryset_for(user):
    queryset = Agreement.objects.select_related(
        "participant",
        "student__user",
        "tutor__user",
        "brainboost_confirmed_by",
    )
    if _has_brainboost_access(user):
        return queryset
    return queryset.filter(
        Q(participant=user)
        | Q(student__user=user)
        | Q(tutor__user=user)
    ).distinct()


@login_required
def agreement_list(request):
    agreements = _agreement_queryset_for(request.user)
    return render(
        request,
        "agreements/agreement_list.html",
        {
            "agreements": agreements,
            "can_confirm_for_brainboost": _has_brainboost_access(request.user),
        },
    )


@login_required
def agreement_detail(request, agreement_id):
    agreement = get_object_or_404(_agreement_queryset_for(request.user), pk=agreement_id)
    form = AgreementAcceptanceForm(agreement=agreement) if agreement.participant_id else None
    tutor_form = AgreementTutorAcceptanceForm(agreement=agreement) if agreement.tutor_id else None
    return render(
        request,
        "agreements/agreement_detail.html",
        {
            "agreement": agreement,
            "form": form,
            "tutor_form": tutor_form,
            "can_accept": (
                agreement.participant_id == request.user.id
                and agreement.status
                in {Agreement.Status.DRAFT, Agreement.Status.PENDING_PARTICIPANT}
            ),
            "can_confirm_for_brainboost": (
                _has_brainboost_access(request.user)
                and agreement.agreement_type == Agreement.AgreementType.TUTOR
                and agreement.status == Agreement.Status.PENDING_BRAINBOOST
            ),
            "can_tutor_accept": (
                agreement.agreement_type == Agreement.AgreementType.LEARNING
                and agreement.tutor_id
                and agreement.tutor.user_id == request.user.id
                and agreement.status == Agreement.Status.PENDING_TUTOR
            ),
            "stripe_public_key": getattr(settings, "STRIPE_PUBLIC_KEY", ""),
        },
    )


def _sync_setup_intent(agreement, setup_intent, *, actor=None):
    """Persist only Stripe references; IBAN/account data never reaches Django."""
    intent_id = setup_intent.get("id", "")
    if intent_id != agreement.stripe_setup_intent_id:
        return False
    status = setup_intent.get("status", "")
    payment_method = setup_intent.get("payment_method", "")
    if hasattr(payment_method, "get"):
        payment_method = payment_method.get("id", "")
    agreement.stripe_mandate_status = (
        Agreement.StripeMandateStatus.ACTIVE
        if status == "succeeded" and payment_method
        else Agreement.StripeMandateStatus.FAILED
        if status in {"canceled", "requires_payment_method"}
        else Agreement.StripeMandateStatus.PENDING
    )
    if agreement.stripe_mandate_status == Agreement.StripeMandateStatus.ACTIVE:
        agreement.stripe_payment_method_id = payment_method
    agreement.save(
        update_fields=["stripe_mandate_status", "stripe_payment_method_id", "updated_at"]
    )
    audit(
        agreement,
        "stripe_sepa_mandate_active"
        if agreement.stripe_mandate_status == Agreement.StripeMandateStatus.ACTIVE
        else "stripe_sepa_mandate_updated",
        actor=actor,
        metadata={"setup_intent_status": status},
    )
    return agreement.stripe_mandate_status == Agreement.StripeMandateStatus.ACTIVE


@login_required
@require_POST
def agreement_sepa_setup(request, agreement_id):
    agreement = get_object_or_404(Agreement, pk=agreement_id, participant=request.user)
    if agreement.status not in {Agreement.Status.DRAFT, Agreement.Status.PENDING_PARTICIPANT}:
        messages.error(request, "Für diese Vereinbarung kann kein Mandat mehr geändert werden.")
        return redirect("agreement_detail", agreement_id=agreement.pk)
    if not getattr(settings, "STRIPE_PUBLIC_KEY", "").strip():
        messages.error(request, "Stripe ist noch nicht vollständig konfiguriert.")
        return redirect("agreement_detail", agreement_id=agreement.pk)
    try:
        stripe = _stripe_client()
        if not agreement.stripe_customer_id:
            customer = stripe.Customer.create(
                email=request.user.email,
                name=request.user.display_name,
                metadata={"brainboost_user_id": str(request.user.pk)},
            )
            agreement.stripe_customer_id = customer.id
        setup_intent = stripe.SetupIntent.create(
            customer=agreement.stripe_customer_id,
            payment_method_types=["sepa_debit"],
            usage="off_session",
            metadata={"agreement_id": str(agreement.pk)},
        )
    except Exception:
        messages.error(request, "Das SEPA-Mandat konnte bei Stripe nicht vorbereitet werden.")
        return redirect("agreement_detail", agreement_id=agreement.pk)
    agreement.stripe_setup_intent_id = setup_intent.id
    agreement.stripe_mandate_status = Agreement.StripeMandateStatus.PENDING
    agreement.save(
        update_fields=[
            "stripe_customer_id",
            "stripe_setup_intent_id",
            "stripe_mandate_status",
            "updated_at",
        ]
    )
    audit(agreement, "stripe_sepa_setup_started", actor=request.user)
    return render(
        request,
        "agreements/agreement_sepa_setup.html",
        {
            "agreement": agreement,
            "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
            "client_secret": setup_intent.client_secret,
        },
    )


@login_required
def agreement_sepa_return(request, agreement_id):
    agreement = get_object_or_404(Agreement, pk=agreement_id, participant=request.user)
    intent_id = request.GET.get("setup_intent", "")
    if not intent_id or intent_id != agreement.stripe_setup_intent_id:
        messages.error(request, "Die Stripe-Rückmeldung gehört nicht zu dieser Vereinbarung.")
        return redirect("agreement_detail", agreement_id=agreement.pk)
    try:
        setup_intent = _stripe_client().SetupIntent.retrieve(intent_id)
    except Exception:
        messages.error(request, "Der Mandatsstatus konnte nicht bei Stripe geprüft werden.")
    else:
        if _sync_setup_intent(agreement, setup_intent, actor=request.user):
            messages.success(request, "Das SEPA-Mandat wurde erfolgreich eingerichtet.")
        else:
            messages.warning(request, "Das SEPA-Mandat ist noch nicht vollständig bestätigt.")
    return redirect("agreement_detail", agreement_id=agreement.pk)


@login_required
@require_POST
def agreement_accept(request, agreement_id):
    agreement = get_object_or_404(Agreement, pk=agreement_id, participant=request.user)
    if agreement.status not in {Agreement.Status.DRAFT, Agreement.Status.PENDING_PARTICIPANT}:
        messages.error(request, "Diese Vereinbarung kann aktuell nicht bestätigt werden.")
        return redirect("agreement_detail", agreement_id=agreement.pk)

    form = AgreementAcceptanceForm(request.POST, agreement=agreement)
    if not form.is_valid():
        return render(
            request,
            "agreements/agreement_detail.html",
            {"agreement": agreement, "form": form, "can_accept": True},
            status=400,
        )
    if not agreement.participant.email:
        form.add_error(None, "Für die E-Mail-Bestätigung fehlt eine E-Mail-Adresse im Profil.")
        return render(
            request,
            "agreements/agreement_detail.html",
            {"agreement": agreement, "form": form, "can_accept": True},
            status=400,
        )
    if (
        form.cleaned_data["payment_method"] == Agreement.PaymentMethod.STRIPE_SEPA
        and agreement.stripe_mandate_status != Agreement.StripeMandateStatus.ACTIVE
    ):
        form.add_error("payment_method", "Bitte richte zuerst das SEPA-Mandat über Stripe ein.")
        return render(
            request,
            "agreements/agreement_detail.html",
            {"agreement": agreement, "form": form, "can_accept": True},
            status=400,
        )

    with transaction.atomic():
        accepted_at = timezone.now()
        agreement.participant_name = form.cleaned_data["participant_name"]
        agreement.payment_method = form.cleaned_data["payment_method"]
        agreement.participant_accepted_at = accepted_at
        agreement.participant_consents = {
            "agreement": True,
            "privacy": form.cleaned_data["accepted_privacy"],
            "webapp": form.cleaned_data.get("accepted_webapp", False),
            "child_protection": form.cleaned_data.get("accepted_child_protection", False),
            "version": agreement.version,
            "accepted_at": accepted_at.isoformat(),
        }
        agreement.status = Agreement.Status.PENDING_EMAIL
        agreement.save(
            update_fields=[
                "participant_name",
                "participant_consents",
                "payment_method",
                "participant_accepted_at",
                "status",
                "updated_at",
            ]
        )
        token = issue_confirmation_token(agreement)
        audit(agreement, "participant_accepted", actor=request.user)

    try:
        send_confirmation_email(request, agreement, token)
    except Exception:
        messages.error(
            request,
            "Die Zustimmung wurde gespeichert, aber die Bestätigungs-Mail konnte nicht versendet werden.",
        )
    else:
        audit(agreement, "confirmation_email_sent", actor=request.user)
        messages.success(request, "Bitte bestätige die Vereinbarung jetzt über den Link in deiner E-Mail.")
    return redirect("agreement_detail", agreement_id=agreement.pk)


def agreement_email_confirm(request, agreement_id, token):
    agreement = get_object_or_404(Agreement, pk=agreement_id)
    if agreement.status != Agreement.Status.PENDING_EMAIL or not token_is_valid(agreement, token):
        return render(request, "agreements/agreement_confirmation_invalid.html", status=400)

    with transaction.atomic():
        agreement.email_confirmed_at = timezone.now()
        agreement.status = (
            Agreement.Status.PENDING_TUTOR
            if agreement.agreement_type == Agreement.AgreementType.LEARNING
            else Agreement.Status.PENDING_BRAINBOOST
        )
        agreement.confirmation_token_digest = ""
        agreement.confirmation_token_expires_at = None
        agreement.save(
            update_fields=[
                "email_confirmed_at",
                "status",
                "confirmation_token_digest",
                "confirmation_token_expires_at",
                "updated_at",
            ]
        )
        audit(agreement, "participant_email_confirmed", actor=agreement.participant)
    if agreement.agreement_type == Agreement.AgreementType.LEARNING:
        try:
            send_tutor_review_email(request, agreement)
        except Exception:
            audit(agreement, "tutor_review_email_failed", actor=agreement.participant)
        else:
            audit(agreement, "tutor_review_email_sent", actor=agreement.participant)
    return render(request, "agreements/agreement_confirmation_success.html", {"agreement": agreement})


@login_required
@require_POST
def agreement_tutor_accept(request, agreement_id):
    agreement = get_object_or_404(
        Agreement.objects.select_related("tutor__user"),
        pk=agreement_id,
        agreement_type=Agreement.AgreementType.LEARNING,
        tutor__user=request.user,
    )
    if agreement.status != Agreement.Status.PENDING_TUTOR:
        messages.error(request, "Diese Lernvereinbarung wartet aktuell nicht auf deine Bestätigung.")
        return redirect("agreement_detail", agreement_id=agreement.pk)
    form = AgreementTutorAcceptanceForm(request.POST, agreement=agreement)
    if not form.is_valid():
        return render(
            request,
            "agreements/agreement_detail.html",
            {
                "agreement": agreement,
                "form": AgreementAcceptanceForm(agreement=agreement),
                "tutor_form": form,
                "can_tutor_accept": True,
            },
            status=400,
        )
    if not request.user.email:
        form.add_error(None, "Für die TutorInnen-Bestätigung fehlt eine E-Mail-Adresse.")
        return render(
            request,
            "agreements/agreement_detail.html",
            {"agreement": agreement, "tutor_form": form, "can_tutor_accept": True},
            status=400,
        )

    with transaction.atomic():
        accepted_at = timezone.now()
        agreement.tutor_name = form.cleaned_data["tutor_name"]
        agreement.tutor_accepted_at = accepted_at
        agreement.tutor_consents = {
            "agreement": True,
            "privacy": form.cleaned_data["accepted_privacy"],
            "version": agreement.version,
            "accepted_at": accepted_at.isoformat(),
        }
        agreement.status = Agreement.Status.PENDING_TUTOR_EMAIL
        agreement.save(
            update_fields=[
                "tutor_name",
                "tutor_accepted_at",
                "tutor_consents",
                "status",
                "updated_at",
            ]
        )
        token = issue_tutor_confirmation_token(agreement)
        audit(agreement, "tutor_accepted", actor=request.user)
    try:
        send_tutor_confirmation_email(request, agreement, token)
    except Exception:
        audit(agreement, "tutor_confirmation_email_failed", actor=request.user)
        messages.error(request, "Die Zustimmung wurde gespeichert, aber die E-Mail konnte nicht versendet werden.")
    else:
        audit(agreement, "tutor_confirmation_email_sent", actor=request.user)
        messages.success(request, "Bitte bestätige jetzt den einmaligen Link in deiner E-Mail.")
    return redirect("agreement_detail", agreement_id=agreement.pk)


def agreement_tutor_email_confirm(request, agreement_id, token):
    agreement = get_object_or_404(
        Agreement.objects.select_related("participant", "tutor__user"),
        pk=agreement_id,
        agreement_type=Agreement.AgreementType.LEARNING,
    )
    if (
        agreement.status != Agreement.Status.PENDING_TUTOR_EMAIL
        or not tutor_token_is_valid(agreement, token)
    ):
        return render(request, "agreements/agreement_confirmation_invalid.html", status=400)

    try:
        with transaction.atomic():
            agreement.tutor_email_confirmed_at = timezone.now()
            agreement.save(update_fields=["tutor_email_confirmed_at", "updated_at"])
            audit(agreement, "tutor_email_confirmed", actor=agreement.tutor.user)
            pdf_bytes = finalize_learning_agreement(request, agreement)
            agreement.tutor_confirmation_token_digest = ""
            agreement.tutor_confirmation_token_expires_at = None
            agreement.save(
                update_fields=[
                    "tutor_confirmation_token_digest",
                    "tutor_confirmation_token_expires_at",
                    "updated_at",
                ]
            )
    except Exception:
        audit(agreement, "learning_pdf_failed", actor=agreement.tutor.user)
        return render(request, "agreements/agreement_confirmation_invalid.html", status=500)
    try:
        send_final_agreement_email(agreement, pdf_bytes)
    except Exception:
        audit(agreement, "final_email_failed", actor=agreement.tutor.user)
    else:
        audit(agreement, "final_email_sent", actor=agreement.tutor.user)
    return render(request, "agreements/agreement_confirmation_success.html", {"agreement": agreement})


@login_required
@require_POST
def agreement_brainboost_confirm(request, agreement_id):
    if not _has_brainboost_access(request.user):
        raise Http404
    agreement = get_object_or_404(
        Agreement,
        pk=agreement_id,
        agreement_type=Agreement.AgreementType.TUTOR,
    )
    if agreement.status != Agreement.Status.PENDING_BRAINBOOST:
        messages.error(request, "Diese Vereinbarung wartet nicht auf die BrainBoost-Bestätigung.")
        return redirect("agreement_detail", agreement_id=agreement.pk)

    try:
        pdf_bytes = finalize_agreement(request, agreement, actor=request.user)
    except Exception:
        messages.error(request, "Das finale Vertrags-PDF konnte nicht erzeugt werden.")
        return redirect("agreement_detail", agreement_id=agreement.pk)
    try:
        send_final_agreement_email(agreement, pdf_bytes)
    except Exception:
        audit(agreement, "final_email_failed", actor=request.user)
        messages.warning(
            request,
            "Die Vereinbarung wurde abgeschlossen, der PDF-Versand muss jedoch wiederholt werden.",
        )
    else:
        audit(agreement, "final_email_sent", actor=request.user)
        messages.success(request, "Die Vereinbarung wurde abgeschlossen und als PDF versendet.")
    return redirect("agreement_detail", agreement_id=agreement.pk)


@login_required
def agreement_pdf_download(request, agreement_id):
    agreement = get_object_or_404(_agreement_queryset_for(request.user), pk=agreement_id)
    if not agreement.final_pdf:
        raise Http404
    return FileResponse(
        agreement.final_pdf.open("rb"),
        as_attachment=True,
        filename=f"{agreement.reference}.pdf",
    )
