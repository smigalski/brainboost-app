from django.urls import path
from django.contrib.auth import views as auth_views

from . import views

urlpatterns = [
    path("", views.landing_page, name="landing_page"),
    path("kontakt/", views.contact, name="contact"),
    path("impressum/", views.impressum, name="impressum"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("termine/", views.lesson_list, name="lesson_list"),
    path("termine/neu/", views.lesson_create, name="lesson_create"),
    path("termine/<int:lesson_id>/bearbeiten/", views.lesson_edit, name="lesson_edit"),
    path("termine/<int:lesson_id>/stornieren/", views.lesson_cancel, name="lesson_cancel"),
    path("termine/<int:lesson_id>/loeschen/", views.lesson_delete, name="lesson_delete"),
    path("material/<str:kind>/upload/", views.material_upload, name="material_upload"),
    path("fortschritt/", views.progress_view, name="progress"),
    path(
        "schueler/<int:student_id>/fortschritt/",
        views.progress_view,
        name="progress_student",
    ),
    path(
        "fortschritt/neu/",
        views.progress_create,
        name="progress_create",
    ),
    path(
        "fortschritt/neu/<int:lesson_id>/",
        views.progress_create,
        name="progress_create_for_lesson",
    ),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
