from django.urls import path, reverse_lazy
from django.contrib.auth import views as auth_views

from . import views

urlpatterns = [
    path("", views.landing_page, name="landing_page"),
    path("kontakt/", views.contact, name="contact"),
    path("impressum/", views.impressum, name="impressum"),
    path("agbs/", views.agbs, name="agbs"),
    path("preise/", views.pricing, name="pricing"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("profil/", views.profile_view, name="profile"),
    path(
        "profil/passwort/",
        auth_views.PasswordChangeView.as_view(
            template_name="password_change.html",
            success_url=reverse_lazy("profile"),
        ),
        name="password_change",
    ),
    path("eltern/neu/", views.parent_create, name="parent_create"),
    path("tutoren/neu/", views.tutor_create, name="tutor_create"),
    path("schueler/neu/", views.student_create, name="student_create"),
    path("schueler/zugewiesen/", views.assigned_student_list, name="assigned_student_list"),
    path("tutoren/zugewiesen/", views.assigned_tutor_list, name="assigned_tutor_list"),
    path("termine/", views.lesson_list, name="lesson_list"),
    path("termine/neu/", views.lesson_create, name="lesson_create"),
    path("termine/<int:lesson_id>/bearbeiten/", views.lesson_edit, name="lesson_edit"),
    path("termine/<int:lesson_id>/stornieren/", views.lesson_cancel, name="lesson_cancel"),
    path("termine/<int:lesson_id>/verlegen/", views.lesson_reschedule_request, name="lesson_reschedule_request"),
    path("termine/<int:lesson_id>/ics/", views.lesson_ics, name="lesson_ics"),
    path("termine/<int:lesson_id>/loeschen/", views.lesson_delete, name="lesson_delete"),
    path("material/<str:kind>/upload/", views.material_upload, name="material_upload"),
    path("loesungen/", views.tutor_solution_list, name="tutor_solution_list"),
    path("vorlagen/", views.tutor_template_list, name="tutor_template_list"),
    path("rechnungen/neu/", views.invoice_upload, name="invoice_upload"),
    path("rechnungen/<int:invoice_id>/genehmigen/", views.invoice_approve, name="invoice_approve"),
    path("rechnungen/", views.invoice_list, name="invoice_list"),
    path("lernfortschritt/", views.progress_view, name="progress"),
    path(
        "schueler/<int:student_id>/lernfortschritt/",
        views.progress_view,
        name="progress_student",
    ),
    path(
        "lernfortschritt/neu/",
        views.progress_create,
        name="progress_create",
    ),
    path(
        "lernfortschritt/neu/<int:lesson_id>/",
        views.progress_create,
        name="progress_create_for_lesson",
    ),
    path(
        "passwort/setzen/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="password_reset_confirm.html",
            success_url=reverse_lazy("password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "passwort/gesetzt/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
