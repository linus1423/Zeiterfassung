"""Eigene Zeiten: Filter, Wochensummen und Download (Issue 28)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.groups.models import Activity, Group, GroupMembership
from apps.tracking.models import TimeEntry
from apps.tracking.views import _billing_start_day, _quick_range


@pytest.fixture
def entries(member, group, activity):
    """Zwei Einträge in dieser Woche, einer davon auf einer zweiten Tätigkeit."""
    other = Activity.objects.create(group=group, name="Lackieren")
    now = timezone.now()
    first = TimeEntry.objects.create(
        user=member,
        group=group,
        activity=activity,
        start=now - timedelta(days=1, hours=8),
        end=now - timedelta(days=1, hours=4),
    )
    second = TimeEntry.objects.create(
        user=member,
        group=group,
        activity=other,
        start=now - timedelta(hours=3),
        end=now - timedelta(hours=1),
    )
    return first, second


def test_list_shows_day_and_week_totals(client, member, entries):
    client.force_login(member)

    response = client.get(reverse("tracking:my_entries"), {"bereich": "woche"})

    assert response.status_code == 200
    body = response.content.decode()
    assert "Wochensummen" in body
    assert "Tagessummen" in body
    assert len(response.context["by_week"]) >= 1
    assert response.context["total"] == timedelta(hours=6)


def test_filter_by_activity(client, member, entries):
    first, second = entries
    client.force_login(member)

    response = client.get(
        reverse("tracking:my_entries"),
        {"bereich": "woche", "activity": second.activity_id},
    )

    assert list(response.context["entries"]) == [second]
    assert response.context["total"] == timedelta(hours=2)


def test_quick_range_previous_week_ends_before_today(client, member, entries):
    client.force_login(member)

    response = client.get(reverse("tracking:my_entries"), {"bereich": "vorwoche"})

    today = timezone.localdate()
    assert response.context["end_day"] < today
    assert response.context["start_day"] == response.context["end_day"] - timedelta(days=6)


def test_csv_download(client, member, entries):
    client.force_login(member)

    response = client.get(reverse("tracking:my_entries"), {"bereich": "woche", "export": "csv"})

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert "meine-zeiten" in response["Content-Disposition"]
    body = b"".join(response.streaming_content).decode("utf-8-sig")
    assert "Tätigkeit" in body
    assert "Montage" in body


def test_xlsx_download(client, member, entries):
    client.force_login(member)

    response = client.get(reverse("tracking:my_entries"), {"bereich": "woche", "export": "xlsx"})

    assert response.status_code == 200
    assert response["Content-Disposition"].endswith('.xlsx"')
    assert response.content[:2] == b"PK"


def test_download_contains_only_own_times(client, member, make_user, group, activity, entries):
    other_person = make_user("fremd@example.com", last_name="Fremd")
    GroupMembership.objects.create(user=other_person, group=group)
    now = timezone.now()
    TimeEntry.objects.create(
        user=other_person,
        group=group,
        activity=activity,
        start=now - timedelta(hours=5),
        end=now - timedelta(hours=4),
    )
    client.force_login(member)

    response = client.get(reverse("tracking:my_entries"), {"bereich": "woche", "export": "csv"})

    body = b"".join(response.streaming_content).decode("utf-8-sig")
    assert "Fremd" not in body


def test_default_period_follows_the_billing_cycle(member, group):
    group.month_start_day = 15
    group.save(update_fields=["month_start_day"])

    assert _billing_start_day(member) == 15


def test_default_period_is_the_calendar_month_with_mixed_cycles(member, make_user, group):
    second = Group.objects.create(name="Lager", month_start_day=20)
    GroupMembership.objects.create(user=member, group=second)
    group.month_start_day = 15
    group.save(update_fields=["month_start_day"])

    assert _billing_start_day(member) == 1


def test_quick_range_month_uses_the_cycle():
    from datetime import date

    start, end = _quick_range("monat", 15, date(2026, 9, 20))

    assert start == date(2026, 9, 15)
    assert end == date(2026, 9, 20)
