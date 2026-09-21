"""Auswertungsseite mit Summen und Diagramm (Issue 56)."""

from datetime import datetime, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.groups.models import Activity, GroupMembership
from apps.reporting import charts, services, summary
from apps.reporting.services import NO_COST_CENTER_LABEL
from apps.tracking.models import TimeEntry

# Feste Uhrzeit an einem festen Tag: sonst liefe ein Eintrag je nach Uhrzeit
# des Testlaufs über Mitternacht und zählte an zwei Tagen (Issue 32).
DAY = timezone.localdate() - timedelta(days=2)
URL = reverse("reporting:summary")


def _entry(user, group, activity, hours: int, hour: int = 8):
    start = timezone.make_aware(datetime.combine(DAY, time(hour, 0)))
    return TimeEntry.objects.create(
        user=user, group=group, activity=activity, start=start, end=start + timedelta(hours=hours)
    )


@pytest.fixture
def times(db, make_user, group, activity, member, other_group):
    """Vier Stunden Montage und zwei Stunden Lackieren in der Werkstatt,
    dazu drei Stunden im Buero, das keine Kostenstelle hat."""
    lackieren = Activity.objects.create(group=group, name="Lackieren")
    _entry(member, group, activity, hours=4)
    _entry(member, group, lackieren, hours=2, hour=13)

    outsider = make_user("buero@example.com", first_name="Bea", last_name="Buero")
    GroupMembership.objects.create(user=outsider, group=other_group)
    buero_activity = Activity.objects.create(group=other_group, name="Ablage")
    _entry(outsider, other_group, buero_activity, hours=3)
    return {"member": member, "outsider": outsider}


def _get(client, **params):
    return client.get(URL, {"start": DAY.isoformat(), "end": DAY.isoformat(), **params})


def _section(response, key: str):
    return next(section for section in response.context["summary"].sections if section.key == key)


# --- Rechte -----------------------------------------------------------------


def test_plain_member_has_no_access(client, member):
    client.force_login(member)

    assert client.get(URL).status_code == 403


def test_accountant_opens_the_page(client, accountant):
    client.force_login(accountant)

    assert client.get(URL).status_code == 200


def test_group_admin_only_sees_own_groups(client, times, group_admin):
    client.force_login(group_admin)

    response = _get(client)

    labels = [row.label for row in _section(response, "group").rows]
    assert labels == ["Werkstatt"]
    assert response.context["summary"].seconds == pytest.approx(6 * 3600)


def test_accountant_sees_every_group(client, times, accountant):
    client.force_login(accountant)

    response = _get(client)

    labels = {row.label for row in _section(response, "group").rows}
    assert labels == {"Werkstatt", "Buero"}
    assert response.context["summary"].seconds == pytest.approx(9 * 3600)


# --- Summen -----------------------------------------------------------------


def test_sums_per_activity_user_group_and_cost_center(client, times, accountant):
    client.force_login(accountant)

    response = _get(client)

    activities = {row.label: row.seconds for row in _section(response, "activity").rows}
    users = {row.label: row.seconds for row in _section(response, "user").rows}
    cost_centers = {row.label: row.seconds for row in _section(response, "cost_center").rows}

    assert activities["Werkstatt · Montage"] == pytest.approx(4 * 3600)
    assert activities["Werkstatt · Lackieren"] == pytest.approx(2 * 3600)
    assert users["Mia Mitglied"] == pytest.approx(6 * 3600)
    assert cost_centers["4711"] == pytest.approx(6 * 3600)
    assert cost_centers[NO_COST_CENTER_LABEL] == pytest.approx(3 * 3600)


def test_every_section_adds_up_to_the_total(client, times, accountant):
    client.force_login(accountant)

    response = _get(client)

    result = response.context["summary"]
    for section in result.sections:
        assert section.seconds == pytest.approx(result.seconds), section.key


def test_view_and_export_agree(client, times, accountant):
    client.force_login(accountant)
    entries = services.query_entries(accountant, start=DAY, end=DAY)
    exported = services.build_rows(entries, "activity", first_day=DAY, last_day=DAY)

    response = _get(client)

    from_view = {row.label: row.seconds for row in _section(response, "activity").rows}
    from_export = {f"{row['group']} · {row['activity']}": row["work_seconds"] for row in exported}
    assert from_view == pytest.approx(from_export)


def test_empty_period_shows_a_note_instead_of_numbers(client, times, accountant):
    client.force_login(accountant)

    response = client.get(URL, {"start": "2020-01-01", "end": "2020-01-31"})

    assert response.context["summary"].is_empty
    assert "In diesem Zeitraum wurde nichts erfasst." in response.content.decode()


def test_invalid_period_shows_the_error(client, accountant):
    client.force_login(accountant)

    response = client.get(URL, {"start": "2026-09-30", "end": "2026-09-01"})

    assert response.status_code == 200
    assert response.context["form"].errors
    assert response.context["summary"] is None


# --- Filter und Schnellschalter --------------------------------------------


def test_filter_by_cost_center(client, times, accountant, group):
    client.force_login(accountant)

    response = _get(client, cost_centers=[group.cost_center])

    assert response.context["summary"].seconds == pytest.approx(6 * 3600)


def test_filter_by_group(client, times, accountant, other_group):
    client.force_login(accountant)

    response = _get(client, groups=[other_group.pk])

    assert [row.label for row in _section(response, "group").rows] == ["Buero"]


def test_filter_by_activity(client, times, accountant, activity):
    client.force_login(accountant)

    response = _get(client, activities=[activity.pk])

    assert response.context["summary"].seconds == pytest.approx(4 * 3600)


def test_quick_switch_sets_the_period(client, times, accountant):
    client.force_login(accountant)

    response = client.get(URL, {"bereich": "woche"})

    today = timezone.localdate()
    assert response.context["start_day"] == today - timedelta(days=today.weekday())
    assert response.context["end_day"] == today


def test_foreign_group_is_refused_by_the_filter(client, times, group_admin, other_group):
    client.force_login(group_admin)

    response = _get(client, groups=[other_group.pk])

    assert response.context["form"].errors
    assert response.context["summary"] is None


# --- Weg zum Export ---------------------------------------------------------


def test_export_link_carries_period_and_filters(client, times, accountant, group):
    client.force_login(accountant)

    response = _get(client, groups=[group.pk], cost_centers=[group.cost_center])

    query = response.context["export_query"]
    assert f"start={DAY.isoformat()}" in query
    assert f"groups={group.pk}" in query
    assert "cost_centers=4711" in query


def test_export_page_takes_over_the_selection(client, times, accountant, group):
    client.force_login(accountant)

    response = client.get(
        reverse("reporting:export"),
        {"start": DAY.isoformat(), "end": DAY.isoformat(), "groups": [group.pk]},
    )

    initial = response.context["form"].initial
    assert initial["start"] == DAY.isoformat()
    assert initial["groups"] == [str(group.pk)]


# --- Diagramm ---------------------------------------------------------------


def test_page_contains_an_embedded_svg(client, times, accountant):
    client.force_login(accountant)

    body = _get(client).content.decode()

    assert "<svg" in body
    assert 'role="img"' in body
    assert "Stunden je Tätigkeit" in body
    # Nichts wird nachgeladen und nichts ausgeführt.
    assert "<script" not in body
    assert "<image" not in body


def test_chart_is_labelled_for_screen_readers(client, times, accountant):
    client.force_login(accountant)

    chart = _section(_get(client), "activity").chart

    assert 'aria-labelledby="diagramm-activity-titel diagramm-activity-beschreibung"' in chart
    assert '<title id="diagramm-activity-titel">' in chart
    assert '<desc id="diagramm-activity-beschreibung">' in chart


def test_chart_writes_every_value_next_to_its_bar(client, times, accountant):
    client.force_login(accountant)

    chart = _section(_get(client), "activity").chart

    assert "Werkstatt · Montage" in chart
    assert "4:00" in chart


def test_names_from_the_database_are_escaped(client, member, group, accountant):
    Activity.objects.create(group=group, name='<script>alert("x")</script>')
    _entry(member, group, Activity.objects.get(name__startswith="<script"), hours=1)
    client.force_login(accountant)

    body = _get(client).content.decode()

    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def test_too_many_bars_are_summarized(db, member, group, accountant):
    for index in range(charts.MAX_BARS + 5):
        activity = Activity.objects.create(group=group, name=f"Arbeit {index:02d}")
        _entry(member, group, activity, hours=1, hour=index % 12)
    entries = services.query_entries(accountant, start=DAY, end=DAY)
    rows = services.base_rows(entries, first_day=DAY, last_day=DAY)

    result = summary.build(rows)
    section = next(item for item in result.sections if item.key == "activity")

    assert len(section.rows) == charts.MAX_BARS + 5
    assert section.chart.count("<rect") == charts.MAX_BARS
    assert "übrige (6)" in section.chart


def test_chart_is_empty_without_data():
    assert charts.bar_chart(chart_id="leer", title="Titel", description="Text", bars=[]) == ""


# --- Größe ------------------------------------------------------------------


def test_page_needs_no_query_per_row(client, times, accountant, django_assert_max_num_queries):
    client.force_login(accountant)

    # Sitzung, Nutzer, Navigation, Formular und die eine Abfrage der Einträge:
    # die Zahl darf nicht mit den Zeilen wachsen.
    with django_assert_max_num_queries(15):
        _get(client)
