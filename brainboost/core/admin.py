import csv

from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _

from .models import (
    CustomUser,
    ParentProfile,
    StudentProfile,
    TutorProfile,
    TutorNumberReservation,
    CancellationRequest,
    Lesson,
    ProgressEntry,
    Invoice,
    TutorTemplate,
    Lead,
)


admin.site.site_header = "BrainBoost Verwaltung"
admin.site.site_title = "BrainBoost Admin"
admin.site.index_title = "Konten, Unterricht und Systemdaten"
admin.site.site_url = "/admins/"


class TutorProfileAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].help_text = (
            "Bewerbungsabsagen und Warteliste über den zugehörigen Lead verwalten. "
            "Dort werden Status und E-Mail-Benachrichtigung zusammen aktualisiert."
        )

    def clean_assigned_tutors(self):
        from .tutor_numbers import hierarchy_edges, root_ids, validate_hierarchy

        tutors = self.cleaned_data["assigned_tutors"]
        using = self.instance._state.db or "default"
        parent_id = self.instance.pk or -1
        edges = {(parent, child) for parent, child in hierarchy_edges(using) if parent != parent_id}
        edges.update((parent_id, tutor.pk) for tutor in tutors)
        validate_hierarchy(edges, root_ids(using))
        return tutors

    class Meta:
        model = TutorProfile
        fields = "__all__"
        widgets = {
            "address": forms.TextInput(
                attrs={
                    "class": "address-autocomplete",
                    "autocomplete": "off",
                    "placeholder": "Wohnadresse eingeben",
                    "data-address-mode": "admin",
                }
            ),
        }


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ("full_name", "email", "role", "is_staff")
    list_filter = ("role", "is_staff", "is_superuser", "is_active")
    ordering = ("first_name", "last_name", "username")
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name", "email")}),
        (_("Role"), {"fields": ("role",)}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "password1",
                    "password2",
                    "role",
                    "is_staff",
                    "is_active",
                ),
            },
        ),
    )

    @admin.display(description="Vor- und Nachname", ordering="first_name")
    def full_name(self, obj):
        return obj.display_name


@admin.register(ParentProfile)
class ParentProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "customer_number")
    search_fields = ("customer_number", "user__username", "user__first_name", "user__last_name", "user__email")


class StudentProfileAdminForm(forms.ModelForm):
    class Meta:
        model = StudentProfile
        fields = "__all__"

    def clean_assigned_tutors(self):
        tutors = self.cleaned_data["assigned_tutors"]
        for tutor in tutors:
            waiting = tutor.status == TutorProfile.Status.WAITLISTED or Lead.objects.filter(
                converted_tutor=tutor,
                status__in=[Lead.Status.WAITLISTED, Lead.Status.AVAILABILITY_REQUESTED, Lead.Status.INTERESTED],
            ).exists()
            already_assigned = self.instance.pk and self.instance.assigned_tutors.filter(pk=tutor.pk).exists()
            if waiting and not already_assigned:
                raise forms.ValidationError("Bitte zuerst das Interesse der TutorIn bestätigen lassen und die Bewerbung in der Lead-Zentrale weiterführen.")
        return tutors


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    form = StudentProfileAdminForm
    list_display = ("user", "profile_number", "bbb_link", "created_by_tutor")
    list_filter = ("created_by_tutor",)
    search_fields = ("profile_number", "user__username", "user__first_name", "user__last_name", "user__email")
    filter_horizontal = ("parents", "assigned_tutors")

    @admin.display(description="BBB-Link")
    def bbb_link(self, obj):
        return obj.zoom_link


@admin.register(TutorProfile)
class TutorProfileAdmin(admin.ModelAdmin):
    form = TutorProfileAdminForm
    list_display = ("user", "tutor_number", "status", "tax_number_pending", "bbb_link")
    list_filter = ("status", "tax_number_pending")
    search_fields = ("tutor_number", "user__username", "user__first_name", "user__last_name", "user__email")
    filter_horizontal = ("assigned_tutors",)
    readonly_fields = ("tutor_number",)

    @property
    def media(self):
        media = super().media
        api_key = getattr(settings, "GOOGLE_MAPS_API_KEY", "")
        if not api_key:
            return media
        return media + forms.Media(
            css={"all": ("core/admin_address_autocomplete.css",)},
            js=(
                "core/address_autocomplete.js",
                "https://maps.googleapis.com/maps/api/js"
                f"?key={api_key}&loading=async&libraries=places"
                "&callback=initAddressAutocomplete",
            )
        )


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("date", "time", "student", "tutor", "status", "duration_minutes")
    list_filter = ("status", "date", "tutor")
    search_fields = ("student__user__username", "tutor__user__username")


@admin.register(ProgressEntry)
class ProgressEntryAdmin(admin.ModelAdmin):
    list_display = ("lesson", "rating", "created_at")
    list_filter = ("rating", "created_at")
    search_fields = ("lesson__student__user__username", "lesson__tutor__user__username")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("student", "uploaded_by", "approved_by", "uploaded_at", "approved_at")
    list_filter = ("uploaded_at", "approved_at")
    search_fields = ("student__user__username", "uploaded_by__user__username", "approved_by__user__username")


@admin.register(TutorTemplate)
class TutorTemplateAdmin(admin.ModelAdmin):
    list_display = ("file", "uploaded_by", "visibility", "uploaded_at")
    list_filter = ("uploaded_at",)
    search_fields = ("file", "uploaded_by__user__username")


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    actions = ("export_leads_csv",)
    list_display = (
        "name",
        "role",
        "subject",
        "grade",
        "status",
        "appointment_at",
        "follow_up_date",
        "follow_up_done",
        "preferred_contact",
        "created_at",
        "utm_campaign",
    )
    list_editable = ("status", "follow_up_date", "follow_up_done")
    list_filter = (
        "role",
        "status",
        "follow_up_done",
        "follow_up_date",
        "subject",
        "tutoring_type",
        "utm_campaign",
        "created_at",
    )
    search_fields = (
        "name",
        "email",
        "phone",
        "subject",
        "teaching_subjects",
        "message",
        "internal_notes",
    )
    readonly_fields = ("created_at", "updated_at", "last_status_change_at", "waitlisted_at", "application_mail_summary")
    fieldsets = (
        (
            "Kontakt",
            {
                "fields": (
                    "status",
                    "role",
                    "name",
                    "email",
                    "phone",
                    "preferred_contact",
                    "privacy_consent",
                )
            },
        ),
        (
            "Pipeline",
            {
                "fields": (
                    "contacted_at",
                    "last_status_change_at",
                    "waitlisted_at",
                    "application_mail_summary",
                    "follow_up_date",
                    "follow_up_done",
                    "internal_notes",
                )
            },
        ),
        (
            "Nachhilfe",
            {
                "fields": (
                    "postal_code",
                    "street",
                    "preferred_weekdays",
                    "subject",
                    "grade",
                    "tutoring_type",
                    "goal",
                    "urgency",
                    "message",
                )
            },
        ),
        (
            "TutorIn",
            {
                "fields": (
                    "education_status",
                    "teaching_subjects",
                    "teaching_grades",
                    "weekly_availability",
                    "experience_level",
                    "motivation",
                )
            },
        ),
        (
            "Marketing",
            {
                "fields": (
                    "source",
                    "campaign",
                    "utm_source",
                    "utm_medium",
                    "utm_campaign",
                    "utm_content",
                    "utm_term",
                    "referrer",
                    "landing_page_path",
                    "initial_querystring",
                )
            },
        ),
        ("Zeitpunkte", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Bewerbungs-E-Mail")
    def application_mail_summary(self, obj):
        record = obj.application_email
        if not record:
            return "Keine E-Mail für diesen Status. Absagen und Wartelisten-E-Mails werden bei einem Statuswechsel automatisch versendet."
        if record.sent_at:
            return f"Versendet an {record.recipient} am {record.sent_at:%d.%m.%Y %H:%M}."
        return (record.error or "Versand ausstehend.") + " Erneuter Versand in der Lead-Zentrale möglich."

    @admin.action(description="Ausgewählte Leads als CSV exportieren")
    def export_leads_csv(self, request, queryset):
        fields = [
            "created_at",
            "role",
            "name",
            "email",
            "phone",
            "preferred_contact",
            "subject",
            "grade",
            "tutoring_type",
            "goal",
            "urgency",
            "status",
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_content",
            "utm_term",
            "source",
            "campaign",
            "internal_notes",
            "postal_code",
            "street",
            "preferred_weekdays_display",
        ]
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="brainboost-leads.csv"'
        writer = csv.writer(response)
        writer.writerow(fields)
        for lead in queryset.order_by("-created_at"):
            writer.writerow([getattr(lead, field) for field in fields])
        return response


@admin.register(TutorNumberReservation)
class TutorNumberReservationAdmin(admin.ModelAdmin):
    list_display = ("number", "tutor", "original_tutor_id", "created_at", "archived_at")
    search_fields = ("number",)
    readonly_fields = ("number", "tutor", "original_tutor_id", "created_at", "archived_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CancellationRequest)
class CancellationRequestAdmin(admin.ModelAdmin):
    list_display = (
        "name_snapshot",
        "email_snapshot",
        "role_snapshot",
        "requested_actions",
        "status",
        "requested_at",
        "confirmed_at",
    )
    list_filter = ("status", "role_snapshot", "delete_account", "terminate_agreement")
    search_fields = ("name_snapshot", "email_snapshot")
    readonly_fields = (
        "user",
        "name_snapshot",
        "email_snapshot",
        "role_snapshot",
        "delete_account",
        "terminate_agreement",
        "reason",
        "requested_at",
        "confirmed_at",
        "confirmed_by",
        "completed_at",
        "internal_email_sent_at",
        "receipt_email_sent_at",
        "confirmation_email_sent_at",
        "email_error",
    )
    actions = ("confirm_requests", "complete_requests", "resend_confirmation_emails")

    @admin.action(description="Ausgewählte Anträge bestätigen und E-Mail senden")
    def confirm_requests(self, request, queryset):
        from .cancellation_requests import confirm_cancellation_request, send_confirmation_email

        confirmed = 0
        failed = 0
        for cancellation in queryset.order_by("pk"):
            cancellation, changed = confirm_cancellation_request(cancellation.pk, request.user)
            if not changed:
                continue
            confirmed += 1
            if not send_confirmation_email(cancellation):
                failed += 1
        self.message_user(
            request,
            f"{confirmed} Antrag/Anträge bestätigt. {failed} Bestätigungsmail(s) fehlgeschlagen.",
            level="warning" if failed else "success",
        )

    @admin.action(description="Ausgewählte bestätigte Anträge als abgeschlossen markieren")
    def complete_requests(self, request, queryset):
        from django.utils import timezone

        completed = queryset.filter(status=CancellationRequest.Status.CONFIRMED).update(
            status=CancellationRequest.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        self.message_user(request, f"{completed} Antrag/Anträge als abgeschlossen markiert.")

    @admin.action(description="Bestätigungsmail für ausgewählte bestätigte Anträge erneut senden")
    def resend_confirmation_emails(self, request, queryset):
        from .cancellation_requests import send_confirmation_email

        sent = 0
        failed = 0
        for cancellation in queryset.filter(status=CancellationRequest.Status.CONFIRMED):
            if send_confirmation_email(cancellation):
                sent += 1
            else:
                failed += 1
        self.message_user(
            request,
            f"{sent} Bestätigungsmail(s) versendet. {failed} fehlgeschlagen.",
            level="warning" if failed else "success",
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
