from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import CustomUser


class Command(BaseCommand):
    help = "Löscht archivierte Konten, deren dreimonatige Aufbewahrungsfrist abgelaufen ist."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Zeigt fällige Konten an, ohne sie zu löschen.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        due_users = list(
            CustomUser.objects.filter(
                is_active=False,
                archived_at__isnull=False,
                scheduled_deletion_at__lte=now,
                is_staff=False,
                is_superuser=False,
            ).order_by("scheduled_deletion_at", "pk")
        )
        if options["dry_run"]:
            for user in due_users:
                self.stdout.write(
                    f"Fällig: ID {user.pk}, {user.email}, seit {user.scheduled_deletion_at:%d.%m.%Y %H:%M}"
                )
            self.stdout.write(self.style.WARNING(f"{len(due_users)} Konto/Konten wären zu löschen."))
            return

        deleted_users = 0
        for user in due_users:
            profile_image = user.profile_image
            with transaction.atomic():
                user.delete()
            if profile_image:
                profile_image.delete(save=False)
            deleted_users += 1
        self.stdout.write(self.style.SUCCESS(f"{deleted_users} archivierte(s) Konto/Konten gelöscht."))
