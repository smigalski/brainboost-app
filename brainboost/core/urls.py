from django.urls import path, reverse_lazy
from django.contrib.auth import views as auth_views

from . import views
from .forms import EmailOrUsernameAuthenticationForm

urlpatterns = [
    path("", views.landing_page, name="landing_page"),
    path("tutorin-werden/", views.tutorin_werden, name="tutorin_werden"),
    path("feedback/brainboost/", views.brainboost_feedback, name="brainboost_feedback"),
    path("kontakt/", views.contact, name="contact"),
    path("impressum/", views.impressum, name="impressum"),
    path("agbs/", views.agbs, name="agbs"),
    path("preise/", views.pricing, name="pricing"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("admins/", views.admin_tasks, name="admin_tasks"),
    path("admins/tasks/<int:task_id>/status/", views.admin_task_status_update, name="admin_task_status_update"),
    path("faq/", views.faq_admin, name="faq_admin"),
    path("faq/frage/", views.faq_submit, name="faq_submit"),
    path("organisation/rundmail/", views.broadcast_email_send, name="broadcast_email_send"),
    path(
        "organisation/schueler-zuweisen/",
        views.tutor_student_assignment,
        name="tutor_student_assignment",
    ),
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
    path("termine/<int:lesson_id>/google-calendar/", views.lesson_google_calendar, name="lesson_google_calendar"),
    path("termine/<int:lesson_id>/ics/", views.lesson_ics, name="lesson_ics"),
    path("termine/<int:lesson_id>/loeschen/", views.lesson_delete, name="lesson_delete"),
    path("material/<str:kind>/upload/", views.material_upload, name="material_upload"),
    path("material/<int:material_id>/download/", views.material_download, name="material_download"),
    path("material/<int:material_id>/loeschen/", views.material_delete, name="material_delete"),
    path("loesungen/", views.tutor_solution_list, name="tutor_solution_list"),
    path("vorlagen/", views.tutor_template_list, name="tutor_template_list"),
    path("vorlagen/<int:template_id>/loeschen/", views.tutor_template_delete, name="tutor_template_delete"),
    path("umfragen/", views.holiday_surveys, name="holiday_surveys"),
    path("rechnungen/neu/", views.invoice_upload, name="invoice_upload"),
    path("rechnungen/<int:invoice_id>/genehmigen/", views.invoice_approve, name="invoice_approve"),
    path("rechnungen/<int:invoice_id>/eltern/<int:parent_id>/benachrichtigen/", views.invoice_notify_parent, name="invoice_notify_parent"),
    path("rechnungen/<int:invoice_id>/loeschen/", views.invoice_delete, name="invoice_delete"),
    path("rechnungen/<int:invoice_id>/zahlungsart/<str:method>/", views.invoice_select_payment, name="invoice_select_payment"),
    path("rechnungen/<int:invoice_id>/checkout/", views.invoice_checkout, name="invoice_checkout"),
    path("rechnungen/<int:invoice_id>/zahlung-bestaetigen/", views.invoice_confirm_payment, name="invoice_confirm_payment"),
    path("stripe/webhook/", views.stripe_webhook, name="stripe_webhook"),
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
        "lernfortschritt/<int:entry_id>/bearbeiten/",
        views.progress_edit,
        name="progress_edit",
    ),
    path(
        "lernfortschritt/<int:entry_id>/loeschen/",
        views.progress_delete,
        name="progress_delete",
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
        "passwort/vergessen/",
        auth_views.PasswordResetView.as_view(
            template_name="password_reset_form.html",
            email_template_name="emails/password_reset_email.txt",
            html_email_template_name="emails/password_reset_email.html",
            subject_template_name="emails/password_reset_subject.txt",
            extra_email_context={"heading": "Passwort zurücksetzen"},
            success_url=reverse_lazy("password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "passwort/vergessen/gesendet/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="password_reset_done.html",
        ),
        name="password_reset_done",
    ),
    path(
        "nutzer/<int:user_id>/passwort-mail/",
        views.resend_set_password_email,
        name="resend_set_password_email",
    ),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="login.html",
            authentication_form=EmailOrUsernameAuthenticationForm,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
