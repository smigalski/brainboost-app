from datetime import date, time
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import FamilyAccountForm, LeadFamilyConversionForm
from .models import CustomUser, Lead, Lesson, ParentProfile, StudentProfile, TutorProfile
from .views.common import _assign_location_and_distance


@override_settings(MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}})
class FamilyAccountFlowTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="family_tutor", role=CustomUser.Roles.TUTOR, is_staff=False,
        )
        self.tutor = TutorProfile.objects.create(
            user=self.tutor_user, address="Tutorweg 1, 38100 Braunschweig",
        )
        self.staff = CustomUser.objects.create_user(username="family_staff", is_staff=True)
        self.lead = Lead.objects.create(
            name="Maria Muster", email="maria@example.com", phone="0176123456",
            role=Lead.Role.PARENT, subject="Mathe", goal="Prüfung", grade="8",
            tutoring_type=Lead.TutoringType.IN_PERSON,
        )
        self.data = {
            "student_kind": "with_parent", "student_first_name": "Sina",
            "student_last_name": "Muster", "student_phone": "0176123456",
            "student_address": "Kindweg 2, 38100 Braunschweig",
            "create_parent": "on", "parent_first_name": "Maria", "parent_last_name": "Muster",
            "parent_email": "maria@example.com", "parent_phone": "0176123456",
            "parent_address": "Elternweg 3, 38100 Braunschweig",
            "assigned_tutor": str(self.tutor.pk),
            "zoom_link": "https://bbb.example.com/room", "zumpad_link": "https://pad.example.com/room",
        }
        self.client.force_login(self.tutor_user)

    def parent(self, username):
        user = CustomUser.objects.create_user(
            username=username, email=f"{username}@example.com", role=CustomUser.Roles.PARENT,
        )
        return ParentProfile.objects.create(user=user, phone_number="0176123456", address="Elternweg 1, 38100 Braunschweig")

    def test_lead_and_tutor_share_fields_and_template(self):
        tutor_page = self.client.get(reverse("student_create"))
        self.client.force_login(self.staff)
        lead_page = self.client.get(reverse("lead_convert_to_family", args=[self.lead.pk]))
        self.assertTemplateUsed(tutor_page, "lead_family_convert.html")
        self.assertTemplateUsed(lead_page, "lead_family_convert.html")
        tutor_fields = tutor_page.context["form"].fields
        lead_fields = lead_page.context["form"].fields
        self.assertEqual(set(tutor_fields), set(lead_fields) - {"assigned_tutor"})
        self.assertContains(tutor_page, "name=\"zoom_link\"")
        self.assertContains(tutor_page, "name=\"zumpad_link\"")
        self.assertNotContains(tutor_page, 'name="password1"')
        self.assertNotContains(tutor_page, 'name="assigned_tutor"')
        self.assertEqual(lead_page.context["form"].initial["affected_courses"], "Mathe")
        self.assertEqual(lead_page.context["form"].initial["tutoring_goal"], "Prüfung")

    def test_tutor_can_create_parent_and_student_in_one_step_without_existing_parents(self):
        response = self.client.post(reverse("student_create"), self.data)
        self.assertRedirects(response, reverse("dashboard"))
        student = StudentProfile.objects.get()
        self.assertEqual(student.parents.count(), 1)
        self.assertEqual(student.created_by_tutor, self.tutor)
        self.assertEqual(list(student.assigned_tutors.all()), [self.tutor])
        self.assertEqual(student.address, self.data["student_address"])
        self.assertEqual(student.zoom_link, self.data["zoom_link"])
        self.assertEqual(student.zumpad_link, self.data["zumpad_link"])
        self.assertFalse(student.user.has_usable_password())
        self.assertFalse(student.user.is_staff)
        self.assertEqual(len(mail.outbox), 1)  # Only the parent has an email.
        self.assertEqual(mail.outbox[0].to, ["maria@example.com"])
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.NEW)

    def test_lead_conversion_links_multiple_existing_parents_without_reinviting_them(self):
        parents = [self.parent("mother"), self.parent("father")]
        self.client.force_login(self.staff)
        response = self.client.post(reverse("lead_convert_to_family", args=[self.lead.pk]), {
            **self.data, "create_parent": "", "existing_parents": [p.pk for p in parents],
            "student_email": "sina@example.com", "affected_courses": "Mathe, Physik", "tutoring_goal": "Abitur",
        })
        self.assertEqual(response.status_code, 302)
        self.lead.refresh_from_db()
        student = self.lead.converted_student
        self.assertSetEqual(set(student.parents.all()), set(parents))
        self.assertIn(self.lead.converted_parent, parents)
        self.assertEqual(ParentProfile.objects.count(), 2)
        self.assertEqual(student.affected_courses, "Mathe, Physik")
        self.assertEqual(student.tutoring_goal, "Abitur")
        self.assertEqual(student.zoom_link, self.data["zoom_link"])
        self.assertEqual(student.zumpad_link, self.data["zumpad_link"])
        self.assertEqual(self.lead.status, Lead.Status.WON)
        self.assertEqual([message.to for message in mail.outbox], [["sina@example.com"]])

    def test_existing_and_new_parent_can_be_combined(self):
        parent = self.parent("father")
        response = self.client.post(reverse("student_create"), {**self.data, "existing_parents": [parent.pk]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StudentProfile.objects.get().parents.count(), 2)
        self.assertEqual(ParentProfile.objects.count(), 2)
        self.assertEqual(len(mail.outbox), 1)

    def test_required_contact_fields_reject_blank_values_without_partial_accounts(self):
        before = CustomUser.objects.count()
        for field in ("student_phone", "student_address", "parent_phone", "parent_address"):
            with self.subTest(field=field):
                response = self.client.post(reverse("student_create"), {**self.data, field: "  "})
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["form"].errors)
                self.assertContains(response, "Bitte prüfe die Angaben")
                self.assertEqual(CustomUser.objects.count(), before)
                self.assertEqual(ParentProfile.objects.count(), 0)
                self.assertEqual(StudentProfile.objects.count(), 0)

    def test_lead_requires_contact_fields_too(self):
        self.client.force_login(self.staff)
        for field in ("student_phone", "student_address", "parent_phone", "parent_address"):
            with self.subTest(field=field):
                response = self.client.post(reverse("lead_convert_to_family", args=[self.lead.pk]), {**self.data, field: ""})
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["form"].errors)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.NEW)
        self.assertIsNone(self.lead.converted_student_id)

    def test_existing_parent_is_reused_without_silently_changing_profile(self):
        parent = self.parent("incomplete")
        parent.address = ""
        parent.save()
        form = FamilyAccountForm({**self.data, "create_parent": "", "existing_parents": [parent.pk]}, tutor=self.tutor)
        self.assertTrue(form.is_valid(), form.errors)
        student = form.save()["student"]
        self.assertEqual(student.address, self.data["student_address"])
        self.assertEqual(list(student.parents.all()), [parent])
        parent.refresh_from_db()
        self.assertEqual(parent.address, "")

    def test_invalid_parent_selection_and_links_are_rejected(self):
        for change, field in (({"existing_parents": [999999]}, "existing_parents"),
                              ({"zoom_link": "kein-link"}, "zoom_link"),
                              ({"zumpad_link": "kein-link"}, "zumpad_link")):
            with self.subTest(field=field):
                form = FamilyAccountForm({**self.data, **change}, tutor=self.tutor)
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_links_can_be_empty(self):
        form = FamilyAccountForm({**self.data, "zoom_link": "", "zumpad_link": ""}, tutor=self.tutor)
        self.assertTrue(form.is_valid(), form.errors)
        result = form.save()
        self.assertEqual(result["student"].zoom_link, "")
        self.assertEqual(result["student"].zumpad_link, "")

    def test_parent_only_uses_same_contact_fields_and_sends_invitation(self):
        response = self.client.post(reverse("parent_create"), self.data)
        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(StudentProfile.objects.count(), 0)
        self.assertEqual(ParentProfile.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        form = FamilyAccountForm(parent_only=True, tutor=self.tutor)
        self.assertEqual(set(form.fields), set(FamilyAccountForm.PARENT_FIELDS))

    def test_independent_account_ignores_stale_hidden_parent_data(self):
        response = self.client.post(reverse("independent_student_create"), {
            **self.data, "student_kind": "independent", "student_email": "solo@example.com",
            "parent_email": "invalid-email", "existing_parents": [999999],
        })
        self.assertRedirects(response, reverse("dashboard"))
        student = StudentProfile.objects.get()
        self.assertEqual(student.user.role, CustomUser.Roles.INDEPENDENT_STUDENT)
        self.assertFalse(student.parents.exists())
        self.assertEqual(ParentProfile.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_independent_account_requires_email(self):
        form = FamilyAccountForm({**self.data, "student_kind": "independent"}, tutor=self.tutor)
        self.assertFalse(form.is_valid())
        self.assertIn("student_email", form.errors)

    def test_parent_lead_cannot_be_converted_without_parent(self):
        form = LeadFamilyConversionForm({**self.data, "student_kind": "independent", "student_email": "solo@example.com"}, lead=self.lead)
        self.assertFalse(form.is_valid())
        self.assertIn("student_kind", form.errors)

    def test_duplicate_emails_do_not_create_duplicate_accounts(self):
        self.parent("existing")
        for change, field in (({"parent_email": "EXISTING@example.com"}, "parent_email"),
                              ({"student_email": "existing@example.com"}, "student_email"),
                              ({"student_email": "MARIA@example.com"}, "student_email")):
            with self.subTest(field=field):
                form = FamilyAccountForm({**self.data, **change}, tutor=self.tutor)
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_tutor_cannot_assign_student_to_someone_else(self):
        other = TutorProfile.objects.create(user=CustomUser.objects.create_user(username="other_tutor", role=CustomUser.Roles.TUTOR))
        response = self.client.post(reverse("student_create"), {**self.data, "assigned_tutor": other.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(StudentProfile.objects.get().assigned_tutors.all()), [self.tutor])

    def test_non_active_tutors_cannot_create_accounts(self):
        for status, _ in TutorProfile.Status.choices:
            if status == TutorProfile.Status.ACTIVE:
                continue
            self.tutor.status = status
            self.tutor.save()
            for route in ("parent_create", "student_create", "independent_student_create"):
                with self.subTest(status=status, route=route):
                    response = self.client.post(reverse(route), self.data)
                    self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        self.assertEqual(ParentProfile.objects.count(), 0)
        self.assertEqual(StudentProfile.objects.count(), 0)

    def test_normal_tutor_cannot_convert_leads_or_parent_user_create_accounts(self):
        response = self.client.post(reverse("lead_convert_to_family", args=[self.lead.pk]), self.data)
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        parent = self.parent("parent_login")
        self.client.force_login(parent.user)
        for route in ("parent_create", "student_create", "independent_student_create"):
            response = self.client.post(reverse(route), self.data)
            self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        self.assertEqual(StudentProfile.objects.count(), 0)

    def test_admin_cannot_assign_inactive_tutor(self):
        self.tutor.status = TutorProfile.Status.PAUSED
        self.tutor.save()
        form = LeadFamilyConversionForm(self.data, lead=self.lead)
        self.assertFalse(form.is_valid())
        self.assertIn("assigned_tutor", form.errors)

    def test_lead_cannot_be_converted_twice(self):
        form = LeadFamilyConversionForm(self.data, lead=self.lead)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        before = CustomUser.objects.count()
        with self.assertRaisesMessage(ValueError, "already_converted"):
            form.save()
        self.assertEqual(CustomUser.objects.count(), before)

    @patch("core.forms.StudentProfile.objects.create", side_effect=IntegrityError("test failure"))
    def test_creation_is_atomic_and_reports_error_without_losing_input(self, _mock_create):
        response = self.client.post(reverse("student_create"), self.data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Die Konten konnten nicht angelegt werden")
        self.assertContains(response, 'value="Sina"')
        self.assertEqual(ParentProfile.objects.count(), 0)
        self.assertEqual(StudentProfile.objects.count(), 0)

    @patch("core.views.common._send_set_password_email", side_effect=RuntimeError("test mail unavailable"))
    def test_mail_failure_keeps_created_accounts_and_warns_user(self, _mock_send):
        response = self.client.post(reverse("student_create"), self.data, follow=True)
        self.assertContains(response, "Passwort-Mail konnte nicht an alle versendet werden")
        self.assertEqual(ParentProfile.objects.count(), 1)
        self.assertEqual(StudentProfile.objects.count(), 1)

    @patch("core.views.common._google_driving_distance_km", return_value=Decimal("5.50"))
    def test_home_lesson_distance_uses_the_saved_student_address(self, route_distance):
        form = FamilyAccountForm(self.data, tutor=self.tutor)
        self.assertTrue(form.is_valid(), form.errors)
        student = form.save()["student"]
        lesson = Lesson(tutor=self.tutor, student=student, date=date(2026, 10, 12), time=time(15),
                        duration_minutes=60, ort=Lesson.Ort.ZUHAUSE_STUDENT, fach="mathe")
        _assign_location_and_distance(lesson)
        route_distance.assert_called_once_with(self.tutor.address, self.data["student_address"])
        self.assertEqual(lesson.distance_km, Decimal("11.00"))
