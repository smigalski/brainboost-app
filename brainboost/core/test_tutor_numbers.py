from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Barrier
from unittest import skipUnless

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase

from .admin import TutorProfileAdminForm
from .models import CustomUser, TutorNumberReservation, TutorProfile


class TutorNumberTests(TestCase):
    def tutor(self, name="Tutor", last="Test"):
        user = CustomUser.objects.create_user(
            username=f"tutor-{CustomUser.objects.count()}", first_name=name,
            last_name=last, role=CustomUser.Roles.TUTOR,
        )
        return TutorProfile.objects.create(user=user)

    def number(self, tutor):
        tutor.refresh_from_db()
        return tutor.tutor_number

    def test_nested_hierarchy_and_sibling_numbers(self):
        kiara = self.tutor("Kiara", "Puppe")
        one, two, child = self.tutor(), self.tutor(), self.tutor()
        kiara.assigned_tutors.add(one, two)
        one.assigned_tutors.add(child)
        self.assertEqual(self.number(kiara), "TUT1")
        self.assertEqual(self.number(one), "TUT1-1")
        self.assertEqual(self.number(two), "TUT1-2")
        self.assertEqual(self.number(child), "TUT1-1-1")
        # A stale Python instance must not write an old number back.
        child.tutor_number = "TUT99"
        child.save()
        self.assertEqual(self.number(child), "TUT1-1-1")

    def test_deleted_number_survives_user_cascade_and_is_never_reused(self):
        kiara = self.tutor("Kiara", "Puppe")
        one = self.tutor()
        kiara.assigned_tutors.add(one)
        original_pk = one.pk
        one.user.delete()
        archived = TutorNumberReservation.objects.get(number="TUT1-1")
        self.assertIsNone(archived.tutor)
        self.assertEqual(archived.original_tutor_id, original_pk)
        self.assertIsNotNone(archived.archived_at)
        two = self.tutor()
        kiara.assigned_tutors.add(two)
        self.assertEqual(self.number(two), "TUT1-2")

    def test_move_updates_descendants_and_archives_previous_numbers(self):
        kiara = self.tutor("Kiara", "Puppe")
        one, child, other = self.tutor(), self.tutor(), self.tutor()
        kiara.assigned_tutors.add(one)
        one.assigned_tutors.add(child)
        one.supervising_tutors.set([other])
        self.assertEqual(self.number(one), f"{self.number(other)}-1")
        self.assertEqual(self.number(child), f"{self.number(other)}-1-1")
        self.assertIsNotNone(TutorNumberReservation.objects.get(number="TUT1-1").archived_at)
        self.assertIsNotNone(TutorNumberReservation.objects.get(number="TUT1-1-1").archived_at)

    def test_invalid_hierarchies_rejected_atomically(self):
        kiara = self.tutor("Kiara", "Puppe")
        one, child, other = self.tutor(), self.tutor(), self.tutor()
        kiara.assigned_tutors.add(one)
        one.assigned_tutors.add(child)
        for parent, subordinate in [(other, one), (child, one), (one, one), (other, kiara)]:
            with self.assertRaises(ValidationError), transaction.atomic():
                parent.assigned_tutors.add(subordinate)
        self.assertEqual(list(one.supervising_tutors.all()), [kiara])

    def test_deleted_parent_does_not_renumber_surviving_children(self):
        kiara = self.tutor("Kiara", "Puppe")
        one, child, other = self.tutor(), self.tutor(), self.tutor()
        kiara.assigned_tutors.add(one)
        one.assigned_tutors.add(child)
        one.delete()
        kiara.assigned_tutors.add(other)
        self.assertEqual(self.number(child), "TUT1-1-1")

    def test_clear_and_reverse_add(self):
        kiara = self.tutor("Kiara", "Puppe")
        one = self.tutor()
        one.supervising_tutors.add(kiara)
        self.assertEqual(self.number(one), "TUT1-1")
        kiara.assigned_tutors.clear()
        self.assertTrue(self.number(one).startswith("TUT"))
        self.assertNotIn("-", self.number(one))
        one.supervising_tutors.add(kiara)
        self.assertEqual(self.number(one), "TUT1-2")

    def test_migration_replaces_existing_numbers_and_archives_them(self):
        kiara = self.tutor("Kiara", "Puppe")
        one = self.tutor()
        kiara.assigned_tutors.add(one)
        TutorNumberReservation.objects.all().delete()
        TutorProfile.objects.filter(pk=kiara.pk).update(tutor_number="TUT-000-001")
        TutorProfile.objects.filter(pk=one.pk).update(tutor_number="TUT-000-002")
        migration = import_module("core.migrations.0070_hierarchical_tutor_numbers")
        with connection.schema_editor() as editor:
            migration.migrate_numbers(apps, editor)
        self.assertEqual(self.number(kiara), "TUT1")
        self.assertEqual(self.number(one), "TUT1-1")
        self.assertIsNotNone(TutorNumberReservation.objects.get(number="TUT-000-002").archived_at)

    def test_admin_rejects_second_supervisor(self):
        parent, other, child = self.tutor(), self.tutor(), self.tutor()
        parent.assigned_tutors.add(child)
        form = TutorProfileAdminForm(instance=other)
        form.cleaned_data = {"assigned_tutors": TutorProfile.objects.filter(pk=child.pk)}
        with self.assertRaises(ValidationError):
            form.clean_assigned_tutors()


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row locking")
class ConcurrentTutorNumberTests(TransactionTestCase):
    def test_simultaneous_allocations_are_unique(self):
        users = [CustomUser.objects.create_user(username=f"parallel-{i}") for i in range(2)]
        barrier = Barrier(2)

        def create_tutor(user_id):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return TutorProfile.objects.create(user_id=user_id).tutor_number
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            numbers = list(executor.map(create_tutor, [user.pk for user in users]))
        self.assertEqual(set(numbers), {"TUT2", "TUT3"})
        self.assertEqual(TutorNumberReservation.objects.filter(archived_at__isnull=True).count(), 2)
