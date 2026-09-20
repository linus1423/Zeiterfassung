"""Tätigkeit wechseln, ohne aus- und wieder einzustempeln (Issue 30)."""

from datetime import timedelta

import pytest
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.groups.models import Activity, GroupMembership
from apps.tracking import services
from apps.tracking.models import TimeEntry


@pytest.fixture
def second_activity(group):
    return Activity.objects.create(group=group, name="Lackieren")


def test_switch_closes_the_old_entry_and_opens_a_new_one(member, group, activity, second_activity):
    old = services.clock_in(member, group, activity)

    new = services.switch_activity(member, second_activity)

    old.refresh_from_db()
    assert old.end == new.start, "zwischen den beiden Einträgen darf keine Lücke liegen"
    assert old.activity == activity
    assert new.activity == second_activity
    assert new.group == group
    assert new.is_open
    assert TimeEntry.objects.open().filter(user=member).count() == 1


def test_switch_keeps_the_worked_time(member, group, activity, second_activity):
    old = services.clock_in(member, group, activity)
    old.start = old.start - timedelta(hours=2)
    old.save(update_fields=["start"])

    services.switch_activity(member, second_activity)

    old.refresh_from_db()
    assert timedelta(hours=1, minutes=59) < old.duration < timedelta(hours=2, minutes=1)


def test_switch_during_a_break_is_rejected(member, group, activity, second_activity):
    services.clock_in(member, group, activity)
    services.start_break(member)

    with pytest.raises(services.ClockError, match="Pause"):
        services.switch_activity(member, second_activity)


def test_switch_to_the_same_activity_is_rejected(member, group, activity):
    services.clock_in(member, group, activity)

    with pytest.raises(services.ClockError):
        services.switch_activity(member, activity)


def test_switch_to_an_activity_of_another_group_is_rejected(member, group, activity, other_group):
    GroupMembership.objects.create(user=member, group=other_group)
    foreign = Activity.objects.create(group=other_group, name="Fremd")
    services.clock_in(member, group, activity)

    with pytest.raises(services.ClockError):
        services.switch_activity(member, foreign)


def test_switch_to_an_inactive_activity_is_rejected(member, group, activity, second_activity):
    second_activity.is_active = False
    second_activity.save(update_fields=["is_active"])
    services.clock_in(member, group, activity)

    with pytest.raises(services.ClockError):
        services.switch_activity(member, second_activity)


def test_switch_without_being_clocked_in_is_rejected(member, second_activity):
    with pytest.raises(services.ClockError):
        services.switch_activity(member, second_activity)


def test_switch_is_logged(member, group, activity, second_activity):
    services.clock_in(member, group, activity)

    services.switch_activity(member, second_activity)

    actions = list(AuditLog.objects.order_by("id").values_list("action", flat=True))
    assert actions == [
        AuditLog.Action.CLOCK_IN,
        AuditLog.Action.CLOCK_OUT,
        AuditLog.Action.CLOCK_IN,
    ]


def test_switch_through_the_web(client, member, group, activity, second_activity):
    client.force_login(member)
    client.post(
        reverse("tracking:clock_in"),
        {"group": group.pk, "activity": activity.pk, "note": ""},
        follow=True,
    )

    response = client.post(
        reverse("tracking:switch_activity"), {"activity": second_activity.pk}, follow=True
    )

    assert response.status_code == 200
    assert services.get_state(member).entry.activity == second_activity


def test_switch_needs_post(client, member):
    client.force_login(member)

    assert client.get(reverse("tracking:switch_activity")).status_code == 405
