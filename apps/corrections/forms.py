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
from apps.tracking.utils import local_day_range

from .models import CorrectionRequest


class CorrectionRequestForm(forms.Form):
    """Antrag auf Änderung oder Nachtrag eines Zeiteintrags."""

    group = forms.ModelChoiceField(queryset=Group.objects.none(), label="Gruppe", empty_label=None)
    activity = forms.ModelChoiceField(queryset=Activity.objects.none(), label="Tätigkeit")
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
            ranges = [local_day_range(start, end)]
            if self.entry is not None:
                ranges.append(local_day_range(self.entry.start, self.entry.end))
            lock = closing.blocking_lock(group, ranges)
            if lock is not None:
                self.add_error(
                    None,
                    f"Der Zeitraum {lock.period.label} ist abgeschlossen. "
                    "Korrekturen sind dort nicht mehr möglich.",
                )

        return cleaned


class MoveRequestForm(forms.Form):
    """Antrag, einen Zeiteintrag in eine andere eigene Gruppe zu verschieben (Issue 37).

    Die Zeiten bleiben, nur die Gruppe wechselt. Eine Tätigkeit gehört immer
    genau einer Gruppe, die alte passt danach also nicht mehr; deshalb wird
    hier gleich die neue gewählt. Sie ist Pflicht: ein Zeiteintrag ohne
    Tätigkeit soll es nicht geben.
    """

    target_group = forms.ModelChoiceField(
        queryset=Group.objects.none(),
        label="Gewünschte Gruppe",
        empty_label=None,
        help_text="Nur Gruppen, in denen du selbst Mitglied bist.",
    )
    activity = forms.ModelChoiceField(
        queryset=Activity.objects.none(),
        label="Tätigkeit in der neuen Gruppe",
        help_text="Die alte Tätigkeit gehört zur alten Gruppe und passt danach nicht mehr.",
    )
    reason = forms.CharField(label="Begründung", widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, user, entry, *args, **kwargs):
        self.user = user
        self.entry = entry
        super().__init__(*args, **kwargs)
        target_ids = [
            group_id for group_id in user.member_group_ids() if group_id != entry.group_id
        ]
        # Ohne wählbare Tätigkeit wäre der Wechsel dorthin nicht abschickbar,
        # also steht eine solche Gruppe gar nicht erst zur Wahl.
        self.fields["target_group"].queryset = Group.objects.filter(
            pk__in=target_ids, is_active=True, activities__is_active=True
        ).distinct()
        self.fields["activity"].queryset = Activity.objects.filter(
            group_id__in=target_ids, is_active=True
        ).select_related("group")

    @property
    def has_targets(self) -> bool:
        """Ohne zweite Gruppe mit Tätigkeiten gibt es nichts zu wechseln."""
        return self.fields["target_group"].queryset.exists()

    def clean(self):
        cleaned = super().clean()
        target, activity = cleaned.get("target_group"), cleaned.get("activity")
        if activity and target and activity.group_id != target.pk:
            self.add_error("activity", "Diese Tätigkeit gehört nicht zur gewünschten Gruppe.")
        if target:
            # Gesperrt ist der Wechsel, sobald einer der beiden Zeiträume
            # abgeschlossen ist: in der alten Gruppe verschwände die Zeit, in
            # der neuen entstünde sie.
            ranges = [local_day_range(self.entry.start, self.entry.end)]
            for group in (self.entry.group, target):
                lock = closing.blocking_lock(group, ranges)
                if lock is not None:
                    self.add_error(
                        None,
                        f"Der Zeitraum {lock.period.label} ist in {group.name} abgeschlossen. "
                        "Ein Wechsel ist dort nicht mehr möglich.",
                    )
                    break
        return cleaned


class ApprovalAdjustForm(forms.Form):
    """Genehmigen mit Änderung (Issue 31).

    Ist ein Antrag nur um eine Kleinigkeit daneben, passt der Admin die
    Zeiten hier an und übernimmt sie, statt den Antrag abzulehnen und um
    einen neuen zu bitten.
    """

    start = forms.DateTimeField(label="Beginn", widget=DateTimeLocalInput())
    end = forms.DateTimeField(label="Ende", widget=DateTimeLocalInput())
    activity = forms.ModelChoiceField(queryset=Activity.objects.none(), label="Tätigkeit")
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
