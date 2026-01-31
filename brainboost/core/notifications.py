import logging
from typing import Iterable, Optional

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from .models import LearningMaterial, Lesson, StudentProfile, Invoice


logger = logging.getLogger(__name__)


def _notifications_enabled(key: str) -> bool:
    config = getattr(settings, "EMAIL_NOTIFICATIONS", None)
    if config is None:
        return True
    return config.get(key, True)


def _unique_emails(emails: Iterable[str]) -> list[str]:
    seen = set()
    unique = []
    for email in emails:
        if not email:
            continue
        normalized = email.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _student_recipients(student: StudentProfile) -> list[str]:
    emails = []
    if student.user.email:
        emails.append(student.user.email)
    for parent in student.parents.select_related("user"):
        if parent.user.email:
            emails.append(parent.user.email)
    return _unique_emails(emails)


def _tutor_email(lesson: Lesson) -> Optional[str]:
    tutor_user = getattr(lesson.tutor, "user", None)
    if tutor_user and tutor_user.email:
        return tutor_user.email
    return None


def _build_urls(request) -> dict:
    return {
        "dashboard_url": request.build_absolute_uri(reverse("dashboard")),
        "lesson_list_url": request.build_absolute_uri(reverse("lesson_list")),
        "invoice_list_url": request.build_absolute_uri(reverse("invoice_list")),
    }


def _send_templated_email(
    subject: str,
    template_base: str,
    context: dict,
    recipients: Iterable[str],
) -> bool:
    to_list = _unique_emails(recipients)
    if not to_list:
        return False
    try:
        text_body = render_to_string(f"emails/{template_base}.txt", context)
        html_body = render_to_string(f"emails/{template_base}.html", context)
    except Exception:
        logger.exception("E-Mail Template-Rendering fehlgeschlagen (%s)", template_base)
        return False
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@brainboost.local")
    ok = False
    for recipient in to_list:
        try:
            message = EmailMultiAlternatives(subject, text_body, from_email, [recipient])
            if html_body.strip():
                message.attach_alternative(html_body, "text/html")
            message.send()
            ok = True
        except Exception:
            logger.exception("E-Mail Versand fehlgeschlagen (%s)", template_base)
    return ok


def notify_lesson_created(request, lesson: Lesson) -> None:
    if not _notifications_enabled("lesson_created"):
        return
    subject = (
        f"Neuer Termin: {lesson.student.user.username} "
        f"am {lesson.date.strftime('%d.%m.%Y')} {lesson.time.strftime('%H:%M')}"
    )
    context = {
        "heading": "Neuer Termin",
        "lesson": lesson,
        "student": lesson.student,
        "tutor": lesson.tutor,
        **_build_urls(request),
    }
    _send_templated_email(subject, "lesson_created", context, _student_recipients(lesson.student))


def notify_lesson_changed(request, lesson: Lesson) -> None:
    if not _notifications_enabled("lesson_changed"):
        return
    subject = (
        f"Termin geändert: {lesson.student.user.username} "
        f"am {lesson.date.strftime('%d.%m.%Y')} {lesson.time.strftime('%H:%M')}"
    )
    context = {
        "heading": "Termin geändert",
        "lesson": lesson,
        "student": lesson.student,
        "tutor": lesson.tutor,
        **_build_urls(request),
    }
    _send_templated_email(subject, "lesson_changed", context, _student_recipients(lesson.student))


def notify_lesson_cancelled(
    request,
    lesson: Lesson,
    actor_label: str,
    reason: str,
    include_tutor: bool = False,
) -> None:
    if not _notifications_enabled("lesson_cancelled"):
        return
    subject = (
        f"Termin storniert: {lesson.student.user.username} "
        f"am {lesson.date.strftime('%d.%m.%Y')} {lesson.time.strftime('%H:%M')}"
    )
    recipients = _student_recipients(lesson.student)
    if include_tutor:
        tutor_email = _tutor_email(lesson)
        if tutor_email:
            recipients.append(tutor_email)
    context = {
        "heading": "Termin storniert",
        "lesson": lesson,
        "student": lesson.student,
        "tutor": lesson.tutor,
        "actor_label": actor_label,
        "reason": reason,
        **_build_urls(request),
    }
    _send_templated_email(subject, "lesson_cancelled", context, recipients)


def notify_lesson_reschedule_requested(
    request,
    lesson: Lesson,
    actor_label: str,
    include_tutor: bool = True,
) -> None:
    if not _notifications_enabled("lesson_reschedule_requested"):
        return
    subject = (
        f"Terminverlegung angefragt: {lesson.student.user.username} "
        f"am {lesson.date.strftime('%d.%m.%Y')} {lesson.time.strftime('%H:%M')}"
    )
    recipients = _student_recipients(lesson.student)
    if include_tutor:
        tutor_email = _tutor_email(lesson)
        if tutor_email:
            recipients.append(tutor_email)
    context = {
        "heading": "Terminverlegung angefragt",
        "lesson": lesson,
        "student": lesson.student,
        "tutor": lesson.tutor,
        "actor_label": actor_label,
        **_build_urls(request),
    }
    _send_templated_email(subject, "lesson_reschedule_requested", context, recipients)


def notify_invoice_uploaded(request, invoice: Invoice) -> None:
    if not _notifications_enabled("invoice_uploaded"):
        return
    subject = f"Neue Rechnung für {invoice.student.user.username}"
    context = {
        "heading": "Neue Rechnung",
        "invoice": invoice,
        "student": invoice.student,
        "tutor": invoice.uploaded_by,
        **_build_urls(request),
    }
    _send_templated_email(subject, "invoice_uploaded", context, _student_recipients(invoice.student))


def notify_material_uploaded(request, material: LearningMaterial) -> None:
    if not _notifications_enabled("material_uploaded"):
        return
    subject = f"Neues Material ({material.get_kind_display()}) für {material.student.user.username}"
    context = {
        "heading": f"Neues Material: {material.get_kind_display()}",
        "material": material,
        "student": material.student,
        "tutor": material.uploaded_by,
        **_build_urls(request),
    }
    _send_templated_email(subject, "material_uploaded", context, _student_recipients(material.student))
