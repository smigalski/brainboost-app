from datetime import date, time
from decimal import Decimal

from django.test import TestCase

from .forms import InvoiceGenerateForm
from .models import CustomUser, Invoice, Lesson, StudentProfile, TutorProfile
from .views import _build_invoice_pdf_context


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
