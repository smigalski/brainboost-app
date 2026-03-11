from django import forms
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.contrib.auth import password_validation

from .models import (
    Lesson,
    ProgressEntry,
    StudentProfile,
    ParentProfile,
    TutorProfile,
    LearningMaterial,
    Invoice,
    TutorTemplate,
    CustomUser,
)


class LessonForm(forms.ModelForm):
    date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )
    time = forms.TimeField(
        widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        input_formats=["%H:%M"],
    )
    duration_minutes = forms.TypedChoiceField(
        choices=[(45, "45 Min"), (60, "60 Min"), (90, "90 Min")],
        coerce=int,
        widget=forms.RadioSelect(attrs={"class": "pill-options"}),
        label="Dauer",
    )

    class Meta:
        model = Lesson
        fields = ["student", "date", "time", "duration_minutes", "ort", "fach", "status"]

    def __init__(self, *args, tutor_profile=None, allowed_students=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_students is not None:
            self.fields["student"].queryset = allowed_students
        elif tutor_profile:
            students_qs = StudentProfile.objects.filter(
                Q(lessons__tutor=tutor_profile) | Q(lessons__isnull=True)
            ).distinct()
            self.fields["student"].queryset = students_qs


class ProgressEntryForm(forms.ModelForm):
    class Meta:
        model = ProgressEntry
        fields = ["lesson", "comment", "rating"]

    def __init__(self, *args, tutor_profile=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tutor_profile:
            self.fields["lesson"].queryset = (
                tutor_profile.lessons.select_related("student__user")
            )


class LearningMaterialForm(forms.ModelForm):
    class Meta:
        model = LearningMaterial
        fields = ["student", "file"]

    def __init__(self, *args, allowed_students=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_students is not None:
            self.fields["student"].queryset = allowed_students
        self.fields["file"].help_text = "Erlaubt: pdf, png, jpg, jpeg, docx. Max 10 MB."

    def clean_file(self):
        f = self.cleaned_data["file"]
        max_size = 10 * 1024 * 1024  # 10 MB
        if f.size > max_size:
            raise forms.ValidationError("Datei ist größer als 10 MB.")
        return f


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ["student", "file"]

    def __init__(self, *args, allowed_students=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_students is not None:
            self.fields["student"].queryset = allowed_students
        self.fields["file"].help_text = "Nur PDF, max 10 MB."

    def clean_file(self):
        f = self.cleaned_data["file"]
        max_size = 10 * 1024 * 1024  # 10 MB
        if f.size > max_size:
            raise forms.ValidationError("Datei ist größer als 10 MB.")
        if not f.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Nur PDF-Dateien sind erlaubt.")
        return f


class TutorTemplateForm(forms.ModelForm):
    class Meta:
        model = TutorTemplate
        fields = ["file"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["file"].help_text = "Erlaubt: pdf, png, jpg, jpeg, docx. Max 10 MB."

    def clean_file(self):
        f = self.cleaned_data["file"]
        max_size = 10 * 1024 * 1024  # 10 MB
        if f.size > max_size:
            raise forms.ValidationError("Datei ist größer als 10 MB.")
        return f


class BaseUserCreateForm(forms.Form):
    _username_field = CustomUser._meta.get_field("username")
    username = forms.CharField(
        max_length=_username_field.max_length,
        help_text=_username_field.help_text,
        validators=_username_field.validators,
        label=_username_field.verbose_name,
    )
    first_name = forms.CharField(max_length=150, required=False, label="Vorname")
    last_name = forms.CharField(max_length=150, required=False, label="Nachname")
    email = forms.EmailField(required=False, label="E-Mail")
    password1 = forms.CharField(widget=forms.PasswordInput, label="Passwort")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Passwort bestätigen")
    is_active = forms.BooleanField(required=False, initial=True, label="Aktiv")

    def clean_username(self):
        username = self.cleaned_data["username"]
        if CustomUser.objects.filter(username__iexact=username).exists():
            raise ValidationError("Dieser Benutzername ist bereits vergeben.")
        return username

    def clean(self):
        cleaned = super().clean()
        pw1 = cleaned.get("password1")
        pw2 = cleaned.get("password2")
        if pw1:
            try:
                password_validation.validate_password(pw1, None)
            except ValidationError as exc:
                self.add_error("password1", exc)
        if pw1 and pw2 and pw1 != pw2:
            self.add_error("password2", "Die Passwörter stimmen nicht überein.")
        return cleaned

    def _build_user(self, role: str) -> CustomUser:
        return CustomUser(
            username=self.cleaned_data["username"],
            first_name=self.cleaned_data.get("first_name", ""),
            last_name=self.cleaned_data.get("last_name", ""),
            email=self.cleaned_data.get("email", ""),
            role=role,
            is_active=self.cleaned_data.get("is_active", True),
            is_staff=False,
            is_superuser=False,
        )


class ParentCreateForm(BaseUserCreateForm):
    email = forms.EmailField(required=True, label="E-Mail")
    role_display = forms.CharField(
        initial=CustomUser.Roles.PARENT.label,
        required=False,
        disabled=True,
        label="Rolle",
    )

    def save(self) -> CustomUser:
        with transaction.atomic():
            user = self._build_user(CustomUser.Roles.PARENT)
            user.set_password(self.cleaned_data["password1"])
            user.save()
            ParentProfile.objects.create(user=user)
        return user


class StudentCreateForm(BaseUserCreateForm):
    email = forms.EmailField(required=True, label="E-Mail")
    role_display = forms.CharField(
        initial=CustomUser.Roles.STUDENT.label,
        required=False,
        disabled=True,
        label="Rolle",
    )
    address = forms.CharField(max_length=255, required=False, label="Adresse")
    zoom_link = forms.URLField(required=False, label="BBB-Link")
    zumpad_link = forms.URLField(required=False, label="ZUMPad-Link")
    parents = forms.ModelMultipleChoiceField(
        queryset=ParentProfile.objects.all(),
        required=False,
        label="Eltern",
        widget=forms.SelectMultiple(attrs={"size": 6}),
    )

    def save(self) -> CustomUser:
        with transaction.atomic():
            user = self._build_user(CustomUser.Roles.STUDENT)
            user.set_password(self.cleaned_data["password1"])
            user.save()
            profile = StudentProfile.objects.create(
                user=user,
                address=self.cleaned_data.get("address", ""),
                zoom_link=self.cleaned_data.get("zoom_link", ""),
                zumpad_link=self.cleaned_data.get("zumpad_link", ""),
            )
            parents = self.cleaned_data.get("parents")
            if parents:
                profile.parents.set(parents)
        return user


class TutorCreateForm(BaseUserCreateForm):
    email = forms.EmailField(required=True, label="E-Mail")
    role_display = forms.CharField(
        initial=CustomUser.Roles.TUTOR.label,
        required=False,
        disabled=True,
        label="Rolle",
    )
    address = forms.CharField(max_length=255, required=False, label="Adresse")

    def save(self) -> CustomUser:
        with transaction.atomic():
            user = self._build_user(CustomUser.Roles.TUTOR)
            user.set_password(self.cleaned_data["password1"])
            user.save()
            TutorProfile.objects.create(
                user=user,
                address=self.cleaned_data.get("address", ""),
            )
        return user
