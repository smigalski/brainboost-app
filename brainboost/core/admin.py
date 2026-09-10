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
    Lesson,
    ProgressEntry,
    Invoice,
    TutorTemplate,
    AdminTask,
    AdminIdea,
    Lead,
)


class TutorProfileAdminForm(forms.ModelForm):
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
    list_display = ("username", "email", "first_name", "last_name", "role", "is_staff")
    list_filter = ("role", "is_staff", "is_superuser", "is_active")
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


@admin.register(ParentProfile)
class ParentProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "customer_number")
    search_fields = ("customer_number", "user__username", "user__first_name", "user__last_name", "user__email")


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
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


@admin.register(AdminTask)
class AdminTaskAdmin(admin.ModelAdmin):
    list_display = ("title", "image", "importance", "days", "status", "owner", "created_at")
    list_filter = ("importance", "status", "owner")
    search_fields = ("title", "owner__username", "owner__first_name", "owner__last_name")


@admin.register(AdminIdea)
class AdminIdeaAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "image", "created_by", "created_at")
    list_filter = ("category", "created_by")
    search_fields = ("title", "created_by__username", "created_by__first_name", "created_by__last_name")


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    actions = ("export_leads_csv",)
    list_display = (
        "name",
        "role",
        "subject",
        "grade",
        "status",
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
    readonly_fields = ("created_at", "updated_at", "last_status_change_at")
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
        ]
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="brainboost-leads.csv"'
        writer = csv.writer(response)
        writer.writerow(fields)
        for lead in queryset.order_by("-created_at"):
            writer.writerow([getattr(lead, field) for field in fields])
        return response
