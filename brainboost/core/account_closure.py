import calendar
import hashlib
import secrets
from datetime import date, timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .agreement_services import audit
from .models import Agreement, CustomUser, Lesson, StudentProfile, TutorProfile


ACCOUNT_CLOSURE_TOKEN_LIFETIME_HOURS = 24
ACCOUNT_ARCHIVE_MONTHS = 3


class AccountClosureNotAllowed(ValueError):
    pass


def _add_months(value, months):
    """Add calendar months while keeping the local wall-clock time."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _tutor_termination_date(today):
    last_day = calendar.monthrange(today.year, today.month)[1]
    current_month_end = date(today.year, today.month, last_day)
    if (current_month_end - today).days >= 7:
        return current_month_end
    if today.month == 12:
        year, month = today.year + 1, 1
    else:
        year, month = today.year, today.month + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def closure_scope_for(user):
    """Return the accounts affected by a self-service cancellation."""
    if user.is_staff or user.is_superuser:
        raise AccountClosureNotAllowed(
            "Admin-Konten können aus Sicherheitsgründen nur in der Django-Administration deaktiviert werden."
        )
    if user.role == CustomUser.Roles.PARENT and hasattr(user, "parent_profile"):
        children = [student.user for student in user.parent_profile.students.select_related("user")]
        return [user] + children
    if user.role == CustomUser.Roles.TUTOR and hasattr(user, "tutor_profile"):
        return [user]
    if (
        user.role in {CustomUser.Roles.STUDENT, CustomUser.Roles.INDEPENDENT_STUDENT}
        and hasattr(user, "student_profile")
    ):
        if user.student_profile.parents.exists():
            raise AccountClosureNotAllowed(
                "Dieses SchülerInnenprofil gehört zu einem Elternkonto. Die Kündigung muss über das Elternprofil erfolgen."
            )
        return [user]
    raise AccountClosureNotAllowed(
        "Dieses Konto kann nicht über die Selbstverwaltung gekündigt werden. Bitte kontaktiere BrainBoost."
    )


def _profiles_from_users(users):
    student_ids = [user.student_profile.pk for user in users if hasattr(user, "student_profile")]
    tutor_ids = [user.tutor_profile.pk for user in users if hasattr(user, "tutor_profile")]
    return student_ids, tutor_ids


def agreements_for_closure(users):
    user_ids = [user.pk for user in users]
    student_ids, tutor_ids = _profiles_from_users(users)
    relation_filter = Q(participant_id__in=user_ids)
    if student_ids:
        relation_filter |= Q(student_id__in=student_ids)
    if tutor_ids:
        relation_filter |= Q(tutor_id__in=tutor_ids)
    return (
        Agreement.objects.filter(relation_filter)
        .exclude(
            status__in={
                Agreement.Status.TERMINATED,
                Agreement.Status.DECLINED,
                Agreement.Status.REVOKED,
                Agreement.Status.SUPERSEDED,
            }
        )
        .select_related("participant", "student__user", "tutor__user")
    )


def future_lessons_for_closure(users, now=None):
    now = now or timezone.now()
    local_now = timezone.localtime(now)
    student_ids, tutor_ids = _profiles_from_users(users)
    relation_filter = Q(pk__in=[])
    if student_ids:
        relation_filter |= Q(student_id__in=student_ids)
    if tutor_ids:
        relation_filter |= Q(tutor_id__in=tutor_ids)
    return Lesson.objects.filter(
        relation_filter,
        status=Lesson.Status.PLANNED,
    ).filter(Q(date__gt=local_now.date()) | Q(date=local_now.date(), time__gte=local_now.time()))


def closure_preview(user):
    users = closure_scope_for(user)
    now = timezone.now()
    return {
        "affected_users": users,
        "agreements": list(agreements_for_closure(users)),
        "future_lesson_count": future_lessons_for_closure(users, now).count(),
        "scheduled_deletion_at": _add_months(now, ACCOUNT_ARCHIVE_MONTHS),
    }


def issue_account_closure_token(user):
    token = secrets.token_urlsafe(32)
    user.account_closure_requested_at = timezone.now()
    user.account_closure_token_digest = hashlib.sha256(token.encode()).hexdigest()
    user.account_closure_token_expires_at = timezone.now() + timedelta(
        hours=ACCOUNT_CLOSURE_TOKEN_LIFETIME_HOURS
    )
    user.save(
        update_fields=[
            "account_closure_requested_at",
            "account_closure_token_digest",
            "account_closure_token_expires_at",
        ]
    )
    return token


def account_closure_token_is_valid(user, token):
    if not user.is_active or not user.account_closure_token_digest:
        return False
    if not user.account_closure_token_expires_at:
        return False
    if user.account_closure_token_expires_at < timezone.now():
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    return secrets.compare_digest(digest, user.account_closure_token_digest)


def send_account_closure_confirmation_email(request, user, token):
    confirmation_url = request.build_absolute_uri(
        reverse("account_closure_confirm", kwargs={"user_id": user.pk, "token": token})
    )
    message = EmailMultiAlternatives(
        "BrainBoost: Kündigung per E-Mail bestätigen",
        (
            f"Hallo {user.display_name},\n\n"
            "du hast die Kündigung deines BrainBoost-Kontos angefordert. "
            "Bestätige sie innerhalb von 24 Stunden über diesen einmalig verwendbaren Link:\n"
            f"{confirmation_url}\n\n"
            "Erst nach dieser Bestätigung werden die betroffenen Konten gesperrt, "
            "Vereinbarungen gekündigt und künftige Termine storniert. "
            "Falls du die Kündigung nicht angefordert hast, ignoriere diese E-Mail."
        ),
        getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@brainboost.local"),
        [user.email],
    )
    if message.send() == 0:
        raise RuntimeError("account_closure_confirmation_email_failed")


def _notification_recipients(users, agreements):
    emails = {user.email.strip() for user in users if user.email.strip()}
    for agreement in agreements:
        if agreement.participant_id and agreement.participant.email:
            emails.add(agreement.participant.email.strip())
        if agreement.student_id and agreement.student.user.email:
            emails.add(agreement.student.user.email.strip())
        if agreement.tutor_id and agreement.tutor.user.email:
            emails.add(agreement.tutor.user.email.strip())
    internal_email = getattr(settings, "INTERNAL_CONTACT_EMAIL", "").strip()
    if internal_email:
        emails.add(internal_email)
    return sorted(email for email in emails if email)


@transaction.atomic
def complete_account_closure(user_id, token):
    initiator = CustomUser.objects.select_for_update().get(pk=user_id)
    if not account_closure_token_is_valid(initiator, token):
        raise AccountClosureNotAllowed("Der Bestätigungslink ist ungültig oder abgelaufen.")

    scoped_users = closure_scope_for(initiator)
    user_ids = [user.pk for user in scoped_users]
    users = list(CustomUser.objects.select_for_update().filter(pk__in=user_ids).order_by("pk"))
    agreement_ids = list(agreements_for_closure(users).values_list("pk", flat=True))
    agreements = list(
        Agreement.objects.select_for_update().filter(pk__in=agreement_ids).order_by("pk")
    )
    now = timezone.now()
    deletion_at = _add_months(now, ACCOUNT_ARCHIVE_MONTHS)
    today = timezone.localdate(now)
    tutor_end_date = _tutor_termination_date(today)
    recipients = _notification_recipients(users, agreements)
    agreement_references = [agreement.reference for agreement in agreements]

    for agreement in agreements:
        effective_on = (
            tutor_end_date
            if agreement.agreement_type == Agreement.AgreementType.TUTOR
            else today
        )
        agreement.status = Agreement.Status.TERMINATED
        agreement.terminated_at = now
        agreement.termination_effective_on = effective_on
        agreement.terminated_by = initiator
        agreement.confirmation_token_digest = ""
        agreement.confirmation_token_expires_at = None
        agreement.tutor_confirmation_token_digest = ""
        agreement.tutor_confirmation_token_expires_at = None
        if agreement.stripe_mandate_status in {
            Agreement.StripeMandateStatus.PENDING,
            Agreement.StripeMandateStatus.ACTIVE,
        }:
            agreement.stripe_mandate_status = Agreement.StripeMandateStatus.INACTIVE
        agreement.save(
            update_fields=[
                "status",
                "terminated_at",
                "termination_effective_on",
                "terminated_by",
                "confirmation_token_digest",
                "confirmation_token_expires_at",
                "tutor_confirmation_token_digest",
                "tutor_confirmation_token_expires_at",
                "stripe_mandate_status",
                "updated_at",
            ]
        )
        audit(
            agreement,
            "agreement_terminated_by_account_closure",
            actor=initiator,
            metadata={
                "initiator_user_id": initiator.pk,
                "initiator_email": initiator.email,
                "initiator_role": initiator.role,
                "confirmed_at": now.isoformat(),
                "termination_effective_on": effective_on.isoformat(),
                "scheduled_deletion_at": deletion_at.isoformat(),
            },
        )

    lessons = future_lessons_for_closure(users, now)
    lesson_count = lessons.update(
        status=Lesson.Status.CANCELLED,
        cancellation_reason="Konto und zugehörige Vereinbarung gekündigt",
        cancelled_at=now,
        cancellation_chargeable=False,
    )

    for affected_user in users:
        affected_user.is_active = False
        affected_user.archived_at = now
        affected_user.scheduled_deletion_at = deletion_at
        affected_user.account_closure_token_digest = ""
        affected_user.account_closure_token_expires_at = None
        affected_user.save(
            update_fields=[
                "is_active",
                "archived_at",
                "scheduled_deletion_at",
                "account_closure_token_digest",
                "account_closure_token_expires_at",
            ]
        )
        if hasattr(affected_user, "tutor_profile"):
            TutorProfile.objects.filter(pk=affected_user.tutor_profile.pk).update(
                status=TutorProfile.Status.PAUSED
            )

    return {
        "initiator_name": initiator.display_name,
        "initiator_email": initiator.email,
        "affected_user_count": len(users),
        "agreement_count": len(agreements),
        "agreement_references": agreement_references,
        "lesson_count": lesson_count,
        "recipients": recipients,
        "archived_at": now,
        "scheduled_deletion_at": deletion_at,
    }


def send_account_closed_notifications(result):
    references = ", ".join(result["agreement_references"]) or "keine"
    deletion_date = timezone.localtime(result["scheduled_deletion_at"]).strftime("%d.%m.%Y")
    body = (
        "Die Kündigung wurde über den einmaligen E-Mail-Link bestätigt.\n\n"
        f"Gekündigte Vereinbarungen: {references}\n"
        f"Kostenfrei stornierte künftige Termine: {result['lesson_count']}\n"
        f"Vorgesehene Löschung der archivierten Profile: {deletion_date}\n\n"
        "Vertrags- und Abrechnungsnachweise, die gesetzlichen Aufbewahrungspflichten "
        "unterliegen, können über die Profillöschung hinaus aufbewahrt werden."
    )
    failed_recipients = []
    for recipient in result["recipients"]:
        message = EmailMultiAlternatives(
            "BrainBoost: Kündigung bestätigt",
            body,
            getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@brainboost.local"),
            [recipient],
        )
        if message.send() == 0:
            failed_recipients.append(recipient)
    return failed_recipients
