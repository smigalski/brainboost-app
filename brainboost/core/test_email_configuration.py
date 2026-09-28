import os
from pathlib import Path
import runpy
from smtplib import SMTPAuthenticationError
from unittest.mock import patch

from django.core import mail
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .models import CustomUser
from .notifications import _lead_operator_recipients
from .views.common import _send_set_password_email


SMTP_MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": "smtp.example.com",
            "port": 587,
            "use_tls": True,
            "username": "smtp-user",
            "password": "smtp-password",
        },
    },
}
MEMORY_MAILERS = {
    "default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"},
}


class MailerEnvironmentTests(SimpleTestCase):
    def load_mailers(self, environment):
        settings_file = Path(__file__).resolve().parents[1] / "brainboost/settings/base.py"
        # Isolate settings loading from real .env files and SMTP credentials.
        with patch.dict(os.environ, environment, clear=True), patch.object(
            Path, "read_text", return_value="",
        ):
            return runpy.run_path(str(settings_file))["MAILERS"]

    def test_existing_environment_variables_configure_smtp(self):
        config = self.load_mailers({
            "EMAIL_HOST": "smtp.example.com",
            "EMAIL_PORT": "2525",
            "EMAIL_USE_TLS": "false",
            "EMAIL_HOST_USER": "existing-user",
            "EMAIL_HOST_PASSWORD": "existing-password",
        })
        with override_settings(MAILERS=config):
            backend = mail.mailers["default"]
            self.assertEqual(backend.host, "smtp.example.com")
            self.assertEqual(backend.port, 2525)
            self.assertFalse(backend.use_tls)
            self.assertEqual(backend.username, "existing-user")
            self.assertEqual(backend.password, "existing-password")

    def test_memory_backend_does_not_receive_smtp_options(self):
        config = self.load_mailers({
            "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
            "EMAIL_HOST_PASSWORD": "unused-password",
        })
        self.assertNotIn("OPTIONS", config["default"])
        with override_settings(MAILERS=config):
            self.assertEqual(mail.send_mail("Test", "Body", "from@example.com", ["to@example.com"]), 1)


@override_settings(
    MAILERS=SMTP_MAILERS,
    DEFAULT_FROM_EMAIL="BrainBoost <sender@example.com>",
    DEFAULT_REPLY_TO_EMAIL="reply@example.com",
)
class PasswordSetupMailerTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/")
        self.user = CustomUser(pk=1, username="new-tutor", email="tutor@example.com")
        self.user.set_unusable_password()

    @patch("django.core.mail.backends.smtp.smtplib.SMTP")
    def test_password_setup_uses_mailer_credentials_and_tls(self, smtp):
        _send_set_password_email(self.request, self.user)

        self.assertEqual(smtp.call_args.args, ("smtp.example.com", 587))
        transport = smtp.return_value
        transport.starttls.assert_called_once()
        transport.login.assert_called_once_with("smtp-user", "smtp-password")
        transport.sendmail.assert_called_once()
        sender, recipients, message = transport.sendmail.call_args.args
        self.assertEqual(sender, "sender@example.com")
        self.assertEqual(recipients, [self.user.email])
        self.assertIn(b"From: BrainBoost <sender@example.com>", message)
        self.assertIn(b"multipart/alternative", message)
        self.assertIn(b"Reply-To: reply@example.com", message)

    @patch("django.core.mail.backends.smtp.smtplib.SMTP")
    def test_incomplete_smtp_credentials_fail_before_connecting(self, smtp):
        for missing in ("username", "password"):
            with self.subTest(missing=missing):
                options = {**SMTP_MAILERS["default"]["OPTIONS"], missing: ""}
                with override_settings(MAILERS={
                    "default": {**SMTP_MAILERS["default"], "OPTIONS": options},
                }):
                    with self.assertRaisesMessage(RuntimeError, "smtp_config_missing"):
                        _send_set_password_email(self.request, self.user)
        smtp.assert_not_called()

    @patch("django.core.mail.backends.smtp.smtplib.SMTP")
    def test_authentication_failure_is_not_silenced(self, smtp):
        smtp.return_value.login.side_effect = SMTPAuthenticationError(535, b"Rejected")
        with self.assertRaises(SMTPAuthenticationError):
            _send_set_password_email(self.request, self.user)
        smtp.return_value.sendmail.assert_not_called()

    @override_settings(MAILERS=MEMORY_MAILERS)
    def test_memory_backend_needs_no_smtp_credentials(self):
        _send_set_password_email(self.request, self.user)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertIn("/passwort/setzen/", mail.outbox[0].body)

    @override_settings(LEAD_NOTIFICATION_EMAIL="", DEFAULT_FROM_EMAIL="")
    def test_lead_recipient_falls_back_to_mailer_username(self):
        self.assertEqual(_lead_operator_recipients(), ["smtp-user"])


@override_settings(MAILERS=MEMORY_MAILERS)
class PasswordResetMailerTests(TestCase):
    def test_django_password_reset_uses_default_mailer(self):
        user = CustomUser.objects.create_user(
            username="reset-tutor", email="reset@example.com", password="test-password",
        )
        response = self.client.post(reverse("password_reset"), {"email": user.email})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [user.email])
        self.assertTrue(mail.outbox[0].alternatives)
