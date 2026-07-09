from __future__ import annotations

from django.db.models import QuerySet

from .models import CustomUser, Invoice, LearningMaterial, Lesson, StudentProfile, TutorProfile


def has_admin_access(user: CustomUser) -> bool:
    return bool(user.is_staff or user.is_superuser)


def is_learning_profile_user(user: CustomUser) -> bool:
    return user.role in {
        CustomUser.Roles.STUDENT,
        CustomUser.Roles.INDEPENDENT_STUDENT,
    }


def is_independent_student_user(user: CustomUser) -> bool:
    return user.role == CustomUser.Roles.INDEPENDENT_STUDENT


def is_tutor_user(user: CustomUser) -> bool:
    return user.role == CustomUser.Roles.TUTOR and hasattr(user, "tutor_profile")


def is_parent_user(user: CustomUser) -> bool:
    return user.role == CustomUser.Roles.PARENT and hasattr(user, "parent_profile")


def assigned_students_qs(tutor_profile: TutorProfile) -> QuerySet[StudentProfile]:
    return (
        StudentProfile.objects.filter(assigned_tutors=tutor_profile)
        .select_related("user")
        .distinct()
    )


def assigned_tutors_qs(tutor_profile: TutorProfile) -> QuerySet[TutorProfile]:
    return tutor_profile.assigned_tutors.select_related("user").distinct()


def can_access_student(user: CustomUser, student: StudentProfile) -> bool:
    if has_admin_access(user):
        return True
    if is_learning_profile_user(user) and hasattr(user, "student_profile"):
        return student.pk == user.student_profile.pk
    if is_parent_user(user):
        return student.parents.filter(pk=user.parent_profile.pk).exists()
    if is_tutor_user(user):
        return student.assigned_tutors.filter(pk=user.tutor_profile.pk).exists()
    return False


def accessible_students_qs(user: CustomUser) -> QuerySet[StudentProfile]:
    if has_admin_access(user):
        return StudentProfile.objects.select_related("user").distinct()
    if is_learning_profile_user(user) and hasattr(user, "student_profile"):
        return StudentProfile.objects.filter(pk=user.student_profile.pk).select_related("user")
    if is_parent_user(user):
        return user.parent_profile.students.select_related("user").distinct()
    if is_tutor_user(user):
        return assigned_students_qs(user.tutor_profile)
    return StudentProfile.objects.none()


def can_view_lesson(user: CustomUser, lesson: Lesson) -> bool:
    if is_tutor_user(user) and lesson.tutor_id == user.tutor_profile.pk:
        return True
    if is_learning_profile_user(user) and hasattr(user, "student_profile"):
        return lesson.student_id == user.student_profile.pk
    if is_parent_user(user):
        return user.parent_profile.students.filter(pk=lesson.student_id).exists()
    return False


def can_cancel_lesson(user: CustomUser, lesson: Lesson) -> bool:
    return can_view_lesson(user, lesson)


def can_request_lesson_reschedule(user: CustomUser, lesson: Lesson) -> bool:
    if is_learning_profile_user(user) and hasattr(user, "student_profile"):
        return lesson.student_id == user.student_profile.pk
    if is_parent_user(user):
        return user.parent_profile.students.filter(pk=lesson.student_id).exists()
    return False


def can_manage_lesson(user: CustomUser, lesson: Lesson) -> bool:
    return is_tutor_user(user) and lesson.tutor_id == user.tutor_profile.pk


def can_access_material(user: CustomUser, material: LearningMaterial) -> bool:
    if has_admin_access(user):
        return True
    if is_tutor_user(user):
        if material.uploaded_by_id == user.tutor_profile.pk:
            return True
        return material.student.assigned_tutors.filter(pk=user.tutor_profile.pk).exists()
    if is_learning_profile_user(user) and hasattr(user, "student_profile"):
        return material.student_id == user.student_profile.pk
    if is_parent_user(user):
        return material.student.parents.filter(pk=user.parent_profile.pk).exists()
    return False


def can_delete_material(user: CustomUser, material: LearningMaterial) -> bool:
    return is_tutor_user(user) and (
        material.uploaded_by_id == user.tutor_profile.pk or has_admin_access(user)
    )


def can_manage_invoice(user: CustomUser, invoice: Invoice) -> bool:
    if not is_tutor_user(user):
        return False
    return (
        invoice.uploaded_by_id == user.tutor_profile.pk
        or user.tutor_profile.assigned_tutors.filter(pk=invoice.uploaded_by_id).exists()
    )


def can_approve_invoice(user: CustomUser, invoice: Invoice) -> bool:
    return (
        is_tutor_user(user)
        and user.tutor_profile.assigned_tutors.filter(pk=invoice.uploaded_by_id).exists()
    )


def can_delete_invoice(user: CustomUser, invoice: Invoice) -> bool:
    return is_tutor_user(user) and (
        invoice.uploaded_by_id == user.tutor_profile.pk or has_admin_access(user)
    )


def can_view_invoice(user: CustomUser, invoice: Invoice) -> bool:
    if not invoice.is_approved:
        return False
    if is_parent_user(user):
        return invoice.student.parents.filter(pk=user.parent_profile.pk).exists()
    if is_independent_student_user(user) and hasattr(user, "student_profile"):
        return invoice.student_id == user.student_profile.pk
    return can_manage_invoice(user, invoice)


def payer_invoice_qs(user: CustomUser) -> QuerySet[Invoice]:
    queryset = Invoice.objects.filter(approved_at__isnull=False)
    if is_parent_user(user):
        return queryset.filter(student__parents=user.parent_profile)
    if is_independent_student_user(user) and hasattr(user, "student_profile"):
        return queryset.filter(student=user.student_profile)
    return Invoice.objects.none()
