from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .admin import CancellationRequestAdmin
from .cancellation_requests import confirm_cancellation_request, send_confirmation_email
from .models import CancellationRequest, ParentProfile, StudentProfile, TutorProfile


@override_settings(
    MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}},
    DEFAULT_FROM_EMAIL="BrainBoost <no-reply@example.com>",
    DEFAULT_REPLY_TO_EMAIL="kontakt@example.com",
    INTERNAL_CONTACT_EMAIL="intern@example.com",
)
class CancellationRequestWorkflowTests(TestCase):
    password = "test12345"

    def create_user(self, username, role):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=self.password,
            first_name=username.title(),
            role=role,
        )
        if role == user.Roles.PARENT:
            ParentProfile.objects.create(user=user)
        elif role in {user.Roles.STUDENT, user.Roles.INDEPENDENT_STUDENT}:
            StudentProfile.objects.create(user=user)
        elif role == user.Roles.TUTOR:
            TutorProfile.objects.create(user=user)
        return user

    def submit(self, user, **overrides):
        self.client.force_login(user)
        data = {
            "delete_account": "on",
            "terminate_agreement": "on",
            "reason": "Bitte zum Monatsende bearbeiten.",
            "current_password": self.password,
            "acknowledged": "on",
            **overrides,
        }
        return self.client.post(reverse("cancellation_request"), data)

    def test_supported_roles_see_profile_button_and_can_open_form(self):
        roles = [
            get_user_model().Roles.PARENT,
            get_user_model().Roles.INDEPENDENT_STUDENT,
            get_user_model().Roles.TUTOR,
        ]
        for index, role in enumerate(roles):
            with self.subTest(role=role):
                user = self.create_user(f"role-{index}", role)
                self.client.force_login(user)
                profile_response = self.client.get(reverse("profile"))
                self.assertContains(profile_response, "Löschung oder Kündigung beantragen")
                self.assertEqual(self.client.get(reverse("cancellation_request")).status_code, 200)

    def test_dependent_student_cannot_submit_request(self):
        user = self.create_user("dependent", get_user_model().Roles.STUDENT)
        self.client.force_login(user)
        self.assertNotContains(self.client.get(reverse("profile")), "Löschung oder Kündigung beantragen")
        response = self.client.get(reverse("cancellation_request"))
        self.assertRedirects(response, reverse("profile"))

    def test_submission_creates_pending_request_and_sends_both_notifications(self):
        user = self.create_user("parent", get_user_model().Roles.PARENT)

        response = self.submit(user)

        self.assertRedirects(response, reverse("cancellation_request"))
        cancellation = CancellationRequest.objects.get(user=user)
        self.assertEqual(cancellation.status, CancellationRequest.Status.REQUESTED)
        self.assertTrue(cancellation.delete_account)
        self.assertTrue(cancellation.terminate_agreement)
        self.assertEqual(cancellation.role_snapshot, user.Roles.PARENT)
        self.assertIsNotNone(cancellation.internal_email_sent_at)
        self.assertIsNotNone(cancellation.receipt_email_sent_at)
        self.assertTrue(user.is_active)
        self.assertEqual({message.to[0] for message in mail.outbox}, {user.email, "intern@example.com"})

    def test_password_and_at_least_one_action_are_required(self):
        user = self.create_user("independent", get_user_model().Roles.INDEPENDENT_STUDENT)
        response = self.submit(
            user,
            delete_account="",
            terminate_agreement="",
            current_password="wrong",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Das aktuelle Passwort ist nicht korrekt")
        self.assertContains(response, "Bitte wähle mindestens eine")
        self.assertFalse(CancellationRequest.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_open_request_prevents_duplicate_submission(self):
        user = self.create_user("tutor", get_user_model().Roles.TUTOR)
        self.submit(user)
        mail.outbox.clear()

        response = self.submit(user, reason="Zweiter Antrag")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dein offener Antrag")
        self.assertEqual(CancellationRequest.objects.filter(user=user).count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_brainboost_confirmation_is_idempotent_and_emails_user(self):
        user = self.create_user("confirm", get_user_model().Roles.PARENT)
        self.submit(user, terminate_agreement="")
        mail.outbox.clear()
        staff = get_user_model().objects.create_user(
            username="staff",
            email="staff@example.com",
            is_staff=True,
        )
        cancellation = CancellationRequest.objects.get(user=user)

        cancellation, changed = confirm_cancellation_request(cancellation.pk, staff)
        self.assertTrue(changed)
        self.assertTrue(send_confirmation_email(cancellation))
        cancellation.refresh_from_db()
        self.assertEqual(cancellation.status, CancellationRequest.Status.CONFIRMED)
        self.assertEqual(cancellation.confirmed_by, staff)
        self.assertIsNotNone(cancellation.confirmed_at)
        self.assertIsNotNone(cancellation.confirmation_email_sent_at)
        self.assertEqual(mail.outbox[0].to, [user.email])
        _, changed_again = confirm_cancellation_request(cancellation.pk, staff)
        self.assertFalse(changed_again)

    def test_admin_confirmation_action_records_delivery_failure(self):
        user = self.create_user("mail-failure", get_user_model().Roles.TUTOR)
        self.submit(user)
        cancellation = CancellationRequest.objects.get(user=user)
        staff = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password=self.password,
        )
        request = self.client.request().wsgi_request
        request.user = staff
        request._messages = __import__(
            "django.contrib.messages.storage.fallback", fromlist=["FallbackStorage"]
        ).FallbackStorage(request)
        model_admin = CancellationRequestAdmin(CancellationRequest, admin.site)

        with patch("core.cancellation_requests.EmailMultiAlternatives.send", return_value=0):
            model_admin.confirm_requests(request, CancellationRequest.objects.filter(pk=cancellation.pk))

        cancellation.refresh_from_db()
        self.assertEqual(cancellation.status, CancellationRequest.Status.CONFIRMED)
        self.assertIn("Bestätigungsmail", cancellation.email_error)
