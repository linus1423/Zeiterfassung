"""Verdichtung und Filter nach Kostenstelle (Issue 57)."""

from datetime import datetime, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.groups.models import Activity, Group, GroupMembership
from apps.reporting import services
from apps.reporting.services import NO_COST_CENTER, NO_COST_CENTER_LABEL
from apps.tracking.models import TimeEntry

# Fester Tag mit fester Uhrzeit: "jetzt minus zwei Tage" liefe je nach
# Uhrzeit des Testlaufs über Mitternacht und zählte dann an zwei Tagen.
DAY = timezone.localdate() - timedelta(days=2)


def _entry(user, group, activity, hours: int, hour: int = 8):
    start = timezone.make_aware(datetime.combine(DAY, time(hour, 0)))
    return TimeEntry.objects.create(
        user=user, group=group, activity=activity, start=start, end=start + timedelta(hours=hours)
    )


@pytest.fixture
def three_groups(db, make_user, group, other_group):
    """Zwei Gruppen auf derselben Kostenstelle und eine ganz ohne.

    "Werkstatt" trägt 4711 (aus conftest), "Lager" bekommt dieselbe Nummer,
    "Buero" bleibt ohne.
    """
    lager = Group.objects.create(name="Lager", cost_center="4711")
    people = {}
    for name, target in (("werkstatt", group), ("lager", lager), ("buero", other_group)):
        person = make_user(f"{name}@example.com", last_name=name.capitalize())
        GroupMembership.objects.create(user=person, group=target)
        activity = Activity.objects.create(group=target, name=f"Arbeit {name}")
        people[name] = (person, target, activity)

    _entry(*people["werkstatt"], hours=4)
    _entry(*people["lager"], hours=3)
    _entry(*people["buero"], hours=2)
    return people


def _rows(accountant, grouping: str):
    entries = services.query_entries(accountant, start=DAY, end=DAY)
    return services.build_rows(entries, grouping, first_day=DAY, last_day=DAY)


def test_cost_center_grouping_adds_up_across_groups(three_groups, accountant):
    rows = {row["cost_center"]: row for row in _rows(accountant, "cost_center")}

    assert set(rows) == {"4711", NO_COST_CENTER_LABEL}
    assert rows["4711"]["work_seconds"] == pytest.approx(7 * 3600)
    assert rows["4711"]["entry_count"] == 2


def test_groups_without_cost_center_get_their_own_row(three_groups, accountant):
    rows = {row["cost_center"]: row for row in _rows(accountant, "cost_center")}

    assert NO_COST_CENTER_LABEL in rows
    assert rows[NO_COST_CENTER_LABEL]["work_seconds"] == pytest.approx(2 * 3600)


def test_cost_center_grouping_keeps_the_full_total(three_groups, accountant):
    per_entry = sum(row["work_seconds"] for row in _rows(accountant, "entry"))
    per_cost_center = sum(row["work_seconds"] for row in _rows(accountant, "cost_center"))

    assert per_cost_center == pytest.approx(per_entry)


def test_cost_center_month_grouping_names_the_period(three_groups, accountant):
    rows = _rows(accountant, "cost_center_month")

    assert {row["cost_center"] for row in rows} == {"4711", NO_COST_CENTER_LABEL}
    assert all(row["period_label"] for row in rows)
    assert all(row["period_start"] <= DAY <= row["period_end"] for row in rows)


def test_filter_by_cost_center(three_groups, accountant):
    found = services.query_entries(accountant, start=DAY, end=DAY, cost_centers=["4711"])

    assert found.count() == 2
    assert all(entry.group.cost_center == "4711" for entry in found)


def test_filter_for_groups_without_cost_center(three_groups, accountant):
    found = services.query_entries(accountant, start=DAY, end=DAY, cost_centers=[NO_COST_CENTER])

    assert [entry.group.name for entry in found] == ["Buero"]


def test_filter_combines_named_and_empty_cost_centers(three_groups, accountant):
    found = services.query_entries(
        accountant, start=DAY, end=DAY, cost_centers=["4711", NO_COST_CENTER]
    )

    assert found.count() == 3


def test_cost_center_choices_offer_the_collecting_row(three_groups, accountant):
    from apps.groups.permissions import readable_groups

    choices = services.cost_center_choices(readable_groups(accountant))

    assert ("4711", "4711") in choices
    assert (NO_COST_CENTER, NO_COST_CENTER_LABEL) in choices


def test_choices_without_the_collecting_row_when_every_group_has_one(db, group, accountant):
    from apps.groups.permissions import readable_groups

    choices = services.cost_center_choices(readable_groups(accountant))

    assert choices == [("4711", "4711")]


def test_group_admin_only_sees_the_cost_centers_of_own_groups(three_groups, group_admin):
    from apps.groups.permissions import readable_groups

    choices = services.cost_center_choices(readable_groups(group_admin))
    entries = services.query_entries(group_admin, start=DAY, end=DAY)
    rows = services.build_rows(entries, "cost_center", first_day=DAY, last_day=DAY)

    assert choices == [("4711", "4711")]
    assert [row["cost_center"] for row in rows] == ["4711"]
    assert rows[0]["work_seconds"] == pytest.approx(4 * 3600)


def test_export_with_cost_center_grouping(client, three_groups, accountant):
    client.force_login(accountant)

    response = client.post(
        reverse("reporting:export"),
        {
            "start": DAY.isoformat(),
            "end": DAY.isoformat(),
            "grouping": "cost_center",
            "columns": ["cost_center", "hhmm"],
            "csv_dialect": "de",
            "action": "csv",
        },
    )

    body = b"".join(response.streaming_content).decode("utf-8-sig")
    assert "Kostenstelle" in body
    assert "4711;7:00" in body
    assert f"{NO_COST_CENTER_LABEL};2:00" in body


def test_export_filtered_by_cost_center(client, three_groups, accountant):
    client.force_login(accountant)

    response = client.post(
        reverse("reporting:export"),
        {
            "start": DAY.isoformat(),
            "end": DAY.isoformat(),
            "cost_centers": [NO_COST_CENTER],
            "grouping": "cost_center",
            "columns": ["cost_center", "hhmm"],
            "csv_dialect": "de",
            "action": "csv",
        },
    )

    body = b"".join(response.streaming_content).decode("utf-8-sig")
    assert NO_COST_CENTER_LABEL in body
    assert "4711" not in body


def test_unknown_grouping_is_rejected():
    with pytest.raises(ValueError, match="Unbekannte Verdichtung"):
        services.aggregate([], "gibt-es-nicht")
