from django import forms
from django.db.models import Q
from django.utils import timezone

from apps.groups import closing
from apps.groups.models import Activity, Group

# Die Pausenfelder gehören zu den Zeiteinträgen, nicht zu den Anträgen:
# der Gruppen-Admin bearbeitet damit auch direkt (Issue 31). Weiter hier
# erreichbar, damit die Aufrufe in dieser Anwendung gleich bleiben.
from apps.tracking.forms import (  # noqa: F401
    MAX_BREAKS,
    BaseBreakFormSet,
    BreakForm,
    BreakFormSet,
    DateTimeLocalInput,
    break_formset,
    validate_breaks_within,
)

from .models import CorrectionRequest


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

        if entry is not None:
            # Ein bestehender Eintrag behält seine Gruppe: über sie läuft,
            # wer entscheiden darf und welcher Abschluss sperrt.
            self.fields["group"].queryset = Group.objects.filter(pk=entry.group_id)
            self.fields["activity"].queryset = Activity.objects.filter(
                group_id=entry.group_id, is_active=True
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
        if self.entry is not None and group and group.pk != self.entry.group_id:
            self.add_error("group", "Ein bestehender Eintrag bleibt in seiner Gruppe.")
            group = None
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


class ApprovalAdjustForm(forms.Form):
    """Genehmigen mit Änderung (Issue 31).

    Ist ein Antrag nur um eine Kleinigkeit daneben, passt der Admin die
    Zeiten hier an und übernimmt sie, statt den Antrag abzulehnen und um
    einen neuen zu bitten.
    """

    start = forms.DateTimeField(label="Beginn", widget=DateTimeLocalInput())
    end = forms.DateTimeField(label="Ende", widget=DateTimeLocalInput())
    activity = forms.ModelChoiceField(
        queryset=Activity.objects.none(), label="Tätigkeit", required=False
    )
    note = forms.CharField(
        label="Begründung der Änderung", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, correction, *args, **kwargs):
        self.correction = correction
        super().__init__(*args, **kwargs)
        # Eine seit dem Antrag deaktivierte Tätigkeit bleibt wählbar, sonst
        # entfiele sie bei der Übernahme stillschweigend.
        choosable = Q(is_active=True)
        if correction.proposed_activity_id:
            choosable |= Q(pk=correction.proposed_activity_id)
        self.fields["activity"].queryset = Activity.objects.filter(
            Q(group=correction.group) & choosable
        ).order_by("sort_order", "name")
        if not self.is_bound:
            self.initial.update(
                {
                    "start": correction.proposed_start,
                    "end": correction.proposed_end,
                    "activity": correction.proposed_activity_id,
                }
            )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end <= start:
            self.add_error("end", "Das Ende muss nach dem Beginn liegen.")
        if start and start > timezone.now():
            self.add_error("start", "Ein Beginn in der Zukunft ist nicht möglich.")
        return cleaned


class DecisionForm(forms.Form):
    note = forms.CharField(
        label="Begründung", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )


class DeleteRequestForm(forms.Form):
    reason = forms.CharField(label="Begründung", widget=forms.Textarea(attrs={"rows": 3}))


CORRECTION_KINDS = CorrectionRequest.Kind
