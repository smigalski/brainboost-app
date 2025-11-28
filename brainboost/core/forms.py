from django import forms

from .models import Lesson, ProgressEntry, StudentProfile, LearningMaterial, Invoice


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
                lessons__tutor=tutor_profile
            ).distinct()
            # Falls noch keine Lessons existieren, Tutor darf alle Schüler wählen
            self.fields["student"].queryset = students_qs if students_qs.exists() else StudentProfile.objects.all()


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
