"""Permanent reservations for hierarchical tutor numbers.

All allocation and relationship changes serialize on the singleton lock row.
Never delete reservations: even former numbers cannot be allocated again.
"""
from django.core.exceptions import ValidationError
from django.db.models.signals import m2m_changed, pre_delete
from django.dispatch import receiver
from django.utils import timezone

from .models import TutorNumberLock, TutorNumberReservation, TutorProfile


def lock_numbers(using):
    TutorNumberLock.objects.using(using).get_or_create(pk=1)
    TutorNumberLock.objects.using(using).select_for_update().get(pk=1)


def validate_hierarchy(edges, root_ids=()):
    parents = {}
    for parent, child in edges:
        if child in parents and parents[child] != parent:
            raise ValidationError("Eine TutorIn darf nur eine übergeordnete TutorIn haben.")
        parents[child] = parent
    for child in parents:
        seen = set()
        node = child
        while node in parents:
            if node in seen:
                raise ValidationError("Kreiszuordnungen und Selbstzuordnungen sind nicht erlaubt.")
            seen.add(node)
            node = parents[node]
    if any(pk in parents for pk in root_ids):
        raise ValidationError("Kiara Puppe (TUT1) muss an der Spitze ihrer Hierarchie bleiben.")
    return parents


def hierarchy_edges(using):
    return set(TutorProfile.assigned_tutors.through.objects.using(using).values_list(
        "from_tutorprofile_id", "to_tutorprofile_id"
    ))


def root_ids(using):
    # The reserved identity is retained even if the account is renamed later.
    return set(TutorNumberReservation.objects.using(using).filter(
        number="TUT1", tutor__isnull=False
    ).values_list("tutor_id", flat=True))


def allocate(tutor, prefix, using, kiara=False):
    reservations = TutorNumberReservation.objects.using(using)
    if kiara and not reservations.filter(number="TUT1").exists():
        number = "TUT1"
    else:
        used = reservations.filter(number__startswith=prefix).values_list("number", flat=True)
        suffixes = [int(n[len(prefix):]) for n in used if n[len(prefix):].isdigit()]
        number = f"{prefix}{max(suffixes + ([1] if prefix == 'TUT' else [0])) + 1}"
    if len(number) > 255:
        raise ValidationError("Die TutorInnenhierarchie überschreitet die maximale Nummernlänge.")
    reservations.filter(tutor=tutor, archived_at__isnull=True).update(archived_at=timezone.now())
    reservations.create(number=number, tutor=tutor, original_tutor_id=tutor.pk)
    TutorProfile.objects.using(using).filter(pk=tutor.pk).update(tutor_number=number)
    tutor.tutor_number = number


def assign_initial_number(tutor, using):
    lock_numbers(using)
    is_kiara = (tutor.user.first_name.strip().casefold(), tutor.user.last_name.strip().casefold()) == ("kiara", "puppe")
    allocate(tutor, "TUT", using, kiara=is_kiara)


def renumber_hierarchy(using, affected):
    profiles = {p.pk: p for p in TutorProfile.objects.using(using).all()}
    parents = validate_hierarchy(hierarchy_edges(using), root_ids(using))
    affected = set(affected)
    while True:
        expanded = affected | {child for child, parent in parents.items() if parent in affected}
        if expanded == affected:
            break
        affected = expanded
    pending = affected & profiles.keys()
    done = profiles.keys() - pending
    while pending:
        ready = sorted(pk for pk in pending if parents.get(pk) is None or parents[pk] in done)
        for pk in ready:
            tutor = profiles[pk]
            prefix = f"{profiles[parents[pk]].tutor_number}-" if pk in parents else "TUT"
            suffix = (tutor.tutor_number or "")[len(prefix):]
            if not (tutor.tutor_number or "").startswith(prefix) or not suffix.isdigit():
                allocate(tutor, prefix, using)
            done.add(pk)
            pending.remove(pk)


@receiver(m2m_changed, sender=TutorProfile.assigned_tutors.through)
def tutor_hierarchy_changed(sender, instance, action, reverse, pk_set, using, **kwargs):
    if action.startswith("pre_"):
        lock_numbers(using)
        if reverse:
            affected = {instance.pk}
        elif action == "pre_clear":
            affected = set(instance.assigned_tutors.using(using).values_list("pk", flat=True))
        else:
            affected = set(pk_set)
        instance._numbering_affected = affected
        if action == "pre_add":
            edges = hierarchy_edges(using)
            edges.update((pk, instance.pk) if reverse else (instance.pk, pk) for pk in pk_set)
            validate_hierarchy(edges, root_ids(using))
    elif action.startswith("post_"):
        renumber_hierarchy(using, instance._numbering_affected)
        instance.tutor_number = TutorProfile.objects.using(using).get(pk=instance.pk).tutor_number


@receiver(pre_delete, sender=TutorProfile)
def archive_deleted_tutor(sender, instance, using, **kwargs):
    lock_numbers(using)
    TutorNumberReservation.objects.using(using).filter(
        tutor=instance, archived_at__isnull=True
    ).update(archived_at=timezone.now())
