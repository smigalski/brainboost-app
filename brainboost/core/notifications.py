import logging
from typing import Iterable, Optional

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from .models import (
    LearningMaterial,
    Lesson,
    StudentProfile,
    ParentProfile,
    Invoice,
    TutorProfile,
    HolidaySurveyResponse,
)


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


def _parent_recipients(student: StudentProfile) -> list[str]:
    emails = []
    for parent in student.parents.select_related("user"):
        if parent.user.email:
            emails.append(parent.user.email)
    return _unique_emails(emails)


def _tutor_recipients(tutors: Iterable[TutorProfile]) -> list[str]:
    emails = []
    for tutor in tutors:
        tutor_user = getattr(tutor, "user", None)
        if tutor_user and tutor_user.email:
            emails.append(tutor_user.email)
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
        "invoice_upload_url": request.build_absolute_uri(reverse("invoice_upload")),
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
        "invoice_file_url": request.build_absolute_uri(invoice.file.url),
        **_build_urls(request),
    }
    _send_templated_email(
        subject,
        "invoice_uploaded",
        context,
        _parent_recipients(invoice.student),
    )


def notify_invoice_parent(request, invoice: Invoice, parent: ParentProfile) -> None:
    if not _notifications_enabled("invoice_uploaded"):
        return
    parent_user = getattr(parent, "user", None)
    if not parent_user or not parent_user.email:
        return
    subject = f"Neue Rechnung für {invoice.student.user.username}"
    context = {
        "heading": "Neue Rechnung",
        "invoice": invoice,
        "student": invoice.student,
        "tutor": invoice.uploaded_by,
        "invoice_file_url": request.build_absolute_uri(invoice.file.url),
        **_build_urls(request),
    }
    _send_templated_email(
        subject,
        "invoice_uploaded",
        context,
        [parent_user.email],
    )


def notify_invoice_pending_approval(request, invoice: Invoice) -> None:
    if not _notifications_enabled("invoice_pending_approval"):
        return
    supervisors = invoice.uploaded_by.supervising_tutors.select_related("user")
    recipients = _tutor_recipients(supervisors)
    if not recipients:
        return
    subject = f"Rechnung wartet auf Freigabe: {invoice.student.user.username}"
    context = {
        "heading": "Rechnung wartet auf Freigabe",
        "invoice": invoice,
        "student": invoice.student,
        "tutor": invoice.uploaded_by,
        **_build_urls(request),
    }
    _send_templated_email(
        subject,
        "invoice_pending_approval",
        context,
        recipients,
    )


def notify_invoice_payment_selected(
    request,
    invoice: Invoice,
    parent: ParentProfile,
) -> None:
    if not _notifications_enabled("invoice_payment_selected"):
        return
    subject = (
        f"Zahlungsart gewählt: {invoice.student.user.get_full_name() or invoice.student.user.username}"
    )
    tutor_user = getattr(invoice.uploaded_by, "user", None)
    if not tutor_user or not tutor_user.email:
        return
    context = {
        "heading": "Zahlungsart gewählt",
        "invoice": invoice,
        "student": invoice.student,
        "tutor": invoice.uploaded_by,
        "parent": parent,
        "payment_method_label": invoice.get_payment_method_display(),
        **_build_urls(request),
    }
    _send_templated_email(
        subject,
        "invoice_payment_selected",
        context,
        [tutor_user.email],
    )


def notify_invoice_payment_received_tutor(
    request,
    invoice: Invoice,
    parent: ParentProfile,
) -> None:
    if not _notifications_enabled("invoice_payment_received_tutor"):
        return
    tutor_user = getattr(invoice.uploaded_by, "user", None)
    if not tutor_user or not tutor_user.email:
        return
    subject = (
        f"Zahlung eingegangen: {invoice.student.user.get_full_name() or invoice.student.user.username}"
    )
    context = {
        "heading": "Zahlung eingegangen",
        "invoice": invoice,
        "student": invoice.student,
        "tutor": invoice.uploaded_by,
        "parent": parent,
        "payment_method_label": invoice.get_payment_method_display(),
        **_build_urls(request),
    }
    _send_templated_email(
        subject,
        "invoice_payment_received_tutor",
        context,
        [tutor_user.email],
    )


def notify_invoice_payment_confirmed(
    request,
    invoice: Invoice,
    parent: ParentProfile,
) -> None:
    if not _notifications_enabled("invoice_payment_confirmed"):
        return
    parent_user = getattr(parent, "user", None)
    if not parent_user or not parent_user.email:
        return
    subject = (
        f"Zahlung bestätigt: {invoice.student.user.get_full_name() or invoice.student.user.username}"
    )
    context = {
        "heading": "Zahlung bestätigt",
        "invoice": invoice,
        "student": invoice.student,
        "tutor": invoice.uploaded_by,
        "parent": parent,
        "payment_method_label": invoice.get_payment_method_display(),
        "invoice_file_url": request.build_absolute_uri(invoice.file.url),
        **_build_urls(request),
    }
    _send_templated_email(
        subject,
        "invoice_payment_confirmed",
        context,
        [parent_user.email],
    )


def notify_material_uploaded(request, material: LearningMaterial) -> None:
    if not _notifications_enabled("material_uploaded"):
        return
    kind_label = "Musterlösung" if material.kind == material.Kind.SOLUTION else material.get_kind_display()
    subject = f"Neues Material ({kind_label}) für {material.student.user.username}"
    context = {
        "heading": f"Neues Material: {kind_label}",
        "kind_label": kind_label,
        "material": material,
        "student": material.student,
        "tutor": material.uploaded_by,
        "material_download_url": request.build_absolute_uri(
            reverse("material_download", args=[material.id])
        ),
        **_build_urls(request),
    }
    _send_templated_email(subject, "material_uploaded", context, _student_recipients(material.student))


def notify_holiday_survey_created(request, response: HolidaySurveyResponse) -> None:
    if not _notifications_enabled("holiday_survey_created"):
        return
    student = response.student
    survey = response.survey
    recipients = _parent_recipients(student)
    if not recipients:
        return
    subject = f"Neue Umfrage für {student.user.get_full_name() or student.user.username}"
    context = {
        "heading": "Neue Umfrage",
        "survey": survey,
        "student": student,
        "tutor": survey.tutor,
        "survey_url": request.build_absolute_uri(reverse("holiday_surveys")),
        **_build_urls(request),
    }
    _send_templated_email(subject, "holiday_survey_created", context, recipients)


def notify_monthly_brainboost_feedback(
    *,
    base_url: str,
    audience: str,
    recipients: Iterable[str],
) -> bool:
    if not _notifications_enabled("monthly_brainboost_feedback"):
        return False
    normalized_base = (base_url or "").strip().rstrip("/")
    feedback_path = reverse("brainboost_feedback")
    feedback_url = (
        f"{normalized_base}{feedback_path}?role={audience}&source=email"
        if normalized_base
        else f"{feedback_path}?role={audience}&source=email"
    )
    audience_label = {
        "student": "SchülerInnen/StudentInnen",
        "parent": "Eltern",
        "tutor": "TutorInnen",
    }.get(audience, "NutzerInnen")
    subject = "BrainBoost Monatsfeedback: Deine Meinung zählt"
    context = {
        "heading": "Monatliches BrainBoost Feedback",
        "audience_label": audience_label,
        "feedback_url": feedback_url,
        "prompt": "BrainBoost möchte die beste Nachhilfeplattform Deutschlands werden! Was ist dafür nötig?",
    }
    return _send_templated_email(
        subject,
        "monthly_brainboost_feedback",
        context,
        recipients,
    )
