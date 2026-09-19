from datetime import date, timedelta

import pytest

from apps.groups.periods import (
    normalize_start_day,
    period_for,
    recent_periods,
    shift_months,
)


def test_calendar_month_is_the_default():
    period = period_for(date(2026, 9, 18))

    assert period.start == date(2026, 9, 1)
    assert period.end == date(2026, 9, 30)
    assert period.label == "September 2026"


@pytest.mark.parametrize(
    ("day", "expected_start", "expected_end"),
    [
        (date(2026, 9, 18), date(2026, 9, 15), date(2026, 10, 14)),
        (date(2026, 9, 15), date(2026, 9, 15), date(2026, 10, 14)),
        (date(2026, 9, 14), date(2026, 8, 15), date(2026, 9, 14)),
        (date(2026, 1, 3), date(2025, 12, 15), date(2026, 1, 14)),
    ],
)
def test_own_cycle_starts_on_the_configured_day(day, expected_start, expected_end):
    period = period_for(day, 15)

    assert (period.start, period.end) == (expected_start, expected_end)
    assert period.contains(day)


def test_cycle_label_names_both_ends():
    period = period_for(date(2026, 9, 18), 15)

    assert period.label == "15.09.2026 bis 14.10.2026"
    assert period.month_label == "September"


def test_february_cycle_ends_before_the_next_start():
    period = period_for(date(2026, 2, 3), 28)

    assert period.start == date(2026, 1, 28)
    assert period.end == date(2026, 2, 27)


def test_neighbours_join_without_a_gap():
    period = period_for(date(2026, 9, 18), 20)

    assert period.previous().end + timedelta(days=1) == period.start
    assert period.end + timedelta(days=1) == period.next().start


@pytest.mark.parametrize(
    ("value", "expected"), [(0, 1), (1, 1), (28, 28), (31, 28), (None, 1), ("nein", 1), ("15", 15)]
)
def test_start_day_is_kept_in_range(value, expected):
    assert normalize_start_day(value) == expected


def test_shift_months_shortens_to_the_last_day():
    assert shift_months(date(2026, 3, 31), -1) == date(2026, 2, 28)
    assert shift_months(date(2026, 9, 18), -24) == date(2024, 9, 18)


def test_recent_periods_start_with_the_current_one(group):
    group.month_start_day = 15
    periods = recent_periods(group, count=3, today=date(2026, 9, 18))

    assert [period.start for period in periods] == [
        date(2026, 9, 15),
        date(2026, 8, 15),
        date(2026, 7, 15),
    ]
