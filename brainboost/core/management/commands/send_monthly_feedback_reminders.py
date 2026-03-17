from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import BrainBoostFeedback, CustomUser, MonthlyFeedbackReminderLog
from core.notifications import notify_monthly_brainboost_feedback


class Command(BaseCommand):
    help = "Sendet den monatlichen anonymen BrainBoost-Feedback-Reminder per E-Mail."

    def add_arguments(self, parser):
        parser.add_argument(
            "--month",
            help="Monat im Format YYYY-MM. Default: aktueller Monat.",
        )
        parser.add_argument(
            "--base-url",
            help="Basis-URL fuer Feedback-Links, z. B. https://www.nachhilfe-brainboost.de",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Erzwingt erneuten Versand, auch wenn der Monat bereits versendet wurde.",
        )

    def handle(self, *args, **options):
        month_value = options.get("month")
        month_start = self._parse_month(month_value) if month_value else date.today().replace(day=1)
        force = bool(options.get("force"))
        base_url = (options.get("base_url") or getattr(settings, "APP_BASE_URL", "")).strip()
        if not base_url:
            raise CommandError("Keine APP_BASE_URL vorhanden. Bitte --base-url setzen oder APP_BASE_URL konfigurieren.")

        role_map = {
            BrainBoostFeedback.Audience.STUDENT: CustomUser.Roles.STUDENT,
            BrainBoostFeedback.Audience.PARENT: CustomUser.Roles.PARENT,
            BrainBoostFeedback.Audience.TUTOR: CustomUser.Roles.TUTOR,
        }

        for audience, user_role in role_map.items():
            if not force and MonthlyFeedbackReminderLog.objects.filter(
                audience=audience,
                month=month_start,
            ).exists():
                self.stdout.write(
                    self.style.WARNING(
                        f"Übersprungen ({audience}): für {month_start:%Y-%m} bereits versendet."
                    )
                )
                continue

            recipients = list(
                CustomUser.objects.filter(role=user_role, is_active=True)
                .exclude(email="")
                .values_list("email", flat=True)
            )
            sent = notify_monthly_brainboost_feedback(
                base_url=base_url,
                audience=audience,
                recipients=recipients,
            )
            if not recipients:
                self.stdout.write(self.style.WARNING(f"Keine Empfänger für {audience} gefunden."))
                continue
            if not sent:
                self.stdout.write(self.style.ERROR(f"Versand fehlgeschlagen für {audience}."))
                continue

            MonthlyFeedbackReminderLog.objects.update_or_create(
                audience=audience,
                month=month_start,
                defaults={"recipients_count": len(recipients)},
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Versendet ({audience}) an {len(recipients)} EmpfängerInnen für {month_start:%Y-%m}."
                )
            )

    def _parse_month(self, value: str) -> date:
        raw = (value or "").strip()
        try:
            year_str, month_str = raw.split("-", 1)
            year = int(year_str)
            month = int(month_str)
            if month < 1 or month > 12:
                raise ValueError
            return date(year, month, 1)
        except Exception as exc:
            raise CommandError("Ungültiger Monat. Bitte YYYY-MM verwenden, z. B. 2026-03.") from exc
