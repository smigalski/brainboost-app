from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django import forms
from django.contrib.auth import password_validation
from django.db import transaction
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

from .models import (
    Lesson,
    ProgressEntry,
    StudentProfile,
    ParentProfile,
    TutorProfile,
    LearningMaterial,
    Invoice,
    HolidaySurvey,
    HolidaySurveyResponse,
    FAQItem,
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
    repeat_enabled = forms.BooleanField(required=False, label="Regelmäßig wiederholen")
    repeat_interval_weeks = forms.IntegerField(
        required=False,
        min_value=1,
        initial=1,
        label="Wochenzyklus",
        help_text="1 = wöchentlich, 2 = alle zwei Wochen usw.",
    )
    repeat_end_mode = forms.ChoiceField(
        required=False,
        label="Wiederholen bis",
        choices=[
            ("", "Einmaliger Termin"),
            ("weeks", "Nach Anzahl an Wochen"),
            ("count", "Nach Anzahl an Terminen"),
            ("until", "Bis zu einem Datum"),
        ],
    )
    repeat_weeks = forms.IntegerField(required=False, min_value=1, label="Anzahl an Wochen")
    repeat_occurrences = forms.IntegerField(
        required=False,
        min_value=2,
        label="Anzahl an Terminen",
        help_text="Inklusive des ersten Termins.",
    )
    repeat_until = forms.DateField(
        required=False,
        label="Wiederholen bis Datum",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )

    class Meta:
        model = Lesson
        fields = [
            "student",
            "date",
            "time",
            "duration_minutes",
            "ort",
            "fach",
            "fach_2",
            "fach_3",
            "status",
        ]

    def __init__(self, *args, tutor_profile=None, allowed_students=None, is_edit=False, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_students is not None:
            self.fields["student"].queryset = allowed_students
        elif tutor_profile:
            students_qs = StudentProfile.objects.filter(
                assigned_tutors=tutor_profile
            ).distinct()
            self.fields["student"].queryset = students_qs
        self.fields["fach"].label = "Fach"
        self.fields["fach_2"].label = "Fach 2"
        self.fields["fach_3"].label = "Fach 3"
        self.fields["fach_2"].required = False
        self.fields["fach_3"].required = False
        blank_choices = [("", "---------"), *Lesson.SUBJECT_CHOICES]
        self.fields["fach_2"].choices = blank_choices
        self.fields["fach_3"].choices = blank_choices
        if is_edit:
            for name in (
                "repeat_enabled",
                "repeat_interval_weeks",
                "repeat_end_mode",
                "repeat_weeks",
                "repeat_occurrences",
                "repeat_until",
            ):
                self.fields.pop(name, None)

    def clean(self):
        cleaned = super().clean()
        subjects = [cleaned.get("fach"), cleaned.get("fach_2"), cleaned.get("fach_3")]
        non_empty_subjects = [subject for subject in subjects if subject]
        if len(non_empty_subjects) != len(set(non_empty_subjects)):
            raise forms.ValidationError("Bitte wähle jedes Fach nur einmal aus.")

        if "repeat_enabled" not in self.fields or not cleaned.get("repeat_enabled"):
            return cleaned

        end_mode = cleaned.get("repeat_end_mode")
        interval = cleaned.get("repeat_interval_weeks")
        if not end_mode:
            self.add_error("repeat_end_mode", "Bitte wähle aus, wie oft sich der Termin wiederholen soll.")
        if not interval:
            self.add_error("repeat_interval_weeks", "Bitte gib den Wochenzyklus an.")

        if end_mode == "weeks" and not cleaned.get("repeat_weeks"):
            self.add_error("repeat_weeks", "Bitte gib die Anzahl an Wochen an.")
        elif end_mode == "count" and not cleaned.get("repeat_occurrences"):
            self.add_error("repeat_occurrences", "Bitte gib die Anzahl an Terminen an.")
        elif end_mode == "until":
            repeat_until = cleaned.get("repeat_until")
            if not repeat_until:
                self.add_error("repeat_until", "Bitte gib ein Enddatum an.")
            elif cleaned.get("date") and repeat_until <= cleaned["date"]:
                self.add_error("repeat_until", "Das Enddatum muss nach dem ersten Termin liegen.")

        return cleaned


class ProgressEntryForm(forms.ModelForm):
    RATING_CHOICES = [(value, str(value)) for value in range(1, 11)]

    rating = forms.TypedChoiceField(
        choices=RATING_CHOICES,
        coerce=int,
        widget=forms.RadioSelect,
        label="Mitarbeit",
    )
    rating_fach_2 = forms.TypedChoiceField(
        choices=RATING_CHOICES,
        coerce=int,
        widget=forms.RadioSelect,
        label="Mitarbeit Fach 2",
        required=False,
    )
    rating_fach_3 = forms.TypedChoiceField(
        choices=RATING_CHOICES,
        coerce=int,
        widget=forms.RadioSelect,
        label="Mitarbeit Fach 3",
        required=False,
    )

    class Meta:
        model = ProgressEntry
        fields = ["lesson", "comment", "rating", "rating_fach_2", "rating_fach_3"]
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
        lesson = self._resolve_lesson()
        if lesson and lesson.fach_2:
            self.fields["rating_fach_2"].label = f"Mitarbeit {lesson.get_fach_2_display()}"
        else:
            self.fields.pop("rating_fach_2", None)
        if lesson and lesson.fach_3:
            self.fields["rating_fach_3"].label = f"Mitarbeit {lesson.get_fach_3_display()}"
        else:
            self.fields.pop("rating_fach_3", None)
        if lesson:
            self.fields["rating"].label = f"Mitarbeit {lesson.get_fach_display()}"

    def _resolve_lesson(self):
        if self.instance and self.instance.pk:
            return self.instance.lesson
        lesson = self.initial.get("lesson")
        if isinstance(lesson, Lesson):
            return lesson
        raw_lesson_id = self.data.get(self.add_prefix("lesson")) or self.initial.get("lesson")
        if raw_lesson_id:
            try:
                return Lesson.objects.get(pk=raw_lesson_id)
            except (Lesson.DoesNotExist, TypeError, ValueError):
                return None
        return None

    def clean(self):
        cleaned = super().clean()
        lesson = cleaned.get("lesson") or self._resolve_lesson()
        if lesson and lesson.fach_2 and cleaned.get("rating_fach_2") in (None, ""):
            self.add_error("rating_fach_2", f"Bitte bewerte auch {lesson.get_fach_2_display()}.")
        if lesson and lesson.fach_3 and cleaned.get("rating_fach_3") in (None, ""):
            self.add_error("rating_fach_3", f"Bitte bewerte auch {lesson.get_fach_3_display()}.")
        return cleaned


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


class InvoiceGenerateForm(forms.Form):
    student = forms.ModelChoiceField(queryset=StudentProfile.objects.none(), label="SchülerIn")
    period = forms.CharField(
        label="Monat / Jahr",
        widget=forms.TextInput(attrs={"type": "month"}),
    )
    discount_value = forms.DecimalField(
        required=False,
        min_value=Decimal("0.01"),
        max_digits=8,
        decimal_places=2,
        label="Rabatt",
        widget=forms.NumberInput(
            attrs={
                "step": "0.01",
                "min": "0.01",
                "placeholder": "z. B. 10.00",
            }
        ),
    )
    discount_type = forms.ChoiceField(
        required=False,
        label="Rabattart",
        choices=[
            ("", "Kein Rabatt"),
            (Invoice.DiscountType.FIXED, "EUR"),
            (Invoice.DiscountType.PERCENT, "%"),
        ],
    )

    def __init__(self, *args, allowed_students=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_students is not None:
            self.fields["student"].queryset = allowed_students

    def clean_period(self):
        value = (self.cleaned_data.get("period") or "").strip()
        try:
            return datetime.strptime(value, "%Y-%m").date()
        except ValueError as exc:
            raise forms.ValidationError("Bitte wähle einen gültigen Monat aus.") from exc

    def clean(self):
        cleaned = super().clean()
        discount_value = cleaned.get("discount_value")
        discount_type = cleaned.get("discount_type") or ""

        if discount_value and not discount_type:
            self.add_error("discount_type", "Bitte wähle EUR oder % für den Rabatt aus.")
        if discount_type and discount_value is None:
            self.add_error("discount_value", "Bitte gib einen Rabattwert ein.")
        if (
            discount_type == Invoice.DiscountType.PERCENT
            and discount_value is not None
            and discount_value > Decimal("100.00")
        ):
            self.add_error("discount_value", "Der prozentuale Rabatt darf höchstens 100 betragen.")

        return cleaned


class HolidaySurveyForm(forms.ModelForm):
    class Meta:
        model = HolidaySurvey
        fields = ["question"]
        labels = {"question": "Ja/Nein-Frage"}


class HolidaySurveyAnswerForm(forms.ModelForm):
    class Meta:
        model = HolidaySurveyResponse
        fields = ["answer"]
        labels = {"answer": "Antwort"}
        widgets = {"answer": forms.RadioSelect}


class FAQItemForm(forms.ModelForm):
    audience_all = forms.BooleanField(required=False, label="Alle")
    show_for_parents = forms.BooleanField(required=False, label="Eltern")
    show_for_students = forms.BooleanField(required=False, label="SchülerInnen/StudentInnen")
    show_for_tutors = forms.BooleanField(required=False, label="TutorInnen")
    show_on_landing = forms.BooleanField(required=False, label="Startseite")

    class Meta:
        model = FAQItem
        fields = [
            "question",
            "answer",
            "show_for_parents",
            "show_for_students",
            "show_for_tutors",
            "show_on_landing",
        ]
        labels = {
            "question": "Frage",
            "answer": "Antwort",
        }
        widgets = {
            "answer": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in (
            "audience_all",
            "show_for_parents",
            "show_for_students",
            "show_for_tutors",
            "show_on_landing",
        ):
            self.fields[name].widget.attrs["class"] = "faq-target-checkbox"
        if self.instance and self.instance.pk:
            self.fields["audience_all"].initial = (
                self.instance.show_for_parents
                and self.instance.show_for_students
                and self.instance.show_for_tutors
                and self.instance.show_on_landing
            )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("audience_all"):
            cleaned["show_for_parents"] = True
            cleaned["show_for_students"] = True
            cleaned["show_for_tutors"] = True
            cleaned["show_on_landing"] = True
        if not any(
            cleaned.get(name)
            for name in (
                "show_for_parents",
                "show_for_students",
                "show_for_tutors",
                "show_on_landing",
            )
        ):
            raise ValidationError("Bitte wähle mindestens eine Zielgruppe aus.")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        cleaned = self.cleaned_data
        instance.show_for_parents = cleaned["show_for_parents"]
        instance.show_for_students = cleaned["show_for_students"]
        instance.show_for_tutors = cleaned["show_for_tutors"]
        instance.show_on_landing = cleaned["show_on_landing"]
        if commit:
            instance.save()
        return instance


class FAQSubmissionForm(forms.ModelForm):
    audience_all = forms.BooleanField(required=False, label="Alle")
    show_for_parents = forms.BooleanField(required=False, label="Eltern")
    show_for_students = forms.BooleanField(required=False, label="SchülerInnen/StudentInnen")
    show_for_tutors = forms.BooleanField(required=False, label="TutorInnen")
    show_on_landing = forms.BooleanField(required=False, label="Startseite")

    class Meta:
        model = FAQItem
        fields = [
            "question",
            "show_for_parents",
            "show_for_students",
            "show_for_tutors",
            "show_on_landing",
        ]
        labels = {
            "question": "Deine Frage",
        }
        widgets = {
            "question": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("audience_all"):
            cleaned["show_for_parents"] = True
            cleaned["show_for_students"] = True
            cleaned["show_for_tutors"] = True
            cleaned["show_on_landing"] = True
        if not any(
            cleaned.get(name)
            for name in (
                "show_for_parents",
                "show_for_students",
                "show_for_tutors",
                "show_on_landing",
            )
        ):
            raise ValidationError("Bitte wähle mindestens eine Zielgruppe aus.")
        return cleaned

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in (
            "audience_all",
            "show_for_parents",
            "show_for_students",
            "show_for_tutors",
            "show_on_landing",
        ):
            self.fields[name].widget.attrs["class"] = "faq-target-checkbox"

    def save(self, commit=True):
        instance = super().save(commit=False)
        cleaned = self.cleaned_data
        instance.show_for_parents = cleaned["show_for_parents"]
        instance.show_for_students = cleaned["show_for_students"]
        instance.show_for_tutors = cleaned["show_for_tutors"]
        instance.show_on_landing = cleaned["show_on_landing"]
        if commit:
            instance.save()
        return instance


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


class BaseProfileUpdateForm(forms.Form):
    max_profile_image_size = 15 * 1024 * 1024
    _username_field = CustomUser._meta.get_field("username")
    avatar_icon = forms.ChoiceField(
        required=False,
        label="Profil-Icon",
        choices=(
            (CustomUser.AvatarIcons.NONE, "Leer"),
            (CustomUser.AvatarIcons.EAGLE, "Adler"),
            (CustomUser.AvatarIcons.SHARK, "Hai"),
            (CustomUser.AvatarIcons.LION, "Löwe"),
            (CustomUser.AvatarIcons.ANT, "Ameise"),
        ),
        widget=forms.RadioSelect,
    )
    profile_image = forms.FileField(required=False, label="Eigenes Profilbild")
    remove_profile_image = forms.BooleanField(
        required=False,
        label="Eigenes Profilbild entfernen",
    )
    username = forms.CharField(
        max_length=_username_field.max_length,
        help_text=_username_field.help_text,
        validators=_username_field.validators,
        label=_username_field.verbose_name,
    )
    first_name = forms.CharField(max_length=150, required=False, label="Vorname")
    last_name = forms.CharField(max_length=150, required=False, label="Nachname")
    email = forms.EmailField(required=False, label="E-Mail")

    def __init__(self, *args, user: CustomUser, **kwargs):
        self.user_instance = user
        super().__init__(*args, **kwargs)
        self.fields["avatar_icon"].initial = user.avatar_icon
        self.fields["username"].initial = user.username
        self.fields["first_name"].initial = user.first_name
        self.fields["last_name"].initial = user.last_name
        self.fields["email"].initial = user.email
        self.fields["profile_image"].help_text = (
            "Optional. Maximal 15 MB. Das Bild wird beim Speichern komprimiert."
        )
        self.fields["remove_profile_image"].initial = False

    def clean_username(self):
        username = self.cleaned_data["username"]
        if CustomUser.objects.filter(username__iexact=username).exclude(
            pk=self.user_instance.pk
        ).exists():
            raise ValidationError("Dieser Benutzername ist bereits vergeben.")
        return username

    def clean_profile_image(self):
        uploaded_file = self.cleaned_data.get("profile_image")
        if not uploaded_file:
            return uploaded_file
        if uploaded_file.size > self.max_profile_image_size:
            raise ValidationError("Das Profilbild darf maximal 15 MB gross sein.")
        try:
            from PIL import Image, UnidentifiedImageError
        except ImportError as exc:
            raise ValidationError(
                "Bild-Uploads sind erst verfuegbar, wenn Pillow installiert ist."
            ) from exc
        try:
            uploaded_file.seek(0)
            with Image.open(uploaded_file) as image:
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise ValidationError("Bitte lade eine gueltige Bilddatei hoch.") from exc
        finally:
            uploaded_file.seek(0)
        return uploaded_file

    def _compressed_profile_image(self):
        uploaded_file = self.cleaned_data.get("profile_image")
        if not uploaded_file:
            return None

        from PIL import Image, ImageOps

        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            elif image.mode == "L":
                image = image.convert("RGB")
            image.thumbnail((1200, 1200))

            output = BytesIO()
            image.save(output, format="JPEG", optimize=True, quality=82)
            output.seek(0)

        stem = Path(uploaded_file.name).stem or "profilbild"
        filename = f"{stem}.jpg"
        return ContentFile(output.read(), name=filename)

    def _update_profile_image(self, user: CustomUser) -> None:
        if self.cleaned_data.get("remove_profile_image") and user.profile_image:
            user.profile_image.delete(save=False)
            user.profile_image = ""

        compressed_file = self._compressed_profile_image()
        if compressed_file:
            if user.profile_image:
                user.profile_image.delete(save=False)
            user.profile_image.save(compressed_file.name, compressed_file, save=False)

    def _save_user(self) -> CustomUser:
        user = self.user_instance
        user.avatar_icon = self.cleaned_data.get("avatar_icon", "")
        user.username = self.cleaned_data["username"]
        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        user.email = self.cleaned_data.get("email", "")
        self._update_profile_image(user)
        user.save()
        return user


class ParentProfileForm(BaseProfileUpdateForm):
    phone_number = forms.CharField(max_length=50, required=False, label="Telefonnummer")

    def __init__(self, *args, user: CustomUser, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["phone_number"].initial = user.parent_profile.phone_number
        self.order_fields(
            [
                "avatar_icon",
                "profile_image",
                "remove_profile_image",
                "username",
                "first_name",
                "last_name",
                "email",
                "phone_number",
            ]
        )

    def save(self) -> CustomUser:
        user = self._save_user()
        profile = user.parent_profile
        profile.phone_number = self.cleaned_data.get("phone_number", "")
        profile.save()
        return user


class StudentProfileForm(BaseProfileUpdateForm):
    phone_number = forms.CharField(max_length=50, required=False, label="Telefonnummer")
    address = forms.CharField(
        max_length=255,
        required=False,
        label="Adresse",
        widget=forms.TextInput(
            attrs={
                "class": "address-autocomplete",
                "autocomplete": "off",
                "placeholder": "Wohnadresse eingeben",
                "data-address-mode": "deferred",
            }
        ),
    )
    def __init__(self, *args, user: CustomUser, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        profile = user.student_profile
        self.fields["phone_number"].initial = profile.phone_number
        self.fields["address"].initial = profile.address
        self.order_fields(
            [
                "avatar_icon",
                "profile_image",
                "remove_profile_image",
                "username",
                "first_name",
                "last_name",
                "email",
                "phone_number",
                "address",
            ]
        )

    def save(self) -> CustomUser:
        user = self._save_user()
        profile = user.student_profile
        profile.phone_number = self.cleaned_data.get("phone_number", "")
        profile.address = self.cleaned_data.get("address", "")
        profile.save()
        return user


class TutorProfileForm(BaseProfileUpdateForm):
    phone_number = forms.CharField(max_length=50, required=False, label="Telefonnummer")
    address = forms.CharField(
        max_length=255,
        required=False,
        label="Adresse",
        widget=forms.TextInput(
            attrs={
                "class": "address-autocomplete",
                "autocomplete": "off",
                "placeholder": "Wohnadresse eingeben",
                "data-address-mode": "deferred",
            }
        ),
    )
    account_holder = forms.CharField(max_length=255, required=False, label="KontoinhaberIn")
    bank_name = forms.CharField(max_length=255, required=False, label="Bankname")
    iban = forms.CharField(max_length=34, required=False, label="IBAN")
    bic = forms.CharField(max_length=11, required=False, label="BIC")

    def __init__(self, *args, user: CustomUser, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        profile = user.tutor_profile
        self.fields["phone_number"].initial = profile.phone_number
        self.fields["address"].initial = profile.address
        self.fields["account_holder"].initial = profile.account_holder
        self.fields["bank_name"].initial = profile.bank_name
        self.fields["iban"].initial = profile.iban
        self.fields["bic"].initial = profile.bic
        self.order_fields(
            [
                "avatar_icon",
                "profile_image",
                "remove_profile_image",
                "username",
                "first_name",
                "last_name",
                "email",
                "phone_number",
                "address",
                "account_holder",
                "bank_name",
                "iban",
                "bic",
            ]
        )

    def save(self) -> CustomUser:
        user = self._save_user()
        profile = user.tutor_profile
        profile.phone_number = self.cleaned_data.get("phone_number", "")
        profile.address = self.cleaned_data.get("address", "")
        profile.account_holder = self.cleaned_data.get("account_holder", "")
        profile.bank_name = self.cleaned_data.get("bank_name", "")
        profile.iban = self.cleaned_data.get("iban", "")
        profile.bic = self.cleaned_data.get("bic", "")
        profile.save()
        return user
