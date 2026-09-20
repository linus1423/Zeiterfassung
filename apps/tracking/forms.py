from django import forms

from apps.groups.models import Activity, Group

from .models import TimeEntry

EMPTY_LABEL = "---------"


def activity_choices(activities, *, grouped: bool) -> list:
    """Auswahlliste der Tätigkeiten, bei mehreren Gruppen nach Gruppe gebündelt.

    Ohne die Bündelung sind zwei gleichnamige Tätigkeiten aus zwei Gruppen in
    der Liste nicht zu unterscheiden.
    """
    if not grouped:
        return [("", EMPTY_LABEL)] + [(item.pk, item.name) for item in activities]

    by_group: dict[str, list] = {}
    for item in activities:
        by_group.setdefault(item.group.name, []).append((item.pk, item.name))
    return [("", EMPTY_LABEL)] + sorted(by_group.items())


def last_entry(user) -> TimeEntry | None:
    """Der zuletzt begonnene Eintrag des Nutzers, für die Vorbelegung."""
    return (
        TimeEntry.objects.filter(user=user)
        .select_related("group", "activity")
        .order_by("-start")
        .first()
    )


class ClockInForm(forms.Form):
    """Auswahl von Gruppe und Tätigkeit beim Einstempeln."""

    group = forms.ModelChoiceField(queryset=Group.objects.none(), label="Gruppe", empty_label=None)
    activity = forms.ModelChoiceField(queryset=Activity.objects.none(), label="Tätigkeit")
    note = forms.CharField(label="Notiz", required=False, max_length=500)

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        group_ids = user.member_group_ids()
        groups = Group.objects.filter(pk__in=group_ids, is_active=True)
        activities = list(
            Activity.objects.filter(group_id__in=group_ids, is_active=True)
            .select_related("group")
            .order_by("group__name", "sort_order", "name")
        )

        self.fields["group"].queryset = groups
        self.fields["activity"].queryset = Activity.objects.filter(
            group_id__in=group_ids, is_active=True
        )

        # Wer nur einer Gruppe angehört, soll sie nicht jedes Mal auswählen.
        self.single_group = groups[0] if len(groups) == 1 else None
        if self.single_group is not None:
            self.fields["group"].widget = forms.HiddenInput()
            self.fields["group"].initial = self.single_group.pk

        self.fields["activity"].choices = activity_choices(
            activities, grouped=self.single_group is None
        )

        if not self.is_bound:
            self._preselect(user, activities)

    def _preselect(self, user, activities) -> None:
        """Gruppe und Tätigkeit des letzten Eintrags vorbelegen, wenn es sie noch gibt."""
        previous = last_entry(user)
        if previous is None:
            return
        if self.single_group is None and previous.group_id in {
            item.group_id for item in activities
        }:
            self.fields["group"].initial = previous.group_id
        if previous.activity_id in {item.pk for item in activities}:
            self.fields["activity"].initial = previous.activity_id

    def clean(self):
        cleaned = super().clean()
        group = cleaned.get("group")
        activity = cleaned.get("activity")
        if group and activity and activity.group_id != group.pk:
            self.add_error("activity", "Diese Tätigkeit gehört nicht zur gewählten Gruppe.")
        return cleaned


class SwitchActivityForm(forms.Form):
    """Wechsel der Tätigkeit während einer laufenden Stempelung."""

    activity = forms.ModelChoiceField(queryset=Activity.objects.none(), label="Neue Tätigkeit")

    def __init__(self, entry, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Activity.objects.filter(group_id=entry.group_id, is_active=True).exclude(
            pk=entry.activity_id
        )
        self.fields["activity"].queryset = queryset
        self.fields["activity"].choices = activity_choices(
            list(queryset.order_by("sort_order", "name")), grouped=False
        )
        self.has_choices = queryset.exists()


class PeriodForm(forms.Form):
    """Zeitraumfilter für Listen und Auswertungen."""

    start = forms.DateField(label="Von", widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="Bis", widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end < start:
            self.add_error("end", "Das Ende darf nicht vor dem Beginn liegen.")
        return cleaned


class MyEntriesFilterForm(PeriodForm):
    """Zeitraum und Tätigkeit für die eigene Zeitliste."""

    activity = forms.ModelChoiceField(
        queryset=Activity.objects.none(),
        label="Tätigkeit",
        required=False,
        empty_label="alle Tätigkeiten",
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        group_ids = user.member_group_ids()
        # Auch deaktivierte Tätigkeiten, sonst verschwindet der Filter für
        # Zeiten, die darauf gebucht wurden.
        queryset = (
            Activity.objects.filter(group_id__in=group_ids)
            .select_related("group")
            .order_by("group__name", "sort_order", "name")
        )
        self.fields["activity"].queryset = queryset
        self.fields["activity"].choices = [("", "alle Tätigkeiten")] + activity_choices(
            list(queryset), grouped=len(set(group_ids)) > 1
        )[1:]
