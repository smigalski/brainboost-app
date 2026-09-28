from datetime import timedelta
from unittest.mock import patch

from django.contrib import admin
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms import modelform_factory
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import CustomUser, Lead, LeadStatusEmail, StudentProfile, TutorProfile
from .forms import TutorStudentAssignmentForm
from .admin import StudentProfileAdminForm
from .tutor_applications import deliver_application_email


@override_settings(
    MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}},
    DEFAULT_FROM_EMAIL="BrainBoost <team@example.com>",
    DEFAULT_REPLY_TO_EMAIL="team@example.com",
    LEAD_NOTIFICATION_EMAIL="office@example.com, team@example.com",
    APP_BASE_URL="https://brainboost.example.com",
)
class TutorApplicationWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = CustomUser.objects.create_user(
            username="application-admin", role=CustomUser.Roles.TUTOR, is_staff=True,
        )
        TutorProfile.objects.create(user=cls.staff)
        cls.user = CustomUser.objects.create_user(
            username="applicant", role=CustomUser.Roles.TUTOR, email="applicant@example.com",
        )
        cls.tutor = TutorProfile.objects.create(user=cls.user, status=TutorProfile.Status.APPLIED)
        cls.lead = Lead.objects.create(
            role=Lead.Role.TUTOR, name="Alex Bewerbung", email="old-address@example.com",
            preferred_contact=Lead.PreferredContact.EMAIL, tutoring_type=Lead.TutoringType.ONLINE,
            converted_tutor=cls.tutor, teaching_subjects="Mathe", weekly_availability="3 Stunden",
            internal_notes="Vertrauliche interne Notiz",
        )

    def change_status(self, status, lead=None):
        lead = lead or self.lead
        self.client.force_login(self.staff)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("lead_update_status", args=[lead.pk]), {"status": status})
        lead.refresh_from_db()
        return response

    def request_availability(self):
        self.change_status(Lead.Status.WAITLISTED)
        self.change_status(Lead.Status.AVAILABILITY_REQUESTED)
        return self.lead.application_email

    def respond(self, record, answer="interested", user=None):
        self.client.force_login(user or self.user)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("tutor_application_respond"), {
                "answer": answer, "request_id": str(record.pk),
            })
        self.lead.refresh_from_db()
        return response

    def test_rejection_sends_once_and_synchronizes_tutor_profile(self):
        self.change_status(Lead.Status.UNSUITABLE)
        self.tutor.refresh_from_db()
        self.assertEqual(self.tutor.status, TutorProfile.Status.REJECTED)
        self.assertTrue(self.lead.follow_up_done)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertEqual(mail.outbox[0].reply_to, ["team@example.com"])
        self.assertIn("nicht weiterzuverfolgen", mail.outbox[0].body)
        self.assertTrue(mail.outbox[0].alternatives)
        self.assertNotIn("Vertrauliche interne Notiz", mail.outbox[0].body)
        self.assertIsNotNone(self.lead.application_email.sent_at)
        self.change_status(Lead.Status.UNSUITABLE)
        self.lead.internal_notes = "Nur Notizen ändern"
        with self.captureOnCommitCallbacks(execute=True):
            self.lead.save(update_fields=["internal_notes"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(LeadStatusEmail.objects.count(), 1)

    def test_parent_rejection_does_not_send_tutor_application_email(self):
        lead = Lead.objects.create(role=Lead.Role.PARENT, name="Eltern", email="parent@example.com")
        self.change_status(Lead.Status.UNSUITABLE, lead)
        self.assertEqual(len(mail.outbox), 0)
        self.change_status(Lead.Status.WAITLISTED, lead)
        self.assertEqual(lead.status, Lead.Status.UNSUITABLE)

    def test_full_waitlist_interest_and_resume_flow(self):
        record = self.request_availability()
        self.tutor.refresh_from_db()
        self.assertEqual(self.tutor.status, TutorProfile.Status.WAITLISTED)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("keine Absage", mail.outbox[0].body)
        self.assertIn("https://brainboost.example.com/dashboard/#bewerbung", mail.outbox[1].body)
        self.respond(record)
        self.assertEqual(self.lead.status, Lead.Status.INTERESTED)
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(mail.outbox[-1].to, ["office@example.com", "team@example.com"])
        self.tutor.refresh_from_db()
        self.assertEqual(self.tutor.status, TutorProfile.Status.WAITLISTED)
        self.client.force_login(self.staff)
        response = self.client.post(reverse("lead_resume_application", args=[self.lead.pk]))
        self.assertRedirects(response, reverse("lead_dashboard"))
        self.lead.refresh_from_db()
        self.tutor.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.CONTACTED)
        self.assertEqual(self.tutor.status, TutorProfile.Status.APPLIED)
        self.assertFalse(self.lead.follow_up_done)
        self.assertFalse(self.tutor.assigned_students.exists())

    def test_applicant_can_remain_waitlisted_and_old_request_cannot_be_reused(self):
        record = self.request_availability()
        self.respond(record, "wait")
        self.assertEqual(self.lead.status, Lead.Status.WAITLISTED)
        self.assertEqual(len(mail.outbox), 3)
        self.change_status(Lead.Status.AVAILABILITY_REQUESTED)
        new_record = self.lead.application_email
        self.respond(record)
        self.assertEqual(self.lead.status, Lead.Status.AVAILABILITY_REQUESTED)
        self.respond(new_record)
        self.assertEqual(self.lead.status, Lead.Status.INTERESTED)
        count = len(mail.outbox)
        self.respond(new_record)
        self.assertEqual(len(mail.outbox), count)

    def test_availability_and_resume_require_correct_previous_state(self):
        self.change_status(Lead.Status.AVAILABILITY_REQUESTED)
        self.assertEqual(self.lead.status, Lead.Status.NEW)
        self.change_status(Lead.Status.WAITLISTED)
        self.change_status(Lead.Status.WON)
        self.assertEqual(self.lead.status, Lead.Status.WAITLISTED)
        self.change_status(Lead.Status.INTERESTED)
        self.assertEqual(self.lead.status, Lead.Status.WAITLISTED)
        self.client.post(reverse("lead_resume_application", args=[self.lead.pk]))
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.WAITLISTED)

    def test_failed_delivery_keeps_status_and_can_be_retried_once(self):
        with patch("core.tutor_applications.EmailMultiAlternatives.send", side_effect=RuntimeError("SMTP down")):
            self.change_status(Lead.Status.UNSUITABLE)
        record = self.lead.application_email
        self.assertEqual(self.lead.status, Lead.Status.UNSUITABLE)
        self.assertIsNone(record.sent_at)
        self.assertTrue(record.error)
        self.assertEqual(record.attempts, 1)
        Lead.objects.filter(pk=self.lead.pk).update(created_at=timezone.now() - timedelta(days=365))
        response = self.client.get(reverse("lead_dashboard"), {"start_date": timezone.localdate().isoformat()})
        self.assertContains(response, "Bewerbungs-E-Mail erneut versuchen")
        self.assertContains(response, "Ausstehende Bewerbungs-E-Mails")
        self.assertContains(response, self.lead.name)
        for _ in range(2):
            self.client.post(reverse("lead_retry_application_email", args=[self.lead.pk]))
        record.refresh_from_db()
        self.assertIsNotNone(record.sent_at)
        self.assertEqual(record.attempts, 2)
        self.assertEqual(record.error, "")
        self.assertEqual(len(mail.outbox), 1)

    def test_unsent_obsolete_decision_is_not_delivered(self):
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                self.lead.status = Lead.Status.WAITLISTED
                self.lead.save()
                self.lead.status = Lead.Status.UNSUITABLE
                self.lead.save()
        self.assertEqual(len(mail.outbox), 1)
        old_record = LeadStatusEmail.objects.get(status=Lead.Status.WAITLISTED)
        self.assertFalse(deliver_application_email(old_record.pk))
        self.assertEqual(len(mail.outbox), 1)

    def test_transaction_rollback_does_not_send_mail_or_change_profile(self):
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    self.lead.status = Lead.Status.UNSUITABLE
                    self.lead.save()
                    raise RuntimeError("rollback")
        self.lead.refresh_from_db()
        self.tutor.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.NEW)
        self.assertEqual(self.tutor.status, TutorProfile.Status.APPLIED)
        self.assertEqual(LeadStatusEmail.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_missing_email_is_visible_and_retry_uses_corrected_address(self):
        CustomUser.objects.filter(pk=self.user.pk).update(email="")
        Lead.objects.filter(pk=self.lead.pk).update(email="")
        self.change_status(Lead.Status.WAITLISTED)
        self.assertEqual(self.lead.application_email.error, "Keine E-Mail-Adresse hinterlegt.")
        CustomUser.objects.filter(pk=self.user.pk).update(email="corrected@example.com")
        self.client.post(reverse("lead_retry_application_email", args=[self.lead.pk]))
        self.assertEqual(mail.outbox[0].to, ["corrected@example.com"])

    def test_django_admin_status_save_uses_the_same_email_workflow(self):
        form_class = modelform_factory(Lead, fields=["status"])
        form = form_class({"status": Lead.Status.UNSUITABLE}, instance=self.lead)
        self.assertTrue(form.is_valid(), form.errors)
        with self.captureOnCommitCallbacks(execute=True):
            admin.site._registry[Lead].save_model(RequestFactory().post("/admin/"), form.save(commit=False), form, True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIsNotNone(self.lead.application_email.sent_at)

    def test_waitlist_is_visible_outside_acquisition_date_filter(self):
        self.change_status(Lead.Status.WAITLISTED)
        Lead.objects.filter(pk=self.lead.pk).update(created_at=timezone.now() - timedelta(days=365))
        response = self.client.get(reverse("lead_dashboard"), {"start_date": timezone.localdate().isoformat()})
        self.assertContains(response, self.lead.name)
        self.assertEqual(list(response.context["waitlisted_leads"]), [self.lead])
        self.assertContains(response, "Verfügbarkeit anfragen")
        self.assertContains(response, "Mathe")

    def test_dashboard_shows_own_request_and_get_does_not_confirm_interest(self):
        self.request_availability()
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Ja, ich habe Interesse")
        response = self.client.get(reverse("tutor_application_respond"))
        self.assertEqual(response.status_code, 405)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.AVAILABILITY_REQUESTED)

    def test_other_tutor_cannot_answer_an_applicants_request(self):
        record = self.request_availability()
        response = self.respond(record, user=self.staff)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.lead.status, Lead.Status.AVAILABILITY_REQUESTED)

    def test_tutor_cannot_change_decisions_or_retry_emails(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("lead_update_status", args=[self.lead.pk]), {"status": Lead.Status.UNSUITABLE})
        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(self.client.post(reverse("lead_retry_application_email", args=[self.lead.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("lead_resume_application", args=[self.lead.pk])).status_code, 403)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.NEW)

    def test_existing_student_assignment_prevents_waitlisting(self):
        student_user = CustomUser.objects.create_user(username="assigned-student", role=CustomUser.Roles.STUDENT)
        student = StudentProfile.objects.create(user=student_user)
        student.assigned_tutors.add(self.tutor)
        self.change_status(Lead.Status.WAITLISTED)
        self.assertEqual(self.lead.status, Lead.Status.NEW)
        self.assertEqual(len(mail.outbox), 0)

    def test_legacy_applicant_can_get_account_without_leaving_waitlist(self):
        lead = Lead.objects.create(role=Lead.Role.TUTOR, name="Legacy Tutor", email="legacy@example.com")
        self.change_status(Lead.Status.WAITLISTED, lead)
        self.change_status(Lead.Status.AVAILABILITY_REQUESTED, lead)
        self.assertEqual(lead.status, Lead.Status.WAITLISTED)
        self.client.post(reverse("lead_convert_to_tutor", args=[lead.pk]))
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.WAITLISTED)
        self.assertEqual(lead.converted_tutor.status, TutorProfile.Status.WAITLISTED)
        self.change_status(Lead.Status.AVAILABILITY_REQUESTED, lead)
        self.assertEqual(lead.status, Lead.Status.AVAILABILITY_REQUESTED)

    def test_interested_status_cannot_be_forged_in_admin_form(self):
        self.request_availability()
        form = modelform_factory(Lead, fields=["status"])({"status": Lead.Status.INTERESTED}, instance=self.lead)
        self.assertFalse(form.is_valid())

    def test_password_email_resend_preserves_waitlist(self):
        self.change_status(Lead.Status.WAITLISTED)
        self.client.post(reverse("lead_convert_to_tutor", args=[self.lead.pk]))
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.WAITLISTED)

    @override_settings(EMAIL_NOTIFICATIONS={"tutor_application_status": False})
    def test_disabled_notifications_are_visible_and_do_not_send(self):
        self.change_status(Lead.Status.UNSUITABLE)
        self.assertIn("deaktiviert", self.lead.application_email.error)
        self.assertEqual(len(mail.outbox), 0)

    def test_response_requires_csrf_token(self):
        record = self.request_availability()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(reverse("tutor_application_respond"), {"answer": "interested", "request_id": record.pk})
        self.assertEqual(response.status_code, 403)

    def test_waitlisted_tutor_cannot_receive_students_before_resume(self):
        self.change_status(Lead.Status.WAITLISTED)
        student_user = CustomUser.objects.create_user(username="waiting-student", role=CustomUser.Roles.STUDENT)
        student = StudentProfile.objects.create(user=student_user)
        student.assigned_tutors.add(self.staff.tutor_profile)
        form = TutorStudentAssignmentForm({
            "source_tutor": self.staff.tutor_profile.pk,
            "target_tutor": self.tutor.pk,
            "reason": TutorStudentAssignmentForm.REASON_HANDOVER,
            "student_ids": [student.pk],
        }, current_tutor=self.staff.tutor_profile, is_admin_tutor=True)
        self.assertFalse(form.is_valid())
        self.assertIn("target_tutor", form.errors)
        admin_form = StudentProfileAdminForm(instance=student)
        admin_form.cleaned_data = {"assigned_tutors": TutorProfile.objects.filter(pk=self.tutor.pk)}
        with self.assertRaisesMessage(ValidationError, "Interesse"):
            admin_form.clean_assigned_tutors()

    def test_zero_messages_sent_is_treated_as_failure(self):
        with patch("core.tutor_applications.EmailMultiAlternatives.send", return_value=0):
            self.change_status(Lead.Status.UNSUITABLE)
        self.assertIsNone(self.lead.application_email.sent_at)
        self.assertTrue(self.lead.application_email.error)
