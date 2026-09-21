"""Abschluss-Übersicht über alle lesbaren Gruppen (Issue 36)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.corrections.models import CorrectionRequest
from apps.groups import closing, overview
from apps.groups.models import GroupMembership
from apps.tracking.models import TimeEntry
from apps.tracking.utils import day_bounds


def _entry(user, group, day, *, hours=8, incomplete=False):
    start, _ = day_bounds(day)
    return TimeEntry.objects.create(
        user=user,
        group=group,
        start=start + timedelta(hours=8),
        end=start + timedelta(hours=8 + hours),
        is_incomplete=incomplete,
    )


@pytest.fixture
def last_period(group):
    return group.current_period().previous()


def _row(rows, group):
    return next(row for row in rows if row.group.pk == group.pk)


def test_a_group_without_times_has_nothing_to_close(group):
    row = _row(overview.closing_overview([group]), group)

    assert row.pending is None
    assert row.last_lock is None
    assert row.is_up_to_date


def test_an_expired_period_without_a_close_shows_up(group, member, last_period):
    _entry(member, group, last_period.start)

    row = _row(overview.closing_overview([group]), group)

    assert row.pending is not None
    assert row.pending.start == last_period.start
    assert not row.is_up_to_date


def test_the_running_period_is_not_expired(group, member):
    _entry(member, group, timezone.localdate())

    assert _row(overview.closing_overview([group]), group).pending is None


def test_a_close_moves_the_row_forward(group, member, group_admin, last_period):
    _entry(member, group, last_period.start)
    closing.close_period(group, last_period, group_admin)

    row = _row(overview.closing_overview([group]), group)

    assert row.pending is None
    assert row.last_lock.period_start == last_period.start


def test_the_oldest_open_period_wins(group, member, group_admin, last_period):
    older = last_period.previous()
    _entry(member, group, older.start)
    _entry(member, group, last_period.start)
    # Nur der neuere ist abgeschlossen, der ältere bleibt offen.
    closing.close_period(group, last_period, group_admin)

    assert _row(overview.closing_overview([group]), group).pending.start == older.start


def test_the_row_counts_the_work_of_that_period(group, member, last_period):
    _entry(member, group, last_period.start, incomplete=True)
    entry = _entry(member, group, last_period.start + timedelta(days=1))
    CorrectionRequest.objects.create(
        time_entry=entry,
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.EDIT,
        proposed_start=entry.start,
        proposed_end=entry.end,
        reason="Pause nachtragen",
    )

    row = _row(overview.closing_overview([group]), group)

    assert (row.open_requests, row.incomplete_entries) == (1, 1)
    assert row.has_open_work


def test_work_outside_that_period_is_not_counted(group, member, last_period):
    _entry(member, group, last_period.previous().start, incomplete=True)
    _entry(member, group, last_period.start)

    row = _row(overview.closing_overview([group]), group)

    # Offen ist der ältere Zeitraum, dort liegt der unvollständige Eintrag.
    assert row.pending.start == last_period.previous().start
    assert row.incomplete_entries == 1


def test_every_group_keeps_its_own_cycle(group, other_group, member, make_user):
    other_member = make_user("lager@example.com")
    GroupMembership.objects.create(user=other_member, group=other_group)
    other_group.month_start_day = 15
    other_group.save(update_fields=["month_start_day"])
    day = timezone.localdate() - timedelta(days=45)
    _entry(member, group, day)
    _entry(other_member, other_group, day)

    rows = overview.closing_overview([group, other_group])

    assert _row(rows, group).pending.start.day == 1
    assert _row(rows, other_group).pending.start.day == 15


def test_the_page_shows_the_groups_of_an_admin(client, group_admin, group, member, last_period):
    _entry(member, group, last_period.start)
    client.force_login(group_admin)

    response = client.get(reverse("groups:closing_overview"))
    content = response.content.decode()

    assert response.status_code == 200
    assert group.name in content
    assert last_period.label in content


def test_accounting_sees_every_group(client, accountant, group, other_group):
    client.force_login(accountant)

    response = client.get(reverse("groups:closing_overview"))
    content = response.content.decode()

    assert group.name in content
    assert other_group.name in content


def test_an_admin_sees_only_his_own_group(client, group, other_group, make_user):
    admin = make_user("werkstattadmin@example.com")
    GroupMembership.objects.create(user=admin, group=group, role=GroupMembership.Role.ADMIN)
    client.force_login(admin)

    content = client.get(reverse("groups:closing_overview")).content.decode()

    assert group.name in content
    assert other_group.name not in content


def test_a_plain_member_has_no_access(client, member):
    client.force_login(member)

    assert client.get(reverse("groups:closing_overview")).status_code == 403


def test_the_numbers_come_in_a_handful_of_queries(
    django_assert_max_num_queries, group, other_group, member, make_user, last_period
):
    second = make_user("lager@example.com")
    GroupMembership.objects.create(user=second, group=other_group)
    _entry(member, group, last_period.start)
    _entry(second, other_group, last_period.start)

    # Nicht je Gruppe eine eigene Abfrage: die Buchhaltung liest alle auf einmal.
    with django_assert_max_num_queries(6):
        overview.closing_overview([group, other_group])
