from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from apps.groups.models import Activity
from apps.groups.periods import MAX_MONTH_START_DAY
from apps.groups.permissions import readable_groups

from .access import may_schedule, visible_profiles
from .columns import COLUMNS, DEFAULT_COLUMNS
from .models import ExportProfile, ExportSchedule
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

    def as_query(self) -> dict:
        """Dieselbe Auswahl als Parameter für eine Adresszeile."""
        selection = self.selection()
        return {
            "start": selection["start"].isoformat(),
            "end": selection["end"].isoformat(),
            "groups": selection["group_ids"],
            "activities": selection["activity_ids"],
            "cost_centers": selection["cost_centers"],
        }


class SummaryForm(ScopeForm):
    """Zeitraum und Filter der Auswertungsseite (Issue 56)."""


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
    """Name und Sichtbarkeit einer gespeicherten Vorlage.

    Die beiden Haken sind getrennt, damit eine Person, die Buchhalterin und
    Gruppen-Admin zugleich ist, selbst entscheidet, wer die Vorlage bekommt
    (Issue 72).
    """

    name = forms.CharField(label="Name der Vorlage", max_length=120)
    share_with_group_admins = forms.BooleanField(
        label="Mit den Admins meiner Gruppen teilen", required=False
    )
    share_with_accounting = forms.BooleanField(label="Mit der Buchhaltung teilen", required=False)


MAX_RECIPIENTS = 10


class ExportScheduleForm(forms.ModelForm):
    """Zeitplan zu einer Vorlage: Tag im Monat, Zeitraum, Format und Empfänger."""

    recipients = forms.CharField(
        label="Empfänger",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Eine oder mehrere E-Mail-Adressen, getrennt durch Komma oder Zeilenumbruch.",
    )

    class Meta:
        model = ExportSchedule
        fields = [
            "profile",
            "day_of_month",
            "timeframe",
            "period_group",
            "export_format",
            "recipients",
            "is_active",
        ]

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["profile"].queryset = visible_profiles(user)
        self.fields["period_group"].queryset = readable_groups(user)
        self.fields["period_group"].empty_label = "Kalendermonat"
        self.fields["day_of_month"].widget.attrs.update({"min": 1, "max": MAX_MONTH_START_DAY})
        if isinstance(self.initial.get("recipients"), list):
            self.initial["recipients"] = ", ".join(self.initial["recipients"])

    def clean_day_of_month(self) -> int:
        day = self.cleaned_data["day_of_month"]
        if not 1 <= day <= MAX_MONTH_START_DAY:
            raise forms.ValidationError(
                f"Bitte einen Tag zwischen 1 und {MAX_MONTH_START_DAY} wählen, "
                "damit es ihn in jedem Monat gibt."
            )
        return day

    def clean_recipients(self) -> list[str]:
        raw = self.cleaned_data["recipients"].replace(";", ",").replace("\n", ",")
        addresses: list[str] = []
        for part in raw.split(","):
            address = part.strip()
            if not address or address in addresses:
                continue
            try:
                validate_email(address)
            except ValidationError as exc:
                raise forms.ValidationError(
                    f"„{address}“ ist keine gültige E-Mail-Adresse."
                ) from exc
            addresses.append(address)
        if not addresses:
            raise forms.ValidationError("Bitte mindestens eine E-Mail-Adresse angeben.")
        if len(addresses) > MAX_RECIPIENTS:
            raise forms.ValidationError(f"Höchstens {MAX_RECIPIENTS} Empfänger je Zeitplan.")
        return addresses

    def clean_profile(self) -> ExportProfile:
        """Die Vorlage muss der Nutzer wirklich benutzen dürfen, nicht nur im Feld stehen haben."""
        profile = self.cleaned_data["profile"]
        if not may_schedule(self.user, profile):
            raise forms.ValidationError("Diese Vorlage darfst du nicht einplanen.")
        return profile

    def clean(self):
        cleaned = super().clean()
        profile = cleaned.get("profile")
        day = cleaned.get("day_of_month")
        if profile and day:
            taken = ExportSchedule.objects.filter(
                profile=profile, created_by=self.user, day_of_month=day
            ).exclude(pk=self.instance.pk)
            if taken.exists():
                self.add_error(
                    "day_of_month",
                    "Zu dieser Vorlage gibt es an diesem Tag schon einen Zeitplan.",
                )
        return cleaned
