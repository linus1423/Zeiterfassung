from django import forms

from apps.groups.models import Activity, Group


class ClockInForm(forms.Form):
    """Auswahl von Gruppe und Taetigkeit beim Einstempeln."""

    group = forms.ModelChoiceField(queryset=Group.objects.none(), label="Gruppe", empty_label=None)
    activity = forms.ModelChoiceField(queryset=Activity.objects.none(), label="Taetigkeit")
    note = forms.CharField(label="Notiz", required=False, max_length=500)

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        group_ids = user.member_group_ids()
        self.fields["group"].queryset = Group.objects.filter(pk__in=group_ids, is_active=True)
        self.fields["activity"].queryset = Activity.objects.filter(
            group_id__in=group_ids, is_active=True
        ).select_related("group")

    def clean(self):
        cleaned = super().clean()
        group = cleaned.get("group")
        activity = cleaned.get("activity")
        if group and activity and activity.group_id != group.pk:
            self.add_error("activity", "Diese Taetigkeit gehoert nicht zur gewaehlten Gruppe.")
        return cleaned


class PeriodForm(forms.Form):
    """Zeitraumfilter fuer Listen und Auswertungen."""

    start = forms.DateField(label="Von", widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="Bis", widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end < start:
            self.add_error("end", "Das Ende darf nicht vor dem Beginn liegen.")
        return cleaned
