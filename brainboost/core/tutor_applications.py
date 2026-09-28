"""Application decisions and retryable emails shared by the lead UI and Django admin."""

import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import Lead, LeadStatusEmail, TutorProfile

logger = logging.getLogger(__name__)

WAITLIST_STATUSES = {
    Lead.Status.WAITLISTED, Lead.Status.AVAILABILITY_REQUESTED, Lead.Status.INTERESTED,
}
EMAIL_TEMPLATES = {
    Lead.Status.UNSUITABLE: ("Deine Bewerbung bei BrainBoost", "tutor_application_rejected"),
    Lead.Status.WAITLISTED: ("Deine Bewerbung: Warteliste bei BrainBoost", "tutor_application_waitlisted"),
    Lead.Status.AVAILABILITY_REQUESTED: ("BrainBoost: Hast du wieder Zeit für Nachhilfe?", "tutor_application_available"),
    Lead.Status.INTERESTED: ("BrainBoost: TutorIn hat Interesse bestätigt", "tutor_application_interested"),
}


def validate_application_transition(lead, old_status):
    if lead.status == old_status:
        return
    if lead.status in WAITLIST_STATUSES and lead.role != Lead.Role.TUTOR:
        raise ValidationError({"status": "Die Warteliste ist nur für TutorInnen-Bewerbungen vorgesehen."})
    if lead.status == Lead.Status.AVAILABILITY_REQUESTED:
        if old_status != Lead.Status.WAITLISTED:
            raise ValidationError({"status": "Verfügbarkeit kann nur von der Warteliste aus angefragt werden."})
        if not lead.converted_tutor_id:
            raise ValidationError({"status": "Bitte zuerst ein TutorInnenkonto für die Rückmeldung anlegen."})
    if lead.status == Lead.Status.INTERESTED and old_status != Lead.Status.AVAILABILITY_REQUESTED:
        raise ValidationError({"status": "Es liegt keine offene Verfügbarkeitsanfrage vor."})
    if lead.status == Lead.Status.INTERESTED and not getattr(lead, "_interest_confirmed", False):
        raise ValidationError({"status": "Das Interesse bestätigt die TutorIn im eigenen Dashboard."})
    if old_status in {Lead.Status.WAITLISTED, Lead.Status.AVAILABILITY_REQUESTED} and lead.status not in (
        WAITLIST_STATUSES | {Lead.Status.UNSUITABLE}
    ):
        raise ValidationError({"status": "Bitte zuerst die Verfügbarkeit anfragen und die Interessenbestätigung abwarten."})
    if lead.role == Lead.Role.TUTOR and lead.status == Lead.Status.WAITLISTED and lead.converted_tutor_id:
        if lead.converted_tutor.assigned_students.exists():
            raise ValidationError({"status": "Dieser TutorIn sind bereits SchülerInnen zugewiesen. Bitte zuerst die Zuweisungen prüfen."})


def prepare_application_transition(lead, old_status):
    validate_application_transition(lead, old_status)
    if lead.role != Lead.Role.TUTOR:
        return set()
    fields = set()
    tutor = lead.converted_tutor if lead.converted_tutor_id else None
    if lead.status in WAITLIST_STATUSES:
        if not lead.waitlisted_at:
            lead.waitlisted_at = timezone.now()
            fields.add("waitlisted_at")
        if tutor and tutor.status != TutorProfile.Status.WAITLISTED:
            lead.waitlist_previous_tutor_status = tutor.status
            fields.add("waitlist_previous_tutor_status")
        if tutor:
            TutorProfile.objects.filter(pk=tutor.pk).update(status=TutorProfile.Status.WAITLISTED)
        lead.follow_up_done = False
        fields.add("follow_up_done")
    elif lead.status == Lead.Status.UNSUITABLE:
        if tutor:
            TutorProfile.objects.filter(pk=tutor.pk).update(status=TutorProfile.Status.REJECTED)
        lead.follow_up_done = True
        fields.add("follow_up_done")
    elif old_status in WAITLIST_STATUSES and tutor:
        # Returning to the existing interview/onboarding process does not grant
        # a previously unapproved applicant active tutor status.
        status = lead.waitlist_previous_tutor_status or TutorProfile.Status.APPLIED
        if status in {TutorProfile.Status.REJECTED, TutorProfile.Status.WAITLISTED}:
            status = TutorProfile.Status.APPLIED
        TutorProfile.objects.filter(pk=tutor.pk).update(status=status)
        lead.follow_up_done = False
        fields.add("follow_up_done")
    elif old_status == Lead.Status.UNSUITABLE and tutor and tutor.status == TutorProfile.Status.REJECTED:
        TutorProfile.objects.filter(pk=tutor.pk).update(status=TutorProfile.Status.APPLIED)
    return fields


def queue_application_email(lead):
    if lead.role != Lead.Role.TUTOR or lead.status not in EMAIL_TEMPLATES:
        return
    record, _ = LeadStatusEmail.objects.get_or_create(
        lead=lead, status_changed_at=lead.last_status_change_at,
        defaults={"status": lead.status},
    )
    transaction.on_commit(lambda: deliver_application_email(record.pk), robust=True)


def deliver_application_email(record_id):
    """Send only the current decision, once; failed attempts remain retryable."""
    lead_id = LeadStatusEmail.objects.filter(pk=record_id).values_list("lead_id", flat=True).first()
    if lead_id is None:
        return False
    with transaction.atomic():
        lead = Lead.objects.select_for_update().filter(pk=lead_id).first()
        record = LeadStatusEmail.objects.select_for_update().filter(pk=record_id).first()
        if lead is None or record is None:
            return False
        if record.sent_at:
            return True
        if lead.role != Lead.Role.TUTOR or lead.status != record.status or lead.last_status_change_at != record.status_changed_at:
            return False
        if not getattr(settings, "EMAIL_NOTIFICATIONS", {}).get("tutor_application_status", True):
            record.error = "Bewerbungs-E-Mails sind in der Konfiguration deaktiviert."
            record.save(update_fields=["error"])
            return False
        tutor_email = lead.converted_tutor.user.email if lead.converted_tutor_id else ""
        if record.status == Lead.Status.INTERESTED:
            configured = getattr(settings, "LEAD_NOTIFICATION_EMAIL", "") or settings.INTERNAL_CONTACT_EMAIL
            recipients = list(dict.fromkeys(value.strip() for value in configured.split(",") if value.strip()))
        else:
            recipients = [(tutor_email or lead.email).strip()]
            recipients = [value for value in recipients if value]
        record.recipient = ", ".join(recipients)
        record.attempts += 1
        record.error = ""
        if not record.recipient:
            record.error = "Keine E-Mail-Adresse hinterlegt."
        else:
            subject, template = EMAIL_TEMPLATES[record.status]
            context = {
                "heading": subject,
                "lead_name": lead.name,
                "dashboard_url": settings.APP_BASE_URL.rstrip("/") + reverse("dashboard"),
                "admin_url": settings.APP_BASE_URL.rstrip("/") + reverse("lead_dashboard") + "#tutor-waitlist",
                "subjects": lead.teaching_subjects or lead.subject,
                "availability": lead.weekly_availability,
            }
            try:
                message = EmailMultiAlternatives(
                    subject=subject,
                    body=render_to_string(f"emails/{template}.txt", context),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=recipients,
                    reply_to=[settings.DEFAULT_REPLY_TO_EMAIL] if settings.DEFAULT_REPLY_TO_EMAIL else [],
                )
                message.attach_alternative(render_to_string(f"emails/{template}.html", context), "text/html")
                if message.send() != 1:
                    raise RuntimeError("email_send_failed")
            except Exception:
                logger.exception("Bewerbungs-E-Mail konnte nicht versendet werden (Lead %s).", lead.pk)
                record.error = "Versand fehlgeschlagen. Bitte E-Mail-Konfiguration prüfen und erneut versuchen."
            else:
                record.sent_at = timezone.now()
        record.save(update_fields=["recipient", "attempts", "error", "sent_at"])
        return record.sent_at is not None
