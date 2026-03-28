from datetime import date, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import authenticate
from django.core import mail
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import BrainBoostFeedbackForm, InvoiceGenerateForm
from .models import (
    BrainBoostFeedback,
    CustomUser,
    FAQItem,
    Invoice,
    Lesson,
    ParentProfile,
    ProgressEntry,
    StudentProfile,
    TutorProfile,
    TemporaryTutorAssignment,
)
from .views import (
    _auto_complete_past_lessons,
    _build_epc_payment_payload,
    _build_invoice_pdf_context,
    _build_progress_chart_data,
    _sync_temporary_tutor_assignments,
)


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


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
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

        with patch("core.views._generate_invoice_pdf", side_effect=fake_generate_invoice_pdf):
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
