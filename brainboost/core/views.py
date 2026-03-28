import base64
import io
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
from pathlib import Path

import logging
import re
import unicodedata
from typing import Optional
from urllib.parse import quote, urlencode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.contrib.staticfiles import finders
from django.core.files.base import ContentFile
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import get_object_or_404, render, redirect
from django.db.models import Count, Q
from django.template.loader import render_to_string
from django.utils.formats import date_format
from django.utils import timezone
from django.http import JsonResponse, HttpResponse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode, url_has_allowed_host_and_scheme
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from .forms import (
    LessonForm,
    ProgressEntryForm,
    LearningMaterialForm,
    InvoiceForm,
    InvoiceGenerateForm,
    HolidaySurveyForm,
    HolidaySurveyAnswerForm,
    FAQItemForm,
    FAQSubmissionForm,
    ParentCreateForm,
    StudentCreateForm,
    TutorTemplateForm,
    TutorCreateForm,
    ParentProfileForm,
    StudentProfileForm,
    TutorProfileForm,
    BrainBoostFeedbackForm,
    EmailOrUsernameAuthenticationForm,
    BroadcastEmailForm,
    TutorStudentAssignmentForm,
)
from .notifications import (
    notify_holiday_survey_created,
    notify_invoice_parent,
    notify_invoice_payment_confirmed,
    notify_invoice_payment_received_tutor,
    notify_invoice_payment_selected,
    notify_invoice_pending_approval,
    notify_lesson_cancelled,
    notify_lesson_changed,
    notify_lesson_created,
    notify_lesson_reschedule_requested,
    notify_material_uploaded,
)
from .models import (
    CustomUser,
    Lesson,
    ProgressEntry,
    StudentProfile,
    ParentProfile,
    TutorProfile,
    LearningMaterial,
    Invoice,
    HolidaySurvey,
    HolidaySurveyResponse,
    FAQItem,
    TutorTemplate,
    BrainBoostFeedback,
    TemporaryTutorAssignment,
)

logger = logging.getLogger(__name__)


def _ensure_profile_for_user(user: CustomUser):
    """Create missing profile objects on-the-fly to keep views simple."""
    if user.role == CustomUser.Roles.STUDENT and not hasattr(user, "student_profile"):
        StudentProfile.objects.create(user=user)
    elif user.role == CustomUser.Roles.PARENT and not hasattr(user, "parent_profile"):
        ParentProfile.objects.create(user=user)
    elif user.role == CustomUser.Roles.TUTOR and not hasattr(user, "tutor_profile"):
        TutorProfile.objects.create(user=user)


def _haversine_km(lat1, lon1, lat2, lon2):
    """Compute distance between two lat/lon points in km."""
    from math import radians, sin, cos, sqrt, atan2

    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(
        dlon / 2
    ) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return round(r * c, 2)


def landing_page(request):
    form = EmailOrUsernameAuthenticationForm(request)
    return render(
        request,
        "landing.html",
        {
            "form": form,
            "faq_items": _faq_items_for_target("landing"),
        },
    )


def _assign_location_and_distance(lesson: Lesson):
    """Set address and distance on lesson based on location choice."""
    address = ""
    distance = None
    if lesson.ort == Lesson.Ort.ZUHAUSE_STUDENT:
        student = lesson.student
        address = student.address
        lat = student.latitude
        lon = student.longitude
        tutor_lat = getattr(lesson.tutor, "latitude", None)
        tutor_lon = getattr(lesson.tutor, "longitude", None)
        if None not in (lat, lon, tutor_lat, tutor_lon):
            base_km = _haversine_km(tutor_lat, tutor_lon, lat, lon)
            distance = round(base_km * 2 * 1.35, 2)
    elif lesson.ort == Lesson.Ort.BIB:
        address = "Bibliothek Braunschweig"
    elif lesson.ort == Lesson.Ort.BIB_WOB:
        address = "Bibliothek Wolfsburg"
        distance = 70.0

    lesson.location_address = address or ""
    lesson.distance_km = distance


def _actor_label(user: CustomUser) -> str:
    if user.role == CustomUser.Roles.TUTOR:
        return f"TutorIn {user.username}"
    if user.role == CustomUser.Roles.PARENT:
        return f"Elternteil {user.username}"
    if user.role == CustomUser.Roles.STUDENT:
        return f"SchülerIn {user.username}"
    return user.username


def _display_name(user: CustomUser) -> str:
    full_name = user.get_full_name().strip()
    return full_name or user.username


def _missing_tutor_bank_field_labels(tutor_profile: TutorProfile) -> list[str]:
    missing_fields: list[str] = []
    if not (tutor_profile.account_holder or "").strip():
        missing_fields.append("KontoinhaberIn")
    if not (tutor_profile.bank_name or "").strip():
        missing_fields.append("Bankname")
    if not (tutor_profile.iban or "").strip():
        missing_fields.append("IBAN")
    if not (tutor_profile.bic or "").strip():
        missing_fields.append("BIC")
    return missing_fields


def _normalize_whatsapp_number(raw_number: str) -> str:
    if not raw_number:
        return ""
    raw_number = raw_number.strip()
    digits_only = "".join(ch for ch in raw_number if ch.isdigit())
    if not digits_only:
        return ""
    if raw_number.startswith("+"):
        return digits_only
    if digits_only.startswith("00"):
        return digits_only[2:]
    if digits_only.startswith("0"):
        return f"49{digits_only[1:]}"
    return digits_only


def _invoice_whatsapp_message(request, invoice: Invoice, parent: ParentProfile) -> str:
    invoice_url = request.build_absolute_uri(invoice.file.url)
    portal_url = request.build_absolute_uri(reverse("invoice_list"))
    student_name = _display_name(invoice.student.user)
    tutor_name = _display_name(invoice.uploaded_by.user)
    parent_name = _display_name(parent.user)
    return (
        f"Hallo {parent_name},\n"
        f"eine neue Rechnung fuer {student_name} ist verfuegbar.\n"
        f"TutorIn: {tutor_name}\n"
        f"Faellig bis: {invoice.due_date.strftime('%d.%m.%Y')}\n"
        f"PDF: {invoice_url}\n"
        f"Portal: {portal_url}"
    )


def _invoice_parent_notification_links(request, invoice: Invoice) -> list[dict]:
    links = []
    for parent in invoice.student.parents.select_related("user"):
        number = _normalize_whatsapp_number(parent.phone_number)
        if not number:
            continue
        parent_name = _display_name(parent.user)
        links.append(
            {
                "name": parent_name,
                "url": reverse("invoice_notify_parent", args=[invoice.id, parent.id]),
            }
        )
    return links


def _lesson_calendar_title_for_user(user: CustomUser, lesson: Lesson) -> str:
    if user.role == CustomUser.Roles.TUTOR:
        return f"{_display_name(lesson.student.user)} - {lesson.subject_display}"
    return f"BrainBoost - {lesson.subject_display} - {_display_name(lesson.tutor.user)}"


def _lesson_google_calendar_url_for_user(user: CustomUser, lesson: Lesson) -> str:
    start = lesson.scheduled_datetime.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    end = lesson.end_datetime.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ctz = timezone.get_current_timezone_name()
    return (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text={quote(_lesson_calendar_title_for_user(user, lesson))}"
        f"&dates={start}/{end}"
        f"&details={quote(lesson.calendar_details)}"
        f"&location={quote(lesson.calendar_location)}"
        f"&ctz={quote(ctz)}"
    )


GERMAN_MONTH_NAMES = {
    1: "Januar",
    2: "Februar",
    3: "Maerz",
    4: "April",
    5: "Mai",
    6: "Juni",
    7: "Juli",
    8: "August",
    9: "September",
    10: "Oktober",
    11: "November",
    12: "Dezember",
}
INVOICE_NUMBER_PATTERN = re.compile(r"RE-(\d{3,})_", re.IGNORECASE)


def _sanitize_invoice_name_part(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = "".join(ch for ch in ascii_text if ch.isalnum())
    return cleaned or "Unbekannt"


def _normalize_iban(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def _format_iban_for_display(value: str) -> str:
    normalized = _normalize_iban(value)
    if not normalized:
        return "-"
    return " ".join(
        normalized[index : index + 4] for index in range(0, len(normalized), 4)
    )


def _build_epc_payment_payload(
    *,
    account_holder: str,
    iban: str,
    bic: str = "",
    amount: Optional[Decimal] = None,
    remittance_information: str = "",
) -> Optional[str]:
    account_holder_clean = (account_holder or "").strip()
    iban_clean = _normalize_iban(iban)
    bic_clean = "".join(ch for ch in (bic or "").upper() if ch.isalnum())
    if not account_holder_clean or not iban_clean:
        return None

    amount_line = ""
    if amount is not None and amount > Decimal("0.00"):
        amount_line = f"EUR{amount.quantize(Decimal('0.01'))}"

    remittance_clean = (remittance_information or "").strip()[:140]
    lines = [
        "BCD",
        "002",
        "1",
        "SCT",
        bic_clean[:11],
        account_holder_clean[:70],
        iban_clean[:34],
        amount_line,
        "",
        remittance_clean,
        "BrainBoost Nachhilfe",
        "",
    ]
    return "\n".join(lines)


def _epc_payment_qr_data_uri(payload: Optional[str]) -> Optional[str]:
    if not payload:
        return None

    try:
        import qrcode
    except ImportError:
        logger.warning("QR-Code konnte nicht erzeugt werden: Paket 'qrcode' nicht installiert.")
        return None

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _static_asset_uri(relative_path: str, request) -> str:
    local_path = finders.find(relative_path)
    if local_path:
        return Path(local_path).as_uri()
    return request.build_absolute_uri(f"/static/{relative_path}")


def _invoice_period_parts(invoice: Invoice) -> tuple[int, int]:
    if invoice.billing_year and invoice.billing_month:
        return invoice.billing_year, invoice.billing_month
    reference = timezone.localtime(invoice.uploaded_at) if invoice.uploaded_at else timezone.now()
    return reference.year, reference.month


def _next_invoice_number() -> int:
    max_number = 0
    latest = Invoice.objects.filter(invoice_number__isnull=False).order_by("-invoice_number").first()
    if latest and latest.invoice_number:
        max_number = latest.invoice_number
    for file_name in Invoice.objects.exclude(file="").values_list("file", flat=True):
        match = INVOICE_NUMBER_PATTERN.search(file_name or "")
        if match:
            max_number = max(max_number, int(match.group(1)))
    return max_number + 1


def _invoice_filename(invoice: Invoice, invoice_number: Optional[int] = None) -> str:
    year, month = _invoice_period_parts(invoice)
    student_name = _sanitize_invoice_name_part(
        f"{invoice.student.user.first_name}{invoice.student.user.last_name}"
    )
    sequence = invoice_number or invoice.invoice_number or _next_invoice_number()
    return f"WRE-{sequence:05d}_{GERMAN_MONTH_NAMES[month]}{str(year)[-2:]}_{student_name}.pdf"


def _rename_invoice_file(invoice: Invoice, filename: str) -> None:
    if not invoice.file:
        return
    invoice.file.open("rb")
    try:
        file_content = invoice.file.read()
    finally:
        invoice.file.close()
    old_name = invoice.file.name
    invoice.file.save(filename, ContentFile(file_content), save=False)
    if old_name and old_name != invoice.file.name:
        invoice.file.storage.delete(old_name)


def _finalize_invoice_number_and_filename(invoice: Invoice) -> None:
    if invoice.invoice_number:
        return
    next_number = _next_invoice_number()
    invoice.invoice_number = next_number
    invoice.sent_at = timezone.now()
    _rename_invoice_file(invoice, _invoice_filename(invoice, next_number))
    invoice.save(update_fields=["invoice_number", "sent_at", "file"])


def _mark_invoice_payment_selected(
    request,
    invoice: Invoice,
    parent_profile: ParentProfile,
    method: str,
    notify_tutor: bool = True,
) -> None:
    invoice.payment_method = method
    invoice.payment_status = Invoice.PaymentStatus.ANNOUNCED
    invoice.payment_requested_by = parent_profile
    invoice.payment_requested_at = timezone.now()
    invoice.save(
        update_fields=[
            "payment_method",
            "payment_status",
            "payment_requested_by",
            "payment_requested_at",
        ]
    )
    if notify_tutor:
        notify_invoice_payment_selected(request, invoice, parent_profile)


INVOICE_RATE_BY_DURATION = {
    45: Decimal("19.00"),
    60: Decimal("25.00"),
    90: Decimal("36.00"),
}
CANCELLATION_FREE_HOURS = 5


def _is_chargeable_cancellation(lesson: Lesson, cancelled_at) -> bool:
    cancellation_deadline = lesson.scheduled_datetime - timedelta(hours=CANCELLATION_FREE_HOURS)
    return cancelled_at > cancellation_deadline


def _easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _holiday_dates_lower_saxony(year: int) -> set[date]:
    easter = _easter_sunday(year)
    return {
        date(year, 1, 1),
        easter - timedelta(days=2),  # Karfreitag
        easter + timedelta(days=1),  # Ostermontag
        date(year, 5, 1),
        easter + timedelta(days=39),  # Christi Himmelfahrt
        easter + timedelta(days=50),  # Pfingstmontag
        date(year, 10, 3),
        date(year, 10, 31),  # Reformationstag (Niedersachsen)
        date(year, 12, 25),
        date(year, 12, 26),
    }


def _lesson_invoice_components(lesson: Lesson) -> dict:
    base_amount = INVOICE_RATE_BY_DURATION.get(lesson.duration_minutes)
    if base_amount is None:
        base_amount = (
            Decimal("19.00") * Decimal(str(lesson.duration_minutes)) / Decimal("45")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    distance = Decimal(str(lesson.computed_distance_km or 0))
    travel_amount = Decimal("0.00")
    if distance > Decimal("3"):
        travel_amount = ((distance - Decimal("3")) * Decimal("0.30")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    is_special_day = (
        lesson.date.weekday() >= 5
        or lesson.date in _holiday_dates_lower_saxony(lesson.date.year)
    )
    subtotal = base_amount + travel_amount
    surcharge_amount = Decimal("0.00")
    if is_special_day:
        surcharge_amount = (subtotal * Decimal("0.27")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    total_amount = (subtotal + surcharge_amount).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    notes = []
    if travel_amount > 0:
        notes.append(f"Fahrtkosten {travel_amount} EUR")
    if is_special_day:
        notes.append(f"27% Zuschlag {surcharge_amount} EUR")

    return {
        "base_amount": base_amount,
        "travel_amount": travel_amount,
        "surcharge_amount": surcharge_amount,
        "total_amount": total_amount,
        "notes": notes,
        "is_special_day": is_special_day,
        "distance_km": distance.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
    }


def _apply_invoice_discount(
    subtotal_amount: Decimal,
    discount_type: str = "",
    discount_value: Optional[Decimal] = None,
) -> dict:
    subtotal_amount = subtotal_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if not discount_type or discount_value is None:
        return {
            "discount_type": "",
            "discount_value": None,
            "discount_amount": None,
            "discount_label": "",
            "total_amount": subtotal_amount,
        }

    discount_value = Decimal(str(discount_value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    if discount_type == Invoice.DiscountType.FIXED:
        if discount_value > subtotal_amount:
            raise ValueError("Der Rabatt in EUR darf die Rechnungssumme nicht übersteigen.")
        discount_amount = discount_value
        discount_label = f"{discount_value} EUR"
    elif discount_type == Invoice.DiscountType.PERCENT:
        if discount_value > Decimal("100.00"):
            raise ValueError("Der prozentuale Rabatt darf höchstens 100 betragen.")
        discount_amount = (subtotal_amount * discount_value / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        discount_label = f"{discount_value}%"
    else:
        raise ValueError("Ungültige Rabattart.")

    return {
        "discount_type": discount_type,
        "discount_value": discount_value,
        "discount_amount": discount_amount,
        "discount_label": discount_label,
        "total_amount": (subtotal_amount - discount_amount).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ),
    }


def _build_invoice_pdf_context(
    tutor_profile: TutorProfile,
    student: StudentProfile,
    period_start,
    lessons,
    discount_type: str = "",
    discount_value: Optional[Decimal] = None,
) -> dict:
    tutor_name = _display_name(tutor_profile.user)
    student_name = _display_name(student.user)
    period_label = date_format(period_start, "F Y")
    today = timezone.localdate()
    subtotal_amount = Decimal("0.00")
    total_travel = Decimal("0.00")
    total_surcharge = Decimal("0.00")
    line_items = []
    for lesson in lessons:
        components = _lesson_invoice_components(lesson)
        lesson_amount = components["total_amount"]
        subtotal_amount += lesson_amount
        total_travel += components["travel_amount"]
        total_surcharge += components["surcharge_amount"]
        line_item_notes = list(components["notes"])
        if lesson.status == Lesson.Status.CANCELLED and lesson.cancellation_chargeable:
            line_item_notes.insert(0, "Zu spät storniert (kostenpflichtig)")
        line_items.append(
            {
                "date": lesson.date,
                "time": lesson.time,
                "subject": lesson.subject_display,
                "duration_minutes": lesson.duration_minutes,
                "location": lesson.get_ort_display(),
                "base_amount": components["base_amount"],
                "travel_amount": components["travel_amount"],
                "surcharge_amount": components["surcharge_amount"],
                "total_amount": components["total_amount"],
                "notes": line_item_notes,
                "is_special_day": components["is_special_day"],
                "distance_km": components["distance_km"],
            }
        )

    discount_data = _apply_invoice_discount(
        subtotal_amount=subtotal_amount,
        discount_type=discount_type,
        discount_value=discount_value,
    )

    return {
        "tutor_name": tutor_name,
        "student_name": student_name,
        "period_label": period_label,
        "invoice_date": today,
        "due_date": today + timedelta(days=7),
        "line_items": line_items,
        "total_travel": total_travel.quantize(Decimal("0.01")),
        "total_surcharge": total_surcharge.quantize(Decimal("0.01")),
        "subtotal_amount": subtotal_amount.quantize(Decimal("0.01")),
        "discount_type": discount_data["discount_type"],
        "discount_value": discount_data["discount_value"],
        "discount_amount": discount_data["discount_amount"],
        "discount_label": discount_data["discount_label"],
        "total_amount": discount_data["total_amount"],
        "account_holder": tutor_profile.account_holder or "-",
        "bank_name": tutor_profile.bank_name or "-",
        "iban": _format_iban_for_display(tutor_profile.iban),
        "bic": tutor_profile.bic or "-",
    }


def _generate_invoice_pdf(
    request,
    tutor_profile: TutorProfile,
    student: StudentProfile,
    period_start,
    lessons,
    invoice_context=None,
) -> bytes:
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "WeasyPrint ist noch nicht vollständig verfügbar. "
            "Bitte die Systembibliotheken für WeasyPrint installieren."
        ) from exc

    context = invoice_context or _build_invoice_pdf_context(
        tutor_profile=tutor_profile,
        student=student,
        period_start=period_start,
        lessons=lessons,
    )
    payment_qr_payload = _build_epc_payment_payload(
        account_holder=tutor_profile.account_holder,
        iban=tutor_profile.iban,
        bic=tutor_profile.bic,
        amount=context.get("total_amount"),
        remittance_information=(
            f"Rechnung {context.get('period_label', '')} {context.get('student_name', '')}"
        ),
    )
    payment_qr_url = _epc_payment_qr_data_uri(payment_qr_payload)
    html = render_to_string(
        "invoice_pdf.html",
        {
            **context,
            "logo_url": _static_asset_uri("design/LogoPNG.png", request),
            "shababa_font_woff2_url": _static_asset_uri(
                "fonts/shababa/shababa-w01-regular.woff2",
                request,
            ),
            "shababa_font_woff_url": _static_asset_uri(
                "fonts/shababa/shababa-w01-regular.woff",
                request,
            ),
            "payment_qr_url": payment_qr_url,
        },
    )
    return HTML(
        string=html,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf()


def _stripe_client():
    secret_key = getattr(settings, "STRIPE_SECRET_KEY", "")
    if not secret_key:
        raise RuntimeError("Stripe ist noch nicht konfiguriert. STRIPE_SECRET_KEY fehlt.")
    import stripe

    stripe.api_key = secret_key
    return stripe


def _build_recurrence_dates(cleaned_data) -> list[date]:
    if not cleaned_data.get("repeat_enabled"):
        return []

    start_date = cleaned_data["date"]
    interval_weeks = cleaned_data["repeat_interval_weeks"]
    end_mode = cleaned_data["repeat_end_mode"]
    dates = []

    if end_mode == "weeks":
        max_weeks = cleaned_data["repeat_weeks"]
        offset = interval_weeks
        while offset <= max_weeks:
            dates.append(start_date + timedelta(weeks=offset))
            offset += interval_weeks
    elif end_mode == "count":
        occurrences = cleaned_data["repeat_occurrences"]
        for index in range(1, occurrences):
            dates.append(start_date + timedelta(weeks=interval_weeks * index))
    elif end_mode == "until":
        repeat_until = cleaned_data["repeat_until"]
        next_date = start_date + timedelta(weeks=interval_weeks)
        while next_date <= repeat_until:
            dates.append(next_date)
            next_date += timedelta(weeks=interval_weeks)

    return dates


def _rating_label(rating) -> str:
    if rating is None:
        return "ohne Mitarbeitsbewertung"
    return f"Mitarbeit {rating}/10"


def _limit_news_items(items: list[dict], limit: int = 3) -> list[dict]:
    return sorted(items, key=lambda item: item["timestamp"], reverse=True)[:limit]


def _monthly_brainboost_feedback_news_item(audience: str) -> dict:
    now = timezone.now()
    return {
        "timestamp": now,
        "title": "Monatsfeedback an BrainBoost",
        "text": "BrainBoost möchte die beste Nachhilfeplattform Deutschlands werden! Was ist dafür nötig?",
        "url": f"{reverse('brainboost_feedback')}?role={audience}&source=news",
        "action_label": "Anonymes Feedback geben",
    }


def _lesson_news_items(lessons) -> list[dict]:
    items = []
    for lesson in lessons:
        labels = []
        if lesson.reschedule_requested:
            labels.append("Terminverlegung angefragt")
        if lesson.status == Lesson.Status.CANCELLED:
            if lesson.cancellation_chargeable:
                labels.append("Termin zu spät storniert (kostenpflichtig)")
            else:
                labels.append("Termin pünktlich storniert")
        elif lesson.status == Lesson.Status.COMPLETED:
            labels.append("Termin abgeschlossen")
        elif lesson.status == Lesson.Status.PLANNED:
            labels.append("Termin geplant")
        items.append(
            {
                "timestamp": lesson.scheduled_datetime,
                "title": f"Termin: {_display_name(lesson.student.user)}",
                "text": f"{date_format(lesson.date, 'l, d.m.Y')} um {lesson.time.strftime('%H:%M')} · {', '.join(labels)}",
            }
        )
    return items


def _student_news_items(student_profile: StudentProfile) -> list[dict]:
    items = [_monthly_brainboost_feedback_news_item(BrainBoostFeedback.Audience.STUDENT)]
    lesson_items = Lesson.objects.filter(student=student_profile).select_related("student__user").order_by("-date", "-time")[:4]
    items.extend(_lesson_news_items(lesson_items))

    progress_entries = (
        ProgressEntry.objects.filter(lesson__student=student_profile)
        .select_related("lesson__tutor__user")
        .order_by("-created_at")[:4]
    )
    for entry in progress_entries:
        items.append(
            {
                "timestamp": entry.created_at,
                "title": "Neuer Lernfortschritt",
                "text": f"{_display_name(entry.lesson.tutor.user)} hat einen Eintrag mit {_rating_label(entry.rating)} hinterlegt.",
            }
        )

    materials = (
        LearningMaterial.objects.filter(student=student_profile, kind=LearningMaterial.Kind.SOLUTION)
        .select_related("uploaded_by__user")
        .order_by("-uploaded_at")[:4]
    )
    for material in materials:
        items.append(
            {
                "timestamp": material.uploaded_at,
                "title": "Neue Musterlösung",
                "text": f"Neue Musterlösung von {_display_name(material.uploaded_by.user)} wurde hochgeladen.",
            }
        )
    return _limit_news_items(items)


def _parent_news_items(parent_profile: ParentProfile) -> list[dict]:
    items = [_monthly_brainboost_feedback_news_item(BrainBoostFeedback.Audience.PARENT)]
    students = parent_profile.students.all()
    lesson_items = (
        Lesson.objects.filter(student__in=students)
        .select_related("student__user")
        .order_by("-date", "-time")[:4]
    )
    items.extend(_lesson_news_items(lesson_items))

    progress_entries = (
        ProgressEntry.objects.filter(lesson__student__in=students)
        .select_related("lesson__student__user", "lesson__tutor__user")
        .order_by("-created_at")[:4]
    )
    for entry in progress_entries:
        items.append(
            {
                "timestamp": entry.created_at,
                "title": f"Lernfortschritt: {_display_name(entry.lesson.student.user)}",
                "text": f"{_display_name(entry.lesson.tutor.user)} hat einen neuen Eintrag mit {_rating_label(entry.rating)} erstellt.",
            }
        )

    invoices = (
        Invoice.objects.filter(student__in=students)
        .select_related("student__user")
        .order_by("-uploaded_at")[:4]
    )
    for invoice in invoices:
        items.append(
            {
                "timestamp": invoice.uploaded_at,
                "title": f"Neue Rechnung: {_display_name(invoice.student.user)}",
                "text": f"Eine neue Rechnung wurde am {invoice.uploaded_at.strftime('%d.%m.%Y %H:%M')} hochgeladen.",
            }
        )

    materials = (
        LearningMaterial.objects.filter(student__in=students, kind=LearningMaterial.Kind.SOLUTION)
        .select_related("student__user")
        .order_by("-uploaded_at")[:4]
    )
    for material in materials:
        items.append(
            {
                "timestamp": material.uploaded_at,
                "title": f"Neue Musterlösung: {_display_name(material.student.user)}",
                "text": "Es wurde eine neue Musterlösung hochgeladen.",
            }
        )
    survey_responses = (
        HolidaySurveyResponse.objects.filter(
            student__in=students,
            answer="",
        )
        .select_related("student__user", "survey__tutor__user")
        .order_by("-survey__created_at")[:4]
    )
    for response in survey_responses:
        items.append(
            {
                "timestamp": response.survey.created_at,
                "title": f"Umfrage: {_display_name(response.student.user)}",
                "text": f"{response.survey.question} Bitte antworte schnellstmöglich in der WebApp.",
                "url": reverse("holiday_surveys"),
                "action_label": "Jetzt antworten",
            }
        )
    return _limit_news_items(items)


def _tutor_news_items(tutor_profile: TutorProfile) -> list[dict]:
    items = [_monthly_brainboost_feedback_news_item(BrainBoostFeedback.Audience.TUTOR)]
    assigned_students = _assigned_students_qs(tutor_profile)
    subordinate_tutors = _assigned_tutors_qs(tutor_profile)

    lesson_items = (
        Lesson.objects.filter(tutor=tutor_profile)
        .select_related("student__user")
        .order_by("-date", "-time")[:4]
    )
    items.extend(_lesson_news_items(lesson_items))

    progress_entries = (
        ProgressEntry.objects.filter(lesson__tutor=tutor_profile)
        .select_related("lesson__student__user")
        .order_by("-created_at")[:4]
    )
    for entry in progress_entries:
        items.append(
            {
                "timestamp": entry.created_at,
                "title": f"Lernfortschritt gespeichert: {_display_name(entry.lesson.student.user)}",
                "text": f"{_rating_label(entry.rating)} wurde eingetragen.",
            }
        )

    materials = (
        LearningMaterial.objects.filter(Q(student__in=assigned_students) | Q(uploaded_by=tutor_profile))
        .select_related("student__user")
        .order_by("-uploaded_at")[:4]
    )
    for material in materials:
        items.append(
            {
                "timestamp": material.uploaded_at,
                "title": f"Neues Material: {_display_name(material.student.user)}",
                "text": f"{material.get_kind_display()} wurde hochgeladen.",
            }
        )

    invoices = (
        Invoice.objects.filter(Q(uploaded_by=tutor_profile) | Q(uploaded_by__in=subordinate_tutors))
        .select_related("student__user", "uploaded_by__user")
        .order_by("-uploaded_at")[:4]
    )
    for invoice in invoices:
        items.append(
            {
                "timestamp": invoice.uploaded_at,
                "title": f"Neue Rechnung: {_display_name(invoice.student.user)}",
                "text": f"Hochgeladen von {_display_name(invoice.uploaded_by.user)}.",
            }
        )
    survey_responses = (
        HolidaySurveyResponse.objects.filter(survey__tutor=tutor_profile)
        .exclude(answer="")
        .select_related("student__user", "parent__user", "survey")
        .order_by("-answered_at")[:4]
    )
    for response in survey_responses:
        items.append(
            {
                "timestamp": response.answered_at or response.survey.created_at,
                "title": f"Umfrage beantwortet: {_display_name(response.student.user)}",
                "text": f"{_display_name(response.parent.user) if response.parent else 'Ein Elternteil'} hat mit {response.get_answer_display()} geantwortet.",
            }
        )
    return _limit_news_items(items)


def contact(request):
    return render(request, "contact.html")


def tutorin_werden(request):
    return render(request, "tutorin_werden.html")


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


def _send_set_password_email(request, user: CustomUser) -> None:
    if not user.email:
        raise ValueError("missing_email")
    if not getattr(settings, "EMAIL_HOST_USER", "") or not getattr(
        settings, "EMAIL_HOST_PASSWORD", ""
    ):
        raise RuntimeError("smtp_config_missing")
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    reset_path = reverse(
        "password_reset_confirm",
        kwargs={"uidb64": uid, "token": token},
    )
    reset_url = request.build_absolute_uri(reset_path)
    context = {
        "heading": "BrainBoost: Bestätigung & Passwort setzen",
        "user": user,
        "set_password_url": reset_url,
    }
    subject = "BrainBoost: Bestätigung & Passwort setzen"
    text_body = render_to_string("emails/registration_confirmation.txt", context)
    html_body = render_to_string("emails/registration_confirmation.html", context)
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@brainboost.local")
    message = EmailMultiAlternatives(subject, text_body, from_email, [user.email])
    message.attach_alternative(html_body, "text/html")
    sent = message.send()
    if sent == 0:
        raise RuntimeError("email_send_failed")


def _has_admin_access(user: CustomUser) -> bool:
    return bool(user.is_staff or user.is_superuser)


def _broadcast_recipient_emails(audience: str) -> list[str]:
    users = CustomUser.objects.filter(is_active=True).exclude(email="")
    if audience == BroadcastEmailForm.AUDIENCE_ADMINS:
        users = users.filter(Q(is_staff=True) | Q(is_superuser=True))
    elif audience == BroadcastEmailForm.AUDIENCE_PARENTS:
        users = users.filter(role=CustomUser.Roles.PARENT)
    elif audience == BroadcastEmailForm.AUDIENCE_STUDENTS:
        users = users.filter(role=CustomUser.Roles.STUDENT)
    elif audience == BroadcastEmailForm.AUDIENCE_TUTORS:
        users = users.filter(role=CustomUser.Roles.TUTOR)

    seen: set[str] = set()
    unique_emails: list[str] = []
    for email in users.values_list("email", flat=True).iterator():
        normalized = email.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_emails.append(email.strip())
    return unique_emails


def _send_broadcast_emails(subject: str, body: str, recipients: list[str]) -> tuple[int, int]:
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@brainboost.local")
    sent = 0
    failed = 0
    context = {
        "heading": subject,
        "subject": subject,
        "message": body,
    }
    text_body = render_to_string("emails/broadcast_email.txt", context)
    html_body = render_to_string("emails/broadcast_email.html", context)
    for recipient in recipients:
        try:
            message = EmailMultiAlternatives(subject, text_body, from_email, [recipient])
            message.attach_alternative(html_body, "text/html")
            delivered = message.send()
            if delivered:
                sent += 1
            else:
                failed += 1
        except Exception:
            failed += 1
            logger.exception("Rundmail Versand fehlgeschlagen fuer %s", recipient)
    return sent, failed


def _assigned_students_qs(tutor_profile: TutorProfile):
    return (
        StudentProfile.objects.filter(assigned_tutors=tutor_profile)
        .select_related("user")
        .distinct()
    )


def _assigned_tutors_qs(tutor_profile: TutorProfile):
    return tutor_profile.assigned_tutors.select_related("user").distinct()


def _tutor_student_assignment_url_with_source(
    current_tutor: TutorProfile, source_tutor: Optional[TutorProfile]
) -> str:
    base_url = reverse("tutor_student_assignment")
    if source_tutor and source_tutor.pk != current_tutor.pk:
        return f"{base_url}?{urlencode({'source_tutor': source_tutor.pk})}"
    return base_url


def _completed_lessons_for_temporary_assignment(assignment: TemporaryTutorAssignment) -> int:
    created_local = timezone.localtime(assignment.created_at)
    return Lesson.objects.filter(
        tutor_id=assignment.target_tutor_id,
        student_id=assignment.student_id,
        status=Lesson.Status.COMPLETED,
    ).filter(
        Q(date__gt=created_local.date())
        | Q(date=created_local.date(), time__gte=created_local.time())
    ).count()


def _close_temporary_assignment(
    assignment: TemporaryTutorAssignment,
    reason: str,
    *,
    remove_target_assignment: bool = True,
) -> None:
    if not assignment.is_active:
        return

    should_remove_target = False
    if remove_target_assignment and not assignment.target_was_preassigned:
        has_other_active = TemporaryTutorAssignment.objects.filter(
            is_active=True,
            student_id=assignment.student_id,
            target_tutor_id=assignment.target_tutor_id,
        ).exclude(pk=assignment.pk).exists()
        should_remove_target = not has_other_active

    assignment.is_active = False
    assignment.ended_reason = reason
    assignment.ended_at = timezone.now()
    assignment.save(update_fields=["is_active", "ended_reason", "ended_at"])

    if should_remove_target:
        assignment.student.assigned_tutors.remove(assignment.target_tutor)


def _sync_temporary_tutor_assignments() -> None:
    today = timezone.localdate()
    active_assignments = TemporaryTutorAssignment.objects.filter(is_active=True).select_related(
        "student",
        "target_tutor",
    )
    for assignment in active_assignments:
        if assignment.ends_on and today > assignment.ends_on:
            _close_temporary_assignment(
                assignment,
                TemporaryTutorAssignment.EndReason.DATE_REACHED,
            )
            continue
        if assignment.max_lessons:
            completed_lessons = _completed_lessons_for_temporary_assignment(assignment)
            if completed_lessons >= assignment.max_lessons:
                _close_temporary_assignment(
                    assignment,
                    TemporaryTutorAssignment.EndReason.LESSONS_REACHED,
                )


def _auto_complete_past_lessons(base_qs=None) -> int:
    """Mark planned lessons as completed once their timeslot is in the past."""
    today = timezone.localdate()
    now_time = timezone.localtime().time()
    queryset = base_qs if base_qs is not None else Lesson.objects.all()
    updated_count = queryset.filter(
        status=Lesson.Status.PLANNED,
        reschedule_requested=False,
    ).filter(
        Q(date__lt=today) | Q(date=today, time__lt=now_time)
    ).update(status=Lesson.Status.COMPLETED)
    _sync_temporary_tutor_assignments()
    return updated_count


def _faq_items_for_target(target: str):
    target_filter = {
        "parent": Q(show_for_parents=True),
        "student": Q(show_for_students=True),
        "tutor": Q(show_for_tutors=True),
        "landing": Q(show_on_landing=True),
    }.get(target)
    if target_filter is None:
        return FAQItem.objects.none()
    return FAQItem.objects.filter(is_published=True).filter(target_filter).order_by("question")


def _has_faq_admin_access(user: CustomUser) -> bool:
    return _has_admin_access(user)


def _build_progress_chart_data(entries, include_student_name: bool = False) -> dict:
    if hasattr(entries, "order_by"):
        ordered_entries = entries.order_by("lesson__date", "lesson__time", "created_at")
    else:
        ordered_entries = sorted(
            entries,
            key=lambda entry: (entry.lesson.date, entry.lesson.time, entry.created_at),
        )
    ordered_entries = list(ordered_entries)
    if not ordered_entries:
        return {"labels": [], "date_keys": [], "detail_labels": [], "datasets": []}

    labels: list[str] = []
    date_keys: list[str] = []
    detail_labels: list[str] = []
    subject_series: dict[str, list[Optional[int]]] = {}
    entry_count = len(ordered_entries)
    for index, entry in enumerate(ordered_entries):
        labels.append(f"{entry.lesson.date:%d.%m}")
        date_keys.append(entry.lesson.date.isoformat())
        base_label = f"{entry.lesson.date:%d.%m} {entry.lesson.time:%H:%M}"
        if include_student_name:
            detail_label = f"{base_label} · {_display_name(entry.lesson.student.user)}"
        else:
            detail_label = base_label
        detail_labels.append(detail_label)

        for subject_label, subject_rating in entry.rating_display_list:
            if subject_label not in subject_series:
                subject_series[subject_label] = [None] * entry_count
            if subject_rating is not None:
                subject_series[subject_label][index] = int(subject_rating)

    datasets = [
        {"label": subject_label, "values": values}
        for subject_label, values in sorted(subject_series.items(), key=lambda item: item[0])
    ]
    return {
        "labels": labels,
        "date_keys": date_keys,
        "detail_labels": detail_labels,
        "datasets": datasets,
    }


@login_required
def dashboard(request):
    _ensure_profile_for_user(request.user)
    context = {"show_faq_target_filters": _has_faq_admin_access(request.user)}
    if request.user.role == CustomUser.Roles.STUDENT:
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
            context["solutions"] = LearningMaterial.objects.filter(
                student__in=students, kind=LearningMaterial.Kind.SOLUTION
            ).select_related("student__user", "related_task")
    elif request.user.role == CustomUser.Roles.TUTOR:
        template = "dashboard_tutor.html"
        if hasattr(request.user, "tutor_profile"):
            tutor_profile = request.user.tutor_profile
            _auto_complete_past_lessons(
                Lesson.objects.filter(tutor=tutor_profile)
            )
            assigned_students = _assigned_students_qs(tutor_profile)
            assigned_tutors = _assigned_tutors_qs(tutor_profile)

            context["is_admin_tutor"] = request.user.is_superuser
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
            if missing_bank_fields and not request.session.get("bank_data_reminder_shown", False):
                context["show_bank_data_popup"] = True
                context["missing_tutor_bank_fields_text"] = ", ".join(missing_bank_fields)
                request.session["bank_data_reminder_shown"] = True
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
    if request.user.role not in (CustomUser.Roles.PARENT, CustomUser.Roles.STUDENT):
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

    return redirect("dashboard")


@login_required
def profile_view(request):
    _ensure_profile_for_user(request.user)

    form_class = {
        CustomUser.Roles.PARENT: ParentProfileForm,
        CustomUser.Roles.STUDENT: StudentProfileForm,
        CustomUser.Roles.TUTOR: TutorProfileForm,
    }.get(request.user.role, ParentProfileForm)

    uses_address_autocomplete = request.user.role in {
        CustomUser.Roles.STUDENT,
        CustomUser.Roles.TUTOR,
    }

    if request.method == "POST":
        form = form_class(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Dein Profil wurde aktualisiert.")
            return redirect("profile")
    else:
        form = form_class(user=request.user)

    return render(
        request,
        "profile.html",
        {
            "form": form,
            "uses_address_autocomplete": uses_address_autocomplete,
            "is_tutor_profile_form": request.user.role == CustomUser.Roles.TUTOR,
        },
    )


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
    if request.user.role != CustomUser.Roles.TUTOR:
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
    if request.user.role != CustomUser.Roles.TUTOR:
        return redirect("dashboard")
    if not ParentProfile.objects.exists():
        messages.error(
            request,
            "Lege zuerst ein Elternteil an, bevor du eine SchülerIn anlegst.",
        )
        return redirect("dashboard")
    if request.method == "POST":
        form = StudentCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            user.student_profile.assigned_tutors.add(request.user.tutor_profile)
            if not user.email:
                messages.success(
                    request,
                    "SchülerIn wurde als Platzhalter ohne WebApp-Zugang angelegt.",
                )
            else:
                try:
                    _send_set_password_email(request, user)
                except Exception as exc:
                    logger.exception("E-Mail Versand fehlgeschlagen (Schüler)")
                    messages.error(
                        request,
                        "SchülerIn wurde angelegt, die Bestätigungs-Mail konnte jedoch nicht gesendet werden. "
                        f"Fehler: {exc.__class__.__name__} ({exc})",
                    )
                else:
                    messages.success(
                        request,
                        "SchülerIn wurde angelegt. Eine Bestätigungs-Mail wurde versendet.",
                    )
            return redirect("dashboard")
    else:
        form = StudentCreateForm()
    return render(request, "student_create.html", {"form": form})


@login_required
def lesson_list(request):
    _ensure_profile_for_user(request.user)
    when = request.GET.get("when", "upcoming")
    today = timezone.localdate()
    try:
        week_offset = int(request.GET.get("offset", 0))
    except (TypeError, ValueError):
        week_offset = 0

    if request.user.role == CustomUser.Roles.STUDENT and hasattr(
        request.user, "student_profile"
    ):
        base_qs = Lesson.objects.filter(student=request.user.student_profile)
    elif request.user.role == CustomUser.Roles.PARENT and hasattr(
        request.user, "parent_profile"
    ):
        base_qs = Lesson.objects.filter(
            student__in=request.user.parent_profile.students.all()
        )
    elif request.user.role == CustomUser.Roles.TUTOR and hasattr(
        request.user, "tutor_profile"
    ):
        base_qs = Lesson.objects.filter(tutor=request.user.tutor_profile)
    else:
        base_qs = Lesson.objects.none()

    _auto_complete_past_lessons(base_qs)

    editable_ids: list[int] = []
    if request.user.role == CustomUser.Roles.TUTOR:
        editable_ids = list(base_qs.values_list("id", flat=True))

    period = request.GET.get("period", "").strip()
    student_id = request.GET.get("student", "").strip()
    weekday = request.GET.get("weekday", "").strip()
    duration = request.GET.get("duration", "").strip()
    ort = request.GET.get("ort", "").strip()

    filtered_qs = base_qs
    if period:
        try:
            period_year, period_month = period.split("-", 1)
        except ValueError:
            period_year, period_month = "", ""
        if period_year and period_month:
            filtered_qs = filtered_qs.filter(
                date__year=period_year,
                date__month=period_month,
            )
    if student_id:
        filtered_qs = filtered_qs.filter(student_id=student_id)
    if weekday:
        filtered_qs = filtered_qs.filter(date__week_day=weekday)
    if duration:
        filtered_qs = filtered_qs.filter(duration_minutes=duration)
    if ort:
        filtered_qs = filtered_qs.filter(ort=ort)

    if when == "past":
        lessons = Lesson.past_qs().filter(
            pk__in=filtered_qs.values_list("pk", flat=True)
        ).order_by("-date", "-time")
    else:
        lessons = Lesson.upcoming_qs().filter(
            pk__in=filtered_qs.values_list("pk", flat=True)
        ).order_by("-date", "-time")

    filter_pairs = [
        ("period", period),
        ("student", student_id),
        ("weekday", weekday),
        ("duration", duration),
        ("ort", ort),
    ]
    active_filter_pairs = [(key, value) for key, value in filter_pairs if value]
    filter_query = urlencode(active_filter_pairs)
    when_query = urlencode([("when", when), *active_filter_pairs])

    cancelable_ids = list(
        base_qs.exclude(status=Lesson.Status.CANCELLED).values_list("id", flat=True)
    )
    progress_lesson_ids = list(
        ProgressEntry.objects.filter(lesson__in=base_qs)
        .values_list("lesson_id", flat=True)
        .distinct()
    )

    period_options = [
        {
            "value": lesson_date.strftime("%Y-%m"),
            "label": date_format(lesson_date, "F Y"),
        }
        for lesson_date in sorted(
            base_qs.dates("date", "month"),
            reverse=True,
        )
    ]
    student_options = (
        StudentProfile.objects.filter(id__in=base_qs.values_list("student_id", flat=True))
        .select_related("user")
        .order_by("user__first_name", "user__last_name")
    )
    duration_options = [
        str(value)
        for value in sorted(
        {
            value
            for value in base_qs.values_list("duration_minutes", flat=True).distinct()
            if value is not None
        }
        )
    ]
    ort_values = set(base_qs.values_list("ort", flat=True).distinct())
    ort_options = [choice for choice in Lesson.Ort.choices if choice[0] in ort_values]
    weekday_options = [
        ("2", "Montag"),
        ("3", "Dienstag"),
        ("4", "Mittwoch"),
        ("5", "Donnerstag"),
        ("6", "Freitag"),
        ("7", "Samstag"),
        ("1", "Sonntag"),
    ]

    # Build a simple week calendar (current week Monday-Sunday)
    week_start = today - timedelta(days=today.weekday()) + timedelta(days=7 * week_offset)
    week_end = week_start + timedelta(days=6)
    week_lessons_qs = (
        base_qs.filter(date__range=(week_start, week_end))
        .order_by("date", "time")
        .select_related("student__user", "tutor__user")
    )
    week_days = []
    for i in range(7):
        day_date = week_start + timezone.timedelta(days=i)
        day_lessons = [l for l in week_lessons_qs if l.date == day_date]
        week_days.append({"date": day_date, "lessons": day_lessons})

    return render(
        request,
        "lesson_list.html",
        {
            "lessons": lessons,
            "when": when,
            "week_days": week_days,
            "week_start": week_start,
            "week_end": week_end,
            "week_offset": week_offset,
            "filter_query": filter_query,
            "when_query": when_query,
            "selected_period": period,
            "selected_student": student_id,
            "selected_weekday": weekday,
            "selected_duration": duration,
            "selected_ort": ort,
            "period_options": period_options,
            "student_options": student_options,
            "weekday_options": weekday_options,
            "duration_options": duration_options,
            "ort_options": ort_options,
            "cancelable_ids": cancelable_ids,
            "editable_ids": editable_ids,
            "progress_lesson_ids": progress_lesson_ids,
        },
    )


@login_required
def lesson_create(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(
        request.user, "tutor_profile"
    ):
        return redirect("lesson_list")

    if request.method == "POST":
        form = LessonForm(
            data=request.POST,
            tutor_profile=request.user.tutor_profile,
            is_edit=False,
        )
        if form.is_valid():
            lesson = form.save(commit=False)
            lesson.tutor = request.user.tutor_profile
            recurrence_dates = _build_recurrence_dates(form.cleaned_data)
            lessons_to_create = [lesson]
            for lesson_date in recurrence_dates:
                lessons_to_create.append(
                    Lesson(
                        date=lesson_date,
                        time=lesson.time,
                        ort=lesson.ort,
                        duration_minutes=lesson.duration_minutes,
                        student=lesson.student,
                        tutor=lesson.tutor,
                        fach=lesson.fach,
                        fach_2=lesson.fach_2,
                        fach_3=lesson.fach_3,
                        status=lesson.status,
                    )
                )
            for lesson_item in lessons_to_create:
                _assign_location_and_distance(lesson_item)
                lesson_item.save()
                notify_lesson_created(request, lesson_item)
            if recurrence_dates:
                messages.success(request, f"{len(lessons_to_create)} Termine wurden angelegt.")
            return redirect("lesson_list")
    else:
        form = LessonForm(tutor_profile=request.user.tutor_profile, is_edit=False)
    return render(request, "lesson_form.html", {"form": form})


@login_required
def lesson_cancel(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    allowed = False
    # TutorIn, StudentIn oder Elternteil dürfen stornieren
    if hasattr(request.user, "tutor_profile") and lesson.tutor == request.user.tutor_profile:
        allowed = True
    if hasattr(request.user, "student_profile") and lesson.student == request.user.student_profile:
        allowed = True
    if hasattr(request.user, "parent_profile") and request.user.parent_profile.students.filter(id=lesson.student_id).exists():
        allowed = True
    if not allowed:
        return JsonResponse({"ok": False, "message": "Keine Berechtigung."}, status=403) if is_ajax else redirect("lesson_list")

    if request.method != "POST":
        return redirect("lesson_list")

    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        msg = "Bitte gib einen Stornierungsgrund an."
        return JsonResponse({"ok": False, "message": msg}, status=400) if is_ajax else redirect("lesson_list")

    if lesson.status == Lesson.Status.CANCELLED:
        msg = "Termin ist bereits storniert."
        return JsonResponse({"ok": False, "message": msg}, status=400) if is_ajax else redirect("lesson_list")

    now = timezone.now()
    time_until_lesson = lesson.scheduled_datetime - now
    is_chargeable = time_until_lesson < timedelta(hours=CANCELLATION_FREE_HOURS)

    lesson.status = Lesson.Status.CANCELLED
    lesson.cancellation_reason = reason
    lesson.cancelled_at = now
    lesson.cancellation_chargeable = is_chargeable
    lesson.reschedule_requested = False
    lesson.save(
        update_fields=[
            "status",
            "cancellation_reason",
            "cancelled_at",
            "cancellation_chargeable",
            "reschedule_requested",
        ]
    )
    notify_lesson_cancelled(
        request,
        lesson,
        actor_label=_actor_label(request.user),
        reason=reason,
        include_tutor=request.user.role != CustomUser.Roles.TUTOR,
    )
    success_message = "Stornierungsanfrage wurde gespeichert."
    if is_chargeable:
        success_message = (
            "Stornierung wurde gespeichert. Da weniger als 5 Stunden vor Termin storniert wurde, "
            "ist der Termin kostenpflichtig und erscheint auf der Rechnung."
        )
    if is_ajax:
        return JsonResponse({"ok": True, "message": success_message})
    return redirect("lesson_list")


@login_required
def lesson_reschedule_request(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

    allowed = False
    if hasattr(request.user, "student_profile") and lesson.student == request.user.student_profile:
        allowed = True
    if hasattr(request.user, "parent_profile") and request.user.parent_profile.students.filter(id=lesson.student_id).exists():
        allowed = True
    if not allowed:
        return JsonResponse({"ok": False, "message": "Keine Berechtigung."}, status=403) if is_ajax else redirect("lesson_list")

    if request.method != "POST":
        return redirect("lesson_list")

    if lesson.status == Lesson.Status.CANCELLED:
        msg = "Stornierte Termine können nicht verlegt werden."
        return JsonResponse({"ok": False, "message": msg}, status=400) if is_ajax else redirect("lesson_list")

    now = timezone.now()
    time_until_lesson = lesson.scheduled_datetime - now
    if time_until_lesson < timedelta(hours=5):
        msg = "Terminverlegung weniger als 5 Stunden vor Termin nicht möglich. Bitte kontaktiere die TutorIn direkt."
        return JsonResponse({"ok": False, "message": msg}, status=400) if is_ajax else redirect("lesson_list")

    if lesson.reschedule_requested:
        msg = "Terminverlegung wurde bereits angefragt."
        return JsonResponse({"ok": True, "message": msg})

    lesson.reschedule_requested = True
    lesson.save(update_fields=["reschedule_requested"])
    notify_lesson_reschedule_requested(
        request,
        lesson,
        actor_label=_actor_label(request.user),
        include_tutor=True,
    )
    success_msg = "TutorIn wurde informiert. Termin ist als Verlegungsanfrage markiert."
    return JsonResponse({"ok": True, "message": success_msg}) if is_ajax else redirect("lesson_list")


@login_required
def lesson_google_calendar(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    allowed = False
    if hasattr(request.user, "tutor_profile") and lesson.tutor == request.user.tutor_profile:
        allowed = True
    if hasattr(request.user, "student_profile") and lesson.student == request.user.student_profile:
        allowed = True
    if hasattr(request.user, "parent_profile") and request.user.parent_profile.students.filter(id=lesson.student_id).exists():
        allowed = True
    if not allowed:
        return redirect("lesson_list")
    return redirect(_lesson_google_calendar_url_for_user(request.user, lesson))


@login_required
def lesson_ics(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    allowed = False
    if hasattr(request.user, "tutor_profile") and lesson.tutor == request.user.tutor_profile:
        allowed = True
    if hasattr(request.user, "student_profile") and lesson.student == request.user.student_profile:
        allowed = True
    if hasattr(request.user, "parent_profile") and request.user.parent_profile.students.filter(id=lesson.student_id).exists():
        allowed = True
    if not allowed:
        return redirect("lesson_list")

    start = lesson.scheduled_datetime.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    end = lesson.end_datetime.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    now_stamp = timezone.now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    summary = lesson.calendar_title
    description = lesson.calendar_details
    location = lesson.calendar_location
    ics_content = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//BrainBoost//DE",
            "BEGIN:VEVENT",
            f"UID:lesson-{lesson.id}@brainboost",
            f"DTSTAMP:{now_stamp}",
            f"DTSTART:{start}",
            f"DTEND:{end}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{description}",
            f"LOCATION:{location}",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )
    response = HttpResponse(ics_content, content_type="text/calendar")
    response["Content-Disposition"] = f'attachment; filename="termin-{lesson.id}.ics"'
    return response


@login_required
def lesson_edit(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    is_tutor = hasattr(request.user, "tutor_profile") and lesson.tutor == request.user.tutor_profile
    if not is_tutor:
        return redirect("lesson_list")
    next_url = request.GET.get("next") or request.POST.get("next") or reverse("lesson_list")
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = reverse("lesson_list")

    if request.method == "POST":
        was_cancelled_before = lesson.status == Lesson.Status.CANCELLED
        form = LessonForm(
            data=request.POST,
            instance=lesson,
            tutor_profile=request.user.tutor_profile if is_tutor else None,
            is_edit=True,
            allowed_students=None,
        )
        if form.is_valid():
            updated = form.save(commit=False)
            if updated.status == Lesson.Status.CANCELLED:
                if not was_cancelled_before or updated.cancelled_at is None:
                    cancelled_at = timezone.now()
                    updated.cancelled_at = cancelled_at
                    updated.cancellation_chargeable = _is_chargeable_cancellation(
                        updated, cancelled_at
                    )
            else:
                updated.cancelled_at = None
                updated.cancellation_chargeable = False
            _assign_location_and_distance(updated)
            updated.save()
            notify_lesson_changed(request, updated)
            return redirect(next_url)
    else:
        form = LessonForm(
            instance=lesson,
            tutor_profile=request.user.tutor_profile if is_tutor else None,
            is_edit=True,
            allowed_students=None,
        )

    return render(
        request,
        "lesson_form.html",
        {
            "form": form,
            "is_edit": True,
            "lesson": lesson,
            "cancel_url": next_url,
            "next_url": next_url,
        },
    )


@login_required
def lesson_delete(request, lesson_id):
    _ensure_profile_for_user(request.user)
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    is_tutor = hasattr(request.user, "tutor_profile") and lesson.tutor == request.user.tutor_profile
    if not is_tutor:
        return redirect("lesson_list")

    if request.method == "POST":
        next_url = request.POST.get("next") or reverse("lesson_list")
        if not url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            next_url = reverse("lesson_list")
        lesson.delete()
        return redirect(next_url)
    return redirect("lesson_edit", lesson_id=lesson_id)


@login_required
def material_upload(request, kind: str):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    if kind not in (LearningMaterial.Kind.TASK, LearningMaterial.Kind.SOLUTION):
        return redirect("dashboard")

    allowed_students = StudentProfile.objects.filter(
        assigned_tutors=request.user.tutor_profile
    ).distinct()
    heading = "Aufgabe hochladen" if kind == LearningMaterial.Kind.TASK else "Musterlösung hochladen"

    if request.method == "POST":
        form = LearningMaterialForm(
            data=request.POST,
            files=request.FILES,
            allowed_students=allowed_students,
            kind=kind,
            tutor_profile=request.user.tutor_profile,
        )
        if form.is_valid():
            material = form.save(commit=False)
            material.kind = kind
            material.uploaded_by = request.user.tutor_profile
            material.save()
            notify_material_uploaded(request, material)
            return redirect("dashboard")
    else:
        form = LearningMaterialForm(
            allowed_students=allowed_students,
            kind=kind,
            tutor_profile=request.user.tutor_profile,
        )

    return render(
        request,
        "material_upload.html",
        {"form": form, "heading": heading},
    )


@login_required
def tutor_solution_list(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    tutor_profile = request.user.tutor_profile
    assigned_students = _assigned_students_qs(tutor_profile)
    solutions = (
        LearningMaterial.objects.filter(kind=LearningMaterial.Kind.SOLUTION)
        .filter(Q(student__in=assigned_students) | Q(uploaded_by=tutor_profile))
        .select_related("student__user", "uploaded_by__user", "related_task")
        .distinct()
    )

    return render(
        request,
        "tutor_solution_list.html",
        {"solutions": solutions},
    )


@login_required
def tutor_template_list(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    is_admin_tutor = request.user.is_staff or request.user.is_superuser

    if request.method == "POST":
        if not is_admin_tutor:
            return redirect("tutor_template_list")
        form = TutorTemplateForm(request.POST, request.FILES)
        if form.is_valid():
            template = form.save(commit=False)
            template.uploaded_by = request.user.tutor_profile
            template.save()
            messages.success(request, "Vorlage wurde hochgeladen.")
            return redirect("tutor_template_list")
    else:
        form = TutorTemplateForm()

    templates = TutorTemplate.objects.select_related("uploaded_by__user")
    return render(
        request,
        "tutor_template_list.html",
        {
            "templates": templates,
            "form": form,
            "is_admin_tutor": is_admin_tutor,
        },
    )


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
                    try:
                        invoice_context = _build_invoice_pdf_context(
                            tutor_profile=tutor_profile,
                            student=student,
                            period_start=period_start,
                            lessons=lessons,
                            discount_type=generate_form.cleaned_data["discount_type"],
                            discount_value=generate_form.cleaned_data["discount_value"],
                        )
                        pdf_bytes = _generate_invoice_pdf(
                            request=request,
                            tutor_profile=tutor_profile,
                            student=student,
                            period_start=period_start,
                            lessons=lessons,
                            invoice_context=invoice_context,
                        )
                    except ValueError as exc:
                        generate_form.add_error("discount_value", str(exc))
                        pdf_bytes = None
                    except RuntimeError as exc:
                        generate_form.add_error(None, str(exc))
                        pdf_bytes = None
                    if pdf_bytes is None:
                        pass
                    else:
                        invoice = Invoice(
                            student=student,
                            uploaded_by=tutor_profile,
                            billing_year=period_start.year,
                            billing_month=period_start.month,
                            discount_type=invoice_context["discount_type"],
                            discount_value=invoice_context["discount_value"],
                            discount_amount=invoice_context["discount_amount"],
                            amount_total=invoice_context["total_amount"],
                        )
                        filename = _invoice_filename(invoice)
                        invoice.file.save(filename, ContentFile(pdf_bytes), save=False)
                        invoice.save()
                        if not tutor_profile.supervising_tutors.exists():
                            invoice.approved_by = tutor_profile
                            invoice.approved_at = timezone.now()
                            invoice.save(update_fields=["approved_by", "approved_at"])
                            messages.success(
                                request,
                                "Rechnung wurde generiert und direkt freigegeben. Versand an Eltern erst über den Eltern-Button.",
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
                    messages.success(request, "Rechnung wurde hochgeladen und direkt freigegeben. Versand an Eltern erst über den Eltern-Button.")
                else:
                    notify_invoice_pending_approval(request, invoice)
                    messages.success(request, "Rechnung wurde hochgeladen und wartet auf Freigabe.")
                return redirect("invoice_upload")
    else:
        form = InvoiceForm(allowed_students=allowed_students)
        generate_form = InvoiceGenerateForm(allowed_students=allowed_students)

    own_invoices = (
        Invoice.objects.filter(uploaded_by=tutor_profile)
        .select_related("student__user", "approved_by__user")
        .order_by("-uploaded_at")
    )
    subordinate_invoices = (
        Invoice.objects.filter(uploaded_by__in=subordinate_tutors)
        .select_related("student__user", "uploaded_by__user", "approved_by__user")
        .order_by("-uploaded_at")
    )
    for invoice in own_invoices:
        invoice.parent_notification_links = (
            _invoice_parent_notification_links(request, invoice) if invoice.is_approved else []
        )
    for invoice in subordinate_invoices:
        invoice.parent_notification_links = (
            _invoice_parent_notification_links(request, invoice) if invoice.is_approved else []
        )

    return render(
        request,
        "invoice_upload.html",
        {
            "form": form,
            "generate_form": generate_form,
            "heading": "Rechnungen",
            "tutor_invoices": own_invoices,
            "subordinate_invoices": subordinate_invoices,
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

    if not tutor_profile.assigned_tutors.filter(pk=invoice.uploaded_by_id).exists():
        messages.error(request, "Du darfst diese Rechnung nicht freigeben.")
        return redirect("invoice_upload")

    if invoice.is_approved:
        messages.info(request, "Diese Rechnung wurde bereits freigegeben.")
        return redirect("invoice_upload")

    invoice.approved_by = tutor_profile
    invoice.approved_at = timezone.now()
    invoice.save(update_fields=["approved_by", "approved_at"])
    messages.success(request, "Rechnung wurde freigegeben. Versand an Eltern erst über den Eltern-Button.")
    return redirect("invoice_upload")


@login_required
def invoice_notify_parent(request, invoice_id, parent_id):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    tutor_profile = request.user.tutor_profile
    invoice = get_object_or_404(
        Invoice.objects.select_related("student__user", "uploaded_by__user", "approved_by__user"),
        pk=invoice_id,
    )
    can_manage = (
        invoice.uploaded_by_id == tutor_profile.id
        or tutor_profile.assigned_tutors.filter(pk=invoice.uploaded_by_id).exists()
    )
    if not can_manage:
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
def invoice_delete(request, invoice_id):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    if request.method != "POST":
        return redirect("invoice_upload")

    tutor_profile = request.user.tutor_profile
    invoice = get_object_or_404(
        Invoice.objects.select_related("student__user"),
        pk=invoice_id,
        uploaded_by=tutor_profile,
    )
    invoice.file.delete(save=False)
    invoice.delete()
    messages.success(request, "Rechnung wurde gelöscht.")
    return redirect("invoice_upload")


@login_required
def invoice_select_payment(request, invoice_id, method):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.PARENT or not hasattr(
        request.user, "parent_profile"
    ):
        return redirect("invoice_list")
    if request.method != "POST":
        return redirect("invoice_list")

    if method not in {Invoice.PaymentMethod.CASH, Invoice.PaymentMethod.BANK_TRANSFER}:
        messages.error(request, "Diese Zahlungsart ist nicht verfügbar.")
        return redirect("invoice_list")

    parent_profile = request.user.parent_profile
    invoice = get_object_or_404(
        Invoice.objects.select_related("student__user", "uploaded_by__user"),
        pk=invoice_id,
        student__parents=parent_profile,
        approved_at__isnull=False,
    )
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
    if request.user.role != CustomUser.Roles.PARENT or not hasattr(
        request.user, "parent_profile"
    ):
        return redirect("invoice_list")
    if request.method != "POST":
        return redirect("invoice_list")

    parent_profile = request.user.parent_profile
    invoice = get_object_or_404(
        Invoice.objects.select_related("student__user", "uploaded_by__user"),
        pk=invoice_id,
        student__parents=parent_profile,
        approved_at__isnull=False,
    )
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
            "parent_id": str(parent_profile.id),
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
        or invoice.payment_requested_by_id != parent_profile.id
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
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
    try:
        if webhook_secret:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        else:
            event = stripe.Event.construct_from(json.loads(payload.decode("utf-8")), stripe.api_key)
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
                if invoice.payment_requested_by:
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
    can_manage = (
        invoice.uploaded_by_id == tutor_profile.id
        or tutor_profile.assigned_tutors.filter(pk=invoice.uploaded_by_id).exists()
    )
    if not can_manage:
        messages.error(request, "Du darfst diesen Zahlungseingang nicht bestätigen.")
        return redirect("invoice_upload")
    if not invoice.can_confirm_receipt:
        messages.info(request, "Für diese Rechnung gibt es aktuell nichts zu bestätigen.")
        return redirect("invoice_upload")

    invoice.payment_status = Invoice.PaymentStatus.PAID
    invoice.paid_at = timezone.now()
    invoice.save(update_fields=["payment_status", "paid_at"])

    if invoice.payment_requested_by:
        notify_invoice_payment_confirmed(request, invoice, invoice.payment_requested_by)

    messages.success(
        request,
        f"Zahlung per {invoice.get_payment_method_display()} wurde als eingegangen bestätigt.",
    )
    return redirect("invoice_upload")


@login_required
def progress_create(request, lesson_id=None):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(
        request.user, "tutor_profile"
    ):
        return redirect("progress")

    tutor_profile = request.user.tutor_profile
    initial = {}
    if lesson_id:
        lesson = get_object_or_404(
            tutor_profile.lessons.select_related("student__user"), pk=lesson_id
        )
        initial["lesson"] = lesson
    else:
        lesson = None

    if request.method == "POST":
        form = ProgressEntryForm(
            data=request.POST, tutor_profile=tutor_profile, initial=initial
        )
        if form.is_valid():
            progress = form.save()
            return redirect(
                "progress_student", student_id=form.cleaned_data["lesson"].student.id
            )
    else:
        form = ProgressEntryForm(
            tutor_profile=tutor_profile,
            initial=initial,
        )

    return render(
        request,
        "progress_form.html",
        {
            "form": form,
            "lesson": lesson,
            "is_edit": False,
            "cancel_url": reverse("progress"),
            "rating_fields": [form[name] for name in ("rating", "rating_fach_2", "rating_fach_3") if name in form.fields],
        },
    )


@login_required
def progress_edit(request, entry_id):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(
        request.user, "tutor_profile"
    ):
        return redirect("progress")

    tutor_profile = request.user.tutor_profile
    entry = get_object_or_404(
        ProgressEntry.objects.select_related("lesson__student__user", "lesson__tutor"),
        pk=entry_id,
        lesson__tutor=tutor_profile,
    )
    next_url = request.GET.get("next") or request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = reverse("progress_student", args=[entry.lesson.student_id])

    if request.method == "POST":
        form = ProgressEntryForm(
            data=request.POST,
            instance=entry,
            tutor_profile=tutor_profile,
        )
        if form.is_valid():
            form.save()
            return redirect(next_url)
    else:
        form = ProgressEntryForm(instance=entry, tutor_profile=tutor_profile)

    return render(
        request,
        "progress_form.html",
        {
            "form": form,
            "lesson": entry.lesson,
            "is_edit": True,
            "cancel_url": next_url,
            "next_url": next_url,
            "rating_fields": [form[name] for name in ("rating", "rating_fach_2", "rating_fach_3") if name in form.fields],
        },
    )


@login_required
def progress_delete(request, entry_id):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(
        request.user, "tutor_profile"
    ):
        return redirect("progress")

    if request.method != "POST":
        return redirect("progress")

    tutor_profile = request.user.tutor_profile
    entry = get_object_or_404(
        ProgressEntry.objects.select_related("lesson__student__user", "lesson__tutor"),
        pk=entry_id,
        lesson__tutor=tutor_profile,
    )
    next_url = request.POST.get("next") or reverse(
        "progress_student", args=[entry.lesson.student_id]
    )
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = reverse("progress_student", args=[entry.lesson.student_id])

    entry.delete()
    messages.success(request, "Lernfortschrittseintrag wurde gelöscht.")
    return redirect(next_url)


@login_required
def progress_view(request, student_id=None):
    _ensure_profile_for_user(request.user)
    entries = ProgressEntry.objects.none()
    viewed_student = None
    student_list = StudentProfile.objects.none()
    period = request.GET.get("period", "").strip()
    selected_student = request.GET.get("student", "").strip()
    weekday = request.GET.get("weekday", "").strip()
    duration = request.GET.get("duration", "").strip()
    ort = request.GET.get("ort", "").strip()

    if request.user.role == CustomUser.Roles.STUDENT and hasattr(
        request.user, "student_profile"
    ):
        viewed_student = request.user.student_profile
        entries = ProgressEntry.objects.filter(
            lesson__student=request.user.student_profile
        )
    elif request.user.role == CustomUser.Roles.PARENT and hasattr(
        request.user, "parent_profile"
    ):
        students = request.user.parent_profile.students.all()
        student_list = students
        if student_id:
            viewed_student = get_object_or_404(students, pk=student_id)
            entries = ProgressEntry.objects.filter(lesson__student=viewed_student)
        else:
            entries = ProgressEntry.objects.filter(lesson__student__in=students)
    elif request.user.role == CustomUser.Roles.TUTOR and hasattr(
        request.user, "tutor_profile"
    ):
        student_list = _assigned_students_qs(request.user.tutor_profile)
        if student_id is None:
            entries = ProgressEntry.objects.filter(
                lesson__tutor=request.user.tutor_profile
            )
        else:
            viewed_student = get_object_or_404(student_list, pk=student_id)
            entries = ProgressEntry.objects.filter(
                lesson__student=viewed_student,
                lesson__tutor=request.user.tutor_profile,
            )

        if period:
            try:
                period_year, period_month = period.split("-", 1)
            except ValueError:
                period_year, period_month = "", ""
            if period_year and period_month:
                entries = entries.filter(
                    lesson__date__year=period_year,
                    lesson__date__month=period_month,
                )
        if selected_student and student_id is None:
            entries = entries.filter(lesson__student_id=selected_student)
        if weekday:
            entries = entries.filter(lesson__date__week_day=weekday)
        if duration:
            entries = entries.filter(lesson__duration_minutes=duration)
        if ort:
            entries = entries.filter(lesson__ort=ort)

    entries = entries.order_by("-lesson__date", "-lesson__time", "-created_at")

    show_progress_chart = request.user.role in {
        CustomUser.Roles.STUDENT,
        CustomUser.Roles.PARENT,
        CustomUser.Roles.TUTOR,
    }
    tutor_chart_requires_student_selection = (
        request.user.role == CustomUser.Roles.TUTOR
        and viewed_student is None
        and not selected_student
    )
    chart_entries = (
        ProgressEntry.objects.none() if tutor_chart_requires_student_selection else entries
    )
    progress_chart_data = (
        _build_progress_chart_data(
            chart_entries,
            include_student_name=(
                request.user.role == CustomUser.Roles.PARENT and viewed_student is None
            ),
        )
        if show_progress_chart
        else {"labels": [], "date_keys": [], "detail_labels": [], "datasets": []}
    )

    period_options = [
        {
            "value": lesson_date.strftime("%Y-%m"),
            "label": date_format(lesson_date, "F Y"),
        }
        for lesson_date in sorted(
            Lesson.objects.filter(id__in=entries.values_list("lesson_id", flat=True))
            .dates("date", "month"),
            reverse=True,
        )
    ]
    duration_options = [
        str(value)
        for value in sorted(
            {
                value
                for value in entries.values_list("lesson__duration_minutes", flat=True).distinct()
                if value is not None
            }
        )
    ]
    ort_values = set(entries.values_list("lesson__ort", flat=True).distinct())
    ort_options = [choice for choice in Lesson.Ort.choices if choice[0] in ort_values]
    weekday_options = [
        ("2", "Montag"),
        ("3", "Dienstag"),
        ("4", "Mittwoch"),
        ("5", "Donnerstag"),
        ("6", "Freitag"),
        ("7", "Samstag"),
        ("1", "Sonntag"),
    ]

    return render(
        request,
        "progress.html",
        {
            "entries": entries,
            "viewed_student": viewed_student,
            "student_id": student_id,
            "student_list": student_list,
            "period_options": period_options,
            "selected_period": period,
            "selected_student": selected_student,
            "selected_weekday": weekday,
            "selected_duration": duration,
            "selected_ort": ort,
            "duration_options": duration_options,
            "ort_options": ort_options,
            "weekday_options": weekday_options,
            "show_progress_chart": show_progress_chart,
            "tutor_chart_requires_student_selection": tutor_chart_requires_student_selection,
            "progress_chart_data": progress_chart_data,
        },
    )


@login_required
def invoice_list(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.PARENT or not hasattr(request.user, "parent_profile"):
        return redirect("dashboard")
    payment_status = request.GET.get("payment")
    if payment_status == "success":
        messages.success(request, "Die Online-Zahlung wurde erfolgreich abgeschlossen. Die Rechnung wurde aktualisiert.")
    elif payment_status == "cancelled":
        messages.info(request, "Die Online-Zahlung wurde abgebrochen.")
    students = request.user.parent_profile.students.all()
    invoices = Invoice.objects.filter(
        student__in=students,
        approved_at__isnull=False,
    ).select_related("student__user", "uploaded_by__user", "approved_by__user")
    return render(
        request,
        "invoice_list.html",
        {
            "invoices": invoices,
            "payment_status_paid": Invoice.PaymentStatus.PAID,
        },
    )
