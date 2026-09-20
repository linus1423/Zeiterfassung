from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.groups import closing
from apps.groups.models import PeriodLock
from apps.groups.periods import period_for


@pytest.fixture
def last_period(group):
    """Der Zeitraum vor dem laufenden, also einer, der abgeschlossen werden darf."""
    return group.current_period().previous()


def test_closing_blocks_the_whole_period(group, group_admin, last_period):
    closing.close_period(group, last_period, group_admin, "Export ist raus.")

    assert closing.is_closed(group, last_period.start)
    assert closing.is_closed(group, last_period.end)
    assert not closing.is_closed(group, last_period.end + timedelta(days=1))
    assert AuditLog.objects.filter(action=AuditLog.Action.PERIOD_CLOSED).exists()


def test_closing_is_per_group(group, other_group, group_admin, last_period):
    closing.close_period(group, last_period, group_admin)

    assert closing.is_closed(group, last_period.start)
    assert not closing.is_closed(other_group, last_period.start)


def test_the_running_period_stays_open(group, group_admin):
    with pytest.raises(closing.ClosingError):
        closing.close_period(group, group.current_period(), group_admin)


def test_a_member_cannot_close(group, member, last_period):
    with pytest.raises(closing.ClosingError):
        closing.close_period(group, last_period, member)


def test_closing_twice_is_refused(group, group_admin, last_period):
    closing.close_period(group, last_period, group_admin)

    with pytest.raises(closing.ClosingError):
        closing.close_period(group, last_period, group_admin)


def test_only_a_system_admin_reopens(group, group_admin, superuser, last_period):
    lock = closing.close_period(group, last_period, group_admin)

    with pytest.raises(closing.ClosingError):
        closing.reopen_period(lock, group_admin)

    closing.reopen_period(lock, superuser)
    assert not PeriodLock.objects.exists()
    assert AuditLog.objects.filter(action=AuditLog.Action.PERIOD_REOPENED).exists()


def test_closed_periods_answers_without_further_queries(
    group, other_group, group_admin, last_period, django_assert_num_queries
):
    closing.close_period(group, last_period, group_admin)
    index = closing.ClosedPeriods([group.pk, other_group.pk])

    with django_assert_num_queries(0):
        assert index.is_closed(group.pk, last_period.start)
        assert not index.is_closed(other_group.pk, last_period.start)
        assert not index.is_closed(group.pk, None)


def test_overview_marks_the_running_period(group):
    rows = closing.period_overview(group, count=3, today=date(2026, 9, 18))

    assert rows[0]["is_current"] is True
    assert rows[0]["lock"] is None
    assert len(rows) == 3


def test_admin_closes_through_the_web(client, group, group_admin, last_period):
    client.force_login(group_admin)

    response = client.post(
        reverse("groups:periods", args=[group.pk]),
        {"action": "close", "period_start": last_period.start.isoformat(), "note": ""},
        follow=True,
    )

    assert response.status_code == 200
    assert PeriodLock.objects.filter(group=group, period_start=last_period.start).exists()


def test_member_may_not_see_the_period_page(client, member, group):
    client.force_login(member)

    response = client.get(reverse("groups:periods", args=[group.pk]))

    assert response.status_code == 403


def test_settings_page_stores_the_cycle(client, group, group_admin):
    client.force_login(group_admin)

    response = client.post(
        reverse("groups:settings", args=[group.pk]),
        {"month_start_day": 15, "cost_center": "4711", "idp_identifier": ""},
        follow=True,
    )

    group.refresh_from_db()
    assert response.status_code == 200
    assert group.month_start_day == 15
    assert group.current_period(date(2026, 9, 18)).start == date(2026, 9, 15)


def test_cycle_outside_the_range_is_refused(client, group, group_admin):
    client.force_login(group_admin)

    client.post(
        reverse("groups:settings", args=[group.pk]),
        {"month_start_day": 30, "cost_center": "", "idp_identifier": ""},
    )

    group.refresh_from_db()
    assert group.month_start_day == 1


def test_lock_knows_its_period(group, group_admin, last_period):
    lock = closing.close_period(group, last_period, group_admin)

    assert lock.period.start == last_period.start
    assert lock.contains(last_period.start)
    assert period_for(last_period.start, group.month_start_day).label == lock.period.label


def test_member_cannot_close_a_period_of_a_foreign_group(group, other_group, group_admin):
    period = other_group.current_period(timezone.localdate()).previous()

    with pytest.raises(closing.ClosingError):
        closing.close_period(other_group, period, group_admin)


def test_a_cycle_change_does_not_allow_a_second_lock(group, group_admin, last_period):
    closing.close_period(group, last_period, group_admin)

    # Nach der Umstellung des Zyklus liegen die Zeiträume anders, der alte
    # Abschluss sperrt seine Tage aber weiter.
    group.month_start_day = 15
    group.save(update_fields=["month_start_day"])
    overlapping = group.current_period().previous()

    with pytest.raises(closing.ClosingError):
        closing.close_period(group, overlapping, group_admin)
    assert closing.is_closed(group, last_period.start)


def test_the_overview_shows_an_overlapping_lock(group, group_admin, last_period):
    closing.close_period(group, last_period, group_admin)
    group.month_start_day = 15
    group.save(update_fields=["month_start_day"])

    rows = closing.period_overview(group, count=4)
    locked = [row for row in rows if row["lock"] is not None]

    assert locked
    assert locked[0]["lock"].period_start == last_period.start


def test_a_broken_lock_id_is_refused(client, group, superuser):
    client.force_login(superuser)

    response = client.post(
        reverse("groups:periods", args=[group.pk]),
        {"action": "reopen", "lock": "keine-zahl"},
        follow=True,
    )

    assert response.status_code == 200
    assert "nicht erkannt" in response.content.decode()
