import hashlib
import os
import secrets
import tempfile
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMultiAlternatives
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import Agreement, AgreementAuditEvent, Lead


LEARNING_TERMS_VERSION = "draft-2026-02"
TUTOR_TERMS_VERSION = "draft-2026-02"

LEARNING_TERMS = """Lernvereinbarung - redaktioneller Entwurf

1. Vertragsparteien und Rolle von BrainBoost
Diese Lernvereinbarung wird unmittelbar zwischen der im Vertrag genannten TutorIn und der volljährigen zahlungspflichtigen VertragspartnerIn geschlossen. Bei minderjährigen SchülerInnen ist dies die ausgewählte erziehungsberechtigte Person; volljährige SchülerInnen und StudentInnen können selbst VertragspartnerIn sein. BrainBoost vermittelt den Kontakt, stellt die WebApp bereit, dokumentiert die Bestätigungen und versendet die Vertragskopie. BrainBoost wird nicht Partei dieser Lernvereinbarung und schuldet die Nachhilfeleistung nicht.

2. SchülerIn/StudentIn und Leistungsbeschreibung
Die TutorIn erbringt individuelle Nachhilfe in den im Vertrag ausgewiesenen Fächern. Unterrichtsort, Unterrichtsform, Dauer und Termine werden zwischen TutorIn und VertragspartnerIn abgestimmt. Abweichende Vereinbarungen bedürfen der Textform.

3. Terminabsprache und Ausfall
Termine werden flexibel über die BrainBoost-WebApp oder einen vereinbarten organisatorischen Kommunikationskanal abgestimmt. Absagen bis fünf Stunden vor Beginn sind kostenfrei. Bei späterer Absage oder Nichterscheinen kann die volle vereinbarte Einheit berechnet werden. Die Anzahl der Termine richtet sich nach dem tatsächlichen Bedarf.

4. Preise und Abrechnung
Es besteht kein monatlicher Festbetrag. Berechnet werden tatsächlich erbrachte sowie nach der Ausfallregelung abrechenbare Einheiten. Die Endpreise betragen 19 Euro für 45 Minuten, 25 Euro für 60 Minuten und 36 Euro für 90 Minuten. Die TutorIn rechnet monatlich per Rechnung ab. Rechnungen sind innerhalb von sieben Werktagen nach Erhalt zu begleichen. Bei Zahlungsverzug gelten die gesetzlichen Regelungen, insbesondere § 288 BGB. Soweit die TutorIn die Kleinunternehmerregelung nach § 19 UStG anwendet, wird keine Umsatzsteuer ausgewiesen; andernfalls gilt die auf der Rechnung ausgewiesene steuerliche Behandlung.

5. Fahrtkosten und Zuschläge
Bei einem einfachen Anfahrtsweg von mindestens drei Kilometern können 0,30 Euro je gefahrenem Kilometer für Hin- und Rückweg berechnet werden. Maßgeblich ist der in der BrainBoost-WebApp beziehungsweise einem geeigneten Routenplaner ermittelte Straßenweg. Für Unterricht an Wochenenden oder gesetzlichen Feiertagen kann bei vorheriger Vereinbarung ein Zuschlag von 27 Prozent je Einheit berechnet werden.

6. BrainBoost-WebApp
BrainBoost stellt zur Organisation der Nachhilfe persönliche Zugänge bereit. Die WebApp dient insbesondere der Terminverwaltung, Kommunikation, Dokumentation, Rechnungsbereitstellung und Bereitstellung von Lernmaterialien. Zugangsdaten dürfen nicht weitergegeben werden. Bei Minderjährigen stimmt die erziehungsberechtigte VertragspartnerIn der Nutzung durch die SchülerIn ausdrücklich zu.

7. Datenschutz
Personenbezogene Daten, insbesondere Namen, Anschriften, Kontaktdaten, schulische Angaben sowie Termin- und WebApp-Nutzungsdaten, werden nur zur Vermittlung, Planung, Durchführung, Dokumentation und Abrechnung der Nachhilfe verarbeitet. Rechtsgrundlage ist insbesondere Art. 6 Abs. 1 lit. b DSGVO. Dienstleister dürfen im Rahmen wirksamer Auftragsverarbeitungsvereinbarungen eingesetzt werden. Es gelten die gesetzlichen Rechte auf Auskunft, Berichtigung, Löschung, Einschränkung, Datenübertragbarkeit und Beschwerde bei einer Aufsichtsbehörde.

8. Laufzeit und Beendigung
Die Lernvereinbarung läuft auf unbestimmte Zeit und kann von beiden Vertragsparteien jederzeit in Textform beendet werden. Bereits erbrachte und nach der Ausfallregelung abrechenbare Leistungen bleiben zahlbar.

9. Elektronischer Vertragsabschluss
Die VertragspartnerIn bestätigt diese versionierte Fassung einschließlich Zahlungsart, Datenschutz und WebApp-Nutzung zunächst elektronisch und anschließend über einen einmalig verwendbaren E-Mail-Link. Danach bestätigt die TutorIn dieselbe unveränderte Fassung ebenfalls elektronisch und über einen eigenen einmalig verwendbaren E-Mail-Link. Erst nach beiden E-Mail-Bestätigungen erzeugt BrainBoost die finale PDF-Kopie und versendet sie an beide Vertragsparteien. Jede nachträgliche inhaltliche Änderung erfordert eine neue Vertragsversion und erneute Bestätigung beider Parteien.
"""

TUTOR_TERMS = """TutorInnenvereinbarung - redaktioneller Entwurf

1. Gegenstand der Vereinbarung
BrainBoost vermittelt der TutorIn einzelne Nachhilfeaufträge, online und/oder vor Ort, in den von ihr angebotenen Fächern. Die TutorIn erbringt die Nachhilfeleistungen gegenüber den SchülerInnen beziehungsweise KundInnen von BrainBoost im Rahmen der jeweils angenommenen Aufträge.

2. Selbstständige Tätigkeit
Die TutorIn handelt als selbstständige UnternehmerIn; ein Arbeitsverhältnis wird nicht begründet. Sie ist bei der Ausführung grundsätzlich frei, soweit bestätigte Termine, Schutzstandards und Datenschutzvorgaben eingehalten werden. Sie ist nicht exklusiv für BrainBoost tätig und verwendet grundsätzlich eigene Arbeitsmittel.

3. Auftragsanfrage, Annahme und Organisation
BrainBoost übermittelt Auftragsanfragen mit den erforderlichen Eckdaten. Ein Auftrag kommt erst durch Annahme der TutorIn in Textform und Bestätigung durch BrainBoost zustande. Aufträge können ohne Angabe von Gründen abgelehnt werden. Änderungen und Absagen bestätigter Termine sind so früh wie möglich mitzuteilen und in der WebApp zu dokumentieren.

4. Qualität und Verhalten
Die TutorIn arbeitet vorbereitet, pünktlich, respektvoll und professionell. Die von BrainBoost vorgegebenen Qualitätsstandards bilden den Rahmen des Angebots, ohne eine arbeitsrechtliche Weisungskette zu begründen. Geschuldet ist eine sorgfältige Nachhilfeleistung, nicht ein bestimmter Lernerfolg oder eine bestimmte Notenverbesserung.

5. Kommunikation und Schutz Minderjähriger
Kommunikation dient der Organisation und Durchführung der Nachhilfe. Bei Minderjährigen erfolgen organisatorische Absprachen grundsätzlich transparent über Erziehungsberechtigte oder freigegebene BrainBoost-Kanäle. Private Social-Media-Kommunikation im Zusammenhang mit der Nachhilfe ist ausgeschlossen. Messenger dürfen nur für organisatorische Zwecke genutzt werden. Gravierende Vorfälle oder der Verdacht auf eine Kindeswohlgefährdung sind BrainBoost unverzüglich mitzuteilen.

6. Vergütung und WebApp-Gebühr
Vergütet werden tatsächlich erbrachte Einheiten und nach der Ausfallregelung abrechenbare Termine. Die Vergütung beträgt netto 19 Euro für 45 Minuten, 25 Euro für 60 Minuten und 36 Euro für 90 Minuten. Für die Bereitstellung, Nutzung und Instandhaltung der WebApp werden monatlich 20 Euro berechnet. Die Gebühr wird über ein bei Stripe eingerichtetes SEPA-Mandat eingezogen; BrainBoost speichert keine vollständigen Bankdaten.

7. Rechnung, Abrechnung und Zahlung
Die TutorIn erstellt bis zum ersten Werktag des Folgemonats eine prüffähige Rechnung für die erbrachten Leistungen und lädt sie zur Gegenprüfung in die WebApp. BrainBoost stellt die geprüfte Rechnung grundsätzlich bis zum dritten Werktag bereit. Nur freigegebene Rechnungen dürfen den jeweiligen KundInnen grundsätzlich bis zum siebten Werktag zur Verfügung gestellt werden. Das Zahlungsziel richtet sich nach der freigegebenen Rechnung.

8. Vertraulichkeit
Nicht öffentlich bekannte Informationen über BrainBoost, KundInnen, Preise, Materialien und interne Abläufe sind vertraulich zu behandeln. Diese Pflicht gilt während der Vertragslaufzeit und zwölf Monate nach Vertragsende fort.

9. Datenschutz und Datensicherheit
Personenbezogene Daten dürfen nur verarbeitet werden, soweit dies für den Auftrag erforderlich ist. SchülerInnendaten dürfen nicht an Dritte weitergegeben oder unnötig privat gespeichert werden. Private Notizen sind datenarm zu halten und spätestens 30 Tage nach der letzten Einheit beziehungsweise bei Auftragsende zu löschen. Geräte und Zugänge sind angemessen zu sichern. Datenschutzvorfälle sind unverzüglich, spätestens innerhalb von 24 Stunden, an BrainBoost zu melden.

10. Kundenschutz
Während der Vertragslaufzeit und für sechs Monate danach dürfen mit KundInnen, die durch BrainBoost bekannt wurden, keine BrainBoost umgehenden Nachhilfeverträge geschlossen werden. Dies gilt auch für mittelbare Umgehungen. Bei einem schuldhaften Verstoß kann BrainBoost eine angemessene, gerichtlich überprüfbare Vertragsstrafe festsetzen; weitergehende Ansprüche bleiben unberührt und eine Vertragsstrafe wird auf einen nachgewiesenen Schaden angerechnet.

11. Haftung und Versicherung
Die TutorIn haftet für vorsätzlich oder fahrlässig verursachte Schäden nach den gesetzlichen Vorschriften.

12. Laufzeit und Kündigung
Die Vereinbarung beginnt mit dem im Vertrag ausgewiesenen Wirksamkeitsdatum und läuft auf unbestimmte Zeit. Sie kann von beiden Parteien mit einer Frist von sieben Tagen zum Monatsende in Textform gekündigt werden. Das Recht zur fristlosen Kündigung aus wichtigem Grund bleibt unberührt.

13. Schlussbestimmungen
Änderungen und Ergänzungen bedürfen der Textform. Es gilt deutsches Recht. Gerichtsstand ist, soweit gesetzlich zulässig, Braunschweig. Die Unwirksamkeit einer einzelnen Bestimmung lässt die übrige Vereinbarung unberührt; an ihre Stelle tritt die gesetzliche Regelung.

Anlage 1 - Vergütung und Abrechnung
Einheiten werden als 45, 60 oder 90 Minuten abgerechnet. Online- und Vor-Ort-Unterricht werden gleich vergütet. Bei einem einfachen Fahrweg von mehr als drei Kilometern werden für Hin- und Rückweg 0,30 Euro je Kilometer auf Grundlage des kürzesten Straßenwegs in der BrainBoost-WebApp erstattet; Wegezeit wird nicht gesondert vergütet. Bei einer Absage weniger als fünf Stunden vor Beginn werden 100 Prozent der vereinbarten Einheitsvergütung gezahlt, sofern der Ausfall dokumentiert wird. Sonderleistungen werden nur nach vorheriger Vereinbarung in Textform vergütet. Die Beträge sind Nettobeträge; Umsatzsteuer wird, soweit geschuldet, zusätzlich ausgewiesen. Bei Anwendung von § 19 UStG erfolgt die Rechnung ohne Umsatzsteuer und mit entsprechendem Hinweis.

Anlage 2 - Datenschutz
Es gilt der Grundsatz der Datenminimierung. Dauerhafte Dokumentation erfolgt ausschließlich in der WebApp. E-Mail und Messenger sind nur für organisatorische Inhalte ohne sensible Daten zulässig. Social Media ist für Nachhilfekommunikation ausgeschlossen. Zugangsdaten dürfen nicht geteilt werden; Geräte müssen mit Passwort oder Biometrie und automatischer Sperre geschützt sein.

Anlage 3 - Kinderschutz
Die TutorIn wahrt professionelle Distanz. Private Treffen, unangemessene Geschenke, sexualisierte oder diskriminierende Inhalte, Flirten, Kommentare zum Körper und unnötiger Körperkontakt sind untersagt. Fotos, Videos oder Audioaufnahmen von Minderjährigen bedürfen der schriftlichen Einwilligung der Erziehungsberechtigten und der Freigabe durch BrainBoost. Akute Gefahren sind zuerst den zuständigen Notfallstellen und anschließend BrainBoost zu melden. Ein erweitertes Führungszeugnis ist vorzulegen. Die Ausstellungskosten trägt die TutorIn; als Ausgleich entfällt die WebApp-Gebühr im ersten Monat.

14. Elektronischer Vertragsabschluss
Die TutorIn bestätigt die vollständig angezeigte, versionierte Vertragsfassung und das über Stripe erteilte SEPA-Mandat. Anschließend bestätigt BrainBoost dieselbe Fassung. Beide Parteien erhalten das finale PDF per E-Mail; die Bestätigungsschritte werden mit Zeitstempel protokolliert.
"""


def _user_profile_data(user) -> dict:
    data = {
        "user_id": user.pk,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.display_name,
        "email": user.email,
        "role": user.role,
    }
    if hasattr(user, "parent_profile"):
        data["phone_number"] = user.parent_profile.phone_number
        data["address"] = user.parent_profile.address
    if hasattr(user, "student_profile"):
        profile = user.student_profile
        data.update(
            address=profile.address,
            phone_number=profile.phone_number,
            school=profile.school,
            grade_level=profile.grade_level,
            birth_date=profile.birth_date.isoformat() if profile.birth_date else "",
            degree_program=profile.degree_program,
        )
    if hasattr(user, "tutor_profile"):
        profile = user.tutor_profile
        data.update(
            address=profile.address,
            phone_number=profile.phone_number,
            tutor_number=profile.tutor_number,
            tutor_status=profile.status,
        )
    return data


def build_agreement_snapshot(agreement: Agreement) -> dict:
    snapshot = {"participant": _user_profile_data(agreement.participant)}
    if agreement.student_id:
        snapshot["student"] = _user_profile_data(agreement.student.user)
    if agreement.tutor_id:
        snapshot["tutor"] = _user_profile_data(agreement.tutor.user)
    if agreement.source_lead_id:
        lead = agreement.source_lead
        snapshot["lead"] = {
            "lead_id": lead.pk,
            "name": lead.name,
            "email": lead.email,
            "phone": lead.phone,
            "subject": lead.subject,
            "grade": lead.grade,
            "tutoring_type": lead.tutoring_type,
            "urgency": lead.urgency,
        }
    return snapshot


def create_tutor_agreement(*, participant, tutor, source_lead=None, created_by=None) -> Agreement:
    agreement, _ = Agreement.objects.get_or_create(
        agreement_type=Agreement.AgreementType.TUTOR,
        participant=participant,
        source_lead=source_lead,
        defaults={
            "tutor": tutor,
            "created_by": created_by,
            "status": Agreement.Status.PENDING_PARTICIPANT,
            "version": TUTOR_TERMS_VERSION,
            "effective_date": timezone.localdate(),
            "terms_snapshot": TUTOR_TERMS,
            "payment_method": Agreement.PaymentMethod.STRIPE_SEPA,
        },
    )
    if not agreement.data_snapshot:
        agreement.data_snapshot = build_agreement_snapshot(agreement)
        agreement.save(update_fields=["data_snapshot", "updated_at"])
    return agreement


def create_tutor_agreement_for_lead(lead: Lead, *, created_by=None) -> Agreement:
    if not lead.converted_tutor_id:
        raise ValueError("lead_has_no_tutor")
    return create_tutor_agreement(
        participant=lead.converted_tutor.user,
        tutor=lead.converted_tutor,
        source_lead=lead,
        created_by=created_by,
    )


def create_learning_agreement(*, participant, student, tutor, source_lead=None, created_by=None):
    open_agreements = Agreement.objects.filter(
        agreement_type=Agreement.AgreementType.LEARNING,
        participant=participant,
        student=student,
        tutor=tutor,
        status__in=[
            Agreement.Status.DRAFT,
            Agreement.Status.PENDING_PARTICIPANT,
            Agreement.Status.PENDING_EMAIL,
            Agreement.Status.PENDING_TUTOR,
            Agreement.Status.PENDING_TUTOR_EMAIL,
            Agreement.Status.PENDING_BRAINBOOST,
        ],
    )
    agreement = open_agreements.filter(version=LEARNING_TERMS_VERSION).first()
    if agreement is None:
        # A changed contract text must never be accepted through an old open
        # agreement. Keep the old snapshot for the audit trail and create a
        # fresh version that both parties must confirm again.
        open_agreements.exclude(version=LEARNING_TERMS_VERSION).update(
            status=Agreement.Status.SUPERSEDED,
            updated_at=timezone.now(),
        )
        agreement = Agreement.objects.create(
            agreement_type=Agreement.AgreementType.LEARNING,
            participant=participant,
            student=student,
            tutor=tutor,
            source_lead=source_lead,
            created_by=created_by,
            status=Agreement.Status.PENDING_PARTICIPANT,
            version=LEARNING_TERMS_VERSION,
            effective_date=timezone.localdate(),
            terms_snapshot=LEARNING_TERMS,
        )
    if not agreement.data_snapshot:
        agreement.data_snapshot = build_agreement_snapshot(agreement)
        agreement.save(update_fields=["data_snapshot", "updated_at"])
    return agreement


def issue_confirmation_token(agreement: Agreement) -> str:
    token = secrets.token_urlsafe(32)
    agreement.confirmation_token_digest = hashlib.sha256(token.encode()).hexdigest()
    agreement.confirmation_token_expires_at = timezone.now() + timedelta(hours=24)
    agreement.save(update_fields=["confirmation_token_digest", "confirmation_token_expires_at", "updated_at"])
    return token


def token_is_valid(agreement: Agreement, token: str) -> bool:
    if not agreement.confirmation_token_digest or not agreement.confirmation_token_expires_at:
        return False
    if agreement.confirmation_token_expires_at < timezone.now():
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    return secrets.compare_digest(digest, agreement.confirmation_token_digest)


def issue_tutor_confirmation_token(agreement: Agreement) -> str:
    token = secrets.token_urlsafe(32)
    agreement.tutor_confirmation_token_digest = hashlib.sha256(token.encode()).hexdigest()
    agreement.tutor_confirmation_token_expires_at = timezone.now() + timedelta(hours=24)
    agreement.save(
        update_fields=[
            "tutor_confirmation_token_digest",
            "tutor_confirmation_token_expires_at",
            "updated_at",
        ]
    )
    return token


def tutor_token_is_valid(agreement: Agreement, token: str) -> bool:
    if not agreement.tutor_confirmation_token_digest or not agreement.tutor_confirmation_token_expires_at:
        return False
    if agreement.tutor_confirmation_token_expires_at < timezone.now():
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    return secrets.compare_digest(digest, agreement.tutor_confirmation_token_digest)


def send_confirmation_email(request, agreement: Agreement, token: str) -> None:
    url = request.build_absolute_uri(
        reverse("agreement_email_confirm", kwargs={"agreement_id": agreement.pk, "token": token})
    )
    subject = f"BrainBoost: {agreement.get_agreement_type_display()} bestätigen"
    body = (
        f"Hallo {agreement.participant.display_name},\n\n"
        "bitte bestätige deine Vereinbarung über diesen einmalig verwendbaren Link:\n"
        f"{url}\n\nDer Link ist 24 Stunden gültig."
    )
    message = EmailMultiAlternatives(
        subject,
        body,
        getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@brainboost.local"),
        [agreement.participant.email],
    )
    if message.send() == 0:
        raise RuntimeError("agreement_confirmation_email_failed")


def send_tutor_review_email(request, agreement: Agreement) -> None:
    url = request.build_absolute_uri(
        reverse("agreement_detail", kwargs={"agreement_id": agreement.pk})
    )
    tutor_user = agreement.tutor.user
    message = EmailMultiAlternatives(
        f"BrainBoost: Lernvereinbarung {agreement.reference} prüfen",
        (
            f"Hallo {tutor_user.display_name},\n\n"
            "die VertragspartnerIn hat die Lernvereinbarung bestätigt. "
            "Bitte melde dich an, prüfe dieselbe Vertragsfassung und bestätige sie:\n"
            f"{url}"
        ),
        getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@brainboost.local"),
        [tutor_user.email],
    )
    if message.send() == 0:
        raise RuntimeError("agreement_tutor_review_email_failed")


def send_tutor_confirmation_email(request, agreement: Agreement, token: str) -> None:
    url = request.build_absolute_uri(
        reverse(
            "agreement_tutor_email_confirm",
            kwargs={"agreement_id": agreement.pk, "token": token},
        )
    )
    tutor_user = agreement.tutor.user
    message = EmailMultiAlternatives(
        f"BrainBoost: Lernvereinbarung {agreement.reference} final bestätigen",
        (
            f"Hallo {tutor_user.display_name},\n\n"
            "bitte bestätige deine Zustimmung über diesen einmalig verwendbaren Link:\n"
            f"{url}\n\nDer Link ist 24 Stunden gültig."
        ),
        getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@brainboost.local"),
        [tutor_user.email],
    )
    if message.send() == 0:
        raise RuntimeError("agreement_tutor_confirmation_email_failed")


def generate_agreement_pdf(request, agreement: Agreement) -> bytes:
    if os.path.isdir("/opt/homebrew/lib"):
        os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")
    os.environ.setdefault("XDG_CACHE_HOME", tempfile.gettempdir())
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        raise RuntimeError("WeasyPrint ist für Vertrags-PDFs nicht verfügbar.") from exc

    logo_path = finders.find("design/LogoPNG.png")
    html = render_to_string(
        "agreements/agreement_pdf.html",
        {
            "agreement": agreement,
            "logo_url": f"file://{logo_path}" if logo_path else "",
        },
    )
    return HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()


def finalize_agreement(request, agreement: Agreement, *, actor) -> bytes:
    if not agreement.email_confirmed_at:
        raise ValueError("participant_email_not_confirmed")
    if agreement.agreement_type == Agreement.AgreementType.LEARNING:
        raise ValueError("learning_agreement_requires_tutor_finalization")

    now = timezone.now()
    agreement.brainboost_confirmed_by = actor
    agreement.brainboost_confirmed_at = now
    pdf_bytes = generate_agreement_pdf(request, agreement)
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    agreement.final_pdf.save(
        f"{agreement.reference}.pdf",
        ContentFile(pdf_bytes),
        save=False,
    )
    agreement.final_pdf_sha256 = digest
    agreement.status = Agreement.Status.COMPLETED
    agreement.completed_at = now
    agreement.save(
        update_fields=[
            "brainboost_confirmed_by",
            "brainboost_confirmed_at",
            "final_pdf",
            "final_pdf_sha256",
            "status",
            "completed_at",
            "updated_at",
        ]
    )
    audit(agreement, "brainboost_confirmed", actor=actor, metadata={"pdf_sha256": digest})
    return pdf_bytes


def finalize_learning_agreement(request, agreement: Agreement) -> bytes:
    if agreement.agreement_type != Agreement.AgreementType.LEARNING:
        raise ValueError("not_a_learning_agreement")
    if not agreement.email_confirmed_at:
        raise ValueError("participant_email_not_confirmed")
    if not agreement.tutor_email_confirmed_at:
        raise ValueError("tutor_email_not_confirmed")

    now = timezone.now()
    pdf_bytes = generate_agreement_pdf(request, agreement)
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    agreement.final_pdf.save(
        f"{agreement.reference}.pdf",
        ContentFile(pdf_bytes),
        save=False,
    )
    agreement.final_pdf_sha256 = digest
    agreement.status = Agreement.Status.COMPLETED
    agreement.completed_at = now
    agreement.save(
        update_fields=[
            "final_pdf",
            "final_pdf_sha256",
            "status",
            "completed_at",
            "updated_at",
        ]
    )
    audit(
        agreement,
        "learning_agreement_completed",
        actor=agreement.tutor.user,
        metadata={"pdf_sha256": digest},
    )
    return pdf_bytes


def send_final_agreement_email(agreement: Agreement, pdf_bytes: bytes) -> None:
    recipients = [agreement.participant.email]
    if agreement.agreement_type == Agreement.AgreementType.LEARNING:
        tutor_email = agreement.tutor.user.email if agreement.tutor_id else ""
        if tutor_email and tutor_email.lower() not in {email.lower() for email in recipients}:
            recipients.append(tutor_email)
    else:
        brainboost_email = getattr(settings, "INTERNAL_CONTACT_EMAIL", "").strip()
        if brainboost_email and brainboost_email.lower() not in {email.lower() for email in recipients}:
            recipients.append(brainboost_email)
    recipients = [email for email in recipients if email]
    if not recipients:
        raise ValueError("agreement_has_no_recipients")

    message = EmailMultiAlternatives(
        f"BrainBoost: {agreement.get_agreement_type_display()} abgeschlossen",
        (
            f"Die Vereinbarung {agreement.reference} wurde von allen Parteien bestätigt.\n\n"
            "Die unveränderliche PDF-Kopie befindet sich im Anhang und in der BrainBoost-WebApp."
        ),
        getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@brainboost.local"),
        recipients,
    )
    message.attach(f"{agreement.reference}.pdf", pdf_bytes, "application/pdf")
    if message.send() == 0:
        raise RuntimeError("agreement_final_email_failed")


def audit(agreement: Agreement, event_type: str, *, actor=None, metadata=None):
    return AgreementAuditEvent.objects.create(
        agreement=agreement,
        event_type=event_type,
        actor=actor,
        metadata=metadata or {},
    )
