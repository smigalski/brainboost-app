import base64
import csv
import io
import json
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
from pathlib import Path

import logging
import re
from smtplib import SMTPAuthenticationError
import unicodedata
from typing import Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.core.cache import cache
from django.contrib.staticfiles import finders
from django.core.files.base import ContentFile
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, render, redirect
from django.db.models import Case, CharField, Count, F, Q, Value, When
from django.db.models.functions import TruncDate
from django.template.loader import render_to_string
from django.utils.formats import date_format
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import get_language
from django.http import JsonResponse, HttpResponse, FileResponse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode, url_has_allowed_host_and_scheme
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from ..access import (
    assigned_students_qs as _assigned_students_qs,
    assigned_tutors_qs as _assigned_tutors_qs,
    can_approve_invoice,
    can_access_material as _can_access_material,
    can_cancel_lesson,
    can_delete_invoice,
    can_delete_material,
    can_manage_invoice,
    can_manage_lesson,
    can_request_lesson_reschedule,
    can_view_lesson,
    has_admin_access as _has_admin_access,
    payer_invoice_qs,
)
from ..forms import (
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
    IndependentStudentCreateForm,
    TutorTemplateForm,
    TutorCreateForm,
    ParentProfileForm,
    StudentProfileForm,
    TutorProfileForm,
    BrainBoostFeedbackForm,
    EmailOrUsernameAuthenticationForm,
    BroadcastEmailForm,
    TutorStudentAssignmentForm,
    CampaignLinkBuilderForm,
    LeadForm,
)
from ..middleware import UTM_KEYS, UTM_SESSION_KEY
from ..notifications import (
    notify_holiday_survey_created,
    notify_invoice_parent,
    notify_invoice_payment_confirmed,
    notify_invoice_payment_received_tutor,
    notify_invoice_payment_selected,
    notify_invoice_pending_approval,
    notify_invoice_student,
    notify_invoice_uploaded,
    notify_lesson_cancelled,
    notify_lesson_changed,
    notify_lesson_created,
    notify_lesson_series_created,
    notify_lesson_reschedule_requested,
    notify_material_uploaded,
    notify_lead_created,
)
from ..models import (
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
    Lead,
)

logger = logging.getLogger(__name__)
GOOGLE_REVIEWS_CACHE_KEY = "landing_google_reviews_v1"
GOOGLE_REVIEWS_CACHE_SECONDS = 60 * 60 * 12
GOOGLE_REVIEWS_SUPPORTED_LANGUAGES = {"de", "en", "es", "pl", "tr", "ru", "ar"}
GOOGLE_ROUTES_ENDPOINT = "https://routes.googleapis.com/directions/v2:computeRoutes"
EMAIL_CHANGE_TOKEN_MAX_AGE = 60 * 60 * 24 * 7
EMAIL_CHANGE_TOKEN_SALT = "brainboost.profile-email-change"


def _ensure_profile_for_user(user: CustomUser):
    """Create missing profile objects on-the-fly to keep views simple."""
    if user.role in {
        CustomUser.Roles.STUDENT,
        CustomUser.Roles.INDEPENDENT_STUDENT,
    } and not hasattr(user, "student_profile"):
        StudentProfile.objects.create(user=user)
    elif user.role == CustomUser.Roles.PARENT and not hasattr(user, "parent_profile"):
        ParentProfile.objects.create(user=user)
    elif user.role == CustomUser.Roles.TUTOR and not hasattr(user, "tutor_profile"):
        TutorProfile.objects.create(user=user)


def _rating_stars(rating) -> list[str]:
    try:
        rounded = int(round(float(rating)))
    except (TypeError, ValueError):
        rounded = 0
    rounded = max(0, min(5, rounded))
    return ["filled"] * rounded + ["empty"] * (5 - rounded)


def _google_review_text(review: dict) -> str:
    text = review.get("text") or review.get("originalText") or {}
    if isinstance(text, dict):
        return (text.get("text") or "").strip()
    return str(text).strip()


def _normalize_google_reviews_payload(payload: dict) -> dict:
    rating = payload.get("rating")
    review_count = payload.get("userRatingCount")
    maps_url = payload.get("googleMapsUri") or settings.GOOGLE_REVIEW_URL
    reviews = []
    for review in payload.get("reviews", [])[:5]:
        author = review.get("authorAttribution") or {}
        review_rating = review.get("rating")
        reviews.append(
            {
                "author": author.get("displayName") or "Google-NutzerIn",
                "author_url": author.get("uri") or "",
                "rating": review_rating,
                "stars": _rating_stars(review_rating),
                "text": _google_review_text(review),
                "relative_time": review.get("relativePublishTimeDescription") or "",
                "review_url": review.get("googleMapsUri") or maps_url,
            }
        )
    return {
        "rating": rating,
        "rating_display": str(rating).replace(".", ",") if rating is not None else "",
        "review_count": review_count,
        "maps_url": maps_url,
        "review_url": settings.GOOGLE_REVIEW_URL,
        "reviews": [review for review in reviews if review["text"]],
    }


def _google_reviews_fallback() -> dict:
    return {
        "rating": None,
        "rating_display": "",
        "review_count": None,
        "maps_url": settings.GOOGLE_REVIEW_URL,
        "review_url": settings.GOOGLE_REVIEW_URL,
        "reviews": [],
    }


def _google_reviews_language_code() -> str:
    language_code = (get_language() or settings.LANGUAGE_CODE or "de").split("-")[0].lower()
    return language_code if language_code in GOOGLE_REVIEWS_SUPPORTED_LANGUAGES else "de"


def _get_google_reviews_summary() -> dict:
    api_key = settings.GOOGLE_PLACES_API_KEY
    place_id = settings.GOOGLE_PLACE_ID
    if not api_key or not place_id:
        return _google_reviews_fallback()

    language_code = _google_reviews_language_code()
    cache_key = f"{GOOGLE_REVIEWS_CACHE_KEY}:{language_code}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    url = f"https://places.googleapis.com/v1/places/{place_id}?languageCode={language_code}"
    fields = "displayName,rating,userRatingCount,googleMapsUri,reviews"
    request = Request(
        url,
        headers={
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": fields,
        },
    )
    try:
        with urlopen(request, timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        logger.exception("Google Places Bewertungen konnten nicht geladen werden.")
        return _google_reviews_fallback()

    summary = _normalize_google_reviews_payload(payload)
    cache.set(cache_key, summary, GOOGLE_REVIEWS_CACHE_SECONDS)
    return summary


def _google_driving_distance_km(origin_address: str, destination_address: str) -> Optional[Decimal]:
    api_key = settings.GOOGLE_ROUTES_API_KEY
    origin_address = (origin_address or "").strip()
    destination_address = (destination_address or "").strip()
    if not api_key or not origin_address or not destination_address:
        return None

    payload = {
        "origin": {"address": origin_address},
        "destination": {"address": destination_address},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_UNAWARE",
        "computeAlternativeRoutes": False,
        "regionCode": "de",
        "units": "METRIC",
    }
    request = Request(
        GOOGLE_ROUTES_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "routes.distanceMeters",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
        logger.exception("Google Routes Fahrstrecke konnte nicht berechnet werden.")
        return None

    routes = data.get("routes") or []
    if not routes:
        logger.warning(
            "Google Routes lieferte keine Route fuer origin=%r destination=%r.",
            origin_address,
            destination_address,
        )
        return None
    try:
        distance_meters = Decimal(str(routes[0]["distanceMeters"]))
    except (KeyError, TypeError, ValueError):
        logger.warning("Google Routes Antwort enthaelt keine gueltige distanceMeters-Angabe.")
        return None
    return (distance_meters / Decimal("1000")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _assign_location_and_distance(lesson: Lesson):
    """Set address and distance on lesson based on location choice."""
    address = ""
    distance = None
    if lesson.ort == Lesson.Ort.ZUHAUSE_STUDENT:
        student = lesson.student
        address = student.address
        one_way_distance = _google_driving_distance_km(lesson.tutor.address, student.address)
        if one_way_distance is not None:
            distance = (one_way_distance * Decimal("2")).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
    elif lesson.ort == Lesson.Ort.BIB:
        address = "Bibliothek Braunschweig"
    elif lesson.ort == Lesson.Ort.BIB_WOB:
        address = "Bibliothek Wolfsburg"
        distance = 70.0

    lesson.location_address = address or ""
    lesson.distance_km = distance


def _actor_label(user: CustomUser) -> str:
    if user.role == CustomUser.Roles.TUTOR:
        return f"TutorIn {user.display_name}"
    if user.role == CustomUser.Roles.PARENT:
        return f"Elternteil {user.display_name}"
    if user.role == CustomUser.Roles.INDEPENDENT_STUDENT:
        return f"StudentIn {user.display_name}"
    if user.role == CustomUser.Roles.STUDENT:
        return f"SchülerIn {user.display_name}"
    return user.display_name


def _is_learning_profile_user(user: CustomUser) -> bool:
    return user.role in {
        CustomUser.Roles.STUDENT,
        CustomUser.Roles.INDEPENDENT_STUDENT,
    }


def _is_independent_student_user(user: CustomUser) -> bool:
    return user.role == CustomUser.Roles.INDEPENDENT_STUDENT


def _display_name(user: CustomUser) -> str:
    full_name = user.get_full_name().strip()
    return full_name or user.email or user.get_role_display()


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
    if not (tutor_profile.tax_number or "").strip() and not tutor_profile.tax_number_pending:
        missing_fields.append("Steuernummer")
    return missing_fields


def _tutor_self_employment_complete(tutor_profile: TutorProfile) -> bool:
    return tutor_profile.self_employment_fields_complete


def _tutor_tax_number_reminder_due(tutor_profile: TutorProfile) -> bool:
    if (tutor_profile.tax_number or "").strip():
        return False
    if tutor_profile.status not in {
        TutorProfile.Status.ACCEPTED,
        TutorProfile.Status.ONBOARDING,
        TutorProfile.Status.ACTIVE,
    }:
        return False
    return timezone.now() - tutor_profile.user.date_joined >= timedelta(days=28)


def _sync_tutor_tax_number_status(tutor_profile: TutorProfile) -> None:
    has_tax_number = bool((tutor_profile.tax_number or "").strip())
    if tutor_profile.status == TutorProfile.Status.PAUSED and has_tax_number:
        tutor_profile.status = TutorProfile.Status.ACTIVE
        tutor_profile.save(update_fields=["status"])
        return
    if (
        tutor_profile.status == TutorProfile.Status.ACTIVE
        and not has_tax_number
        and timezone.now() - tutor_profile.user.date_joined >= timedelta(days=60)
    ):
        tutor_profile.status = TutorProfile.Status.PAUSED
        tutor_profile.save(update_fields=["status"])


def _tutor_has_completed_lesson(tutor_profile: TutorProfile):
    return Lesson.objects.filter(
        tutor=tutor_profile,
        status=Lesson.Status.COMPLETED,
    ).exists()


def _tutor_has_completed_self_created_student_lesson(tutor_profile: TutorProfile):
    return Lesson.objects.filter(
        tutor=tutor_profile,
        student__created_by_tutor=tutor_profile,
        status=Lesson.Status.COMPLETED,
    ).exists()


def _sync_tutor_onboarding_status(tutor_profile: TutorProfile) -> None:
    original_status = tutor_profile.status
    if tutor_profile.status == TutorProfile.Status.ACCEPTED and _tutor_self_employment_complete(
        tutor_profile
    ):
        tutor_profile.status = TutorProfile.Status.ONBOARDING
    if tutor_profile.status == TutorProfile.Status.ONBOARDING and _tutor_has_completed_lesson(
        tutor_profile
    ):
        tutor_profile.status = TutorProfile.Status.ACTIVE
    if tutor_profile.status != original_status:
        tutor_profile.save(update_fields=["status"])


def _tutor_can_create_accounts(tutor_profile: TutorProfile) -> bool:
    return tutor_profile.status == TutorProfile.Status.ACTIVE


def _tutor_onboarding_steps(tutor_profile: TutorProfile) -> list[dict]:
    status = tutor_profile.status
    meeting_status = status if status in {
        TutorProfile.Status.INVITED,
        TutorProfile.Status.MET,
        TutorProfile.Status.ACCEPTED,
        TutorProfile.Status.REJECTED,
    } else TutorProfile.Status.ACCEPTED
    successful_meeting_done = status in {
        TutorProfile.Status.ACCEPTED,
        TutorProfile.Status.ONBOARDING,
        TutorProfile.Status.ACTIVE,
    }
    rejected = status == TutorProfile.Status.REJECTED
    self_employment_done = _tutor_self_employment_complete(tutor_profile)
    first_assigned_lesson_done = _tutor_has_completed_lesson(tutor_profile)
    self_created_lesson_done = _tutor_has_completed_self_created_student_lesson(tutor_profile)

    return [
        {
            "title": "Bewerbungsprofil ausgefüllt",
            "done": True,
            "status_label": "beworben",
            "description": "",
        },
        {
            "title": "Kennenlerngespräch (30min)",
            "done": successful_meeting_done,
            "status_label": meeting_status,
            "description": (
                "Dein Profil wird innerhalb von 7 Tagen gelöscht."
                if rejected
                else ""
            ),
            "show_bbb_button": bool(tutor_profile.bbb_link) and not rejected,
            "bbb_link": tutor_profile.bbb_link,
        },
        {
            "title": "Selbständigkeit",
            "done": self_employment_done,
            "status_label": TutorProfile.Status.ONBOARDING,
            "description": (
                "IBAN, BIC, Steuernummer und Verifikation sind vollständig."
                if self_employment_done
                else "Nach Annahme werden IBAN, BIC und Steuernummer im Profil freigeschaltet."
            ),
        },
        {
            "title": "SchülerInnen und StudentInnen erhalten",
            "done": first_assigned_lesson_done,
            "status_label": TutorProfile.Status.ACTIVE,
            "description": (
                "Ab der ersten erfolgreich absolvierten Einheit werden die Konto-Anlage-Buttons freigeschaltet."
            ),
        },
        {
            "title": "Eigene SchülerInnen/StudentInnen anlegen",
            "done": self_created_lesson_done,
            "status_label": "",
            "description": (
                "Erledigt, sobald du selbst ein neues Lernprofil angelegt und eine Nachhilfeeinheit damit absolviert hast."
            ),
        },
    ]


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


def _invoice_student_whatsapp_message(request, invoice: Invoice) -> str:
    invoice_url = request.build_absolute_uri(invoice.file.url)
    portal_url = request.build_absolute_uri(reverse("invoice_list"))
    student_name = _display_name(invoice.student.user)
    tutor_name = _display_name(invoice.uploaded_by.user)
    return (
        f"Hallo {student_name},\n"
        "eine neue Rechnung ist verfuegbar.\n"
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


def _invoice_student_pays_directly(invoice: Invoice) -> bool:
    return (
        invoice.student.user.role == CustomUser.Roles.INDEPENDENT_STUDENT
        or not invoice.student.parents.exists()
    )


def _invoice_student_notification_links(invoice: Invoice) -> list[dict]:
    if not _invoice_student_pays_directly(invoice):
        return []
    student = invoice.student
    if not student.user.email and not _normalize_whatsapp_number(student.phone_number):
        return []
    return [
        {
            "name": _display_name(student.user),
            "url": reverse("invoice_notify_student", args=[invoice.id]),
        }
    ]


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
INVOICE_NUMBER_PATTERN = re.compile(r"RE-A(\d{2})-(\d{4})", re.IGNORECASE)
LEGACY_INVOICE_NUMBER_PATTERN = re.compile(r"WRE-(\d{5,})_", re.IGNORECASE)


def _sanitize_invoice_name_part(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", ascii_text).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned or "Unbekannt"


def _format_invoice_number(sequence: int) -> str:
    if sequence < 1:
        raise ValueError("Rechnungsnummern müssen positiv sein.")
    group = ((sequence - 1) // 9999) + 1
    number = ((sequence - 1) % 9999) + 1
    if group > 99:
        raise ValueError("Der Rechnungsnummernkreis RE-A99-9999 ist ausgeschöpft.")
    return f"RE-A{group:02d}-{number:04d}"


def _parse_invoice_number(value: str) -> Optional[int]:
    match = INVOICE_NUMBER_PATTERN.search(value or "")
    if not match:
        legacy_match = LEGACY_INVOICE_NUMBER_PATTERN.search(value or "")
        if legacy_match:
            return int(legacy_match.group(1))
        return None
    group = int(match.group(1))
    number = int(match.group(2))
    return ((group - 1) * 9999) + number


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


INVOICE_MONTH_NAMES = {
    1: "Januar",
    2: "Februar",
    3: "März",
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


def _selected_positive_int(value: str) -> Optional[int]:
    if not value:
        return None
    try:
        selected = int(value)
    except (TypeError, ValueError):
        return None
    return selected if selected > 0 else None


def _invoice_filter_context(
    invoices: list[Invoice],
    request,
    *,
    person_field: str = "student",
    query_prefix: str = "",
):
    person_param = f"{query_prefix}{person_field}"
    month_param = f"{query_prefix}month"
    year_param = f"{query_prefix}year"
    selected_person = _selected_positive_int(request.GET.get(person_param))
    selected_month = _selected_positive_int(request.GET.get(month_param))
    selected_year = _selected_positive_int(request.GET.get(year_param))

    person_options = {}
    month_numbers = set()
    years = set()
    for invoice in invoices:
        if person_field == "tutor":
            person_options[invoice.uploaded_by_id] = _display_name(invoice.uploaded_by.user)
        else:
            person_options[invoice.student_id] = _display_name(invoice.student.user)
        year, month = _invoice_period_parts(invoice)
        years.add(year)
        month_numbers.add(month)

    filtered = []
    for invoice in invoices:
        year, month = _invoice_period_parts(invoice)
        person_id = invoice.uploaded_by_id if person_field == "tutor" else invoice.student_id
        if selected_person and person_id != selected_person:
            continue
        if selected_month and month != selected_month:
            continue
        if selected_year and year != selected_year:
            continue
        filtered.append(invoice)

    return {
        "invoices": filtered,
        "filters": {
            person_field: selected_person or "",
            "month": selected_month or "",
            "year": selected_year or "",
        },
        "person_options": [
            {"id": person_id, "name": name}
            for person_id, name in sorted(person_options.items(), key=lambda item: item[1].lower())
        ],
        "month_options": [
            {"value": month, "label": INVOICE_MONTH_NAMES[month]}
            for month in sorted(month_numbers)
        ],
        "year_options": sorted(years, reverse=True),
    }


def _next_invoice_number() -> int:
    max_number = 0
    latest = Invoice.objects.filter(invoice_number__isnull=False).order_by("-invoice_number").first()
    if latest and latest.invoice_number:
        max_number = latest.invoice_number
    for file_name in Invoice.objects.exclude(file="").values_list("file", flat=True):
        parsed_number = _parse_invoice_number(file_name or "")
        if parsed_number:
            max_number = max(max_number, parsed_number)
    return max_number + 1


def _invoice_filename(invoice: Invoice, invoice_number: Optional[int] = None) -> str:
    year, month = _invoice_period_parts(invoice)
    student_name = _sanitize_invoice_name_part(
        _display_name(invoice.student.user)
    )
    sequence = invoice_number or invoice.invoice_number or _next_invoice_number()
    return f"{_format_invoice_number(sequence)}_{GERMAN_MONTH_NAMES[month]}{str(year)[-2:]}_{student_name}.pdf"


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
    parent_profile: Optional[ParentProfile],
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


def _notify_independent_student_invoice_uploaded(request, invoice: Invoice) -> None:
    if invoice.student.user.role == CustomUser.Roles.INDEPENDENT_STUDENT:
        notify_invoice_uploaded(request, invoice)


def _invoice_release_message(invoice: Invoice, verb: str) -> str:
    if invoice.student.user.role == CustomUser.Roles.INDEPENDENT_STUDENT:
        return f"Rechnung wurde {verb} und die StudentIn wurde per Mail benachrichtigt, falls eine E-Mail hinterlegt ist."
    return f"Rechnung wurde {verb}. Versand an Eltern erst über den Eltern-Button."


def _invoice_recipient_address(student: StudentProfile) -> dict[str, str]:
    parent = student.parents.select_related("user").order_by("user__last_name", "user__first_name", "user__username").first()
    recipient_user = parent.user if parent else student.user
    customer_number = parent.customer_number if parent else student.profile_number
    address = (student.address or "").strip()
    normalized_address = "\n".join(
        part.strip()
        for part in re.split(r"[\n,]+", address)
        if part.strip()
    )
    return {
        "name": _display_name(recipient_user),
        "address": normalized_address or "Keine Anschrift hinterlegt",
        "customer_number": customer_number or "-",
    }


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
    invoice_number: str = "",
) -> dict:
    tutor_name = _display_name(tutor_profile.user)
    student_name = _display_name(student.user)
    recipient_address = _invoice_recipient_address(student)
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
        "recipient_name": recipient_address["name"],
        "student_address": recipient_address["address"],
        "customer_number": recipient_address["customer_number"],
        "tutor_tax_number": tutor_profile.tax_number or "-",
        "brainboost_tax_number": getattr(settings, "BRAINBOOST_TAX_NUMBER", "") or "_________________",
        "period_label": period_label,
        "invoice_number": invoice_number,
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
                "title": "Neue Ergebnisse",
                "text": f"Neue Ergebnisse von {_display_name(material.uploaded_by.user)} wurden hochgeladen.",
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
                "title": f"Neue Ergebnisse: {_display_name(material.student.user)}",
                "text": "Es wurden neue Ergebnisse hochgeladen.",
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


def _lead_initial_from_query(request) -> dict:
    valid_roles = {choice[0] for choice in Lead.Role.choices}
    attribution = request.session.get(UTM_SESSION_KEY, {}) if hasattr(request, "session") else {}
    initial = {
        "source": request.GET.get("source", "").strip()
        or attribution.get("source", "")
        or "website"
    }
    role = request.GET.get("role", "").strip()
    if role in valid_roles:
        initial["role"] = role
    for key in ["campaign", *UTM_KEYS]:
        value = request.GET.get(key, "").strip() or attribution.get(key, "")
        if value:
            initial[key] = value
    if not initial.get("campaign") and initial.get("utm_campaign"):
        initial["campaign"] = initial["utm_campaign"]
    return initial


def _lead_attribution_from_request(request) -> dict:
    attribution = request.session.get(UTM_SESSION_KEY, {}) if hasattr(request, "session") else {}
    values = {}
    for key in UTM_KEYS:
        values[key] = (request.GET.get(key, "").strip() or attribution.get(key, ""))[:120]

    campaign = (
        request.GET.get("campaign", "").strip()
        or request.GET.get("utm_campaign", "").strip()
        or attribution.get("campaign", "")
    )
    if not campaign:
        campaign = values.get("utm_campaign", "")
    values["campaign"] = campaign[:120]
    values["referrer"] = attribution.get("referrer", "")[:500]
    values["landing_page_path"] = attribution.get("landing_page_path", "")[:500]
    values["initial_querystring"] = attribution.get("initial_querystring", "")
    return values


def _default_mail_reply_to() -> list[str]:
    configured = getattr(settings, "DEFAULT_REPLY_TO_EMAIL", "")
    return [configured] if configured else []


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
    reply_to = _default_mail_reply_to()
    message = EmailMultiAlternatives(
        subject,
        text_body,
        from_email,
        [user.email],
        reply_to=reply_to,
    )
    message.attach_alternative(html_body, "text/html")
    sent = message.send()
    if sent == 0:
        raise RuntimeError("email_send_failed")


def _email_change_token(user: CustomUser, email: str) -> str:
    return signing.dumps(
        {"user_id": user.pk, "email": email},
        salt=EMAIL_CHANGE_TOKEN_SALT,
    )


def _send_email_change_confirmation(request, user: CustomUser) -> None:
    if not user.pending_email:
        raise ValueError("missing_pending_email")
    token = _email_change_token(user, user.pending_email)
    confirm_url = request.build_absolute_uri(
        reverse("profile_email_confirm", kwargs={"token": token})
    )
    context = {
        "heading": "BrainBoost: Neue E-Mail bestätigen",
        "user": user,
        "new_email": user.pending_email,
        "confirm_url": confirm_url,
    }
    subject = "BrainBoost: Neue E-Mail bestätigen"
    text_body = render_to_string("emails/profile_email_change.txt", context)
    html_body = render_to_string("emails/profile_email_change.html", context)
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@brainboost.local")
    message = EmailMultiAlternatives(
        subject,
        text_body,
        from_email,
        [user.pending_email],
        reply_to=_default_mail_reply_to(),
    )
    message.attach_alternative(html_body, "text/html")
    sent = message.send()
    if sent == 0:
        raise RuntimeError("email_send_failed")


def _username_base_from_lead(lead: Lead) -> str:
    email_local = (lead.email or "").split("@", 1)[0]
    source = email_local or lead.name or f"tutor-{lead.pk}"
    return slugify(source).replace("-", "_")[:120] or f"tutor_{lead.pk}"


def _unique_username_from_lead(lead: Lead) -> str:
    base = _username_base_from_lead(lead)
    candidate = base
    suffix = 1
    while CustomUser.objects.filter(username__iexact=candidate).exists():
        suffix += 1
        candidate = f"{base[:140]}_{suffix}"
    return candidate


def _split_lead_name(lead: Lead) -> tuple[str, str]:
    parts = (lead.name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0][:150], ""
    return parts[0][:150], " ".join(parts[1:])[:150]


def _create_tutor_from_lead(lead: Lead, *, mark_lead_won: bool = True) -> CustomUser:
    if lead.converted_tutor_id:
        if mark_lead_won and (
            lead.status != Lead.Status.WON or not lead.follow_up_done
        ):
            lead.status = Lead.Status.WON
            lead.follow_up_done = True
            lead.save(update_fields=["status", "follow_up_done", "updated_at"])
        return lead.converted_tutor.user
    if not lead.email:
        raise ValueError("missing_email")

    first_name, last_name = _split_lead_name(lead)
    with transaction.atomic():
        lead = Lead.objects.select_for_update().get(pk=lead.pk)
        if lead.converted_tutor_id:
            return TutorProfile.objects.select_related("user").get(pk=lead.converted_tutor_id).user
        user = CustomUser(
            username=_unique_username_from_lead(lead),
            first_name=first_name,
            last_name=last_name,
            email=lead.email,
            role=CustomUser.Roles.TUTOR,
            is_active=True,
            is_staff=False,
            is_superuser=False,
        )
        user.set_unusable_password()
        user.save()
        tutor_profile = TutorProfile.objects.create(
            user=user,
            phone_number=lead.phone,
            status=TutorProfile.Status.APPLIED,
        )
        lead.converted_tutor = tutor_profile
        update_fields = ["converted_tutor", "updated_at"]
        if mark_lead_won:
            lead.status = Lead.Status.WON
            lead.follow_up_done = True
            update_fields.extend(["status", "follow_up_done"])
        lead.save(update_fields=update_fields)
    return user


LEAD_EXPORT_FIELDS = [
    "created_at",
    "role",
    "name",
    "email",
    "phone",
    "preferred_contact",
    "subject",
    "grade",
    "tutoring_type",
    "goal",
    "urgency",
    "status",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "source",
    "campaign",
    "internal_notes",
]


def _lead_group_expression(field_name: str):
    return Case(
        When(**{field_name: ""}, then=Value("unknown")),
        default=F(field_name),
        output_field=CharField(),
    )


def _lead_group_counts(queryset, field_name: str, choices=None) -> list[dict]:
    label_map = dict(choices or [])
    rows = (
        queryset.annotate(group_value=_lead_group_expression(field_name))
        .values("group_value")
        .annotate(total=Count("id"))
        .order_by("group_value")
    )
    return [
        {
            "value": row["group_value"] or "unknown",
            "label": label_map.get(row["group_value"], row["group_value"] or "unknown"),
            "total": row["total"],
        }
        for row in rows
    ]


def _lead_campaign_stats(queryset) -> list[dict]:
    rows = (
        queryset.annotate(utm_campaign_group=_lead_group_expression("utm_campaign"))
        .values("utm_campaign_group")
        .annotate(
            total=Count("id"),
            won=Count("id", filter=Q(status=Lead.Status.WON)),
            lost=Count("id", filter=Q(status=Lead.Status.LOST)),
            unsuitable=Count("id", filter=Q(status=Lead.Status.UNSUITABLE)),
        )
        .order_by("utm_campaign_group")
    )
    stats = []
    for row in rows:
        total = row["total"] or 0
        stats.append(
            {
                "campaign": row["utm_campaign_group"] or "unknown",
                "total": total,
                "won": row["won"],
                "lost": row["lost"],
                "unsuitable": row["unsuitable"],
                "conversion_rate": (row["won"] / total * 100) if total else 0,
            }
        )
    return stats


def _filter_leads_by_period(queryset, start_date: str, end_date: str):
    if start_date:
        queryset = queryset.filter(created_at__date__gte=start_date)
    if end_date:
        queryset = queryset.filter(created_at__date__lte=end_date)
    return queryset


def _valid_iso_date(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        date.fromisoformat(value)
    except ValueError:
        return ""
    return value


def _write_leads_csv(queryset) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="brainboost-leads.csv"'
    writer = csv.writer(response)
    writer.writerow(LEAD_EXPORT_FIELDS)
    for lead in queryset.order_by("-created_at"):
        writer.writerow([getattr(lead, field) for field in LEAD_EXPORT_FIELDS])
    return response


def _build_campaign_url(base_url: str, params: dict[str, str]) -> str:
    parsed = urlsplit(base_url.strip())
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key not in params
    ]
    query_items.extend((key, value) for key, value in params.items() if value)
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query_items),
            parsed.fragment,
        )
    )


def _broadcast_recipient_emails(audience: str) -> list[str]:
    users = CustomUser.objects.filter(is_active=True).exclude(email="")
    if audience == BroadcastEmailForm.AUDIENCE_ADMINS:
        users = users.filter(Q(is_staff=True) | Q(is_superuser=True))
    elif audience == BroadcastEmailForm.AUDIENCE_PARENTS:
        users = users.filter(role=CustomUser.Roles.PARENT)
    elif audience == BroadcastEmailForm.AUDIENCE_STUDENTS:
        users = users.filter(
            role__in=[
                CustomUser.Roles.STUDENT,
                CustomUser.Roles.INDEPENDENT_STUDENT,
            ]
        )
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
    reply_to = _default_mail_reply_to()
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
            message = EmailMultiAlternatives(
                subject,
                text_body,
                from_email,
                [recipient],
                reply_to=reply_to,
            )
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

__all__ = [name for name in globals() if not name.startswith("__")]
