"""Aufteilung an der Tagesgrenze (Issue 32)."""

from datetime import date, datetime, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.tracking.daysplit import day_parts, entries_in_range, parts_in_range
from apps.tracking.models import BreakEntry, TimeEntry


def _local(day: date, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


@pytest.fixture
def night_shift(member, group, activity):
    """Schicht von 22:00 bis 06:00 mit einer Pause über Mitternacht."""
    first = timezone.localdate() - timedelta(days=3)
    entry = TimeEntry.objects.create(
        user=member,
        group=group,
        activity=activity,
        start=_local(first, 22),
        end=_local(first + timedelta(days=1), 6),
    )
    BreakEntry.objects.create(
        time_entry=entry,
        start=_local(first, 23, 30),
        end=_local(first + timedelta(days=1), 0, 30),
    )
    return entry


def test_a_night_shift_lands_on_both_days(night_shift):
    parts = day_parts(night_shift)

    assert len(parts) == 2
    assert parts[0].day == timezone.localdate() - timedelta(days=3)
    assert parts[1].day == parts[0].day + timedelta(days=1)
    # Zwei Stunden Anwesenheit am ersten Tag, davon eine halbe Stunde Pause.
    assert parts[0].gross == timedelta(hours=2)
    assert parts[0].breaks == timedelta(minutes=30)
    assert parts[0].work == timedelta(hours=1, minutes=30)
    assert parts[1].gross == timedelta(hours=6)
    assert parts[1].breaks == timedelta(minutes=30)
    assert parts[1].work == timedelta(hours=5, minutes=30)


def test_the_parts_add_up_to_the_whole_entry(night_shift):
    parts = day_parts(night_shift)

    assert sum((part.work for part in parts), timedelta()) == night_shift.duration
    assert sum((part.breaks for part in parts), timedelta()) == night_shift.break_duration


def test_a_part_can_be_limited_to_a_period(night_shift):
    second_day = timezone.localdate() - timedelta(days=2)

    parts = day_parts(night_shift, first_day=second_day)

    assert [part.day for part in parts] == [second_day]


def test_an_entry_within_one_day_stays_one_part(member, group, activity):
    day = timezone.localdate() - timedelta(days=1)
    entry = TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=_local(day, 8), end=_local(day, 16)
    )

    parts = day_parts(entry)

    assert len(parts) == 1
    assert parts[0].work == timedelta(hours=8)
    assert parts[0].is_partial is False


def test_an_entry_ending_at_midnight_has_no_second_part(member, group, activity):
    day = timezone.localdate() - timedelta(days=2)
    entry = TimeEntry.objects.create(
        user=member,
        group=group,
        activity=activity,
        start=_local(day, 20),
        end=_local(day + timedelta(days=1), 0),
    )

    assert [part.day for part in day_parts(entry)] == [day]


def test_the_switch_to_summer_time_shortens_the_night(member, group, activity, settings):
    """In der Nacht auf den 29.03.2026 fehlt eine Stunde; sie darf nicht mitgezählt werden."""
    settings.TIME_ZONE = "Europe/Berlin"
    timezone.activate("Europe/Berlin")
    try:
        entry = TimeEntry.objects.create(
            user=member,
            group=group,
            activity=activity,
            start=_local(date(2026, 3, 28), 22),
            end=_local(date(2026, 3, 29), 6),
        )

        parts = day_parts(entry)

        assert [part.day for part in parts] == [date(2026, 3, 28), date(2026, 3, 29)]
        assert parts[0].gross == timedelta(hours=2)
        # Von 00:00 bis 06:00 Ortszeit vergehen an diesem Tag nur fünf Stunden.
        assert parts[1].gross == timedelta(hours=5)
        assert sum((part.gross for part in parts), timedelta()) == entry.gross_duration
    finally:
        timezone.deactivate()


def test_entries_running_into_the_period_are_included(night_shift, member):
    second_day = timezone.localdate() - timedelta(days=2)

    found = entries_in_range(TimeEntry.objects.filter(user=member), second_day, second_day)

    assert list(found) == [night_shift]


def test_a_running_entry_counts_until_now(member, group, activity):
    entry = TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=timezone.now() - timedelta(hours=2)
    )

    parts = day_parts(entry)

    assert parts[-1].end <= timezone.now()
    assert sum((part.work for part in parts), timedelta()) > timedelta(hours=1, minutes=59)


def test_my_entries_counts_only_the_part_in_the_period(client, member, night_shift):
    second_day = timezone.localdate() - timedelta(days=2)
    client.force_login(member)

    response = client.get(
        reverse("tracking:my_entries"),
        {"start": second_day.isoformat(), "end": second_day.isoformat()},
    )

    assert response.context["total"] == timedelta(hours=5, minutes=30)
    assert [day for day, _ in response.context["by_day"]] == [second_day]


def test_the_group_overview_splits_the_night(client, group_admin, group, night_shift):
    second_day = timezone.localdate() - timedelta(days=2)
    client.force_login(group_admin)

    response = client.get(
        reverse("groups:detail", args=[group.pk]),
        {"start": second_day.isoformat(), "end": second_day.isoformat()},
    )

    assert response.context["total"] == timedelta(hours=5, minutes=30)


def test_the_export_makes_one_row_per_day(night_shift):
    from apps.reporting import services as reporting

    rows = reporting.build_rows([night_shift], "entry")

    assert len(rows) == 2
    assert rows[0]["end"] == time(0, 0)
    assert rows[1]["start"] == time(0, 0)
    assert rows[0]["work_seconds"] + rows[1]["work_seconds"] == night_shift.duration.total_seconds()


def test_parts_in_range_sorts_chronologically(night_shift, member, group, activity):
    day = timezone.localdate() - timedelta(days=5)
    earlier = TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=_local(day, 8), end=_local(day, 12)
    )

    parts = parts_in_range([night_shift, earlier])

    assert [part.day for part in parts] == [
        day,
        timezone.localdate() - timedelta(days=3),
        timezone.localdate() - timedelta(days=2),
    ]


def test_an_entry_over_midnight_is_marked_in_the_list(client, member, night_shift):
    client.force_login(member)
    first_day = timezone.localdate() - timedelta(days=3)

    response = client.get(
        reverse("tracking:my_entries"),
        {"start": first_day.isoformat(), "end": timezone.localdate().isoformat()},
    )

    assert night_shift.spans_days is True
    assert "über Mitternacht" in response.content.decode()


def test_an_entry_ending_at_midnight_is_not_marked(member, group, activity):
    day = timezone.localdate() - timedelta(days=2)
    entry = TimeEntry.objects.create(
        user=member,
        group=group,
        activity=activity,
        start=_local(day, 20),
        end=_local(day + timedelta(days=1), 0),
    )

    assert entry.spans_days is False
