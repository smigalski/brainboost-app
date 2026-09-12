from datetime import date, time, timedelta
from decimal import Decimal
from io import StringIO
import json
import re
from smtplib import SMTPAuthenticationError
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

from django import forms
from django.contrib.auth import authenticate
from django.contrib.messages import get_messages
from django.core import mail
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import (
    BrainBoostFeedbackForm,
    InvoiceGenerateForm,
    LearningMaterialForm,
    TutorProfileForm,
)
from .access import can_access_student, can_manage_lesson, can_view_invoice
from .models import (
    BrainBoostFeedback,
    CustomUser,
    FAQItem,
    Invoice,
    LearningMaterial,
    Lesson,
    ParentProfile,
    ProgressEntry,
    StudentProfile,
    TutorProfile,
    TemporaryTutorAssignment,
    Lead,
)
from .views import (
    _auto_complete_past_lessons,
    _assign_location_and_distance,
    _build_epc_payment_payload,
    _build_campaign_url,
    _build_invoice_pdf_context,
    _build_progress_chart_data,
    _format_invoice_number,
    _invoice_filename,
    _google_driving_distance_km,
    _lead_campaign_stats,
    _sync_temporary_tutor_assignments,
)


@override_settings(SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies")
class SeoEndpointTests(SimpleTestCase):
    sitemap_urls = {
        "https://www.nachhilfe-brainboost.de/",
        "https://www.nachhilfe-brainboost.de/nachhilfe-braunschweig/",
        "https://www.nachhilfe-brainboost.de/nachhilfe-braunschweig/eltern/",
        "https://www.nachhilfe-brainboost.de/nachhilfe-braunschweig/schuelerinnen/",
        "https://www.nachhilfe-brainboost.de/tutor-werden/",
    }

    def test_robots_txt_references_sitemap(self):
        response = self.client.get("/robots.txt")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain")
        self.assertEqual(
            response.content.decode(),
            "User-agent: *\n"
            "Allow: /\n\n"
            "Sitemap: https://www.nachhilfe-brainboost.de/sitemap.xml\n",
        )

    def test_sitemap_xml_contains_public_landing_pages(self):
        response = self.client.get("/sitemap.xml")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml")
        root = ElementTree.fromstring(response.content)
        namespace = {"sitemap": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locations = {
            element.text for element in root.findall("sitemap:url/sitemap:loc", namespace)
        }

        self.assertEqual(locations, self.sitemap_urls)

    @override_settings(DEBUG=False, INDEXNOW_KEY="abc123456789xyz")
    def test_indexnow_key_file_returns_configured_key_in_production(self):
        response = self.client.get("/abc123456789xyz.txt")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain")
        self.assertEqual(response.content.decode(), "abc123456789xyz")

    @override_settings(
        DEBUG=False,
        INDEXNOW_KEY="abc123456789xyz",
        CANONICAL_DOMAIN="www.nachhilfe-brainboost.de",
    )
    @patch("core.management.commands.submit_indexnow.urlopen")
    def test_submit_indexnow_posts_sitemap_urls(self, mocked_urlopen):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def getcode(self):
                return 200

        mocked_urlopen.return_value = Response()
        output = StringIO()

        call_command("submit_indexnow", stdout=output)

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.indexnow.org/IndexNow")
        self.assertEqual(payload["host"], "www.nachhilfe-brainboost.de")
        self.assertEqual(payload["key"], "abc123456789xyz")
        self.assertEqual(
            payload["keyLocation"],
            "https://www.nachhilfe-brainboost.de/abc123456789xyz.txt",
        )
        self.assertEqual(set(payload["urlList"]), self.sitemap_urls)
        self.assertNotIn("abc123456789xyz", output.getvalue())
        self.assertIn("Status 200", output.getvalue())

    @override_settings(
        DEBUG=False,
        INDEXNOW_KEY="abc123456789xyz",
        CANONICAL_DOMAIN="www.nachhilfe-brainboost.de",
    )
    @patch("core.management.commands.submit_indexnow.urlopen")
    def test_submit_indexnow_accepts_202_response(self, mocked_urlopen):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def getcode(self):
                return 202

        mocked_urlopen.return_value = Response()
        output = StringIO()

        call_command("submit_indexnow", stdout=output)

        self.assertIn("Status 202", output.getvalue())


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AccessPolicyTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="access_tutor",
            password="pw",
            role=CustomUser.Roles.TUTOR,
        )
        self.other_tutor_user = CustomUser.objects.create_user(
            username="access_other_tutor",
            password="pw",
            role=CustomUser.Roles.TUTOR,
        )
        self.parent_user = CustomUser.objects.create_user(
            username="access_parent",
            password="pw",
            role=CustomUser.Roles.PARENT,
        )
        self.student_user = CustomUser.objects.create_user(
            username="access_student",
            password="pw",
            role=CustomUser.Roles.STUDENT,
        )
        self.other_student_user = CustomUser.objects.create_user(
            username="access_other_student",
            password="pw",
            role=CustomUser.Roles.STUDENT,
        )
        self.tutor = TutorProfile.objects.create(user=self.tutor_user)
        self.other_tutor = TutorProfile.objects.create(user=self.other_tutor_user)
        self.parent = ParentProfile.objects.create(user=self.parent_user)
        self.student = StudentProfile.objects.create(user=self.student_user)
        self.other_student = StudentProfile.objects.create(user=self.other_student_user)
        self.student.parents.add(self.parent)
        self.student.assigned_tutors.add(self.tutor)
        self.lesson = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=date(2026, 4, 1),
            time=time(15, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
        )
        self.invoice = Invoice.objects.create(
            student=self.student,
            uploaded_by=self.tutor,
            approved_by=self.tutor,
            approved_at=timezone.now(),
            amount_total=Decimal("25.00"),
            file=SimpleUploadedFile("rechnung.pdf", b"%PDF-1.4\n"),
        )

    def test_can_access_student_for_linked_users_only(self):
        self.assertTrue(can_access_student(self.tutor_user, self.student))
        self.assertTrue(can_access_student(self.parent_user, self.student))
        self.assertTrue(can_access_student(self.student_user, self.student))
        self.assertFalse(can_access_student(self.other_tutor_user, self.student))
        self.assertFalse(can_access_student(self.student_user, self.other_student))

    def test_can_manage_lesson_only_for_lesson_tutor(self):
        self.assertTrue(can_manage_lesson(self.tutor_user, self.lesson))
        self.assertFalse(can_manage_lesson(self.other_tutor_user, self.lesson))
        self.assertFalse(can_manage_lesson(self.parent_user, self.lesson))
        self.assertFalse(can_manage_lesson(self.student_user, self.lesson))

    def test_can_view_invoice_requires_approved_invoice_and_relationship(self):
        self.assertTrue(can_view_invoice(self.parent_user, self.invoice))
        self.assertTrue(can_view_invoice(self.tutor_user, self.invoice))
        self.assertFalse(can_view_invoice(self.other_tutor_user, self.invoice))
        self.assertFalse(can_view_invoice(self.student_user, self.invoice))

        self.invoice.approved_at = None
        self.invoice.save(update_fields=["approved_at"])
        self.assertFalse(can_view_invoice(self.parent_user, self.invoice))
        self.assertFalse(can_view_invoice(self.tutor_user, self.invoice))


class AssignedTutorListTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="supervising_tutor",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        self.tutor = TutorProfile.objects.create(user=self.tutor_user)
        subordinate_user = CustomUser.objects.create_user(
            username="assigned_tutor",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
            first_name="Tina",
            last_name="Tutorin",
        )
        self.subordinate = TutorProfile.objects.create(user=subordinate_user)
        self.tutor.assigned_tutors.add(self.subordinate)
        self.client.login(username="supervising_tutor", password="test12345")

    def test_assigned_tutor_first_and_last_name_are_displayed(self):
        response = self.client.get(reverse("assigned_tutor_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tina Tutorin")

    def test_tutor_string_uses_display_name(self):
        self.assertEqual(str(self.subordinate), "TutorIn: Tina Tutorin")

    def test_user_string_uses_first_and_last_name(self):
        self.assertEqual(str(self.subordinate.user), "Tina Tutorin")


@override_settings(GOOGLE_MAPS_API_KEY="test-maps-key")
class TutorProfileAdminAddressAutocompleteTests(TestCase):
    def setUp(self):
        self.admin_user = CustomUser.objects.create_superuser(
            username="admin_address_autocomplete",
            password="test12345",
            email="admin@example.com",
        )
        tutor_user = CustomUser.objects.create_user(
            username="admin_edited_tutor",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        self.tutor = TutorProfile.objects.create(user=tutor_user)
        self.client.force_login(self.admin_user)

    def test_change_form_loads_google_address_autocomplete(self):
        response = self.client.get(
            reverse("admin:core_tutorprofile_change", args=[self.tutor.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="address-autocomplete"')
        self.assertContains(response, 'data-address-mode="admin"')
        self.assertContains(response, "core/address_autocomplete.js")
        self.assertContains(response, "core/admin_address_autocomplete.css")
        self.assertContains(response, "key=test-maps-key")
        self.assertContains(response, "libraries=places")

@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    MEDIA_ROOT=tempfile.mkdtemp(),
)
class IndependentStudentAccountTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="tutor",
            password="pw",
            role=CustomUser.Roles.TUTOR,
        )
        self.tutor_profile = TutorProfile.objects.create(user=self.tutor_user)

    def test_tutor_can_create_independent_student_without_parent(self):
        self.client.login(username="tutor", password="pw")

        response = self.client.post(
            reverse("independent_student_create"),
            data={
                "username": "studentin",
                "first_name": "Sina",
                "last_name": "Studiert",
                "email": "sina.studiert@example.com",
                "phone_number": "0176 123",
                "is_active": "on",
                "address": "Campus 1",
                "degree_program": "Informatik",
                "affected_courses": "Analysis I\nAlgorithmen",
                "tutoring_goal": "Klausurvorbereitung",
                "zoom_link": "",
                "zumpad_link": "",
            },
        )

        self.assertRedirects(response, reverse("dashboard"))
        user = CustomUser.objects.get(email="sina.studiert@example.com")
        self.assertEqual(user.role, CustomUser.Roles.INDEPENDENT_STUDENT)
        self.assertEqual(user.student_profile.parents.count(), 0)
        self.assertEqual(user.student_profile.degree_program, "Informatik")
        self.assertEqual(user.student_profile.affected_courses, "Analysis I\nAlgorithmen")
        self.assertEqual(user.student_profile.tutoring_goal, "Klausurvorbereitung")
        self.assertTrue(user.student_profile.assigned_tutors.filter(pk=self.tutor_profile.pk).exists())

    def test_regular_student_creation_requires_parent_without_toggle(self):
        self.client.login(username="tutor", password="pw")

        response = self.client.post(
            reverse("student_create"),
            data={
                "username": "schuelerin",
                "first_name": "Sina",
                "last_name": "Schule",
                "email": "",
                "phone_number": "",
                "is_active": "on",
                "address": "",
                "zoom_link": "",
                "zumpad_link": "",
                "create_without_parents": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ohne Eltern anlegen")
        self.assertContains(response, "Bitte wähle mindestens ein Elternteil aus")
        self.assertEqual(StudentProfile.objects.count(), 0)

    def test_student_create_toggle_creates_independent_student(self):
        self.client.login(username="tutor", password="pw")

        response = self.client.post(
            reverse("student_create"),
            data={
                "username": "ohneeltern",
                "first_name": "Sina",
                "last_name": "Solo",
                "email": "sina.solo@example.com",
                "phone_number": "",
                "is_active": "on",
                "address": "",
                "zoom_link": "",
                "zumpad_link": "",
                "create_without_parents": "on",
            },
        )

        self.assertRedirects(response, reverse("dashboard"))
        user = CustomUser.objects.get(username="ohneeltern")
        self.assertEqual(user.role, CustomUser.Roles.INDEPENDENT_STUDENT)
        self.assertEqual(user.student_profile.parents.count(), 0)
        self.assertTrue(user.student_profile.assigned_tutors.filter(pk=self.tutor_profile.pk).exists())

    def test_independent_student_can_access_and_announce_own_invoice(self):
        student_user = CustomUser.objects.create_user(
            username="studentin",
            password="pw",
            role=CustomUser.Roles.INDEPENDENT_STUDENT,
            email="studentin@example.com",
        )
        student_profile = StudentProfile.objects.create(user=student_user)
        invoice = Invoice.objects.create(
            student=student_profile,
            uploaded_by=self.tutor_profile,
            approved_by=self.tutor_profile,
            approved_at=timezone.now(),
            file=SimpleUploadedFile("rechnung.pdf", b"%PDF-1.4", content_type="application/pdf"),
            amount_total=Decimal("25.00"),
        )
        self.client.login(username="studentin", password="pw")

        list_response = self.client.get(reverse("invoice_list"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "Hier findest du deine Rechnungen")

        response = self.client.post(
            reverse("invoice_select_payment", args=[invoice.id, Invoice.PaymentMethod.CASH])
        )

        self.assertRedirects(response, reverse("invoice_list"))
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_status, Invoice.PaymentStatus.ANNOUNCED)
        self.assertEqual(invoice.payment_method, Invoice.PaymentMethod.CASH)
        self.assertIsNone(invoice.payment_requested_by)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    LEAD_NOTIFICATION_EMAIL="operator@example.com",
    DEFAULT_FROM_EMAIL="BrainBoost <brainboost.nachhilfe@gmail.com>",
    DEFAULT_REPLY_TO_EMAIL="brainboost.nachhilfe@gmail.com",
)
class LeadFormFlowTests(TestCase):
    def _parent_data(self, **overrides):
        data = {
            "role": Lead.Role.PARENT,
            "name": "Maria Muster",
            "email": "maria@example.com",
            "phone": "",
            "preferred_contact": Lead.PreferredContact.EMAIL,
            "subject": "Mathe",
            "grade": "8. Klasse",
            "tutoring_type": Lead.TutoringType.ONLINE,
            "goal": "Noten verbessern",
            "urgency": "Möglichst bald",
            "message": "Bitte melden.",
            "privacy_consent": "on",
        }
        data.update(overrides)
        return data

    def _tutor_data(self, **overrides):
        data = {
            "role": Lead.Role.TUTOR,
            "name": "Tina Tutor",
            "email": "tina@example.com",
            "phone": "",
            "preferred_contact": Lead.PreferredContact.EMAIL,
            "tutoring_type": Lead.TutoringType.BOTH,
            "education_status": Lead.EducationStatus.STUDENT,
            "teaching_subjects": "Mathe, Physik",
            "teaching_grades": "5-10",
            "weekly_availability": "4 Stunden",
            "experience_level": Lead.ExperienceLevel.SOME,
            "privacy_consent": "on",
        }
        data.update(overrides)
        return data

    def test_parent_lead_is_created_and_emails_are_sent(self):
        response = self.client.post(reverse("contact"), data=self._parent_data())

        self.assertRedirects(response, reverse("lead_thanks_tutoring"))
        lead = Lead.objects.get()
        self.assertEqual(lead.role, Lead.Role.PARENT)
        self.assertEqual(lead.subject, "Mathe")
        self.assertEqual(lead.status, Lead.Status.NEW)
        self.assertTrue(lead.privacy_consent)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("operator@example.com", mail.outbox[0].to)
        self.assertEqual(mail.outbox[0].reply_to, ["brainboost.nachhilfe@gmail.com"])
        self.assertIn("maria@example.com", mail.outbox[1].to)
        self.assertEqual(mail.outbox[1].from_email, "BrainBoost <brainboost.nachhilfe@gmail.com>")
        self.assertEqual(mail.outbox[1].reply_to, ["brainboost.nachhilfe@gmail.com"])

    @override_settings(APP_BASE_URL="https://www.nachhilfe-brainboost.de")
    def test_internal_lead_email_links_to_contact_action(self):
        response = self.client.post(reverse("contact"), data=self._parent_data())

        self.assertRedirects(response, reverse("lead_thanks_tutoring"))
        lead = Lead.objects.get()
        internal_mail = mail.outbox[0]
        lead_url = f"https://www.nachhilfe-brainboost.de{reverse('lead_dashboard')}?lead_id={lead.id}"
        self.assertIn("Kontaktieren", internal_mail.body)
        self.assertIn(lead_url, internal_mail.body)
        self.assertIn("Kontaktieren", internal_mail.alternatives[0][0])
        self.assertIn(lead_url, internal_mail.alternatives[0][0])

    def test_public_contact_email_is_rendered_with_gmail_address(self):
        response = self.client.get(reverse("contact"))

        self.assertContains(response, "mailto:brainboost.nachhilfe@gmail.com")
        self.assertContains(response, "Montag bis Freitag, 9:00 - 19:00 Uhr")
        self.assertNotContains(response, "9:00 - 21:00 Uhr")

    def test_all_shared_required_fields_have_visible_required_markers(self):
        response = self.client.get(reverse("contact"))

        self.assertContains(
            response,
            'Du bist ... <span class="lead-required">*</span>',
            html=True,
        )
        self.assertContains(
            response,
            '<label class="lead-label" for="id_email">E-Mail <span class="lead-required">*</span></label>',
            html=True,
        )
        self.assertNotContains(response, "data-tutor-email-required")

    def test_all_urgency_buttons_share_one_single_choice_group(self):
        response = self.client.get(reverse("contact"))

        self.assertContains(
            response,
            '<input type="radio" name="urgency_option"',
            count=4,
        )
        self.assertNotContains(response, 'name="urgency_reference_mode"')

    def test_utm_parameters_are_saved_from_query_string(self):
        response = self.client.post(
            reverse("contact")
            + "?utm_source=meta&utm_medium=paid&utm_campaign=abi2026&utm_content=story&utm_term=mathe&role=student",
            data=self._parent_data(role=Lead.Role.STUDENT),
        )

        self.assertRedirects(response, reverse("lead_thanks_tutoring"))
        lead = Lead.objects.get()
        self.assertEqual(lead.role, Lead.Role.STUDENT)
        self.assertEqual(lead.utm_source, "meta")
        self.assertEqual(lead.utm_medium, "paid")
        self.assertEqual(lead.utm_campaign, "abi2026")
        self.assertEqual(lead.utm_content, "story")
        self.assertEqual(lead.utm_term, "mathe")
        self.assertEqual(lead.campaign, "abi2026")

    def test_utm_parameters_are_saved_from_session_after_landing_visit(self):
        self.client.get(
            reverse("nachhilfe_anfrage")
            + "?utm_source=meta&utm_medium=paid&utm_campaign=abi2026&utm_content=story&utm_term=mathe",
            HTTP_REFERER="https://example.com/ad",
        )

        response = self.client.post(reverse("contact"), data=self._parent_data())

        self.assertRedirects(response, reverse("lead_thanks_tutoring"))
        lead = Lead.objects.get()
        self.assertEqual(lead.utm_source, "meta")
        self.assertEqual(lead.utm_medium, "paid")
        self.assertEqual(lead.utm_campaign, "abi2026")
        self.assertEqual(lead.utm_content, "story")
        self.assertEqual(lead.utm_term, "mathe")
        self.assertEqual(lead.referrer, "https://example.com/ad")
        self.assertEqual(lead.landing_page_path, reverse("nachhilfe_anfrage"))
        self.assertEqual(
            lead.initial_querystring,
            "utm_source=meta&utm_medium=paid&utm_campaign=abi2026&utm_content=story&utm_term=mathe",
        )

    def test_direct_utm_parameters_override_session_values(self):
        self.client.get(reverse("nachhilfe_anfrage") + "?utm_source=old&utm_campaign=old-campaign")

        response = self.client.post(
            reverse("contact") + "?utm_source=meta&utm_campaign=new-campaign",
            data=self._parent_data(),
        )

        self.assertRedirects(response, reverse("lead_thanks_tutoring"))
        lead = Lead.objects.get()
        self.assertEqual(lead.utm_source, "meta")
        self.assertEqual(lead.utm_campaign, "new-campaign")
        self.assertEqual(lead.campaign, "new-campaign")

    def test_invalid_form_without_email_is_not_saved(self):
        response = self.client.post(
            reverse("contact"),
            data=self._parent_data(email="", phone="0176 123456"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Lead.objects.count(), 0)
        self.assertContains(response, "Bitte gib eine E-Mail-Adresse an.")

    def test_urgency_now_is_saved_from_button_selection(self):
        response = self.client.post(
            reverse("contact"),
            data=self._parent_data(urgency="", urgency_option="now"),
        )

        self.assertRedirects(response, reverse("lead_thanks_tutoring"))
        self.assertEqual(Lead.objects.get().urgency, "Ab jetzt")

    def test_urgency_in_weeks_is_composed_from_number_input(self):
        response = self.client.post(
            reverse("contact"),
            data=self._parent_data(
                urgency="",
                urgency_option="weeks",
                urgency_weeks="3",
            ),
        )

        self.assertRedirects(response, reverse("lead_thanks_tutoring"))
        self.assertEqual(Lead.objects.get().urgency, "In 3 Wochen")

    def test_urgency_reference_is_composed_from_switch_and_text(self):
        response = self.client.post(
            reverse("contact"),
            data=self._parent_data(
                urgency="",
                urgency_option="reference_nach",
                urgency_reference="Herbstferien",
            ),
        )

        self.assertRedirects(response, reverse("lead_thanks_tutoring"))
        self.assertEqual(Lead.objects.get().urgency, "Nach Herbstferien")

    def test_privacy_checkbox_is_required(self):
        data = self._parent_data()
        data.pop("privacy_consent")

        response = self.client.post(reverse("contact"), data=data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Lead.objects.count(), 0)
        self.assertContains(response, "Bitte stimme der Verarbeitung")

    def test_tutor_lead_redirects_to_tutor_thank_you_page(self):
        response = self.client.post(reverse("contact"), data=self._tutor_data())

        self.assertRedirects(response, reverse("lead_thanks_tutor"))
        lead = Lead.objects.get()
        self.assertEqual(lead.role, Lead.Role.TUTOR)
        self.assertEqual(lead.subject, "Mathe, Physik")
        self.assertEqual(lead.grade, "5-10")

    def test_tutor_lead_requires_email_even_when_phone_is_present(self):
        response = self.client.post(
            reverse("contact"),
            data=self._tutor_data(email="", phone="0176 123456"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Lead.objects.count(), 0)
        self.assertContains(response, "Bitte gib eine E-Mail-Adresse an.")

    def test_contact_page_marks_tutor_tab_as_application_profile(self):
        response = self.client.get(reverse("contact") + "?role=tutor")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Das ist dein Bewerbungsprofil als TutorIn.")
        self.assertContains(response, "in ein TutorInnen-Profil um")
        self.assertContains(response, "SchülerIn/StudentIn")
        self.assertNotContains(response, 'name="motivation"')
        self.assertContains(
            response,
            '<label class="lead-label" for="id_email">E-Mail <span class="lead-required">*</span></label>',
            html=True,
        )
        self.assertEqual(response.context["form"]["role"].value(), Lead.Role.TUTOR)

    def test_parent_landing_links_to_prefilled_contact_form(self):
        response = self.client.get(reverse("nachhilfe_anfrage"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertContains(
            response,
            "<title>Nachhilfe Braunschweig &amp; online | Mathe, Englisch, Deutsch | BrainBoost</title>",
            html=True,
        )
        self.assertEqual(content.count("<title"), 1)
        self.assertContains(
            response,
            '<meta name="description" content="Individuelle Nachhilfe in Braunschweig und online: Mathe, Deutsch, Englisch und weitere Fächer. Für Eltern, SchülerInnen und TutorInnen. Jetzt kostenlos anfragen.">',
            html=True,
        )
        self.assertEqual(content.count('name="description"'), 1)
        self.assertEqual(content.count("<h1"), 1)
        self.assertContains(response, "Nachhilfe in Braunschweig und online")
        self.assertContains(response, "Mathe Nachhilfe Braunschweig")
        self.assertContains(response, "Englisch Nachhilfe Braunschweig")
        self.assertContains(response, "Deutsch Nachhilfe Braunschweig")
        self.assertContains(response, "Für Eltern")
        self.assertContains(response, "Für SchülerInnen")
        self.assertContains(response, "Für TutorInnen")
        self.assertContains(response, reverse("contact") + "?role=parent")
        self.assertContains(response, reverse("contact") + "?role=student")
        self.assertContains(response, reverse("contact") + "?role=tutor")
        self.assertContains(response, reverse("landing_eltern"))
        self.assertContains(response, reverse("landing_schuelerinnen"))
        self.assertContains(response, reverse("tutor_werden"))
        self.assertContains(response, "Mehr über BrainBoost erfahren", count=5)
        self.assertContains(response, "Für Eltern erklärt")
        self.assertContains(response, "Für SchülerInnen erklärt")
        self.assertContains(response, "Für Tutoren erklärt")
        self.assertContains(response, 'data-cta="nachhilfe-braunschweig-hero"')
        self.assertContains(response, 'data-cta="nachhilfe-braunschweig-parent"')
        self.assertContains(response, 'data-cta="nachhilfe-braunschweig-parent-explained"')
        self.assertContains(response, 'data-cta="nachhilfe-braunschweig-parent-home"')
        self.assertContains(response, 'data-cta="nachhilfe-braunschweig-student"')
        self.assertContains(response, 'data-cta="nachhilfe-braunschweig-student-explained"')
        self.assertContains(response, 'data-cta="nachhilfe-braunschweig-student-home"')
        self.assertContains(response, 'data-cta="nachhilfe-braunschweig-tutor"')
        self.assertContains(response, 'data-cta="nachhilfe-braunschweig-tutor-explained"')
        self.assertContains(response, 'data-cta="nachhilfe-braunschweig-tutor-home"')

    def test_homepage_hero_uses_ctas_instead_of_login_form(self):
        response = self.client.get(reverse("landing_page"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Willkommen bei")
        self.assertContains(response, "Kostenlose Probestunde buchen")
        self.assertContains(response, "Nachhilfe anfragen")
        self.assertContains(response, "Tutor:in werden")
        self.assertContains(response, "Zum Login / Registrieren", count=1)
        self.assertContains(response, reverse("contact") + "?role=parent")
        self.assertContains(response, reverse("contact") + "?role=student")
        self.assertContains(response, reverse("contact") + "?role=tutor")
        self.assertContains(response, reverse("login"))
        self.assertContains(response, "Die Registrierung erfolgt gemeinsam mit deinem/deiner Tutor:in.")
        self.assertContains(response, 'data-bb-language-popup')
        self.assertContains(response, 'data-bb-language-arena')
        self.assertContains(response, 'data-language-code="de"')
        self.assertContains(response, 'data-language-code="en"')
        self.assertContains(response, 'data-language-code="es"')
        self.assertContains(response, 'data-language-code="pl"')
        self.assertContains(response, 'data-bb-language-close')
        self.assertNotContains(response, 'class="language-orbit__button"')
        self.assertNotContains(response, 'action="%s"' % reverse("login"))
        self.assertNotContains(response, 'name="username"')
        self.assertNotContains(response, 'name="password"')

    def test_language_popup_is_rendered_on_public_landing_pages(self):
        for url_name in [
            "landing_page",
            "nachhilfe_anfrage",
            "tutor_werden",
            "landing_schuelerinnen",
        ]:
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'data-bb-language-popup')
                self.assertContains(response, 'data-bb-language-close')
                self.assertContains(response, "Sprache wählen")

    def test_role_landing_pages_link_to_prefilled_contact_form(self):
        parent_response = self.client.get(reverse("landing_eltern"))
        student_response = self.client.get(reverse("landing_schuelerinnen"))

        self.assertEqual(parent_response.status_code, 200)
        self.assertContains(parent_response, "Nachhilfe für Eltern in Braunschweig und online")
        self.assertContains(parent_response, reverse("contact") + "?role=parent")
        self.assertContains(parent_response, reverse("nachhilfe_anfrage"))
        self.assertContains(parent_response, 'data-cta="landing-eltern-hero"')
        self.assertContains(parent_response, 'data-cta="landing-eltern-bottom"')

        self.assertEqual(student_response.status_code, 200)
        self.assertContains(student_response, "Nachhilfe für SchülerInnen in Braunschweig und online")
        self.assertContains(student_response, reverse("contact") + "?role=student")
        self.assertContains(student_response, reverse("nachhilfe_anfrage"))
        self.assertContains(student_response, 'data-cta="landing-schuelerinnen-hero"')
        self.assertContains(student_response, 'data-cta="landing-schuelerinnen-bottom"')

    def test_contact_role_query_prefills_selected_role(self):
        for role in [Lead.Role.PARENT, Lead.Role.STUDENT, Lead.Role.TUTOR]:
            with self.subTest(role=role):
                response = self.client.get(reverse("contact") + f"?role={role}")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["form"].initial["role"], role)

    def test_contact_form_field_labels_are_translated(self):
        expectations = {
            "en": [
                "Phone number",
                "Preferred contact method",
                "Current status",
                "Grade levels",
                "Please select",
                "First and last name",
            ],
            "pl": [
                "Numer telefonu",
                "Preferowana forma kontaktu",
                "Aktualny status",
                "Poziomy klas",
                "Proszę wybrać",
                "Imię i nazwisko",
            ],
            "tr": [
                "Telefon numarası",
                "Tercih edilen iletişim yöntemi",
                "Mevcut durum",
                "Sınıf seviyeleri",
                "Lütfen seçin",
                "Ad ve soyad",
            ],
            "ru": [
                "Номер телефона",
                "Предпочтительный способ связи",
                "Текущий статус",
                "Классы",
                "Пожалуйста, выберите",
                "Имя и фамилия",
            ],
            "ar": [
                "رقم الهاتف",
                "طريقة التواصل المفضلة",
                "الحالة الحالية",
                "المراحل الدراسية",
                "يرجى الاختيار",
                "الاسم واللقب",
            ],
        }

        for language, texts in expectations.items():
            with self.subTest(language=language):
                response = self.client.get(
                    reverse("contact") + "?role=tutor",
                    HTTP_ACCEPT_LANGUAGE=language,
                )

                self.assertEqual(response.status_code, 200)
                for text in texts:
                    self.assertContains(response, text)
                self.assertNotContains(response, "Telefonnummer")
                self.assertNotContains(response, "Bevorzugte Kontaktart")
                self.assertNotContains(response, "Aktueller Status")
                self.assertNotContains(response, "Klassenstufen")

    def test_tutor_landing_links_to_prefilled_contact_form(self):
        response = self.client.get(reverse("tutor_werden"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Werde TutorIn bei BrainBoost")
        self.assertContains(response, "Flexible Nachhilfe geben")
        self.assertContains(response, reverse("contact") + "?role=tutor")
        self.assertContains(response, 'data-cta="tutor-bewerbung-hero"')
        self.assertContains(response, 'data-cta="tutor-bewerbung-bottom"')

    def test_legacy_tutorin_route_redirects_to_new_tutor_landing(self):
        response = self.client.get(reverse("tutorin_werden"))

        self.assertRedirects(response, reverse("tutor_werden"))

    @override_settings(META_PIXEL_ID="")
    def test_meta_pixel_is_not_rendered_without_pixel_id(self):
        response = self.client.get(reverse("landing_page"))

        self.assertNotContains(response, "connect.facebook.net")
        self.assertNotContains(response, "fbq('track', 'PageView')")

    @override_settings(COOKIEBOT_ID="", META_PIXEL_ID="123456789")
    def test_meta_pixel_is_not_rendered_without_cookiebot_id(self):
        response = self.client.get(reverse("landing_page"))

        self.assertNotContains(response, "connect.facebook.net")
        self.assertNotContains(response, "fbq('track', 'PageView')")

    @override_settings(COOKIEBOT_ID="34936317-2b98-4a57-82fc-98e5d67b4203", META_PIXEL_ID="123456789")
    def test_meta_pixel_pageview_is_rendered_with_pixel_id(self):
        response = self.client.get(reverse("landing_page"))

        self.assertContains(response, 'id="Cookiebot"')
        self.assertContains(response, 'data-cbid="34936317-2b98-4a57-82fc-98e5d67b4203"')
        self.assertContains(response, 'data-blockingmode="auto"')
        self.assertContains(response, 'type="text/plain" data-cookieconsent="marketing"')
        self.assertContains(response, "connect.facebook.net")
        self.assertContains(response, "fbq('set', 'autoConfig', false, '123456789')")
        self.assertContains(response, "fbq('init', '123456789')")
        self.assertContains(response, "fbq('track', 'PageView')")
        self.assertNotContains(response, "facebook.com/tr?id=123456789")

    @override_settings(COOKIEBOT_ID="34936317-2b98-4a57-82fc-98e5d67b4203", META_PIXEL_ID="123456789")
    def test_landing_pages_render_view_content_events(self):
        parent_response = self.client.get(reverse("nachhilfe_anfrage"))
        tutor_response = self.client.get(reverse("tutor_werden"))

        self.assertContains(parent_response, 'type="text/plain" data-cookieconsent="marketing"')
        self.assertContains(parent_response, 'fbq("track", "ViewContent"')
        self.assertContains(parent_response, "Nachhilfe Anfrage Landingpage")
        self.assertContains(parent_response, "parents_students")
        self.assertContains(tutor_response, "Tutor Bewerbung Landingpage")
        self.assertContains(tutor_response, "tutors")

    @override_settings(COOKIEBOT_ID="34936317-2b98-4a57-82fc-98e5d67b4203", META_PIXEL_ID="123456789")
    def test_lead_event_is_rendered_only_after_successful_submission(self):
        direct_response = self.client.get(reverse("lead_thanks_tutoring"))
        self.assertNotContains(direct_response, 'fbq("track", "Lead"')

        post_response = self.client.post(reverse("contact"), data=self._parent_data())
        self.assertEqual(post_response.status_code, 302)
        thanks_response = self.client.get(reverse("lead_thanks_tutoring"))
        self.assertContains(thanks_response, 'fbq("track", "Lead"')
        self.assertContains(thanks_response, "Nachhilfe Anfrage")
        self.assertContains(thanks_response, "parents_students")

        refresh_response = self.client.get(reverse("lead_thanks_tutoring"))
        self.assertNotContains(refresh_response, 'fbq("track", "Lead"')


class LeadAdminToolsTests(TestCase):
    def setUp(self):
        self.staff_user = CustomUser.objects.create_user(
            username="staff",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
            is_staff=True,
        )
        self.normal_user = CustomUser.objects.create_user(
            username="student",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
        )

    def _lead(self, **overrides):
        data = {
            "role": Lead.Role.PARENT,
            "name": "Maria Muster",
            "email": "maria@example.com",
            "preferred_contact": Lead.PreferredContact.EMAIL,
            "subject": "Mathe",
            "grade": "8. Klasse",
            "tutoring_type": Lead.TutoringType.ONLINE,
            "goal": "Noten verbessern",
            "urgency": "bald",
            "privacy_consent": True,
            "source": "website",
        }
        data.update(overrides)
        return Lead.objects.create(**data)

    def test_anonymous_users_cannot_see_lead_dashboard(self):
        response = self.client.get(reverse("lead_dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_non_staff_users_cannot_see_lead_dashboard(self):
        self.client.force_login(self.normal_user)

        response = self.client.get(reverse("lead_dashboard"))

        self.assertRedirects(response, reverse("dashboard"))

    def test_staff_users_can_see_lead_dashboard(self):
        lead = self._lead(
            role=Lead.Role.TUTOR,
            name="Tina Tutor",
            email="tina@example.com",
            phone="+49 176 123456",
            message="Ich möchte Nachhilfe geben.",
        )
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("lead_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lead-Zentrale")
        self.assertContains(response, "Neue Leads")
        self.assertContains(response, "Kontaktieren")
        self.assertContains(response, reverse("lead_mark_contacted", args=[lead.id]))
        self.assertContains(response, reverse("lead_update_status", args=[lead.id]))
        self.assertContains(response, reverse("lead_delete", args=[lead.id]))
        self.assertContains(response, 'name="status"')
        self.assertContains(response, "Entfernen")
        self.assertContains(response, 'data-email="tina@example.com"')
        self.assertContains(response, 'data-phone="+49 176 123456"')
        self.assertContains(response, 'data-goal="Noten verbessern"')
        self.assertContains(response, "Guten Tag")
        self.assertContains(response, "Gerne helfen wir...")
        self.assertContains(response, "Dein Ziel:")
        self.assertContains(response, "Deine Nachricht an uns war:")
        self.assertNotContains(response, "--- Nachricht der Anfrage ---")
        self.assertContains(response, "Zur TutorIn machen")

    def test_staff_user_can_mark_new_lead_as_contacted(self):
        lead = self._lead()
        self.client.force_login(self.staff_user)

        response = self.client.post(reverse("lead_mark_contacted", args=[lead.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], Lead.Status.CONTACTED)
        self.assertEqual(payload["status_label"], "Kontaktiert")
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.CONTACTED)
        self.assertIsNotNone(lead.contacted_at)
        self.assertIsNotNone(lead.last_status_change_at)

    def test_contact_action_does_not_downgrade_closed_leads(self):
        lead = self._lead(status=Lead.Status.WON)
        self.client.force_login(self.staff_user)

        response = self.client.post(reverse("lead_mark_contacted", args=[lead.id]))

        self.assertEqual(response.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.WON)

    def test_non_staff_users_cannot_mark_lead_as_contacted(self):
        lead = self._lead()
        self.client.force_login(self.normal_user)

        response = self.client.post(reverse("lead_mark_contacted", args=[lead.id]))

        self.assertEqual(response.status_code, 403)
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.NEW)

    def test_staff_user_can_update_lead_status_from_dashboard(self):
        lead = self._lead()
        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse("lead_update_status", args=[lead.id]),
            data={"status": Lead.Status.APPOINTMENT_PLANNED, "next": reverse("lead_dashboard")},
        )

        self.assertRedirects(response, reverse("lead_dashboard"))
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.APPOINTMENT_PLANNED)
        self.assertIsNotNone(lead.last_status_change_at)

    def test_staff_user_status_update_to_contacted_sets_contacted_at(self):
        lead = self._lead()
        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse("lead_update_status", args=[lead.id]),
            data={"status": Lead.Status.CONTACTED, "next": reverse("lead_dashboard")},
        )

        self.assertRedirects(response, reverse("lead_dashboard"))
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.CONTACTED)
        self.assertIsNotNone(lead.contacted_at)

    def test_non_staff_users_cannot_update_lead_status(self):
        lead = self._lead()
        self.client.force_login(self.normal_user)

        response = self.client.post(
            reverse("lead_update_status", args=[lead.id]),
            data={"status": Lead.Status.WON},
        )

        self.assertRedirects(response, reverse("dashboard"))
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.NEW)

    def test_staff_user_can_delete_lead_from_dashboard(self):
        lead = self._lead()
        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse("lead_delete", args=[lead.id]),
            data={"next": reverse("lead_dashboard")},
        )

        self.assertRedirects(response, reverse("lead_dashboard"))
        self.assertFalse(Lead.objects.filter(pk=lead.pk).exists())

    def test_non_staff_users_cannot_delete_lead(self):
        lead = self._lead()
        self.client.force_login(self.normal_user)

        response = self.client.post(reverse("lead_delete", args=[lead.id]))

        self.assertRedirects(response, reverse("dashboard"))
        self.assertTrue(Lead.objects.filter(pk=lead.pk).exists())

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST_USER="smtp-user",
        EMAIL_HOST_PASSWORD="smtp-password",
        DEFAULT_FROM_EMAIL="BrainBoost <brainboost.nachhilfe@gmail.com>",
        DEFAULT_REPLY_TO_EMAIL="brainboost.nachhilfe@gmail.com",
    )
    def test_staff_user_can_convert_tutor_lead_to_tutor_profile(self):
        lead = self._lead(
            role=Lead.Role.TUTOR,
            name="Tina Tutor",
            email="tina.tutor@example.com",
            phone="0176 123456",
            subject="Mathe, Physik",
            grade="5-10",
            teaching_subjects="Mathe, Physik",
            teaching_grades="5-10",
            weekly_availability="4 Stunden",
            experience_level=Lead.ExperienceLevel.SOME,
        )
        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse("lead_convert_to_tutor", args=[lead.id]),
            data={"next": reverse("lead_dashboard")},
        )

        self.assertRedirects(response, reverse("lead_dashboard"))
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.WON)
        self.assertTrue(lead.follow_up_done)
        self.assertIsNotNone(lead.converted_tutor)
        tutor_user = lead.converted_tutor.user
        self.assertEqual(tutor_user.role, CustomUser.Roles.TUTOR)
        self.assertEqual(tutor_user.email, "tina.tutor@example.com")
        self.assertEqual(tutor_user.first_name, "Tina")
        self.assertEqual(tutor_user.last_name, "Tutor")
        self.assertFalse(tutor_user.has_usable_password())
        self.assertEqual(lead.converted_tutor.phone_number, "0176 123456")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("tina.tutor@example.com", mail.outbox[0].to)
        self.assertIn("Passwort", mail.outbox[0].subject)
        self.assertIn("Profil vervollständigen", mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST_USER="smtp-user",
        EMAIL_HOST_PASSWORD="smtp-password",
    )
    @patch(
        "core.views.leads._send_set_password_email",
        side_effect=SMTPAuthenticationError(535, b"5.7.8 Error: authentication failed"),
    )
    def test_smtp_authentication_failure_keeps_tutor_and_shows_retryable_message(self, mocked_send):
        lead = self._lead(
            role=Lead.Role.TUTOR,
            name="Tina Tutor",
            email="tina.retry@example.com",
            phone="0176 123456",
            teaching_subjects="Mathe",
            teaching_grades="5-10",
            weekly_availability="4 Stunden",
            experience_level=Lead.ExperienceLevel.SOME,
        )
        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse("lead_convert_to_tutor", args=[lead.id]),
            data={"next": reverse("lead_dashboard")},
        )

        self.assertRedirects(response, reverse("lead_dashboard"))
        lead.refresh_from_db()
        self.assertIsNotNone(lead.converted_tutor)
        self.assertEqual(lead.converted_tutor.user.email, "tina.retry@example.com")
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("SMTP-Anmeldung fehlgeschlagen" in message for message in messages))
        self.assertFalse(any("5.7.8 Error" in message for message in messages))
        mocked_send.assert_called_once()

        dashboard_response = self.client.get(reverse("lead_dashboard"))
        self.assertContains(dashboard_response, "TutorIn angelegt")
        self.assertContains(dashboard_response, "Mail erneut senden")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST_USER="smtp-user",
        EMAIL_HOST_PASSWORD="smtp-password",
        DEFAULT_FROM_EMAIL="BrainBoost <brainboost.nachhilfe@gmail.com>",
    )
    def test_converted_tutor_lead_can_resend_password_mail(self):
        tutor_user = CustomUser.objects.create_user(
            username="converted_retry",
            email="converted.retry@example.com",
            role=CustomUser.Roles.TUTOR,
        )
        tutor = TutorProfile.objects.create(user=tutor_user)
        lead = self._lead(
            role=Lead.Role.TUTOR,
            name="Converted Retry",
            email="converted.retry@example.com",
            converted_tutor=tutor,
            status=Lead.Status.WON,
            follow_up_done=True,
        )
        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse("lead_convert_to_tutor", args=[lead.id]),
            data={"next": reverse("lead_dashboard")},
        )

        self.assertRedirects(response, reverse("lead_dashboard"))
        self.assertEqual(CustomUser.objects.filter(email="converted.retry@example.com").count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("converted.retry@example.com", mail.outbox[0].to)

    def test_non_tutor_leads_cannot_be_converted_to_tutor(self):
        lead = self._lead(role=Lead.Role.PARENT)
        self.client.force_login(self.staff_user)

        response = self.client.post(reverse("lead_convert_to_tutor", args=[lead.id]))

        self.assertRedirects(response, reverse("lead_dashboard"))
        self.assertFalse(CustomUser.objects.filter(email=lead.email, role=CustomUser.Roles.TUTOR).exists())
        lead.refresh_from_db()
        self.assertIsNone(lead.converted_tutor)

    def test_csv_export_is_staff_only_and_contains_expected_fields(self):
        self._lead(utm_campaign="eltern_mathe_braunschweig", internal_notes="Anrufen")

        anonymous_response = self.client.get(reverse("lead_export_csv"))
        self.assertEqual(anonymous_response.status_code, 302)

        self.client.force_login(self.normal_user)
        normal_response = self.client.get(reverse("lead_export_csv"))
        self.assertRedirects(normal_response, reverse("dashboard"))

        self.client.force_login(self.staff_user)
        staff_response = self.client.get(reverse("lead_export_csv"))

        self.assertEqual(staff_response.status_code, 200)
        self.assertEqual(staff_response["Content-Type"], "text/csv; charset=utf-8")
        csv_body = staff_response.content.decode("utf-8")
        self.assertIn("created_at,role,name,email,phone,preferred_contact", csv_body)
        self.assertIn("eltern_mathe_braunschweig", csv_body)
        self.assertIn("Anrufen", csv_body)

    def test_utm_campaign_grouping_uses_unknown_and_counts_statuses(self):
        self._lead(utm_campaign="eltern_mathe_braunschweig", status=Lead.Status.WON)
        self._lead(utm_campaign="eltern_mathe_braunschweig", status=Lead.Status.LOST)
        self._lead(utm_campaign="", status=Lead.Status.UNSUITABLE)

        rows = {row["campaign"]: row for row in _lead_campaign_stats(Lead.objects.all())}

        self.assertEqual(rows["eltern_mathe_braunschweig"]["total"], 2)
        self.assertEqual(rows["eltern_mathe_braunschweig"]["won"], 1)
        self.assertEqual(rows["eltern_mathe_braunschweig"]["lost"], 1)
        self.assertEqual(rows["unknown"]["total"], 1)
        self.assertEqual(rows["unknown"]["unsuitable"], 1)

    def test_campaign_link_builder_creates_valid_url(self):
        generated_url = _build_campaign_url(
            "https://www.nachhilfe-brainboost.de/nachhilfe-braunschweig/?existing=1",
            {
                "utm_source": "meta",
                "utm_medium": "paid_social",
                "utm_campaign": "eltern_mathe_braunschweig",
                "utm_content": "video_1",
                "utm_term": "",
                "role": Lead.Role.PARENT,
            },
        )

        parsed = urlsplit(generated_url)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "www.nachhilfe-brainboost.de")
        self.assertEqual(parsed.path, "/nachhilfe-braunschweig/")
        self.assertEqual(query["existing"], ["1"])
        self.assertEqual(query["utm_source"], ["meta"])
        self.assertEqual(query["utm_medium"], ["paid_social"])
        self.assertEqual(query["utm_campaign"], ["eltern_mathe_braunschweig"])
        self.assertEqual(query["utm_content"], ["video_1"])
        self.assertNotIn("utm_term", query)
        self.assertEqual(query["role"], [Lead.Role.PARENT])

    def test_campaign_link_builder_view_is_staff_only(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(
            reverse("campaign_link_builder"),
            {
                "base_url": "https://www.nachhilfe-brainboost.de/nachhilfe-braunschweig/",
                "utm_source": "meta",
                "utm_medium": "paid_social",
                "utm_campaign": "eltern_mathe_braunschweig",
                "utm_content": "video_1",
                "role": Lead.Role.PARENT,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "utm_campaign=eltern_mathe_braunschweig")
        self.assertContains(response, "role=parent")


class AdminHubTests(TestCase):
    def setUp(self):
        self.staff_user = CustomUser.objects.create_user(
            username="staff-admin-hub",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
            is_staff=True,
        )
        self.regular_user = CustomUser.objects.create_user(
            username="regular-admin-hub",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )

    def test_admin_hub_links_to_current_tools(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("admin_tasks"))

        self.assertEqual(response.status_code, 200)
        expected_links = {
            "Kampagnen-Link bauen": reverse("campaign_link_builder"),
            "Meta-Ads-Struktur": reverse("meta_ads_guide"),
            "Django-Admin": reverse("admin:index"),
            "Leads": reverse("lead_dashboard"),
        }
        for label, url in expected_links.items():
            with self.subTest(label=label):
                self.assertContains(response, label)
                self.assertContains(response, f'href="{url}"')

        self.assertNotContains(response, ">Ideen<", html=False)
        self.assertNotContains(response, ">Tasks<", html=False)
        self.assertNotContains(response, ">Kanban<", html=False)

    def test_leads_button_opens_lead_centre(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("admin_tasks"))

        self.assertContains(
            response,
            f'class="admin-tool-card admin-tool-card--featured" href="{reverse("lead_dashboard")}"',
        )

    def test_admin_tools_share_workspace_navigation(self):
        self.client.force_login(self.staff_user)

        for page_name in (
            "admin_tasks",
            "campaign_link_builder",
            "meta_ads_guide",
            "lead_dashboard",
        ):
            with self.subTest(page_name=page_name):
                response = self.client.get(reverse(page_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, reverse("admin_tasks"))
                self.assertContains(response, reverse("campaign_link_builder"))
                self.assertContains(response, reverse("meta_ads_guide"))
                self.assertContains(response, reverse("admin:index"))
                self.assertContains(response, reverse("lead_dashboard"))

    def test_admin_hub_remains_restricted_to_admins(self):
        self.client.force_login(self.regular_user)

        response = self.client.get(reverse("admin_tasks"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))

    def test_removed_task_actions_are_unavailable(self):
        self.client.force_login(self.staff_user)

        self.assertEqual(self.client.post(reverse("admin_tasks")).status_code, 405)
        self.assertEqual(self.client.post("/admins/tasks/1/status/").status_code, 404)


class LearningMaterialFormTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="tutor-material",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        self.tutor = TutorProfile.objects.create(user=self.tutor_user)
        self.student_one_user = CustomUser.objects.create_user(
            username="student-material-1",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
            first_name="Anna",
            last_name="Eins",
        )
        self.student_two_user = CustomUser.objects.create_user(
            username="student-material-2",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
            first_name="Ben",
            last_name="Zwei",
        )
        self.student_one = StudentProfile.objects.create(user=self.student_one_user)
        self.student_two = StudentProfile.objects.create(user=self.student_two_user)
        self.student_one.assigned_tutors.add(self.tutor)
        self.student_two.assigned_tutors.add(self.tutor)
        self.task_one = LearningMaterial.objects.create(
            student=self.student_one,
            uploaded_by=self.tutor,
            kind=LearningMaterial.Kind.TASK,
            file="materials/student_1/task-one.pdf",
        )
        self.task_two = LearningMaterial.objects.create(
            student=self.student_two,
            uploaded_by=self.tutor,
            kind=LearningMaterial.Kind.TASK,
            file="materials/student_2/task-two.pdf",
        )

    def test_solution_related_task_queryset_is_filtered_by_selected_student(self):
        form = LearningMaterialForm(
            data={"student": str(self.student_one.id)},
            allowed_students=StudentProfile.objects.filter(assigned_tutors=self.tutor),
            kind=LearningMaterial.Kind.SOLUTION,
            tutor_profile=self.tutor,
        )

        self.assertQuerySetEqual(
            form.fields["related_task"].queryset,
            [self.task_one],
            transform=lambda item: item,
        )

    def test_solution_related_task_options_have_student_ids_for_client_filtering(self):
        form = LearningMaterialForm(
            allowed_students=StudentProfile.objects.filter(assigned_tutors=self.tutor),
            kind=LearningMaterial.Kind.SOLUTION,
            tutor_profile=self.tutor,
        )

        html = form.as_p()

        self.assertIn('data-material-student-select="true"', html)
        self.assertIn('data-related-task-select="true"', html)
        self.assertIn(f'data-student-id="{self.student_one.id}"', html)
        self.assertIn(f'data-student-id="{self.student_two.id}"', html)


class InvoiceGenerateFormTests(TestCase):
    def setUp(self):
        self.student_user = CustomUser.objects.create_user(
            username="student1",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
        )
        self.student = StudentProfile.objects.create(user=self.student_user)

    def test_discount_value_requires_type(self):
        form = InvoiceGenerateForm(
            data={
                "student": self.student.pk,
                "period": "2026-03",
                "discount_value": "10.00",
                "discount_type": "",
            },
            allowed_students=StudentProfile.objects.filter(pk=self.student.pk),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("discount_type", form.errors)

    def test_percent_discount_must_not_exceed_hundred(self):
        form = InvoiceGenerateForm(
            data={
                "student": self.student.pk,
                "period": "2026-03",
                "discount_value": "120.00",
                "discount_type": Invoice.DiscountType.PERCENT,
            },
            allowed_students=StudentProfile.objects.filter(pk=self.student.pk),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("discount_value", form.errors)

    def test_period_field_is_select_with_billable_month_options(self):
        tutor_user = CustomUser.objects.create_user(
            username="invoice_month_tutor",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        tutor = TutorProfile.objects.create(user=tutor_user)
        Lesson.objects.create(
            tutor=tutor,
            student=self.student,
            date=date(2026, 3, 10),
            time=time(15, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.COMPLETED,
        )

        form = InvoiceGenerateForm(
            allowed_students=StudentProfile.objects.filter(pk=self.student.pk),
        )

        self.assertIsInstance(form.fields["period"].widget, forms.Select)
        self.assertIn(("2026-03", "März 2026"), list(form.fields["period"].choices))


class EmailOrUsernameLoginTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="login_user",
            email="login.user@example.com",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )

    def test_authenticate_with_username(self):
        auth_user = authenticate(username="login_user", password="test12345")
        self.assertIsNotNone(auth_user)
        self.assertEqual(auth_user.pk, self.user.pk)

    def test_authenticate_with_email(self):
        auth_user = authenticate(username="login.user@example.com", password="test12345")
        self.assertIsNotNone(auth_user)
        self.assertEqual(auth_user.pk, self.user.pk)


class TutorBankDataReminderTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="tutor_missing_bank_data",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        self.tutor_profile = TutorProfile.objects.create(user=self.user)

    def test_dashboard_shows_bank_data_popup_once_per_login_session(self):
        logged_in = self.client.login(username="tutor_missing_bank_data", password="test12345")
        self.assertTrue(logged_in)

        first_response = self.client.get(reverse("dashboard"))
        self.assertEqual(first_response.status_code, 200)
        self.assertTrue(first_response.context.get("show_bank_data_popup"))
        self.assertEqual(
            first_response.context.get("missing_tutor_bank_fields"),
            ["KontoinhaberIn", "Bankname", "IBAN", "BIC", "Steuernummer"],
        )

        second_response = self.client.get(reverse("dashboard"))
        self.assertEqual(second_response.status_code, 200)
        self.assertFalse(second_response.context.get("show_bank_data_popup", False))

    def test_dashboard_does_not_show_popup_when_bank_data_complete(self):
        self.tutor_profile.account_holder = "Max Mustermann"
        self.tutor_profile.bank_name = "Sparkasse"
        self.tutor_profile.iban = "DE44500105175407324931"
        self.tutor_profile.bic = "DEUTDEFFXXX"
        self.tutor_profile.tax_number = "12/345/67890"
        self.tutor_profile.save()

        logged_in = self.client.login(username="tutor_missing_bank_data", password="test12345")
        self.assertTrue(logged_in)

        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context.get("show_bank_data_popup", False))
        self.assertEqual(response.context.get("missing_tutor_bank_fields"), [])

    def test_dashboard_shows_tax_number_reminder_after_four_weeks(self):
        self.tutor_profile.account_holder = "Max Mustermann"
        self.tutor_profile.bank_name = "Sparkasse"
        self.tutor_profile.iban = "DE44500105175407324931"
        self.tutor_profile.bic = "DEUTDEFFXXX"
        self.tutor_profile.tax_number_pending = True
        self.tutor_profile.save()
        self.user.date_joined = timezone.now() - timedelta(days=29)
        self.user.save(update_fields=["date_joined"])

        self.client.login(username="tutor_missing_bank_data", password="test12345")
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get("show_tax_number_popup"))

    def test_dashboard_pauses_active_tutor_after_two_months_without_tax_number(self):
        self.tutor_profile.account_holder = "Max Mustermann"
        self.tutor_profile.bank_name = "Sparkasse"
        self.tutor_profile.iban = "DE44500105175407324931"
        self.tutor_profile.bic = "DEUTDEFFXXX"
        self.tutor_profile.tax_number_pending = True
        self.tutor_profile.status = TutorProfile.Status.ACTIVE
        self.tutor_profile.save()
        self.user.date_joined = timezone.now() - timedelta(days=61)
        self.user.save(update_fields=["date_joined"])

        self.client.login(username="tutor_missing_bank_data", password="test12345")
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.tutor_profile.refresh_from_db()
        self.assertEqual(self.tutor_profile.status, TutorProfile.Status.PAUSED)
        self.assertFalse(response.context.get("tutor_can_create_accounts"))

    def test_pending_tax_number_is_not_reported_as_missing_bank_field(self):
        self.tutor_profile.tax_number_pending = True
        self.tutor_profile.save(update_fields=["tax_number_pending"])

        self.client.login(username="tutor_missing_bank_data", password="test12345")
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Steuernummer", response.context.get("missing_tutor_bank_fields"))


class ProfileNumberTests(TestCase):
    def test_profiles_receive_role_specific_numbers(self):
        parent_user = CustomUser.objects.create_user(
            username="number_parent",
            role=CustomUser.Roles.PARENT,
        )
        student_user = CustomUser.objects.create_user(
            username="number_student",
            role=CustomUser.Roles.STUDENT,
        )
        independent_user = CustomUser.objects.create_user(
            username="number_independent_student",
            role=CustomUser.Roles.INDEPENDENT_STUDENT,
        )
        tutor_user = CustomUser.objects.create_user(
            username="number_tutor",
            role=CustomUser.Roles.TUTOR,
        )

        parent = ParentProfile.objects.create(user=parent_user)
        student = StudentProfile.objects.create(user=student_user)
        independent_student = StudentProfile.objects.create(user=independent_user)
        tutor = TutorProfile.objects.create(user=tutor_user)

        self.assertEqual(parent.customer_number, "ELT-000-001")
        self.assertEqual(student.profile_number, "SCHU-000-001")
        self.assertEqual(independent_student.profile_number, "STUD-000-001")
        self.assertEqual(tutor.tutor_number, "TUT-000-001")

    def test_profile_page_shows_tutor_number(self):
        user = CustomUser.objects.create_user(
            username="number_profile_tutor",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        tutor = TutorProfile.objects.create(user=user)

        self.client.login(username="number_profile_tutor", password="test12345")
        response = self.client.get(reverse("profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TutorInnennummer")
        self.assertContains(response, tutor.tutor_number)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ProfileEmailChangeTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="email_change_parent",
            password="test12345",
            role=CustomUser.Roles.PARENT,
            first_name="Erika",
            last_name="Mustermann",
            email="alt@example.com",
        )
        ParentProfile.objects.create(user=self.user)

    def test_profile_email_change_requires_confirmation_before_replacing_email(self):
        self.client.login(username="email_change_parent", password="test12345")
        response = self.client.post(
            reverse("profile"),
            data={
                "username": self.user.username,
                "first_name": "Erika",
                "last_name": "Mustermann",
                "email": "neu@example.com",
                "phone_number": "0176 123456",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "alt@example.com")
        self.assertEqual(self.user.pending_email, "neu@example.com")
        self.assertIsNotNone(self.user.pending_email_requested_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["neu@example.com"])

        match = re.search(r"http://testserver(?P<path>/profil/email/bestaetigen/[^\s]+)", mail.outbox[0].body)
        self.assertIsNotNone(match)
        confirm_response = self.client.get(match.group("path"))

        self.assertEqual(confirm_response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "neu@example.com")
        self.assertEqual(self.user.pending_email, "")
        self.assertIsNone(self.user.pending_email_requested_at)

    def test_profile_page_shows_pending_email_notice(self):
        self.user.pending_email = "wartet@example.com"
        self.user.pending_email_requested_at = timezone.now()
        self.user.save(update_fields=["pending_email", "pending_email_requested_at"])

        self.client.login(username="email_change_parent", password="test12345")
        response = self.client.get(reverse("profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ausstehende Bestätigung: wartet@example.com")


class TutorProfileBankFieldValidationTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="tutor_profile_form",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
            email="tutor.profile@example.com",
        )
        TutorProfile.objects.create(user=self.user)

    def _base_form_data(self):
        return {
            "username": self.user.username,
            "first_name": "Tina",
            "last_name": "Tutor",
            "email": self.user.email,
            "phone_number": "0176 123456",
            "address": "Tutorstrasse 1, Braunschweig",
            "account_holder": "Tina Tutor",
            "bank_name": "Sparkasse",
            "iban": "DE44500105175407324931",
            "bic": "DEUTDEFFXXX",
            "tax_number": "12/345/67890",
        }

    def test_tutor_profile_required_fields_are_marked_on_profile_page(self):
        self.client.login(username="tutor_profile_form", password="test12345")

        response = self.client.get(reverse("profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TutorInnennummer")
        self.assertContains(response, self.user.tutor_profile.tutor_number)
        self.assertContains(response, 'class="required-marker"', count=9)
        self.assertContains(response, 'class="profile-field-info"')
        self.assertContains(response, 'title="betriebliche Steuernummer')
        self.assertContains(
            response,
            "betriebliche Steuernummer im 13-stelligen ELSTER-/Bundesformat oder Steuernummer im normalen Bescheidformat bitte einfügen",
        )
        self.assertContains(response, "Fragebogen ausgefüllt und warte auf Steuernummer")
        self.assertNotContains(response, "Selbständigkeit verifiziert")

    def test_tutor_profile_requires_contact_address_and_bank_fields(self):
        data = self._base_form_data()
        for field_name in ["email", "phone_number", "address", "account_holder", "bank_name", "iban", "bic", "tax_number"]:
            data[field_name] = ""
        form = TutorProfileForm(data=data, user=self.user)

        self.assertFalse(form.is_valid())
        for field_name in ["email", "phone_number", "address", "account_holder", "bank_name", "iban", "bic", "tax_number"]:
            self.assertIn(field_name, form.errors)

    def test_profile_form_normalizes_iban_and_bic(self):
        data = self._base_form_data()
        data["iban"] = "de44-5001 0517 5407 3249 31"
        data["bic"] = "deut deff xxx"
        form = TutorProfileForm(data=data, user=self.user)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["iban"], "DE44500105175407324931")
        self.assertEqual(form.cleaned_data["bic"], "DEUTDEFFXXX")

    def test_profile_form_accepts_normal_tax_number_format(self):
        data = self._base_form_data()
        data["tax_number"] = "12/345/67890"
        form = TutorProfileForm(data=data, user=self.user)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["tax_number"], "12/345/67890")

    def test_profile_form_accepts_13_digit_tax_number_format(self):
        data = self._base_form_data()
        data["tax_number"] = "1234567890123"
        form = TutorProfileForm(data=data, user=self.user)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["tax_number"], "1234567890123")

    def test_profile_form_rejects_invalid_tax_number_format(self):
        data = self._base_form_data()
        data["tax_number"] = "123/45"
        form = TutorProfileForm(data=data, user=self.user)

        self.assertFalse(form.is_valid())
        self.assertIn("tax_number", form.errors)

    def test_profile_form_allows_empty_tax_number_when_questionnaire_is_pending(self):
        data = self._base_form_data()
        data["tax_number"] = ""
        data["tax_number_pending"] = "on"
        form = TutorProfileForm(data=data, user=self.user)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["tax_number"], "")
        self.assertTrue(form.cleaned_data["tax_number_pending"])

    def test_profile_form_rejects_invalid_bic_length(self):
        data = self._base_form_data()
        data["bic"] = "DEUTDEFFXX"
        form = TutorProfileForm(data=data, user=self.user)

        self.assertFalse(form.is_valid())
        self.assertIn("bic", form.errors)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="BrainBoost <brainboost.nachhilfe@gmail.com>",
    DEFAULT_REPLY_TO_EMAIL="brainboost.nachhilfe@gmail.com",
)
class BroadcastEmailTests(TestCase):
    def setUp(self):
        self.admin_user = CustomUser.objects.create_user(
            username="admin_sender",
            email="admin.sender@example.com",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
            is_superuser=True,
            is_staff=True,
        )
        self.tutor_user = CustomUser.objects.create_user(
            username="tutor_receiver",
            email="tutor.receiver@example.com",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        self.staff_admin_user = CustomUser.objects.create_user(
            username="staff_admin_sender",
            email="staff.admin@example.com",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
            is_staff=True,
        )
        self.parent_user = CustomUser.objects.create_user(
            username="parent_receiver",
            email="parent.receiver@example.com",
            password="test12345",
            role=CustomUser.Roles.PARENT,
        )

    def test_admin_can_send_broadcast_to_tutors(self):
        logged_in = self.client.login(username="admin_sender", password="test12345")
        self.assertTrue(logged_in)

        response = self.client.post(
            reverse("broadcast_email_send"),
            data={
                "audience": "tutors",
                "subject": "Team Info",
                "message": "Bitte morgen an die neuen Zeiten denken.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("broadcast_email_send"))
        recipients = {email.to[0] for email in mail.outbox}
        self.assertIn("admin.sender@example.com", recipients)
        self.assertIn("tutor.receiver@example.com", recipients)
        self.assertNotIn("parent.receiver@example.com", recipients)
        self.assertEqual(mail.outbox[0].from_email, "BrainBoost <brainboost.nachhilfe@gmail.com>")
        self.assertEqual(mail.outbox[0].reply_to, ["brainboost.nachhilfe@gmail.com"])

    def test_non_admin_cannot_send_broadcast(self):
        logged_in = self.client.login(username="tutor_receiver", password="test12345")
        self.assertTrue(logged_in)

        response = self.client.post(
            reverse("broadcast_email_send"),
            data={
                "audience": "all",
                "subject": "Info",
                "message": "Test",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))
        self.assertEqual(len(mail.outbox), 0)

    def test_staff_admin_can_send_broadcast(self):
        logged_in = self.client.login(username="staff_admin_sender", password="test12345")
        self.assertTrue(logged_in)

        response = self.client.post(
            reverse("broadcast_email_send"),
            data={
                "audience": "parents",
                "subject": "Eltern-Info",
                "message": "Bitte die neuen Termine im Portal prüfen.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("broadcast_email_send"))
        recipients = {email.to[0] for email in mail.outbox}
        self.assertEqual(recipients, {"parent.receiver@example.com"})


class InvoiceDiscountContextTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="tutor1",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
            first_name="Test",
            last_name="Tutor",
        )
        self.student_user = CustomUser.objects.create_user(
            username="student2",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
            first_name="Test",
            last_name="Student",
        )
        self.tutor = TutorProfile.objects.create(user=self.tutor_user)
        self.student = StudentProfile.objects.create(user=self.student_user)

    def test_build_invoice_context_applies_percent_discount_to_total(self):
        lesson = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=date(2026, 3, 10),
            time=time(15, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.COMPLETED,
        )

        context = _build_invoice_pdf_context(
            tutor_profile=self.tutor,
            student=self.student,
            period_start=date(2026, 3, 1),
            lessons=[lesson],
            discount_type=Invoice.DiscountType.PERCENT,
            discount_value=Decimal("10.00"),
        )

        self.assertEqual(context["subtotal_amount"], Decimal("25.00"))
        self.assertEqual(context["discount_amount"], Decimal("2.50"))
        self.assertEqual(context["total_amount"], Decimal("22.50"))

    def test_build_invoice_context_rejects_fixed_discount_above_subtotal(self):
        lesson = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=date(2026, 3, 11),
            time=time(15, 0),
            duration_minutes=45,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.COMPLETED,
        )

        with self.assertRaisesMessage(
            ValueError,
            "Der Rabatt in EUR darf die Rechnungssumme nicht übersteigen.",
        ):
            _build_invoice_pdf_context(
                tutor_profile=self.tutor,
                student=self.student,
                period_start=date(2026, 3, 1),
                lessons=[lesson],
                discount_type=Invoice.DiscountType.FIXED,
                discount_value=Decimal("20.00"),
            )

    def test_build_invoice_context_adds_note_for_late_cancelled_lessons(self):
        lesson = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=date(2026, 3, 12),
            time=time(15, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.CANCELLED,
            cancellation_chargeable=True,
            cancelled_at=timezone.now(),
            cancellation_reason="Krankheit",
        )

        context = _build_invoice_pdf_context(
            tutor_profile=self.tutor,
            student=self.student,
            period_start=date(2026, 3, 1),
            lessons=[lesson],
        )

        self.assertIn("Zu spät storniert (kostenpflichtig)", context["line_items"][0]["notes"])

    def test_build_invoice_context_formats_iban_for_display(self):
        self.tutor.iban = "DE44500105175407324931"
        self.tutor.save(update_fields=["iban"])
        lesson = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=date(2026, 3, 13),
            time=time(15, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.COMPLETED,
        )

        context = _build_invoice_pdf_context(
            tutor_profile=self.tutor,
            student=self.student,
            period_start=date(2026, 3, 1),
            lessons=[lesson],
        )

        self.assertEqual(context["iban"], "DE44 5001 0517 5407 3249 31")


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    MEDIA_ROOT=tempfile.mkdtemp(),
)
class InvoiceNumberingTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="invoice_number_tutor",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
            first_name="Tina",
            last_name="Tutorin",
        )
        self.student_user = CustomUser.objects.create_user(
            username="invoice_number_student",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
            first_name="Max",
            last_name="Mustermann",
        )
        self.tutor = TutorProfile.objects.create(user=self.tutor_user)
        self.student = StudentProfile.objects.create(user=self.student_user)
        self.student.assigned_tutors.add(self.tutor)
        self.lesson = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=date(2026, 5, 8),
            time=time(15, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.COMPLETED,
        )
        self.client.login(username="invoice_number_tutor", password="test12345")

    def test_generated_invoices_receive_unique_number_and_filename(self):
        captured_numbers = []

        def fake_generate_invoice_pdf(*args, **kwargs):
            captured_numbers.append(kwargs["invoice_context"]["invoice_number"])
            return b"%PDF-1.4\n%fake\n"

        with patch("core.views.invoices._generate_invoice_pdf", side_effect=fake_generate_invoice_pdf):
            for _ in range(2):
                response = self.client.post(
                    reverse("invoice_upload"),
                    data={
                        "action": "generate",
                        "student": str(self.student.id),
                        "period": "2026-05",
                        "discount_type": "",
                        "discount_value": "",
                    },
                )
                self.assertEqual(response.status_code, 302)

        invoices = list(Invoice.objects.order_by("invoice_number"))
        self.assertEqual([invoice.invoice_number for invoice in invoices], [1, 2])
        self.assertEqual(captured_numbers, ["RE-A01-0001", "RE-A01-0002"])
        self.assertEqual(
            invoices[0].display_filename,
            "RE-A01-0001_Mai26_Max_Mustermann.pdf",
        )
        self.assertEqual(
            invoices[1].display_filename,
            "RE-A01-0002_Mai26_Max_Mustermann.pdf",
        )

    def test_invoice_filename_uses_formatted_number_month_and_sanitized_name(self):
        invoice = Invoice(
            student=self.student,
            uploaded_by=self.tutor,
            invoice_number=1,
            billing_year=2026,
            billing_month=5,
        )

        self.assertEqual(
            _invoice_filename(invoice),
            "RE-A01-0001_Mai26_Max_Mustermann.pdf",
        )

    def test_invoice_pdf_template_shows_number_above_date_not_filename(self):
        context = _build_invoice_pdf_context(
            tutor_profile=self.tutor,
            student=self.student,
            period_start=date(2026, 5, 1),
            lessons=[self.lesson],
            invoice_number=_format_invoice_number(1),
        )

        html = render_to_string(
            "invoice_pdf.html",
            {
                **context,
                "logo_url": "",
                "shababa_font_woff2_url": "",
                "shababa_font_woff_url": "",
                "payment_qr_url": None,
            },
        )

        number_index = html.index("Rechnungsnummer: RE-A01-0001")
        date_index = html.index("Rechnungsdatum:")
        self.assertLess(number_index, date_index)
        self.assertNotIn("RE-A01-0001_Mai26_Max_Mustermann.pdf", html)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    MEDIA_ROOT=tempfile.mkdtemp(),
)
class StripeWebhookSecurityTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="stripe_webhook_tutor",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        self.student_user = CustomUser.objects.create_user(
            username="stripe_webhook_student",
            password="test12345",
            role=CustomUser.Roles.INDEPENDENT_STUDENT,
        )
        self.tutor = TutorProfile.objects.create(user=self.tutor_user)
        self.student = StudentProfile.objects.create(user=self.student_user)
        self.invoice = Invoice.objects.create(
            student=self.student,
            uploaded_by=self.tutor,
            approved_by=self.tutor,
            approved_at=timezone.now(),
            amount_total=Decimal("25.00"),
            file=SimpleUploadedFile("rechnung.pdf", b"%PDF-1.4\n"),
        )

    @override_settings(STRIPE_SECRET_KEY="sk_test_123", STRIPE_WEBHOOK_SECRET="")
    @patch("core.views.invoices._stripe_client")
    def test_webhook_rejects_events_without_configured_secret(self, mocked_stripe_client):
        stripe = SimpleNamespace(
            Webhook=SimpleNamespace(construct_event=Mock()),
            Event=SimpleNamespace(construct_from=Mock()),
        )
        mocked_stripe_client.return_value = stripe

        response = self.client.post(
            reverse("stripe_webhook"),
            data=json.dumps({"type": "checkout.session.completed"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 500)
        stripe.Webhook.construct_event.assert_not_called()
        stripe.Event.construct_from.assert_not_called()
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, Invoice.PaymentStatus.OPEN)

    @override_settings(
        STRIPE_SECRET_KEY="sk_test_123",
        STRIPE_WEBHOOK_SECRET="whsec_test_123",
    )
    @patch("core.views.invoices.notify_invoice_payment_received_tutor")
    @patch("core.views.invoices._stripe_client")
    def test_webhook_uses_signature_secret_and_marks_invoice_paid(
        self,
        mocked_stripe_client,
        mocked_notify,
    ):
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_123",
                    "payment_intent": "pi_test_123",
                    "metadata": {"invoice_id": str(self.invoice.id)},
                }
            },
        }
        stripe = SimpleNamespace(
            Webhook=SimpleNamespace(construct_event=Mock(return_value=event)),
            Event=SimpleNamespace(construct_from=Mock()),
        )
        mocked_stripe_client.return_value = stripe

        response = self.client.post(
            reverse("stripe_webhook"),
            data=json.dumps(event),
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="signed-payload",
        )

        self.assertEqual(response.status_code, 200)
        stripe.Webhook.construct_event.assert_called_once()
        payload, signature, secret = stripe.Webhook.construct_event.call_args.args
        self.assertEqual(json.loads(payload.decode("utf-8")), event)
        self.assertEqual(signature, "signed-payload")
        self.assertEqual(secret, "whsec_test_123")
        stripe.Event.construct_from.assert_not_called()
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, Invoice.PaymentStatus.PAID)
        self.assertEqual(self.invoice.payment_method, Invoice.PaymentMethod.ONLINE)
        self.assertEqual(self.invoice.stripe_checkout_session_id, "cs_test_123")
        self.assertEqual(self.invoice.stripe_payment_intent_id, "pi_test_123")
        mocked_notify.assert_called_once()


class InvoicePdfTemplateLayoutTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="invoice_layout_tutor",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
            first_name="Tina",
            last_name="Tutorin",
        )
        self.student_user = CustomUser.objects.create_user(
            username="invoice_layout_student",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
            first_name="Max",
            last_name="Mustermann",
        )
        self.tutor = TutorProfile.objects.create(
            user=self.tutor_user,
            tax_number="12/345/67890",
        )
        self.student = StudentProfile.objects.create(
            user=self.student_user,
            address="Musterstraße 12\n38100 Braunschweig",
        )

    def _lesson(self, duration_minutes):
        return Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=date(2026, 5, duration_minutes // 15),
            time=time(15, 0),
            duration_minutes=duration_minutes,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.COMPLETED,
        )

    def _render_invoice_html(self):
        context = _build_invoice_pdf_context(
            tutor_profile=self.tutor,
            student=self.student,
            period_start=date(2026, 5, 1),
            lessons=[self._lesson(45), self._lesson(60), self._lesson(90)],
            invoice_number=_format_invoice_number(1),
        )
        return render_to_string(
            "invoice_pdf.html",
            {
                **context,
                "logo_url": "",
                "shababa_font_woff2_url": "",
                "shababa_font_woff_url": "",
                "payment_qr_url": None,
            },
        )

    def test_line_items_show_base_price_column_for_supported_durations(self):
        html = self._render_invoice_html()

        self.assertIn("Grundpreis", html)
        self.assertIn("45 Min.", html)
        self.assertIn("19,00 EUR", html)
        self.assertIn("60 Min.", html)
        self.assertIn("25,00 EUR", html)
        self.assertIn("90 Min.", html)
        self.assertIn("36,00 EUR", html)
        self.assertNotIn("Grundpreis 19,00 EUR", html)
        self.assertNotIn("45 Min. = 19,00 EUR", html)
        self.assertNotIn("60 Min. = 25,00 EUR", html)
        self.assertNotIn("90 Min. = 36,00 EUR", html)

    @override_settings(BRAINBOOST_TAX_NUMBER="98/765/43210")
    def test_header_student_address_and_page_footer_are_rendered(self):
        html = self._render_invoice_html()

        self.assertIn(
            "Inh.: Kiara Puppe, Anschrift: Karl-Schmidt-Str. 20, 38114 Braunschweig",
            html,
        )
        self.assertIn("text-decoration: underline", html)
        self.assertIn("<strong>Anschrift:</strong>", html)
        self.assertIn("<strong>SchülerIn/StudentIn:</strong>", html)
        self.assertIn("<strong>Kundennummer:</strong>", html)
        self.assertIn(self.student.profile_number, html)
        self.assertLess(html.index("Max Mustermann"), html.index("Leistungsdaten"))
        self.assertIn("Musterstraße 12", html)
        self.assertIn("38100 Braunschweig", html)
        self.assertIn("Steuernummer TutorIn: 12/345/67890", html)
        self.assertIn("12/345/67890", html)
        self.assertIn("BrainBoost Steuernummer: 98/765/43210", html)
        self.assertIn("Gemäß § 19 UStG wird keine Umsatzsteuer berechnet.", html)
        self.assertIn("@bottom-center", html)

    def test_address_uses_parent_name_when_student_has_parent_and_splits_commas(self):
        parent_user = CustomUser.objects.create_user(
            username="invoice_layout_parent",
            password="test12345",
            role=CustomUser.Roles.PARENT,
            first_name="Erika",
            last_name="Mustermann",
        )
        parent = ParentProfile.objects.create(user=parent_user)
        self.student.parents.add(parent)
        self.student.address = "Karl-Schmidt-Straße 1, Braunschweig-Nordstadt, Germany"
        self.student.save(update_fields=["address"])

        context = _build_invoice_pdf_context(
            tutor_profile=self.tutor,
            student=self.student,
            period_start=date(2026, 5, 1),
            lessons=[self._lesson(60)],
        )

        self.assertEqual(context["recipient_name"], "Erika Mustermann")
        self.assertEqual(context["customer_number"], parent.customer_number)
        self.assertEqual(
            context["student_address"],
            "Karl-Schmidt-Straße 1\nBraunschweig-Nordstadt\nGermany",
        )

    def test_address_uses_student_name_when_student_has_no_parent(self):
        context = _build_invoice_pdf_context(
            tutor_profile=self.tutor,
            student=self.student,
            period_start=date(2026, 5, 1),
            lessons=[self._lesson(60)],
        )

        self.assertEqual(context["recipient_name"], "Max Mustermann")
        self.assertEqual(context["customer_number"], self.student.profile_number)

    def test_invoice_customer_number_uses_student_number_for_independent_student(self):
        independent_user = CustomUser.objects.create_user(
            username="invoice_layout_independent",
            password="test12345",
            role=CustomUser.Roles.INDEPENDENT_STUDENT,
            first_name="Sina",
            last_name="Studentin",
        )
        independent_student = StudentProfile.objects.create(
            user=independent_user,
            address="Campusweg 2\n38100 Braunschweig",
        )
        lesson = Lesson.objects.create(
            tutor=self.tutor,
            student=independent_student,
            date=date(2026, 5, 10),
            time=time(15, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.COMPLETED,
        )

        context = _build_invoice_pdf_context(
            tutor_profile=self.tutor,
            student=independent_student,
            period_start=date(2026, 5, 1),
            lessons=[lesson],
        )

        self.assertTrue(independent_student.profile_number.startswith("STUD-"))
        self.assertEqual(context["customer_number"], independent_student.profile_number)


class GoogleRoutesDistanceTests(TestCase):
    @override_settings(GOOGLE_ROUTES_API_KEY="routes-test-key")
    @patch("core.views.common.urlopen")
    def test_google_driving_distance_km_uses_routes_api_distance_meters(self, mocked_urlopen):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps({"routes": [{"distanceMeters": 12345}]}).encode("utf-8")

        mocked_urlopen.return_value = Response()

        distance = _google_driving_distance_km(
            "Tutorstrasse 1, Braunschweig",
            "Schuelerstrasse 2, Braunschweig",
        )

        self.assertEqual(distance, Decimal("12.35"))
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://routes.googleapis.com/directions/v2:computeRoutes")
        self.assertEqual(request.headers["X-goog-api-key"], "routes-test-key")
        self.assertEqual(request.headers["X-goog-fieldmask"], "routes.distanceMeters")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["origin"]["address"], "Tutorstrasse 1, Braunschweig")
        self.assertEqual(payload["destination"]["address"], "Schuelerstrasse 2, Braunschweig")
        self.assertEqual(payload["travelMode"], "DRIVE")

    @override_settings(GOOGLE_ROUTES_API_KEY="")
    @patch("core.views.common.urlopen")
    def test_google_driving_distance_km_skips_without_api_key(self, mocked_urlopen):
        distance = _google_driving_distance_km(
            "Tutorstrasse 1, Braunschweig",
            "Schuelerstrasse 2, Braunschweig",
        )

        self.assertIsNone(distance)
        mocked_urlopen.assert_not_called()

    @patch("core.views.common._google_driving_distance_km", return_value=Decimal("7.25"))
    def test_assign_location_and_distance_stores_round_trip_for_home_lessons(self, mocked_distance):
        tutor_user = CustomUser.objects.create_user(
            username="routes_tutor",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        student_user = CustomUser.objects.create_user(
            username="routes_student",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
        )
        tutor = TutorProfile.objects.create(
            user=tutor_user,
            address="Tutorstrasse 1, Braunschweig",
        )
        student = StudentProfile.objects.create(
            user=student_user,
            address="Schuelerstrasse 2, Braunschweig",
        )
        lesson = Lesson(
            tutor=tutor,
            student=student,
            date=date(2026, 6, 10),
            time=time(15, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ZUHAUSE_STUDENT,
            fach="mathe",
            status=Lesson.Status.PLANNED,
        )

        _assign_location_and_distance(lesson)

        self.assertEqual(lesson.location_address, "Schuelerstrasse 2, Braunschweig")
        self.assertEqual(lesson.distance_km, Decimal("14.50"))
        mocked_distance.assert_called_once_with(
            "Tutorstrasse 1, Braunschweig",
            "Schuelerstrasse 2, Braunschweig",
        )


class LessonCancellationChargeableTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="tutor_cancel_test",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        self.student_user = CustomUser.objects.create_user(
            username="student_cancel_test",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
        )
        self.parent_user = CustomUser.objects.create_user(
            username="parent_cancel_test",
            password="test12345",
            role=CustomUser.Roles.PARENT,
        )
        self.tutor = TutorProfile.objects.create(user=self.tutor_user)
        self.student = StudentProfile.objects.create(user=self.student_user)
        self.parent = ParentProfile.objects.create(user=self.parent_user)
        self.student.parents.add(self.parent)
        self.logged_in = self.client.login(username="parent_cancel_test", password="test12345")
        self.assertTrue(self.logged_in)

    def test_late_cancellation_is_marked_chargeable(self):
        soon = timezone.localtime() + timedelta(hours=2)
        lesson = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=soon.date(),
            time=soon.time().replace(second=0, microsecond=0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.PLANNED,
        )

        response = self.client.post(
            reverse("lesson_cancel", args=[lesson.id]),
            data={"reason": "Kurzfristig verhindert"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        lesson.refresh_from_db()
        self.assertEqual(lesson.status, Lesson.Status.CANCELLED)
        self.assertTrue(lesson.cancellation_chargeable)
        self.assertIsNotNone(lesson.cancelled_at)

    def test_early_cancellation_stays_not_chargeable(self):
        later = timezone.localtime() + timedelta(days=2)
        lesson = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=later.date(),
            time=later.time().replace(second=0, microsecond=0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.PLANNED,
        )

        response = self.client.post(
            reverse("lesson_cancel", args=[lesson.id]),
            data={"reason": "Rechtzeitig abgesagt"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        lesson.refresh_from_db()
        self.assertEqual(lesson.status, Lesson.Status.CANCELLED)
        self.assertFalse(lesson.cancellation_chargeable)
        self.assertIsNotNone(lesson.cancelled_at)


class LessonDeleteScopeTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="tutor_delete_test",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        self.other_tutor_user = CustomUser.objects.create_user(
            username="other_tutor_delete_test",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        self.student_user = CustomUser.objects.create_user(
            username="student_delete_test",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
        )
        self.other_student_user = CustomUser.objects.create_user(
            username="other_student_delete_test",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
        )
        self.tutor = TutorProfile.objects.create(user=self.tutor_user)
        self.other_tutor = TutorProfile.objects.create(user=self.other_tutor_user)
        self.student = StudentProfile.objects.create(user=self.student_user)
        self.other_student = StudentProfile.objects.create(user=self.other_student_user)
        self.client.login(username="tutor_delete_test", password="test12345")

    def _lesson(self, **overrides):
        data = {
            "tutor": self.tutor,
            "student": self.student,
            "date": date(2026, 5, 4),
            "time": time(15, 0),
            "duration_minutes": 60,
            "ort": Lesson.Ort.ONLINE,
            "fach": "mathe",
            "status": Lesson.Status.PLANNED,
        }
        data.update(overrides)
        return Lesson.objects.create(**data)

    def test_lesson_edit_shows_delete_scope_options_for_tutor(self):
        lesson = self._lesson()

        response = self.client.get(reverse("lesson_edit", args=[lesson.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="delete_scope" value="single"')
        self.assertContains(response, 'name="delete_scope" value="same_weekday_time"')
        self.assertContains(response, 'name="delete_scope" value="future_student"')

    def test_single_delete_only_deletes_selected_lesson(self):
        selected = self._lesson()
        other = self._lesson(date=date(2026, 5, 11))

        response = self.client.post(
            reverse("lesson_delete", args=[selected.id]),
            {"delete_scope": "single"},
        )

        self.assertRedirects(response, reverse("lesson_list"))
        self.assertFalse(Lesson.objects.filter(pk=selected.pk).exists())
        self.assertTrue(Lesson.objects.filter(pk=other.pk).exists())

    def test_same_weekday_time_delete_only_deletes_matching_student_series(self):
        selected = self._lesson(date=date(2026, 5, 4), time=time(15, 0))
        same_weekday_time = self._lesson(date=date(2026, 5, 11), time=time(15, 0))
        different_time = self._lesson(date=date(2026, 5, 18), time=time(16, 0))
        different_weekday = self._lesson(date=date(2026, 5, 12), time=time(15, 0))
        other_student_same_slot = self._lesson(
            student=self.other_student,
            date=date(2026, 5, 11),
            time=time(15, 0),
        )
        other_tutor_same_slot = self._lesson(
            tutor=self.other_tutor,
            date=date(2026, 5, 11),
            time=time(15, 0),
        )

        response = self.client.post(
            reverse("lesson_delete", args=[selected.id]),
            {"delete_scope": "same_weekday_time"},
        )

        self.assertRedirects(response, reverse("lesson_list"))
        self.assertFalse(Lesson.objects.filter(pk=selected.pk).exists())
        self.assertFalse(Lesson.objects.filter(pk=same_weekday_time.pk).exists())
        self.assertTrue(Lesson.objects.filter(pk=different_time.pk).exists())
        self.assertTrue(Lesson.objects.filter(pk=different_weekday.pk).exists())
        self.assertTrue(Lesson.objects.filter(pk=other_student_same_slot.pk).exists())
        self.assertTrue(Lesson.objects.filter(pk=other_tutor_same_slot.pk).exists())

    def test_future_student_delete_deletes_selected_and_later_student_lessons(self):
        earlier = self._lesson(date=date(2026, 5, 1), time=time(15, 0))
        selected = self._lesson(date=date(2026, 5, 4), time=time(15, 0))
        same_day_later = self._lesson(date=date(2026, 5, 4), time=time(17, 0))
        future_different_day = self._lesson(date=date(2026, 5, 5), time=time(10, 0))
        other_student_future = self._lesson(
            student=self.other_student,
            date=date(2026, 5, 5),
            time=time(10, 0),
        )

        response = self.client.post(
            reverse("lesson_delete", args=[selected.id]),
            {"delete_scope": "future_student"},
        )

        self.assertRedirects(response, reverse("lesson_list"))
        self.assertTrue(Lesson.objects.filter(pk=earlier.pk).exists())
        self.assertFalse(Lesson.objects.filter(pk=selected.pk).exists())
        self.assertFalse(Lesson.objects.filter(pk=same_day_later.pk).exists())
        self.assertFalse(Lesson.objects.filter(pk=future_different_day.pk).exists())
        self.assertTrue(Lesson.objects.filter(pk=other_student_future.pk).exists())


class LessonListOrderTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="tutor_order_test",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        self.student_user = CustomUser.objects.create_user(
            username="student_order_test",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
        )
        self.tutor = TutorProfile.objects.create(user=self.tutor_user)
        self.student = StudentProfile.objects.create(user=self.student_user)
        self.client.login(username="tutor_order_test", password="test12345")

    def _lesson(self, lesson_date, lesson_time):
        return Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=lesson_date,
            time=lesson_time,
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.PLANNED,
        )

    def test_upcoming_lessons_are_listed_chronologically(self):
        today = timezone.localdate()
        later = self._lesson(today + timedelta(days=21), time(15, 0))
        sooner_afternoon = self._lesson(today + timedelta(days=7), time(16, 0))
        sooner_morning = self._lesson(today + timedelta(days=7), time(10, 0))

        response = self.client.get(reverse("lesson_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["lessons"]),
            [sooner_morning, sooner_afternoon, later],
        )


class InvoiceGenerationChargeableCancellationTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="tutor_invoice_cancel",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        self.student_user = CustomUser.objects.create_user(
            username="student_invoice_cancel",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
        )
        self.tutor = TutorProfile.objects.create(user=self.tutor_user)
        self.student = StudentProfile.objects.create(user=self.student_user)
        self.student.assigned_tutors.add(self.tutor)
        logged_in = self.client.login(username="tutor_invoice_cancel", password="test12345")
        self.assertTrue(logged_in)

    def test_invoice_generation_includes_only_completed_and_late_cancelled_lessons(self):
        completed = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=date(2026, 3, 4),
            time=time(10, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.COMPLETED,
        )
        late_cancelled = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=date(2026, 3, 5),
            time=time(10, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.CANCELLED,
            cancellation_chargeable=True,
            cancelled_at=timezone.now(),
            cancellation_reason="Kurzfristig",
        )
        early_cancelled = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=date(2026, 3, 6),
            time=time(10, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.CANCELLED,
            cancellation_chargeable=False,
            cancelled_at=timezone.now(),
            cancellation_reason="Rechtzeitig",
        )

        captured = {}

        def fake_generate_invoice_pdf(*args, **kwargs):
            captured["lessons"] = list(kwargs["lessons"])
            return b"%PDF-1.4\n%fake\n"

        with patch("core.views.invoices._generate_invoice_pdf", side_effect=fake_generate_invoice_pdf):
            response = self.client.post(
                reverse("invoice_upload"),
                data={
                    "action": "generate",
                    "student": str(self.student.id),
                    "period": "2026-03",
                    "discount_type": "",
                    "discount_value": "",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Invoice.objects.count(), 1)
        selected_ids = {lesson.id for lesson in captured["lessons"]}
        self.assertEqual(selected_ids, {completed.id, late_cancelled.id})
        self.assertNotIn(early_cancelled.id, selected_ids)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    MEDIA_ROOT=tempfile.mkdtemp(),
)
class InvoiceUploadListFilterTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="tutor_invoice_filters",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        self.tutor = TutorProfile.objects.create(user=self.tutor_user)
        self.student_user = CustomUser.objects.create_user(
            username="student_invoice_filters",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
            first_name="Sina",
            last_name="Filter",
            email="sina@example.com",
        )
        self.student = StudentProfile.objects.create(
            user=self.student_user,
            phone_number="0176 12345678",
        )
        self.other_student_user = CustomUser.objects.create_user(
            username="other_invoice_filters",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
            first_name="Oskar",
            last_name="Andere",
        )
        self.other_student = StudentProfile.objects.create(user=self.other_student_user)
        self.student.assigned_tutors.add(self.tutor)
        self.other_student.assigned_tutors.add(self.tutor)
        self.client.login(username="tutor_invoice_filters", password="test12345")

    def _invoice(self, student, filename, year, month, tutor=None):
        tutor = tutor or self.tutor
        return Invoice.objects.create(
            student=student,
            uploaded_by=tutor,
            approved_by=tutor,
            approved_at=timezone.now(),
            billing_year=year,
            billing_month=month,
            file=SimpleUploadedFile(filename, b"%PDF-1.4", content_type="application/pdf"),
            amount_total=Decimal("25.00"),
        )

    def test_my_invoices_can_be_filtered_by_student_month_and_year(self):
        matching = self._invoice(self.student, "matching.pdf", 2026, 3)
        other_student = self._invoice(self.other_student, "other-student.pdf", 2026, 3)
        other_month = self._invoice(self.student, "other-month.pdf", 2026, 4)
        other_year = self._invoice(self.student, "other-year.pdf", 2025, 3)

        response = self.client.get(
            reverse("invoice_upload"),
            {"student": str(self.student.id), "month": "3", "year": "2026"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["tutor_invoices"]), [matching])
        self.assertContains(response, "matching.pdf")
        self.assertNotContains(response, "other-student.pdf")
        self.assertNotContains(response, "other-month.pdf")
        self.assertNotContains(response, "other-year.pdf")
        self.assertIn(other_student.student_id, [option["id"] for option in response.context["invoice_student_options"]])

    def test_student_without_parents_has_direct_notification_button_and_route(self):
        invoice = self._invoice(self.student, "direct-student.pdf", 2026, 3)

        response = self.client.get(reverse("invoice_upload"))

        notify_url = reverse("invoice_notify_student", args=[invoice.id])
        self.assertContains(response, notify_url)
        self.assertContains(response, "News/Mail/WhatsApp an Sina Filter")

        response = self.client.get(notify_url)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("https://wa.me/4917612345678"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Neue Rechnung", mail.outbox[0].subject)

    def test_subordinate_invoices_can_be_filtered_by_tutor_month_and_year(self):
        subordinate_user = CustomUser.objects.create_user(
            username="subordinate_invoice_filters",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
            first_name="Tina",
            last_name="Tutorin",
        )
        subordinate = TutorProfile.objects.create(user=subordinate_user)
        other_subordinate_user = CustomUser.objects.create_user(
            username="other_subordinate_invoice_filters",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
            first_name="Tom",
            last_name="Tutor",
        )
        other_subordinate = TutorProfile.objects.create(user=other_subordinate_user)
        self.tutor.assigned_tutors.add(subordinate, other_subordinate)
        matching = self._invoice(self.student, "subordinate-matching.pdf", 2026, 3, tutor=subordinate)
        self._invoice(self.student, "subordinate-other-tutor.pdf", 2026, 3, tutor=other_subordinate)
        self._invoice(self.student, "subordinate-other-month.pdf", 2026, 4, tutor=subordinate)
        self._invoice(self.student, "subordinate-other-year.pdf", 2025, 3, tutor=subordinate)

        response = self.client.get(
            reverse("invoice_upload"),
            {
                "subordinate_tutor": str(subordinate.id),
                "subordinate_month": "3",
                "subordinate_year": "2026",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["subordinate_invoices"]), [matching])
        self.assertContains(response, "subordinate-matching.pdf")
        self.assertNotContains(response, "subordinate-other-tutor.pdf")
        self.assertNotContains(response, "subordinate-other-month.pdf")
        self.assertNotContains(response, "subordinate-other-year.pdf")
        self.assertIn(
            other_subordinate.id,
            [option["id"] for option in response.context["subordinate_invoice_tutor_options"]],
        )


class LessonStatusAutoCompleteTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="tutor_auto",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        self.student_user = CustomUser.objects.create_user(
            username="student_auto",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
        )
        self.tutor = TutorProfile.objects.create(user=self.tutor_user)
        self.student = StudentProfile.objects.create(user=self.student_user)

    def test_planned_past_lessons_are_marked_completed(self):
        now = timezone.localtime()
        past_lesson = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=now.date(),
            time=(now - timedelta(hours=2)).time().replace(second=0, microsecond=0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.PLANNED,
        )

        updated = _auto_complete_past_lessons(Lesson.objects.filter(pk=past_lesson.pk))
        past_lesson.refresh_from_db()

        self.assertEqual(updated, 1)
        self.assertEqual(past_lesson.status, Lesson.Status.COMPLETED)

    def test_reschedule_requested_lessons_stay_planned(self):
        now = timezone.localtime()
        lesson = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=now.date(),
            time=(now - timedelta(hours=2)).time().replace(second=0, microsecond=0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.PLANNED,
            reschedule_requested=True,
        )

        updated = _auto_complete_past_lessons(Lesson.objects.filter(pk=lesson.pk))
        lesson.refresh_from_db()

        self.assertEqual(updated, 0)
        self.assertEqual(lesson.status, Lesson.Status.PLANNED)


class BrainBoostFeedbackFormTests(TestCase):
    def test_requires_at_least_one_feedback_field(self):
        form = BrainBoostFeedbackForm(
            data={
                "audience": BrainBoostFeedback.Audience.STUDENT,
                "what_is_needed": "   ",
                "what_went_bad": "",
                "wishes": "",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)


class BrainBoostFeedbackViewTests(TestCase):
    def test_public_feedback_view_saves_anonymous_feedback(self):
        response = self.client.post(
            reverse("brainboost_feedback"),
            data={
                "audience": BrainBoostFeedback.Audience.PARENT,
                "what_is_needed": "Mehr Transparenz bei Prozessen.",
                "what_went_bad": "",
                "wishes": "",
                "source": BrainBoostFeedback.Source.NEWS,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(BrainBoostFeedback.objects.count(), 1)
        feedback = BrainBoostFeedback.objects.get()
        self.assertEqual(feedback.audience, BrainBoostFeedback.Audience.PARENT)
        self.assertEqual(feedback.source, BrainBoostFeedback.Source.NEWS)


class InvoicePaymentQrPayloadTests(TestCase):
    def test_builds_epc_payload_with_tutor_bank_details(self):
        payload = _build_epc_payment_payload(
            account_holder="BrainBoost Nachhilfe",
            iban="DE40 5002 4024 1563 4174 30",
            bic="DEFFDEFFXXX",
            amount=Decimal("129.50"),
            remittance_information="Rechnung Maerz 2026 Max Mustermann",
        )

        self.assertIsNotNone(payload)
        lines = payload.splitlines()
        self.assertEqual(lines[0], "BCD")
        self.assertEqual(lines[3], "SCT")
        self.assertEqual(lines[4], "DEFFDEFFXXX")
        self.assertEqual(lines[5], "BrainBoost Nachhilfe")
        self.assertEqual(lines[6], "DE40500240241563417430")
        self.assertEqual(lines[7], "EUR129.50")

    def test_returns_none_without_required_bank_data(self):
        payload = _build_epc_payment_payload(
            account_holder="",
            iban="",
            bic="",
            amount=Decimal("25.00"),
            remittance_information="Test",
        )

        self.assertIsNone(payload)


class FAQSubmissionVisibilityAndDefaultsTests(TestCase):
    def test_non_admin_parent_submission_uses_parent_default_target(self):
        parent_user = CustomUser.objects.create_user(
            username="parent_faq",
            password="test12345",
            role=CustomUser.Roles.PARENT,
        )
        logged_in = self.client.login(username="parent_faq", password="test12345")
        self.assertTrue(logged_in)

        response = self.client.post(
            reverse("faq_submit"),
            data={"question": "Wie läuft die Terminabsprache?"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(FAQItem.objects.count(), 1)
        item = FAQItem.objects.get()
        self.assertTrue(item.show_for_parents)
        self.assertFalse(item.show_for_students)
        self.assertFalse(item.show_for_tutors)
        self.assertFalse(item.show_on_landing)

    def test_admin_parent_submission_keeps_selected_targets(self):
        admin_parent_user = CustomUser.objects.create_user(
            username="parent_admin_faq",
            password="test12345",
            role=CustomUser.Roles.PARENT,
            is_staff=True,
        )
        logged_in = self.client.login(username="parent_admin_faq", password="test12345")
        self.assertTrue(logged_in)

        response = self.client.post(
            reverse("faq_submit"),
            data={
                "question": "Bitte auch für TutorInnen anzeigen.",
                "show_for_tutors": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(FAQItem.objects.count(), 1)
        item = FAQItem.objects.get()
        self.assertFalse(item.show_for_parents)
        self.assertFalse(item.show_for_students)
        self.assertTrue(item.show_for_tutors)
        self.assertFalse(item.show_on_landing)


class DashboardProgressOrderTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="tutor_progress_order",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
            first_name="Tina",
            last_name="Tutor",
        )
        self.student_user = CustomUser.objects.create_user(
            username="student_progress_order",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
            first_name="Sina",
            last_name="Student",
        )
        self.parent_user = CustomUser.objects.create_user(
            username="parent_progress_order",
            password="test12345",
            role=CustomUser.Roles.PARENT,
            first_name="Paula",
            last_name="Parent",
        )
        self.tutor = TutorProfile.objects.create(user=self.tutor_user)
        self.student = StudentProfile.objects.create(user=self.student_user)
        self.parent = ParentProfile.objects.create(user=self.parent_user)
        self.student.parents.add(self.parent)

        newer_lesson = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=date(2026, 3, 15),
            time=time(17, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.COMPLETED,
        )
        older_lesson = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student,
            date=date(2026, 3, 1),
            time=time(15, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="deutsch",
            status=Lesson.Status.COMPLETED,
        )

        self.newer_entry = ProgressEntry.objects.create(
            lesson=newer_lesson,
            comment="Neuer Eintrag",
            rating=8,
        )
        self.older_entry = ProgressEntry.objects.create(
            lesson=older_lesson,
            comment="Alter Eintrag",
            rating=6,
        )

    def test_student_dashboard_lists_progress_newest_first(self):
        logged_in = self.client.login(
            username="student_progress_order",
            password="test12345",
        )
        self.assertTrue(logged_in)

        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        entries = list(response.context["progress_entries"])
        chart_data = response.context["progress_chart_data"]

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].id, self.newer_entry.id)
        self.assertEqual(entries[1].id, self.older_entry.id)
        self.assertTrue(response.context["show_progress_chart"])
        self.assertEqual(chart_data["labels"], ["01.03", "15.03"])
        self.assertEqual(len(chart_data["datasets"]), 2)

    def test_parent_dashboard_lists_progress_newest_first(self):
        logged_in = self.client.login(
            username="parent_progress_order",
            password="test12345",
        )
        self.assertTrue(logged_in)

        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        entries = list(response.context["progress_entries"])
        chart_data = response.context["progress_chart_data"]

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].id, self.newer_entry.id)
        self.assertEqual(entries[1].id, self.older_entry.id)
        self.assertTrue(response.context["show_progress_chart"])
        self.assertEqual(chart_data["labels"], ["01.03", "15.03"])
        self.assertEqual(len(chart_data["datasets"]), 2)


class ProgressChartDataTests(TestCase):
    def test_build_progress_chart_data_groups_by_subject_and_orders_chronologically(self):
        tutor_user = CustomUser.objects.create_user(
            username="tutor_chart_data",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        student_user = CustomUser.objects.create_user(
            username="student_chart_data",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
            first_name="Max",
            last_name="Muster",
        )
        tutor = TutorProfile.objects.create(user=tutor_user)
        student = StudentProfile.objects.create(user=student_user)

        older_lesson = Lesson.objects.create(
            tutor=tutor,
            student=student,
            date=date(2026, 3, 1),
            time=time(15, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            fach_2="deutsch",
            status=Lesson.Status.COMPLETED,
        )
        newer_lesson = Lesson.objects.create(
            tutor=tutor,
            student=student,
            date=date(2026, 3, 10),
            time=time(16, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.COMPLETED,
        )

        ProgressEntry.objects.create(
            lesson=older_lesson,
            comment="Alt",
            rating=5,
            rating_fach_2=7,
        )
        ProgressEntry.objects.create(
            lesson=newer_lesson,
            comment="Neu",
            rating=9,
        )

        chart_data = _build_progress_chart_data(
            ProgressEntry.objects.filter(lesson__student=student)
        )

        self.assertEqual(
            chart_data["labels"],
            ["01.03", "10.03"],
        )
        self.assertEqual(chart_data["date_keys"], ["2026-03-01", "2026-03-10"])
        self.assertEqual(
            chart_data["detail_labels"],
            ["01.03 15:00", "10.03 16:00"],
        )
        self.assertEqual(chart_data["datasets"][0]["label"], "Deutsch")
        self.assertEqual(chart_data["datasets"][0]["values"], [7, None])
        self.assertEqual(chart_data["datasets"][1]["label"], "Mathe")
        self.assertEqual(chart_data["datasets"][1]["values"], [5, 9])


class TutorProgressChartSelectionTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="tutor_progress_chart",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        self.student_one_user = CustomUser.objects.create_user(
            username="student_chart_one",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
            first_name="Ava",
            last_name="Eins",
        )
        self.student_two_user = CustomUser.objects.create_user(
            username="student_chart_two",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
            first_name="Ben",
            last_name="Zwei",
        )
        self.tutor = TutorProfile.objects.create(user=self.tutor_user)
        self.student_one = StudentProfile.objects.create(user=self.student_one_user)
        self.student_two = StudentProfile.objects.create(user=self.student_two_user)
        self.student_one.assigned_tutors.add(self.tutor)
        self.student_two.assigned_tutors.add(self.tutor)

        lesson_one = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student_one,
            date=date(2026, 3, 10),
            time=time(15, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.COMPLETED,
        )
        lesson_two = Lesson.objects.create(
            tutor=self.tutor,
            student=self.student_two,
            date=date(2026, 3, 12),
            time=time(16, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="deutsch",
            status=Lesson.Status.COMPLETED,
        )
        ProgressEntry.objects.create(lesson=lesson_one, comment="Eintrag A", rating=8)
        ProgressEntry.objects.create(lesson=lesson_two, comment="Eintrag B", rating=7)

    def test_tutor_progress_chart_requires_student_selection(self):
        logged_in = self.client.login(
            username="tutor_progress_chart",
            password="test12345",
        )
        self.assertTrue(logged_in)

        response = self.client.get(reverse("progress"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["show_progress_chart"])
        self.assertTrue(response.context["tutor_chart_requires_student_selection"])
        self.assertEqual(response.context["progress_chart_data"]["datasets"], [])

    def test_tutor_progress_chart_shows_selected_student_data(self):
        logged_in = self.client.login(
            username="tutor_progress_chart",
            password="test12345",
        )
        self.assertTrue(logged_in)

        response = self.client.get(
            reverse("progress"),
            {"student": str(self.student_one.id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["tutor_chart_requires_student_selection"])
        chart_data = response.context["progress_chart_data"]
        self.assertEqual(chart_data["labels"], ["10.03"])
        self.assertEqual(len(chart_data["datasets"]), 1)
        self.assertEqual(chart_data["datasets"][0]["label"], "Mathe")


class TutorStudentAssignmentTests(TestCase):
    def setUp(self):
        self.source_user = CustomUser.objects.create_user(
            username="tutor_source_assign",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
            first_name="Sofia",
            last_name="Source",
        )
        self.target_user = CustomUser.objects.create_user(
            username="tutor_target_assign",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
            first_name="Tim",
            last_name="Target",
        )
        self.admin_user = CustomUser.objects.create_user(
            username="tutor_admin_assign",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
            first_name="Alex",
            last_name="Admin",
            is_staff=True,
        )
        self.source_tutor = TutorProfile.objects.create(user=self.source_user)
        self.target_tutor = TutorProfile.objects.create(user=self.target_user)
        self.admin_tutor = TutorProfile.objects.create(user=self.admin_user)

        self.student_user = CustomUser.objects.create_user(
            username="student_assign_one",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
            first_name="Mia",
            last_name="Student",
        )
        self.student = StudentProfile.objects.create(user=self.student_user)
        self.student.assigned_tutors.add(self.source_tutor)

        self.foreign_student_user = CustomUser.objects.create_user(
            username="student_assign_foreign",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
            first_name="Noah",
            last_name="Foreign",
        )
        self.foreign_student = StudentProfile.objects.create(user=self.foreign_student_user)
        self.foreign_student.assigned_tutors.add(self.target_tutor)

    def test_tutor_dashboard_renders_assignment_section(self):
        logged_in = self.client.login(username="tutor_source_assign", password="test12345")
        self.assertTrue(logged_in)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SchülerInnen zuweisen")

    def test_vertretung_assigns_student_to_target_and_keeps_source(self):
        logged_in = self.client.login(username="tutor_source_assign", password="test12345")
        self.assertTrue(logged_in)

        response = self.client.post(
            reverse("tutor_student_assignment"),
            data={
                "target_tutor": str(self.target_tutor.id),
                "reason": "vertretung",
                "temporary_end_mode": "lessons",
                "temporary_lessons": "2",
                "student_ids": [str(self.student.id)],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("tutor_student_assignment"))
        self.student.refresh_from_db()
        assigned_ids = set(self.student.assigned_tutors.values_list("id", flat=True))
        self.assertEqual(assigned_ids, {self.source_tutor.id, self.target_tutor.id})

    def test_abgabe_transfers_student_from_source_to_target(self):
        logged_in = self.client.login(username="tutor_source_assign", password="test12345")
        self.assertTrue(logged_in)

        response = self.client.post(
            reverse("tutor_student_assignment"),
            data={
                "target_tutor": str(self.target_tutor.id),
                "reason": "abgabe",
                "student_ids": [str(self.student.id)],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("tutor_student_assignment"))
        self.student.refresh_from_db()
        assigned_ids = set(self.student.assigned_tutors.values_list("id", flat=True))
        self.assertEqual(assigned_ids, {self.target_tutor.id})

    def test_non_admin_cannot_assign_students_of_other_tutors(self):
        logged_in = self.client.login(username="tutor_source_assign", password="test12345")
        self.assertTrue(logged_in)

        response = self.client.post(
            reverse("tutor_student_assignment"),
            data={
                "target_tutor": str(self.admin_tutor.id),
                "reason": "vertretung",
                "temporary_end_mode": "lessons",
                "temporary_lessons": "2",
                "student_ids": [str(self.foreign_student.id)],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("tutor_student_assignment"))
        self.foreign_student.refresh_from_db()
        assigned_ids = set(self.foreign_student.assigned_tutors.values_list("id", flat=True))
        self.assertEqual(assigned_ids, {self.target_tutor.id})

    def test_admin_can_assign_other_tutor_students_to_self_without_consent(self):
        logged_in = self.client.login(username="tutor_admin_assign", password="test12345")
        self.assertTrue(logged_in)

        response = self.client.post(
            reverse("tutor_student_assignment"),
            data={
                "source_tutor": str(self.source_tutor.id),
                "target_tutor": str(self.admin_tutor.id),
                "reason": "vertretung",
                "temporary_end_mode": "lessons",
                "temporary_lessons": "2",
                "student_ids": [str(self.student.id)],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"{reverse('tutor_student_assignment')}?source_tutor={self.source_tutor.id}",
        )
        self.student.refresh_from_db()
        assigned_ids = set(self.student.assigned_tutors.values_list("id", flat=True))
        self.assertEqual(assigned_ids, {self.source_tutor.id, self.admin_tutor.id})

    def test_temporary_vertretung_is_removed_after_configured_lessons(self):
        logged_in = self.client.login(username="tutor_source_assign", password="test12345")
        self.assertTrue(logged_in)

        response = self.client.post(
            reverse("tutor_student_assignment"),
            data={
                "target_tutor": str(self.target_tutor.id),
                "reason": "vertretung",
                "temporary_end_mode": "lessons",
                "temporary_lessons": "2",
                "student_ids": [str(self.student.id)],
            },
        )
        self.assertEqual(response.status_code, 302)

        now = timezone.localtime()
        lesson_day = now.date() + timedelta(days=1)
        Lesson.objects.create(
            tutor=self.target_tutor,
            student=self.student,
            date=lesson_day,
            time=time(10, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="mathe",
            status=Lesson.Status.COMPLETED,
        )
        Lesson.objects.create(
            tutor=self.target_tutor,
            student=self.student,
            date=lesson_day,
            time=time(11, 0),
            duration_minutes=60,
            ort=Lesson.Ort.ONLINE,
            fach="deutsch",
            status=Lesson.Status.COMPLETED,
        )

        _sync_temporary_tutor_assignments()

        self.student.refresh_from_db()
        assigned_ids = set(self.student.assigned_tutors.values_list("id", flat=True))
        self.assertEqual(assigned_ids, {self.source_tutor.id})
        self.assertEqual(
            TemporaryTutorAssignment.objects.filter(is_active=True).count(),
            0,
        )

    def test_temporary_vertretung_is_removed_after_end_date(self):
        logged_in = self.client.login(username="tutor_source_assign", password="test12345")
        self.assertTrue(logged_in)

        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        response = self.client.post(
            reverse("tutor_student_assignment"),
            data={
                "target_tutor": str(self.target_tutor.id),
                "reason": "vertretung",
                "temporary_end_mode": "date",
                "temporary_end_date": yesterday,
                "student_ids": [str(self.student.id)],
            },
        )
        self.assertEqual(response.status_code, 302)

        _sync_temporary_tutor_assignments()

        self.student.refresh_from_db()
        assigned_ids = set(self.student.assigned_tutors.values_list("id", flat=True))
        self.assertEqual(assigned_ids, {self.source_tutor.id})
