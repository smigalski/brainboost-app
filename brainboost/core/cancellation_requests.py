import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.utils import timezone

from .models import CancellationRequest


logger = logging.getLogger(__name__)


def _send(subject, body, recipient):
    if not recipient:
        raise ValueError("Für die Benachrichtigung fehlt eine E-Mail-Adresse.")
    message = EmailMultiAlternatives(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [recipient],
        reply_to=[settings.DEFAULT_REPLY_TO_EMAIL],
    )
    if message.send() != 1:
        raise RuntimeError("email_send_failed")


def send_request_notifications(cancellation_request):
    """Send independent notifications and persist their delivery result."""
    errors = []
    internal_recipient = settings.INTERNAL_CONTACT_EMAIL
    internal_body = (
        "Ein neuer Kündigungs-/Löschantrag wurde gestellt.\n\n"
        f"Name: {cancellation_request.name_snapshot}\n"
        f"E-Mail: {cancellation_request.email_snapshot}\n"
        f"Rolle: {cancellation_request.get_role_snapshot_display()}\n"
        f"Antrag: {cancellation_request.requested_actions}\n"
        f"Anmerkung: {cancellation_request.reason or 'Keine'}\n"
        f"Antrags-ID: {cancellation_request.pk}\n\n"
        "Bitte den Antrag in der Django-Administration prüfen und bestätigen."
    )
    try:
        _send(
            "BrainBoost: Neuer Kündigungs-/Löschantrag",
            internal_body,
            internal_recipient,
        )
    except Exception as exc:
        logger.exception("Interne Benachrichtigung für Antrag %s fehlgeschlagen.", cancellation_request.pk)
        errors.append(f"Interne Benachrichtigung: {exc}")
    else:
        cancellation_request.internal_email_sent_at = timezone.now()

    receipt_body = (
        f"Hallo {cancellation_request.name_snapshot},\n\n"
        "wir haben deinen Antrag erhalten:\n"
        f"{cancellation_request.requested_actions}\n\n"
        "BrainBoost prüft den Antrag. Die Kontolöschung beziehungsweise Kündigung "
        "wird erst nach unserer Bestätigung bearbeitet. Bis dahin bleiben dein "
        "Konto und bestehende Vereinbarungen unverändert.\n\n"
        "Falls du diesen Antrag nicht gestellt hast, antworte bitte umgehend auf diese E-Mail."
    )
    try:
        _send(
            "BrainBoost: Dein Antrag ist eingegangen",
            receipt_body,
            cancellation_request.email_snapshot,
        )
    except Exception as exc:
        logger.exception("Eingangsbestätigung für Antrag %s fehlgeschlagen.", cancellation_request.pk)
        errors.append(f"Eingangsbestätigung: {exc}")
    else:
        cancellation_request.receipt_email_sent_at = timezone.now()

    cancellation_request.email_error = "\n".join(errors)
    cancellation_request.save(
        update_fields=[
            "internal_email_sent_at",
            "receipt_email_sent_at",
            "email_error",
        ]
    )
    return errors


@transaction.atomic
def confirm_cancellation_request(request_id, staff_user):
    cancellation_request = CancellationRequest.objects.select_for_update().get(pk=request_id)
    if cancellation_request.status != CancellationRequest.Status.REQUESTED:
        return cancellation_request, False
    cancellation_request.status = CancellationRequest.Status.CONFIRMED
    cancellation_request.confirmed_at = timezone.now()
    cancellation_request.confirmed_by = staff_user
    cancellation_request.email_error = ""
    cancellation_request.save(
        update_fields=["status", "confirmed_at", "confirmed_by", "email_error"]
    )
    return cancellation_request, True


def send_confirmation_email(cancellation_request):
    body = (
        f"Hallo {cancellation_request.name_snapshot},\n\n"
        "BrainBoost hat folgenden Antrag bestätigt:\n"
        f"{cancellation_request.requested_actions}\n\n"
        "Wir bearbeiten die Kontolöschung beziehungsweise Vereinbarungskündigung "
        "entsprechend der geltenden Fristen. Bei Rückfragen antwortest du einfach auf diese E-Mail."
    )
    try:
        _send(
            "BrainBoost: Dein Antrag wurde bestätigt",
            body,
            cancellation_request.email_snapshot,
        )
    except Exception as exc:
        logger.exception("Bestätigungsmail für Antrag %s fehlgeschlagen.", cancellation_request.pk)
        cancellation_request.email_error = f"Bestätigungsmail: {exc}"
        cancellation_request.save(update_fields=["email_error"])
        return False
    cancellation_request.confirmation_email_sent_at = timezone.now()
    cancellation_request.email_error = ""
    cancellation_request.save(update_fields=["confirmation_email_sent_at", "email_error"])
    return True
