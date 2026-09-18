from datetime import timedelta

import pytest
from django.utils import timezone

from apps.groups.models import Activity, GroupMembership
from apps.tracking import services
from apps.tracking.models import BreakEntry, TimeEntry


def test_clock_in_creates_open_entry(member, group, activity):
    entry = services.clock_in(member, group, activity)

    assert entry.is_open
    assert entry.source == TimeEntry.Source.CLOCK
    assert services.get_state(member).label == "arbeitet"


def test_clock_in_twice_is_rejected(member, group, activity):
    services.clock_in(member, group, activity)

    with pytest.raises(services.ClockError):
        services.clock_in(member, group, activity)


def test_clock_in_requires_membership(make_user, group, activity):
    stranger = make_user("fremd@example.com")

    with pytest.raises(services.ClockError):
        services.clock_in(stranger, group, activity)


def test_clock_in_rejects_activity_of_other_group(member, group, other_group):
    foreign = Activity.objects.create(group=other_group, name="Fremd")
    GroupMembership.objects.create(user=member, group=other_group)

    with pytest.raises(services.ClockError):
        services.clock_in(member, group, foreign)


def test_clock_in_rejects_inactive_activity(member, group, activity):
    activity.is_active = False
    activity.save(update_fields=["is_active"])

    with pytest.raises(services.ClockError):
        services.clock_in(member, group, activity)


def test_break_cycle(member, group, activity):
    services.clock_in(member, group, activity)

    services.start_break(member)
    assert services.get_state(member).label == "in Pause"

    with pytest.raises(services.ClockError):
        services.start_break(member)

    services.end_break(member)
    assert services.get_state(member).label == "arbeitet"

    with pytest.raises(services.ClockError):
        services.end_break(member)


def test_clock_out_closes_running_break(member, group, activity):
    services.clock_in(member, group, activity)
    services.start_break(member)

    entry = services.clock_out(member)

    assert entry.end is not None
    assert not entry.breaks.filter(end__isnull=True).exists()
    assert services.get_state(member).label == "ausgestempelt"


def test_clock_out_without_entry_is_rejected(member):
    with pytest.raises(services.ClockError):
        services.clock_out(member)


def test_duration_subtracts_breaks(member, group, activity):
    start = timezone.now() - timedelta(hours=8)
    entry = TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=start, end=start + timedelta(hours=8)
    )
    BreakEntry.objects.create(
        time_entry=entry,
        start=start + timedelta(hours=4),
        end=start + timedelta(hours=4, minutes=30),
    )

    assert entry.duration == timedelta(hours=7, minutes=30)
    assert entry.break_duration == timedelta(minutes=30)


def test_close_stale_entries_marks_incomplete(member, group, activity):
    start = timezone.now() - timedelta(hours=20)
    TimeEntry.objects.create(user=member, group=group, activity=activity, start=start)

    closed = services.close_stale_entries(max_hours=16)

    entry = TimeEntry.objects.get(user=member)
    assert closed == 1
    assert entry.is_incomplete
    assert entry.end == start + timedelta(hours=16)


def test_close_stale_entries_keeps_fresh_entries(member, group, activity):
    services.clock_in(member, group, activity)

    assert services.close_stale_entries(max_hours=16) == 0
    assert TimeEntry.objects.open().filter(user=member).exists()


@pytest.mark.parametrize(
    ("worked_hours", "break_minutes", "expected"),
    [(5, 0, False), (7, 10, True), (7, 35, False), (10, 40, True), (10, 50, False)],
)
def test_statutory_break_warning(worked_hours, break_minutes, expected):
    warning = services.statutory_break_warning(
        timedelta(hours=worked_hours), timedelta(minutes=break_minutes)
    )

    assert bool(warning) is expected


def test_close_stale_entries_keeps_a_running_break_inside_the_entry(member, group, activity):
    start = timezone.now() - timedelta(hours=20)
    entry = TimeEntry.objects.create(user=member, group=group, activity=activity, start=start)
    pause_start = timezone.now() - timedelta(hours=1)
    BreakEntry.objects.create(time_entry=entry, start=pause_start)

    services.close_stale_entries(max_hours=16)

    entry.refresh_from_db()
    pause = entry.breaks.get()
    assert pause.end is not None
    assert pause.start >= entry.start
    assert pause.end <= entry.end
