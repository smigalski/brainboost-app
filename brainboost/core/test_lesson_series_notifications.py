from datetime import date, time
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import CustomUser, Lesson, StudentProfile, TutorProfile
from .notifications import notify_lesson_series_created


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="BrainBoost <brainboost@example.com>",
)
class LessonSeriesNotificationTests(TestCase):
    def setUp(self):
        self.tutor_user = CustomUser.objects.create_user(
            username="series_tutor",
            email="tutor@example.com",
            password="test12345",
            role=CustomUser.Roles.TUTOR,
        )
        self.student_user = CustomUser.objects.create_user(
            username="series_student",
            email="student@example.com",
            password="test12345",
            role=CustomUser.Roles.STUDENT,
        )
        self.tutor = TutorProfile.objects.create(user=self.tutor_user)
        self.student = StudentProfile.objects.create(user=self.student_user)
        self.student.assigned_tutors.add(self.tutor)
        self.client.force_login(self.tutor_user)

    def _post_data(self, **overrides):
        data = {
            "student": str(self.student.pk),
            "date": "2026-09-07",
            "time": "16:00",
            "duration_minutes": "60",
            "ort": Lesson.Ort.ONLINE,
            "fach": "mathe",
            "fach_2": "",
            "fach_3": "",
            "status": Lesson.Status.PLANNED,
        }
        data.update(overrides)
        return data

    @patch("core.views.lessons.notify_lesson_created")
    @patch("core.views.lessons.notify_lesson_series_created")
    def test_weekly_series_sends_one_summary_instead_of_one_mail_per_lesson(
        self,
        series_notify,
        single_notify,
    ):
        response = self.client.post(
            reverse("lesson_create"),
            data=self._post_data(
                repeat_enabled="on",
                repeat_interval_weeks="1",
                repeat_end_mode="count",
                repeat_occurrences="45",
            ),
        )

        self.assertRedirects(response, reverse("lesson_list"))
        lessons = list(Lesson.objects.order_by("date"))
        self.assertEqual(len(lessons), 45)
        single_notify.assert_not_called()
        series_notify.assert_called_once()
        call = series_notify.call_args
        self.assertEqual(call.args[1], lessons)
        self.assertEqual(call.kwargs["repeat_interval_weeks"], 1)

    @patch("core.views.lessons.notify_lesson_created")
    @patch("core.views.lessons.notify_lesson_series_created")
    def test_single_lesson_keeps_existing_single_notification(
        self,
        series_notify,
        single_notify,
    ):
        response = self.client.post(reverse("lesson_create"), data=self._post_data())

        self.assertRedirects(response, reverse("lesson_list"))
        self.assertEqual(Lesson.objects.count(), 1)
        series_notify.assert_not_called()
        single_notify.assert_called_once()

    def test_summary_email_explains_recurrence_range_and_count(self):
        lessons = [
            Lesson.objects.create(
                student=self.student,
                tutor=self.tutor,
                date=date(2026, 9, 7 + (7 * index)),
                time=time(16, 0),
                duration_minutes=60,
                ort=Lesson.Ort.ONLINE,
                fach="mathe",
            )
            for index in range(3)
        ]

        notify_lesson_series_created(
            self.client.request().wsgi_request,
            lessons,
            repeat_interval_weeks=1,
        )

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("Neue Terminserie", message.subject)
        self.assertIn("wöchentlich", message.body)
        self.assertIn("07.09.2026", message.body)
        self.assertIn("21.09.2026", message.body)
        self.assertIn("3 Termine", message.body)
