from django.test import TestCase
from django.urls import reverse

from .models import (
    CustomUser,
    HolidaySurvey,
    HolidaySurveyResponse,
    ParentProfile,
    StudentProfile,
    TutorProfile,
)


class HolidaySurveyAnswerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        tutor_user = CustomUser.objects.create_user(
            username="survey_tutor", role=CustomUser.Roles.TUTOR,
        )
        cls.tutor = TutorProfile.objects.create(user=tutor_user)
        cls.parent_user = CustomUser.objects.create_user(
            username="survey_parent", role=CustomUser.Roles.PARENT,
        )
        cls.parent = ParentProfile.objects.create(user=cls.parent_user)
        child_user = CustomUser.objects.create_user(
            username="survey_child", role=CustomUser.Roles.STUDENT,
        )
        cls.child = StudentProfile.objects.create(user=child_user)
        cls.child.parents.add(cls.parent)
        cls.independent_user = CustomUser.objects.create_user(
            username="survey_independent", role=CustomUser.Roles.INDEPENDENT_STUDENT,
        )
        cls.independent = StudentProfile.objects.create(user=cls.independent_user)
        survey = HolidaySurvey.objects.create(tutor=cls.tutor)
        cls.child_response = HolidaySurveyResponse.objects.create(
            survey=survey, student=cls.child,
        )
        cls.independent_response = HolidaySurveyResponse.objects.create(
            survey=survey, student=cls.independent,
        )

    def answer_cases(self):
        return (
            (self.parent_user, self.child_response, self.parent),
            (self.independent_user, self.independent_response, None),
        )

    def test_missing_answer_does_not_mark_response_as_answered(self):
        for user, survey_response, _ in self.answer_cases():
            with self.subTest(role=user.role):
                self.client.force_login(user)
                response = self.client.post(
                    reverse("holiday_surveys"), {"response_id": survey_response.pk},
                )
                self.assertEqual(response.status_code, 200)
                survey_response.refresh_from_db()
                self.assertEqual(survey_response.answer, "")
                self.assertIsNone(survey_response.answered_at)
                self.assertIsNone(survey_response.parent_id)
                form = response.context["response_items"][0]["form"]
                self.assertFormError(form, "answer", "Dieses Feld ist zwingend erforderlich.")
                self.assertContains(response, str(form.errors["answer"][0]))

    def test_invalid_answer_shows_validation_error(self):
        for user, survey_response, _ in self.answer_cases():
            with self.subTest(role=user.role):
                self.client.force_login(user)
                response = self.client.post(reverse("holiday_surveys"), {
                    "response_id": survey_response.pk, "answer": "invalid",
                })
                self.assertEqual(response.status_code, 200)
                form = response.context["response_items"][0]["form"]
                self.assertIn("answer", form.errors)
                self.assertContains(response, str(form.errors["answer"][0]))
                survey_response.refresh_from_db()
                self.assertEqual(survey_response.answer, "")
                self.assertIsNone(survey_response.answered_at)

    def test_valid_answer_stores_responding_parent_or_independent_student(self):
        for answer in HolidaySurveyResponse.Answer.values:
            for user, survey_response, parent in self.answer_cases():
                with self.subTest(role=user.role, answer=answer):
                    self.client.force_login(user)
                    response = self.client.post(reverse("holiday_surveys"), {
                        "response_id": survey_response.pk, "answer": answer,
                    })
                    self.assertRedirects(response, reverse("holiday_surveys"))
                    survey_response.refresh_from_db()
                    self.assertEqual(survey_response.answer, answer)
                    self.assertIsNotNone(survey_response.answered_at)
                    self.assertEqual(survey_response.parent, parent)

    def test_validation_errors_belong_only_to_submitted_survey(self):
        survey = HolidaySurvey.objects.create(tutor=self.tutor, question="Weitere Umfrage?")
        for user, survey_response, _ in self.answer_cases():
            with self.subTest(role=user.role):
                other_response = HolidaySurveyResponse.objects.create(
                    survey=survey, student=survey_response.student,
                )
                self.client.force_login(user)
                response = self.client.post(reverse("holiday_surveys"), {
                    "response_id": survey_response.pk, "answer": "invalid",
                })
                items = {item["response"].pk: item["form"] for item in response.context["response_items"]}
                self.assertTrue(items[survey_response.pk].is_bound)
                self.assertIn("answer", items[survey_response.pk].errors)
                self.assertFalse(items[other_response.pk].is_bound)
                self.assertFalse(items[other_response.pk].errors)

    def test_users_cannot_answer_another_familys_survey(self):
        for user, other_response in (
            (self.parent_user, self.independent_response),
            (self.independent_user, self.child_response),
        ):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                response = self.client.post(reverse("holiday_surveys"), {
                    "response_id": other_response.pk, "answer": "yes",
                })
                self.assertEqual(response.status_code, 404)
                other_response.refresh_from_db()
                self.assertEqual(other_response.answer, "")

    def test_regular_student_cannot_answer_surveys(self):
        self.client.force_login(self.child.user)
        response = self.client.post(reverse("holiday_surveys"), {
            "response_id": self.child_response.pk, "answer": "yes",
        })
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        self.child_response.refresh_from_db()
        self.assertEqual(self.child_response.answer, "")
