from django import forms

from .models import AuditLog


class AuditFilterForm(forms.Form):
    """Filter über dem Protokoll einer Gruppe. Alle Felder sind freiwillig."""

    person = forms.ChoiceField(label="Person", required=False, choices=[])
    action = forms.ChoiceField(
        label="Aktion",
        required=False,
        choices=[("", "alle Aktionen")] + list(AuditLog.Action.choices),
    )
    start = forms.DateField(
        label="Von", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    end = forms.DateField(
        label="Bis", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(self, people, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["person"].choices = [("", "alle Personen")] + [
            (str(person.pk), person.full_name) for person in people
        ]

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end < start:
            self.add_error("end", "Das Ende darf nicht vor dem Beginn liegen.")
        return cleaned

    @property
    def person_id(self) -> int | None:
        value = self.cleaned_data.get("person") if self.is_valid() else None
        return int(value) if value else None
