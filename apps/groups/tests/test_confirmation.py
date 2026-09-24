"""Mitarbeitende bestätigen ihren Abrechnungszeitraum (Issue 50)."""

from datetime import datetime, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.corrections.models import CorrectionRequest
from apps.groups import closing, confirmation
from apps.groups.models import PeriodConfirmation
from apps.tracking import entry_editing
from apps.tracking.models import TimeEntry


def at(day, hour):
    """Feste Uhrzeit an einem Tag: 'jetzt minus x' liefe nachts über Mitternacht."""
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


@pytest.fixture
def last_period(group):
    """Der Zeitraum vor dem laufenden, also einer, der bestätigt werden darf."""
    return group.current_period().previous()


@pytest.fixture
def own_entries(member, group, activity, last_period):
    """Vier und acht Stunden an zwei Tagen des abgelaufenen Zeitraums."""
    first_day = last_period.start
    second_day = last_period.start + timedelta(days=1)
    first = TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=at(first_day, 8), end=at(first_day, 12)
    )
    second = TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=at(second_day, 8), end=at(second_day, 16)
    )
    return first, second


def test_confirmation_stores_the_confirmed_state(member, group, last_period, own_entries):
    result = confirmation.confirm(member, group, last_period)

    assert result.entry_count == 2
    assert result.total == timedelta(hours=12)
    assert result.period_end == last_period.end
    assert confirmation.state_for(member, group, last_period).is_confirmed


def test_confirmation_is_in_the_audit_log(member, group, last_period, own_entries):
    confirmation.confirm(member, group, last_period)

    row = AuditLog.objects.get(action=AuditLog.Action.PERIOD_CONFIRMED)
    assert row.actor_id == member.pk
    assert row.subject_id == member.pk
    assert row.group_id == group.pk
    assert last_period.label in row.note


def test_the_audit_note_keeps_the_dates_of_an_own_cycle(member, group, activity):
    """Bei eigenem Zyklus steht im Namen des Zeitraums ein Datum mit Punkten."""
    group.month_start_day = 15
    group.save(update_fields=["month_start_day"])
    period = group.current_period().previous()
    TimeEntry.objects.create(
        user=member,
        group=group,
        activity=activity,
        start=at(period.start, 8),
        end=at(period.start, 12),
    )

    confirmation.confirm(member, group, period)

    note = AuditLog.objects.get(action=AuditLog.Action.PERIOD_CONFIRMED).note
    assert period.label in note
    assert "4,00 Stunden" in note


def test_a_change_afterwards_voids_the_confirmation(
    member, group_admin, group, last_period, own_entries, activity
):
    confirmation.confirm(member, group, last_period)
    first, _ = own_entries

    entry_editing.update_entry(
        first,
        editor=group_admin,
        activity=activity,
        reason="Beginn war früher.",
        start=at(last_period.start, 7),
        end=at(last_period.start, 12),
    )

    state = confirmation.state_for(member, group, last_period)
    assert state.is_stale
    assert not state.is_confirmed


def test_an_added_entry_voids_the_confirmation(
    member, group_admin, group, activity, last_period, own_entries
):
    confirmation.confirm(member, group, last_period)

    entry_editing.create_entry(
        editor=group_admin,
        user=member,
        group=group,
        activity=activity,
        reason="Nachtrag.",
        start=at(last_period.start + timedelta(days=2), 8),
        end=at(last_period.start + timedelta(days=2), 10),
    )

    assert confirmation.state_for(member, group, last_period).is_stale


def test_a_deleted_entry_voids_the_confirmation(member, group, last_period, own_entries):
    confirmation.confirm(member, group, last_period)
    first, _ = own_entries

    first.delete()

    assert confirmation.state_for(member, group, last_period).is_stale


def test_a_change_in_another_period_leaves_the_confirmation_alone(
    member, group_admin, group, activity, last_period, own_entries
):
    confirmation.confirm(member, group, last_period)
    before = last_period.previous()

    entry_editing.create_entry(
        editor=group_admin,
        user=member,
        group=group,
        activity=activity,
        reason="Nachtrag im Zeitraum davor.",
        start=at(before.start + timedelta(days=1), 8),
        end=at(before.start + timedelta(days=1), 10),
    )

    assert confirmation.state_for(member, group, last_period).is_confirmed


def test_confirming_again_refreshes_the_confirmation(
    member, group_admin, group, last_period, own_entries, activity
):
    first_time = confirmation.confirm(member, group, last_period)
    first, _ = own_entries
    entry_editing.update_entry(
        first,
        editor=group_admin,
        activity=activity,
        reason="Ende war später.",
        start=at(last_period.start, 8),
        end=at(last_period.start, 13),
    )

    second_time = confirmation.confirm(member, group, last_period)

    assert second_time.pk == first_time.pk
    assert second_time.total == timedelta(hours=13)
    assert confirmation.state_for(member, group, last_period).is_confirmed
    assert PeriodConfirmation.objects.count() == 1


def test_only_a_member_confirms(superuser, group, last_period):
    assert not confirmation.may_confirm(superuser, group)

    with pytest.raises(confirmation.ConfirmationError):
        confirmation.confirm(superuser, group, last_period)


def test_the_running_period_cannot_be_confirmed(member, group):
    with pytest.raises(confirmation.ConfirmationError):
        confirmation.confirm(member, group, group.current_period())


def test_a_closed_period_cannot_be_confirmed(member, group, group_admin, last_period):
    closing.close_period(group, last_period, group_admin)

    with pytest.raises(confirmation.ConfirmationError):
        confirmation.confirm(member, group, last_period)


def test_the_page_shows_day_totals_and_the_sum(client, member, group, last_period, own_entries):
    client.force_login(member)

    response = client.get(reverse("tracking:confirm_period"))

    assert response.status_code == 200
    assert response.context["group"] == group
    assert response.context["period"].start == last_period.start
    assert response.context["total"] == timedelta(hours=12)
    assert len(response.context["by_day"]) == 2
    assert "Tagessummen" in response.content.decode()


def test_the_page_counts_only_own_times(
    client, member, make_user, group, activity, last_period, own_entries
):
    from apps.groups.models import GroupMembership

    other = make_user("fremd@example.com", last_name="Fremd")
    GroupMembership.objects.create(user=other, group=group)
    TimeEntry.objects.create(
        user=other,
        group=group,
        activity=activity,
        start=at(last_period.start, 8),
        end=at(last_period.start, 18),
    )
    client.force_login(member)

    response = client.get(reverse("tracking:confirm_period"))

    assert response.context["total"] == timedelta(hours=12)


def test_the_page_warns_about_incomplete_entries_and_open_requests(
    client, member, group, activity, last_period, own_entries
):
    first, _ = own_entries
    first.is_incomplete = True
    first.save(update_fields=["is_incomplete"])
    CorrectionRequest.objects.create(
        time_entry=first,
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.EDIT,
        proposed_start=at(last_period.start, 7),
        proposed_end=at(last_period.start, 12),
        reason="Beginn war früher.",
    )
    client.force_login(member)

    response = client.get(reverse("tracking:confirm_period"))

    assert response.context["incomplete_count"] == 1
    assert response.context["open_requests"] == 1
    body = response.content.decode()
    assert "Unvollständige Zeiteinträge" in body
    assert "Offene Korrekturanträge" in body


def test_confirming_through_the_web(client, member, group, last_period, own_entries):
    client.force_login(member)

    response = client.post(
        reverse("tracking:confirm_period"),
        {"gruppe": group.pk, "zeitraum": last_period.key},
        follow=True,
    )

    assert response.status_code == 200
    assert "ist bestätigt" in response.content.decode()
    stored = PeriodConfirmation.objects.get(user=member, group=group)
    assert stored.period_start == last_period.start


def test_the_page_shows_a_voided_confirmation(
    client, member, group_admin, group, last_period, own_entries, activity
):
    confirmation.confirm(member, group, last_period)
    first, _ = own_entries
    entry_editing.update_entry(
        first,
        editor=group_admin,
        activity=activity,
        reason="Beginn war früher.",
        start=at(last_period.start, 7),
        end=at(last_period.start, 12),
    )
    client.force_login(member)

    response = client.get(reverse("tracking:confirm_period"))

    assert response.context["state"].is_stale
    assert "Bestätigung verfallen" in response.content.decode()


def test_the_page_shows_a_standing_confirmation(client, member, group, last_period, own_entries):
    confirmation.confirm(member, group, last_period)
    client.force_login(member)

    response = client.get(reverse("tracking:confirm_period"))

    body = response.content.decode()
    assert "Erneut bestätigen" in body
    assert "12:00" in body


def test_a_foreign_group_cannot_be_confirmed(client, member, other_group, last_period, own_entries):
    client.force_login(member)

    client.post(
        reverse("tracking:confirm_period"),
        {"gruppe": other_group.pk, "zeitraum": last_period.key},
        follow=True,
    )

    assert not PeriodConfirmation.objects.filter(group=other_group).exists()


def test_without_a_group_the_page_stays_friendly(client, make_user):
    lonely = make_user("allein@example.com", last_name="Allein")
    client.force_login(lonely)

    response = client.get(reverse("tracking:confirm_period"))

    assert response.status_code == 200
    assert "keiner Gruppe" in response.content.decode()


def test_the_closing_page_names_who_has_not_confirmed(
    client, member, group_admin, group, last_period, own_entries
):
    client.force_login(group_admin)

    response = client.get(reverse("groups:periods", args=[group.pk]))

    body = response.content.decode()
    assert "2 von 2 offen" in body
    assert member.full_name in body


def test_the_closing_page_counts_a_confirmation(
    client, member, group_admin, group, last_period, own_entries
):
    confirmation.confirm(member, group, last_period)
    client.force_login(group_admin)

    response = client.get(reverse("groups:periods", args=[group.pk]))

    body = response.content.decode()
    assert "1 von 2 offen" in body
    assert "Alex Admin" in body


def test_a_missing_confirmation_does_not_block_the_closing(
    client, member, group_admin, group, last_period, own_entries
):
    client.force_login(group_admin)

    client.post(
        reverse("groups:periods", args=[group.pk]),
        {"action": "close", "period_start": last_period.start.isoformat(), "note": ""},
        follow=True,
    )

    assert closing.is_closed(group, last_period.start)


def test_missing_confirmations_stays_at_a_few_queries(
    member, group_admin, group, last_period, own_entries, django_assert_max_num_queries
):
    confirmation.confirm(member, group, last_period)
    periods = confirmation.choosable_periods(group)

    with django_assert_max_num_queries(4):
        rows = confirmation.missing_confirmations(group, periods)

    assert rows[last_period.start].count == 1
    assert rows[last_period.start].members == 2


def test_the_group_wide_state_matches_the_single_one(
    member, group_admin, group, last_period, own_entries
):
    """Die Sammelabfrage der Abschluss-Seite zählt wie die Abfrage der eigenen Seite."""
    confirmation.confirm(member, group, last_period)
    periods = confirmation.choosable_periods(group)

    rows = confirmation.missing_confirmations(group, periods)

    for period in periods:
        state = confirmation.state_for(member, group, period)
        assert (member in rows[period.start].missing) is state.is_missing


def test_choosable_periods_skip_the_running_one(group):
    periods = confirmation.choosable_periods(group)

    today = timezone.localdate()
    assert len(periods) == confirmation.CHOOSABLE_PERIODS
    assert all(period.end < today for period in periods)
    assert periods[0].start == group.current_period().previous().start
