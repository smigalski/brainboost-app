from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
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
)


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
    list_display = ("user",)


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "bbb_link")
    filter_horizontal = ("parents", "assigned_tutors")

    @admin.display(description="BBB-Link")
    def bbb_link(self, obj):
        return obj.zoom_link


@admin.register(TutorProfile)
class TutorProfileAdmin(admin.ModelAdmin):
    list_display = ("user",)
    filter_horizontal = ("assigned_tutors",)


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
    list_display = ("title", "importance", "days", "status", "owner", "created_at")
    list_filter = ("importance", "status", "owner")
    search_fields = ("title", "owner__username", "owner__first_name", "owner__last_name")
