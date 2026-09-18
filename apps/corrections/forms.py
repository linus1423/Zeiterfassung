from django import forms
from django.utils import timezone

from apps.groups.models import Activity, Group

from .models import CorrectionRequest


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def format_value(self, value):
        if value is None:
            return ""
        if hasattr(value, "tzinfo"):
            value = timezone.localtime(value)
        return value.strftime("%Y-%m-%dT%H:%M")


class CorrectionRequestForm(forms.Form):
    """Antrag auf Aenderung oder Nachtrag eines Zeiteintrags."""

    group = forms.ModelChoiceField(queryset=Group.objects.none(), label="Gruppe", empty_label=None)
    activity = forms.ModelChoiceField(
        queryset=Activity.objects.none(), label="Taetigkeit", required=False
    )
    start = forms.DateTimeField(label="Beginn", widget=DateTimeLocalInput())
    end = forms.DateTimeField(label="Ende", widget=DateTimeLocalInput())
    break_start = forms.DateTimeField(
        label="Pause von", required=False, widget=DateTimeLocalInput()
    )
    break_end = forms.DateTimeField(label="Pause bis", required=False, widget=DateTimeLocalInput())
    reason = forms.CharField(label="Begruendung", widget=forms.Textarea(attrs={"rows": 3}))

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
            first_break = entry.breaks.first()
            if first_break is not None:
                self.initial.update(
                    {"break_start": first_break.start, "break_end": first_break.end}
                )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        group, activity = cleaned.get("group"), cleaned.get("activity")
        break_start, break_end = cleaned.get("break_start"), cleaned.get("break_end")

        if start and end and end <= start:
            self.add_error("end", "Das Ende muss nach dem Beginn liegen.")
        if start and start > timezone.now():
            self.add_error("start", "Ein Beginn in der Zukunft ist nicht moeglich.")
        if activity and group and activity.group_id != group.pk:
            self.add_error("activity", "Diese Taetigkeit gehoert nicht zur gewaehlten Gruppe.")

        if bool(break_start) != bool(break_end):
            self.add_error("break_end", "Bitte Beginn und Ende der Pause angeben.")
        elif break_start and break_end:
            if break_end <= break_start:
                self.add_error("break_end", "Das Pausenende muss nach dem Pausenbeginn liegen.")
            elif start and end and (break_start < start or break_end > end):
                self.add_error("break_start", "Die Pause muss innerhalb der Arbeitszeit liegen.")

        return cleaned

    def proposed_breaks(self) -> list[dict]:
        start = self.cleaned_data.get("break_start")
        end = self.cleaned_data.get("break_end")
        if start and end:
            return [{"start": start.isoformat(), "end": end.isoformat()}]
        return []


class DecisionForm(forms.Form):
    note = forms.CharField(
        label="Begruendung", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )


class DeleteRequestForm(forms.Form):
    reason = forms.CharField(label="Begruendung", widget=forms.Textarea(attrs={"rows": 3}))


CORRECTION_KINDS = CorrectionRequest.Kind
