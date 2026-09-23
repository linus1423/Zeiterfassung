"""Arbeitszeitnachweis je Person und Zeitraum (Issue 35)."""

from datetime import date, datetime, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.groups import closing
from apps.groups.models import Activity, GroupMembership
from apps.reporting import timesheet
from apps.tracking.models import BreakEntry, TimeEntry

FIRST = date(2026, 6, 1)
LAST = date(2026, 6, 30)


def _at(day: date, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _entry(user, group, day, start_hour, hours, *, pause=None, incomplete=False):
    activity, _ = Activity.objects.get_or_create(group=group, name="Montage")
    entry = TimeEntry.objects.create(
        user=user,
        group=group,
        activity=activity,
        start=_at(day, start_hour),
        end=_at(day, start_hour) + timedelta(hours=hours),
        is_incomplete=incomplete,
    )
    if pause is not None:
        begin, minutes = pause
        BreakEntry.objects.create(
            time_entry=entry,
            start=_at(day, begin),
            end=_at(day, begin) + timedelta(minutes=minutes),
        )
    return entry


def test_every_day_of_the_period_has_a_row(member, group):
    sheet = timesheet.build(member, FIRST, LAST)

    assert len(sheet.rows) == 30
    assert sheet.rows[0].day == FIRST
    assert sheet.rows[-1].day == LAST
    assert all(row.is_empty for row in sheet.rows)
    assert sheet.total == timedelta()


def test_a_day_shows_start_end_pause_and_work(member, group):
    _entry(member, group, date(2026, 6, 2), 8, 8, pause=(12, 30))

    row = timesheet.build(member, FIRST, LAST).rows[1]

    assert not row.is_empty
    assert timezone.localtime(row.start).hour == 8
    assert timezone.localtime(row.end).hour == 16
    assert row.pause == timedelta(minutes=30)
    assert row.work == timedelta(hours=7, minutes=30)
    assert row.weekday == "Dienstag"


def test_the_gap_between_two_entries_counts_as_a_break(member, group):
    day = date(2026, 6, 3)
    _entry(member, group, day, 8, 4)
    _entry(member, group, day, 13, 4)

    row = timesheet.build(member, FIRST, LAST).rows[2]

    # Die Zeile geht auf: Ende minus Beginn minus Pause ergibt die Arbeitszeit.
    assert row.work == timedelta(hours=8)
    assert row.pause == timedelta(hours=1)
    assert (row.end - row.start) - row.pause == row.work


def test_a_night_shift_lands_on_both_days(member, group, activity):
    TimeEntry.objects.create(
        user=member,
        group=group,
        activity=activity,
        start=_at(date(2026, 6, 4), 22),
        end=_at(date(2026, 6, 5), 6),
    )

    rows = timesheet.build(member, FIRST, LAST).rows

    assert rows[3].work == timedelta(hours=2)
    assert rows[4].work == timedelta(hours=6)


def test_the_total_counts_only_the_period(member, group):
    _entry(member, group, date(2026, 5, 31), 8, 8)
    _entry(member, group, date(2026, 6, 1), 8, 8)

    sheet = timesheet.build(member, FIRST, LAST)

    assert sheet.total == timedelta(hours=8)
    assert sheet.days_worked == 1


def test_a_running_entry_is_named_but_not_counted(member, group, activity):
    today = timezone.localdate()
    TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=timezone.now() - timedelta(hours=2)
    )

    sheet = timesheet.build(member, today, today)

    assert sheet.open_entries == 1
    assert sheet.total == timedelta()


def test_an_automatically_closed_day_is_marked(member, group):
    _entry(member, group, date(2026, 6, 2), 8, 16, incomplete=True)

    sheet = timesheet.build(member, FIRST, LAST)

    assert sheet.has_incomplete
    assert sheet.rows[1].is_incomplete


def test_the_sheet_says_whether_the_period_is_closed(member, group, group_admin):
    _entry(member, group, date(2026, 6, 2), 8, 8)
    assert not timesheet.build(member, FIRST, LAST).closed

    closing.close_period(group, group.current_period(FIRST), group_admin)

    assert timesheet.build(member, FIRST, LAST).closed


def test_a_partly_closed_period_does_not_count_as_closed(member, group, group_admin):
    _entry(member, group, date(2026, 6, 2), 8, 8)
    closing.close_period(group, group.current_period(FIRST), group_admin)

    # Ein Blatt über zwei Zeiträume, von denen nur der erste abgeschlossen ist.
    assert not timesheet.build(member, FIRST, date(2026, 7, 15)).closed


def test_the_group_sheet_counts_only_that_group(member, group, other_group, group_admin):
    GroupMembership.objects.create(user=member, group=other_group)
    _entry(member, group, date(2026, 6, 2), 8, 8)
    _entry(member, other_group, date(2026, 6, 3), 8, 4)

    sheets = timesheet.build_for_group(group, FIRST, LAST)

    assert {sheet.user.pk for sheet in sheets} == {member.pk, group_admin.pk}
    member_sheet = next(sheet for sheet in sheets if sheet.user.pk == member.pk)
    assert member_sheet.total == timedelta(hours=8)
    assert [g.pk for g in member_sheet.groups] == [group.pk]


@pytest.mark.parametrize(
    "viewer_name,allowed",
    [("member", True), ("group_admin", True), ("accountant", True), ("outsider", False)],
)
def test_who_may_see_a_sheet(request, member, viewer_name, allowed, make_user):
    viewer = (
        make_user("fremd@example.com")
        if viewer_name == "outsider"
        else request.getfixturevalue(viewer_name)
    )

    assert timesheet.may_see(viewer, member) is allowed


def test_the_page_renders_for_the_person(client, member, group):
    _entry(member, group, date(2026, 6, 2), 8, 8)
    client.force_login(member)

    response = client.get(
        reverse("reporting:timesheet", args=[member.pk]),
        {"start": "2026-06-01", "end": "2026-06-30"},
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Arbeitszeitnachweis" in content
    assert "Unterschrift" in content
    assert "02.06.2026" in content


def test_a_stranger_gets_no_sheet(client, member, group, make_user):
    outsider = make_user("fremd@example.com")
    client.force_login(outsider)

    response = client.get(reverse("reporting:timesheet", args=[member.pk]))

    assert response.status_code == 403


def test_the_group_page_shows_a_sheet_per_member(client, group_admin, member, group):
    client.force_login(group_admin)

    response = client.get(reverse("reporting:group_timesheets", args=[group.pk]))

    content = response.content.decode()
    assert response.status_code == 200
    assert content.count("Arbeitszeitnachweis</h2>") == 2


def test_a_plain_member_may_not_read_the_group_sheets(client, member, group):
    client.force_login(member)

    assert client.get(reverse("reporting:group_timesheets", args=[group.pk])).status_code == 403


def test_an_overlong_period_falls_back_to_the_current_one(client, member, group):
    client.force_login(member)

    response = client.get(
        reverse("reporting:timesheet", args=[member.pk]),
        {"start": "2020-01-01", "end": "2026-12-31"},
    )

    assert response.status_code == 200
    assert "höchstens ein Jahr" in response.content.decode()


def test_a_group_admin_sees_only_the_times_of_his_own_group(
    client, group_admin, member, group, other_group
):
    GroupMembership.objects.create(user=member, group=other_group)
    _entry(member, group, date(2026, 6, 2), 8, 8)
    _entry(member, other_group, date(2026, 6, 3), 8, 4)
    client.force_login(group_admin)

    response = client.get(
        reverse("reporting:timesheet", args=[member.pk]),
        {"start": "2026-06-01", "end": "2026-06-30"},
    )

    sheet = response.context["sheets"][0]
    assert sheet.total == timedelta(hours=8)
    assert [one.pk for one in sheet.groups] == [group.pk]


def test_the_person_sees_all_her_groups(client, member, group, other_group):
    GroupMembership.objects.create(user=member, group=other_group)
    _entry(member, group, date(2026, 6, 2), 8, 8)
    _entry(member, other_group, date(2026, 6, 3), 8, 4)
    client.force_login(member)

    response = client.get(
        reverse("reporting:timesheet", args=[member.pk]),
        {"start": "2026-06-01", "end": "2026-06-30"},
    )

    assert response.context["sheets"][0].total == timedelta(hours=12)
