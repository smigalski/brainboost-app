from datetime import date, time, timedelta
from decimal import Decimal

from django.test import TestCase
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
)
from .views import (
    _auto_complete_past_lessons,
    _build_epc_payment_payload,
    _build_invoice_pdf_context,
    _build_progress_chart_data,
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

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].id, self.newer_entry.id)
        self.assertEqual(entries[1].id, self.older_entry.id)

    def test_parent_dashboard_lists_progress_newest_first(self):
        logged_in = self.client.login(
            username="parent_progress_order",
            password="test12345",
        )
        self.assertTrue(logged_in)

        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        entries = list(response.context["progress_entries"])

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].id, self.newer_entry.id)
        self.assertEqual(entries[1].id, self.older_entry.id)


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
