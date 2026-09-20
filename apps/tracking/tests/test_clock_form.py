"""Auswahl beim Einstempeln: Bündelung, Vorbelegung, verstecktes Gruppenfeld (Issue 27)."""

from datetime import timedelta

from django import forms
from django.utils import timezone

from apps.groups.models import Activity, Group, GroupMembership
from apps.tracking.forms import ClockInForm
from apps.tracking.models import TimeEntry


def test_group_field_is_hidden_for_a_single_group(member, group, activity):
    form = ClockInForm(member)

    assert isinstance(form.fields["group"].widget, forms.HiddenInput)
    assert form.fields["group"].initial == group.pk


def test_activities_are_grouped_when_there_are_several_groups(member, group, activity):
    second = Group.objects.create(name="Lager")
    GroupMembership.objects.create(user=member, group=second)
    Activity.objects.create(group=second, name="Montage")

    form = ClockInForm(member)
    choices = form.fields["activity"].choices

    assert not isinstance(form.fields["group"].widget, forms.HiddenInput)
    # Erster Eintrag ist die leere Auswahl, danach eine Gruppe je Bündel.
    labels = [label for label, _ in choices[1:]]
    assert labels == ["Lager", "Werkstatt"]


def test_last_used_activity_is_preselected(member, group, activity):
    other = Activity.objects.create(group=group, name="Lackieren")
    now = timezone.now()
    TimeEntry.objects.create(
        user=member, group=group, activity=other, start=now - timedelta(hours=2), end=now
    )

    form = ClockInForm(member)

    assert form.fields["activity"].initial == other.pk


def test_preselection_skips_a_deactivated_activity(member, group, activity):
    other = Activity.objects.create(group=group, name="Lackieren", is_active=False)
    now = timezone.now()
    TimeEntry.objects.create(
        user=member, group=group, activity=other, start=now - timedelta(hours=2), end=now
    )

    form = ClockInForm(member)

    assert form.fields["activity"].initial is None


def test_activity_of_another_group_is_still_rejected(member, group, activity, other_group):
    GroupMembership.objects.create(user=member, group=other_group)
    foreign = Activity.objects.create(group=other_group, name="Fremd")

    form = ClockInForm(member, {"group": group.pk, "activity": foreign.pk})

    assert not form.is_valid()
    assert "activity" in form.errors
