from datetime import datetime

from django import forms
from django.utils import timezone

from apps.groups import closing
from apps.groups.models import Activity, Group

from .models import CorrectionRequest

# Ein Antrag deckt einen Arbeitstag ab, mehr als sechs Pausen sind dafür
# unrealistisch und halten das Formular übersichtlich.
MAX_BREAKS = 6


class DateTimeLocalInput(forms.DateTimeInput):
    """Feld für Datum und Uhrzeit im Browser.

    Der Wert kommt in drei Formen an: als Text aus einem abgeschickten
    Formular, als naive Ortszeit (so bereitet Django Anfangswerte auf) und
    als Zeitpunkt mit Zeitzone. Alle drei müssen dasselbe Format ergeben,
    sonst zeigt der Browser das Feld leer oder die Seite bricht ab.
    """

    input_type = "datetime-local"

    def format_value(self, value):
        if value is None or value == "":
            return ""
        if isinstance(value, datetime):
            if timezone.is_aware(value):
                value = timezone.localtime(value)
            return value.strftime("%Y-%m-%dT%H:%M")
        return str(value)


class BreakForm(forms.Form):
    """Eine einzelne beantragte Pause."""

    start = forms.DateTimeField(label="Pause von", required=False, widget=DateTimeLocalInput())
    end = forms.DateTimeField(label="Pause bis", required=False, widget=DateTimeLocalInput())

    @property
    def is_empty(self) -> bool:
        return not (self.cleaned_data.get("start") or self.cleaned_data.get("end"))

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if bool(start) != bool(end):
            raise forms.ValidationError("Bitte Beginn und Ende der Pause angeben.")
        if start and end and end <= start:
            self.add_error("end", "Das Pausenende muss nach dem Pausenbeginn liegen.")
        return cleaned


class BaseBreakFormSet(forms.BaseFormSet):
    """Prüft die Pausen gemeinsam: Reihenfolge, Überschneidung, Vollständigkeit."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        periods = []
        for form in self.forms:
            if not form.cleaned_data or form.is_empty:
                continue
            periods.append((form.cleaned_data["start"], form.cleaned_data["end"], form))

        periods.sort(key=lambda item: item[0])
        previous_end = None
        for start, end, form in periods:
            if previous_end is not None and start < previous_end:
                form.add_error("start", "Die Pausen dürfen sich nicht überschneiden.")
            previous_end = end

    def breaks(self) -> list[dict]:
        """Die beantragten Pausen, chronologisch und ohne leere Zeilen."""
        items = [
            (form.cleaned_data["start"], form.cleaned_data["end"])
            for form in self.forms
            if form.cleaned_data and not form.is_empty
        ]
        items.sort(key=lambda item: item[0])
        return [{"start": start.isoformat(), "end": end.isoformat()} for start, end in items]


# Alle Zeilen werden gleich angezeigt: ohne JavaScript gibt es kein
# "Zeile hinzufügen", und leere Zeilen werden beim Speichern verworfen.
BreakFormSet = forms.formset_factory(
    BreakForm, formset=BaseBreakFormSet, extra=MAX_BREAKS, max_num=MAX_BREAKS, validate_max=True
)


def break_formset(*args, entry=None, **kwargs):
    """Formset für die Pausen, beim Ändern mit den bisherigen Pausen vorbelegt."""
    initial = None
    if entry is not None:
        initial = [
            {"start": pause.start, "end": pause.end}
            for pause in entry.breaks.all()
            if pause.end is not None
        ]
    return BreakFormSet(*args, initial=initial or None, prefix="pausen", **kwargs)


def validate_breaks_within(formset, start, end) -> bool:
    """Prüft die Pausen gegen die beantragte Arbeitszeit."""
    ok = True
    for form in formset.forms:
        if not form.cleaned_data or form.is_empty:
            continue
        if form.cleaned_data["start"] < start or form.cleaned_data["end"] > end:
            form.add_error("start", "Die Pause muss innerhalb der Arbeitszeit liegen.")
            ok = False
    return ok


class CorrectionRequestForm(forms.Form):
    """Antrag auf Änderung oder Nachtrag eines Zeiteintrags."""

    group = forms.ModelChoiceField(queryset=Group.objects.none(), label="Gruppe", empty_label=None)
    activity = forms.ModelChoiceField(
        queryset=Activity.objects.none(), label="Tätigkeit", required=False
    )
    start = forms.DateTimeField(label="Beginn", widget=DateTimeLocalInput())
    end = forms.DateTimeField(label="Ende", widget=DateTimeLocalInput())
    reason = forms.CharField(label="Begründung", widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, user, *args, entry=None, **kwargs):
        self.user = user
        self.entry = entry
        super().__init__(*args, **kwargs)
        group_ids = user.member_group_ids()
        self.fields["group"].queryset = Group.objects.filter(pk__in=group_ids, is_active=True)
        self.fields["activity"].queryset = Activity.objects.filter(
            group_id__in=group_ids, is_active=True
        ).select_related("group")

        if entry is not None and not self.is_bound:
            self.initial.update(
                {
                    "group": entry.group_id,
                    "activity": entry.activity_id,
                    "start": entry.start,
                    "end": entry.end,
                }
            )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        group, activity = cleaned.get("group"), cleaned.get("activity")

        if start and end and end <= start:
            self.add_error("end", "Das Ende muss nach dem Beginn liegen.")
        if start and start > timezone.now():
            self.add_error("start", "Ein Beginn in der Zukunft ist nicht möglich.")
        if activity and group and activity.group_id != group.pk:
            self.add_error("activity", "Diese Tätigkeit gehört nicht zur gewählten Gruppe.")

        if group:
            days = [timezone.localtime(value).date() for value in (start, end) if value is not None]
            if self.entry is not None:
                days.append(timezone.localtime(self.entry.start).date())
            lock = closing.blocking_lock(group, days)
            if lock is not None:
                self.add_error(
                    None,
                    f"Der Zeitraum {lock.period.label} ist abgeschlossen. "
                    "Korrekturen sind dort nicht mehr möglich.",
                )

        return cleaned


class DecisionForm(forms.Form):
    note = forms.CharField(
        label="Begründung", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )


class DeleteRequestForm(forms.Form):
    reason = forms.CharField(label="Begründung", widget=forms.Textarea(attrs={"rows": 3}))


CORRECTION_KINDS = CorrectionRequest.Kind
