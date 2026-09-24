"""Export mit eigenem Abrechnungszyklus und Abschluss (Issues 5 und 8)."""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.groups import closing
from apps.groups.models import Activity
from apps.reporting import services
from apps.reporting.columns import resolve
from apps.tracking.models import TimeEntry
from apps.tracking.utils import day_bounds


def _entry(user, group, day: date, hours: int = 8) -> TimeEntry:
    start, _ = day_bounds(day)
    activity, _ = Activity.objects.get_or_create(group=group, name="Montage")
    return TimeEntry.objects.create(
        user=user,
        group=group,
        activity=activity,
        start=start + timedelta(hours=8),
        end=start + timedelta(hours=8 + hours),
    )


@pytest.fixture
def cycle_group(group):
    group.month_start_day = 15
    group.save(update_fields=["month_start_day"])
    return group


def test_rows_carry_the_cycle_of_the_group(cycle_group, member):
    entry = _entry(member, cycle_group, date(2026, 9, 20))

    row = services.build_rows([entry], "entry")[0]

    assert row["period_start"] == date(2026, 9, 15)
    assert row["period_end"] == date(2026, 10, 14)
    assert row["period_label"] == "15.09.2026 bis 14.10.2026"
    assert row["month"] == "September"


def test_a_calendar_month_group_keeps_the_plain_label(group, member):
    entry = _entry(member, group, date(2026, 9, 20))

    row = services.build_rows([entry], "entry")[0]

    assert row["period_label"] == "September 2026"
    assert row["period_start"] == date(2026, 9, 1)


def test_the_cycle_splits_the_monthly_summary(cycle_group, member):
    # Beide Tage liegen im September, aber in verschiedenen Abrechnungszeiträumen.
    entries = [
        _entry(member, cycle_group, date(2026, 9, 10)),
        _entry(member, cycle_group, date(2026, 9, 20)),
    ]

    rows = services.build_rows(entries, "user_month")

    assert len(rows) == 2
    assert {row["period_start"] for row in rows} == {date(2026, 8, 15), date(2026, 9, 15)}


def test_the_calendar_month_summary_stays_one_row(group, member):
    entries = [
        _entry(member, group, date(2026, 9, 10)),
        _entry(member, group, date(2026, 9, 20)),
    ]

    rows = services.build_rows(entries, "user_month")

    assert len(rows) == 1
    assert rows[0]["work_seconds"] == 16 * 3600


def test_a_closed_period_is_marked_in_the_export(group, member, group_admin):
    period = group.current_period().previous()
    entry = _entry(member, group, period.start)
    closing.close_period(group, period, group_admin)
    closed = closing.ClosedPeriods([group.pk])

    rows = services.build_rows([entry], "entry", closed)

    assert rows[0]["period_closed"] == "ja"


def test_without_a_close_the_column_stays_empty(group, member):
    entry = _entry(member, group, timezone.localdate() - timedelta(days=1))

    rows = services.build_rows([entry], "entry", closing.ClosedPeriods([group.pk]))

    assert rows[0]["period_closed"] == ""


def test_the_period_columns_are_exportable(cycle_group, member):
    entry = _entry(member, cycle_group, date(2026, 9, 20))
    rows = services.build_rows([entry], "entry")
    columns = resolve(["full_name", "period_label", "period_start", "period_closed", "hours"])

    values = [services.format_value(rows[0], column) for column in columns]

    assert values[1] == "15.09.2026 bis 14.10.2026"
    assert values[2] == "15.09.2026"
    assert values[4] == "8,00"
