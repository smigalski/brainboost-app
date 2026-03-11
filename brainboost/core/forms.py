from django import forms
from django.db import transaction
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
                assigned_tutors=tutor_profile
            ).distinct()
            self.fields["student"].queryset = students_qs


class ProgressEntryForm(forms.ModelForm):
    rating = forms.TypedChoiceField(
        choices=[(value, str(value)) for value in range(1, 11)],
        coerce=int,
        widget=forms.RadioSelect,
        label="Mitarbeit",
    )

    class Meta:
        model = ProgressEntry
        fields = ["lesson", "comment", "rating"]
        labels = {
            "lesson": "Termin",
            "comment": "Kommentar",
        }

    def __init__(self, *args, tutor_profile=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tutor_profile:
            self.fields["lesson"].queryset = (
                tutor_profile.lessons.select_related("student__user")
            )


class LearningMaterialForm(forms.ModelForm):
    class Meta:
        model = LearningMaterial
        fields = ["student", "related_task", "file"]

    def __init__(self, *args, allowed_students=None, kind=None, tutor_profile=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_students is not None:
            self.fields["student"].queryset = allowed_students
        self.kind = kind
        self.tutor_profile = tutor_profile
        if kind == LearningMaterial.Kind.SOLUTION:
            tasks_qs = LearningMaterial.objects.filter(kind=LearningMaterial.Kind.TASK)
            if allowed_students is not None:
                tasks_qs = tasks_qs.filter(student__in=allowed_students)
            self.fields["related_task"].queryset = tasks_qs.select_related("student__user").order_by("-uploaded_at")
            self.fields["related_task"].required = True
            self.fields["related_task"].label = "Zugehoerige Aufgabe"
        else:
            self.fields.pop("related_task")
        self.fields["file"].help_text = "Erlaubt: pdf, png, jpg, jpeg, docx. Max 10 MB."

    def clean(self):
        cleaned = super().clean()
        if self.kind != LearningMaterial.Kind.SOLUTION:
            return cleaned

        student = cleaned.get("student")
        related_task = cleaned.get("related_task")
        if not related_task:
            self.add_error("related_task", "Bitte waehle die zugehoerige Aufgabe aus.")
            return cleaned
        if related_task.kind != LearningMaterial.Kind.TASK:
            self.add_error("related_task", "Es kann nur eine Aufgabe verknuepft werden.")
        if student and related_task.student_id != student.id:
            self.add_error("related_task", "Die Aufgabe muss zur ausgewaehlten SchuelerIn passen.")
        return cleaned

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
        if pw1 or pw2:
            if not pw1:
                self.add_error("password1", "Bitte gib ein Passwort ein.")
            if not pw2:
                self.add_error("password2", "Bitte bestaetige das Passwort.")
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
    email = forms.EmailField(required=False, label="E-Mail")
    phone_number = forms.CharField(max_length=50, required=False, label="Telefonnummer")
    role_display = forms.CharField(
        initial=CustomUser.Roles.PARENT.label,
        required=False,
        disabled=True,
        label="Rolle",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password1"].required = False
        self.fields["password2"].required = False
        self.fields["email"].help_text = "Optional. Wenn leer, wird nur ein Platzhalter-Eintrag ohne E-Mail-Versand angelegt."
        self.fields["password1"].help_text = "Optional. Leer lassen fuer einen Platzhalter ohne Login."
        self.order_fields(
            [
                "username",
                "first_name",
                "last_name",
                "email",
                "phone_number",
                "password1",
                "password2",
                "is_active",
                "role_display",
            ]
        )

    def save(self) -> CustomUser:
        with transaction.atomic():
            user = self._build_user(CustomUser.Roles.PARENT)
            if self.cleaned_data.get("password1"):
                user.set_password(self.cleaned_data["password1"])
            else:
                user.set_unusable_password()
            user.save()
            ParentProfile.objects.create(
                user=user,
                phone_number=self.cleaned_data.get("phone_number", ""),
            )
        return user


class StudentCreateForm(BaseUserCreateForm):
    email = forms.EmailField(required=False, label="E-Mail")
    role_display = forms.CharField(
        initial=CustomUser.Roles.STUDENT.label,
        required=False,
        disabled=True,
        label="Rolle",
    )
    address = forms.CharField(
        max_length=255,
        required=False,
        label="Adresse",
        widget=forms.TextInput(
            attrs={
                "class": "address-autocomplete",
                "autocomplete": "off",
                "placeholder": "Wohnadresse eingeben",
            }
        ),
    )
    phone_number = forms.CharField(max_length=50, required=False, label="Telefonnummer")
    zoom_link = forms.URLField(required=False, label="BBB-Link")
    zumpad_link = forms.URLField(required=False, label="ZUMPad-Link")
    parents = forms.ModelMultipleChoiceField(
        queryset=ParentProfile.objects.all(),
        required=False,
        label="Eltern",
        widget=forms.SelectMultiple(attrs={"size": 6}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password1"].required = False
        self.fields["password2"].required = False
        self.fields["email"].help_text = "Optional. Wenn leer, wird nur ein Platzhalter-Eintrag ohne E-Mail-Versand angelegt."
        self.fields["password1"].help_text = "Optional. Leer lassen fuer einen Platzhalter ohne Login."
        self.order_fields(
            [
                "username",
                "first_name",
                "last_name",
                "email",
                "phone_number",
                "password1",
                "password2",
                "is_active",
                "role_display",
                "address",
                "zoom_link",
                "zumpad_link",
                "parents",
            ]
        )

    def save(self) -> CustomUser:
        with transaction.atomic():
            user = self._build_user(CustomUser.Roles.STUDENT)
            if self.cleaned_data.get("password1"):
                user.set_password(self.cleaned_data["password1"])
            else:
                user.set_unusable_password()
            user.save()
            profile = StudentProfile.objects.create(
                user=user,
                address=self.cleaned_data.get("address", ""),
                phone_number=self.cleaned_data.get("phone_number", ""),
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
    address = forms.CharField(
        max_length=255,
        required=False,
        label="Adresse",
        widget=forms.TextInput(
            attrs={
                "class": "address-autocomplete",
                "autocomplete": "off",
                "placeholder": "Wohnadresse eingeben",
            }
        ),
    )
    phone_number = forms.CharField(max_length=50, required=False, label="Telefonnummer")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(
            [
                "username",
                "first_name",
                "last_name",
                "email",
                "phone_number",
                "password1",
                "password2",
                "is_active",
                "role_display",
                "address",
            ]
        )

    def save(self) -> CustomUser:
        with transaction.atomic():
            user = self._build_user(CustomUser.Roles.TUTOR)
            user.set_password(self.cleaned_data["password1"])
            user.save()
            TutorProfile.objects.create(
                user=user,
                address=self.cleaned_data.get("address", ""),
                phone_number=self.cleaned_data.get("phone_number", ""),
            )
        return user
