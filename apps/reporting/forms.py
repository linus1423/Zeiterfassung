from django import forms
from django.contrib.auth import get_user_model

from apps.groups.models import Activity
from apps.groups.permissions import readable_groups

from .columns import COLUMNS, DEFAULT_COLUMNS
from .models import ExportProfile
from .services import cost_center_choices

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


class ScopeForm(forms.Form):
    """Zeitraum und Filter, die Auswertungsseite und Export gemeinsam haben.

    Die Auswahl ist immer auf die Gruppen begrenzt, die der Nutzer lesen
    darf; ein manipuliertes Formular kann also keine fremden Zeiten holen.
    """

    start = forms.DateField(label="Von", widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="Bis", widget=forms.DateInput(attrs={"type": "date"}))
    groups = forms.ModelMultipleChoiceField(
        label="Gruppen",
        queryset=None,
        required=False,
        help_text="Leer bedeutet: alle Gruppen, die du sehen darfst.",
    )
    activities = forms.ModelMultipleChoiceField(
        label="Tätigkeiten", queryset=Activity.objects.none(), required=False
    )
    cost_centers = forms.MultipleChoiceField(
        label="Kostenstellen",
        choices=[],
        required=False,
        help_text="Leer bedeutet: alle Kostenstellen.",
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Einmal ermittelt und gemerkt, damit die Unterklasse nicht erneut fragt.
        groups = readable_groups(user)
        self.visible_groups = groups
        self.fields["groups"].queryset = groups
        self.fields["activities"].queryset = Activity.objects.filter(
            group__in=groups
        ).select_related("group")
        self.fields["cost_centers"].choices = cost_center_choices(groups)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end < start:
            self.add_error("end", "Das Ende darf nicht vor dem Beginn liegen.")
        return cleaned

    def selection(self) -> dict:
        """Zeitraum und Filter in der Form, die query_entries erwartet."""
        return {
            "start": self.cleaned_data["start"],
            "end": self.cleaned_data["end"],
            "group_ids": [group.pk for group in self.cleaned_data["groups"]],
            "activity_ids": [activity.pk for activity in self.cleaned_data["activities"]],
            "cost_centers": list(self.cleaned_data["cost_centers"]),
        }


class ExportForm(ScopeForm):
    """Zeitraum, Filter, Verdichtung und Spaltenauswahl für den Export."""

    users = forms.ModelMultipleChoiceField(label="Nutzer", queryset=None, required=False)
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
        super().__init__(user, *args, **kwargs)
        self.fields["users"].queryset = (
            User.objects.filter(group_memberships__group__in=self.visible_groups, is_active=True)
            .distinct()
            .order_by("last_name", "first_name")
        )

    def selection(self) -> dict:
        selection = super().selection()
        selection["user_ids"] = [user.pk for user in self.cleaned_data["users"]]
        return selection

    def ordered_columns(self) -> list[str]:
        """Die Spaltenreihenfolge folgt der Reihenfolge in COLUMNS."""
        chosen = set(self.cleaned_data.get("columns") or [])
        return [column.key for column in COLUMNS if column.key in chosen]


class ProfileSaveForm(forms.Form):
    name = forms.CharField(label="Name der Vorlage", max_length=120)
    is_shared = forms.BooleanField(label="Mit der Buchhaltung teilen", required=False)
