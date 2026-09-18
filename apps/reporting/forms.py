from django import forms
from django.contrib.auth import get_user_model

from apps.groups.models import Activity
from apps.groups.permissions import readable_groups

from .columns import COLUMNS, DEFAULT_COLUMNS
from .models import ExportProfile

User = get_user_model()

CSV_DIALECTS = {
    "de": {
        "label": "Deutsch (Semikolon, Komma, UTF-8 mit BOM)",
        "delimiter": ";",
        "decimal": ",",
        "with_bom": True,
    },
    "int": {
        "label": "International (Komma, Punkt, UTF-8)",
        "delimiter": ",",
        "decimal": ".",
        "with_bom": False,
    },
}


class ExportForm(forms.Form):
    """Zeitraum, Filter, Verdichtung und Spaltenauswahl fuer den Export."""

    start = forms.DateField(label="Von", widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="Bis", widget=forms.DateInput(attrs={"type": "date"}))
    groups = forms.ModelMultipleChoiceField(
        label="Gruppen",
        queryset=None,
        required=False,
        help_text="Leer bedeutet: alle Gruppen, die du sehen darfst.",
    )
    users = forms.ModelMultipleChoiceField(label="Nutzer", queryset=None, required=False)
    activities = forms.ModelMultipleChoiceField(
        label="Taetigkeiten", queryset=Activity.objects.none(), required=False
    )
    grouping = forms.ChoiceField(
        label="Verdichtung",
        choices=ExportProfile.Grouping.choices,
        initial=ExportProfile.Grouping.ENTRY,
    )
    columns = forms.MultipleChoiceField(
        label="Spalten",
        choices=[(column.key, column.label) for column in COLUMNS],
        initial=DEFAULT_COLUMNS,
        widget=forms.CheckboxSelectMultiple,
    )
    csv_dialect = forms.ChoiceField(
        label="CSV-Variante",
        choices=[(key, value["label"]) for key, value in CSV_DIALECTS.items()],
        initial="de",
        required=False,
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        groups = readable_groups(user)
        self.fields["groups"].queryset = groups
        self.fields["activities"].queryset = Activity.objects.filter(
            group__in=groups
        ).select_related("group")
        self.fields["users"].queryset = (
            User.objects.filter(group_memberships__group__in=groups, is_active=True)
            .distinct()
            .order_by("last_name", "first_name")
        )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end < start:
            self.add_error("end", "Das Ende darf nicht vor dem Beginn liegen.")
        return cleaned

    def ordered_columns(self) -> list[str]:
        """Die Spaltenreihenfolge folgt der Reihenfolge in COLUMNS."""
        chosen = set(self.cleaned_data.get("columns") or [])
        return [column.key for column in COLUMNS if column.key in chosen]


class ProfileSaveForm(forms.Form):
    name = forms.CharField(label="Name der Vorlage", max_length=120)
    is_shared = forms.BooleanField(label="Mit der Buchhaltung teilen", required=False)
