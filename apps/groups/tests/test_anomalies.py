"""Auffälligkeiten nach dem Arbeitszeitgesetz je Gruppe (Issue 49)."""

from datetime import date, datetime, time

from django.urls import reverse
from django.utils import timezone

from apps.groups.models import GroupMembership
from apps.tracking.models import TimeEntry

MONDAY = date(2026, 5, 11)
TUESDAY = date(2026, 5, 12)


def at(day: date, hour: int):
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


def long_day(user, group, day=MONDAY):
    """Elf Stunden ohne Pause: Höchstarbeitszeit und Pause zugleich verletzt."""
    return TimeEntry.objects.create(user=user, group=group, start=at(day, 6), end=at(day, 17))


def url(group, **params):
    address = reverse("groups:anomalies", args=[group.pk])
    query = {"start": MONDAY.isoformat(), "end": TUESDAY.isoformat()}
    query.update(params)
    return f"{address}?" + "&".join(f"{key}={value}" for key, value in query.items())


def test_the_admin_sees_the_violations_of_his_group(client, group, group_admin, member):
    long_day(member, group)
    client.force_login(group_admin)

    response = client.get(url(group))
    body = response.content.decode()

    assert response.status_code == 200
    assert member.full_name in body
    assert "Höchstarbeitszeit" in body
    assert "Pause" in body
    assert {row.rule for row in response.context["rows"]} == {"break", "daily_max"}


def test_accounting_may_read_every_group(client, group, member, accountant):
    long_day(member, group)
    client.force_login(accountant)

    response = client.get(url(group))

    assert response.status_code == 200
    assert len(response.context["rows"]) == 2


def test_a_plain_member_has_no_access(client, group, member):
    client.force_login(member)

    assert client.get(url(group)).status_code == 403


def test_an_admin_of_another_group_has_no_access(client, group, other_group, make_user):
    admin = make_user("buero@example.com")
    GroupMembership.objects.create(user=admin, group=other_group, role=GroupMembership.Role.ADMIN)
    client.force_login(admin)

    assert client.get(url(group)).status_code == 403


def test_the_period_filter_narrows_the_list(client, group, group_admin, member):
    long_day(member, group, MONDAY)
    long_day(member, group, TUESDAY)
    client.force_login(group_admin)

    response = client.get(url(group, start=TUESDAY.isoformat()))

    assert {row.day for row in response.context["rows"]} == {TUESDAY}


def test_an_empty_period_says_so(client, group, group_admin, member):
    TimeEntry.objects.create(user=member, group=group, start=at(MONDAY, 8), end=at(MONDAY, 12))
    client.force_login(group_admin)

    response = client.get(url(group))

    assert response.context["rows"] == []
    assert "nichts aufgefallen" in response.content.decode()


def test_times_of_another_group_do_not_show_up(client, group, other_group, group_admin, member):
    GroupMembership.objects.create(user=member, group=other_group)
    long_day(member, other_group)
    client.force_login(group_admin)

    response = client.get(url(group))

    assert response.context["rows"] == []


def test_the_page_does_not_grow_with_the_number_of_people(
    client, django_assert_max_num_queries, group, group_admin, make_user
):
    for index in range(5):
        person = make_user(f"person{index}@example.com")
        GroupMembership.objects.create(user=person, group=group)
        long_day(person, group)
    client.force_login(group_admin)

    # Die Prüfung selbst braucht zwei Abfragen, der Rest ist Anmeldung und
    # Navigation; je Person kommt keine dazu.
    with django_assert_max_num_queries(12):
        response = client.get(url(group))

    assert len(response.context["rows"]) == 10


def test_the_switched_off_check_says_so(client, group, group_admin, member, settings):
    long_day(member, group)
    settings.STATUTORY_BREAK_WARNINGS = False
    settings.STATUTORY_LIMIT_WARNINGS = False
    client.force_login(group_admin)

    response = client.get(url(group))

    assert response.context["rows"] == []
    assert "abgeschaltet" in response.content.decode()


def test_the_group_overview_links_to_the_list(client, group, group_admin):
    client.force_login(group_admin)

    body = client.get(reverse("groups:list")).content.decode()

    assert reverse("groups:anomalies", args=[group.pk]) in body


def test_a_broken_filter_falls_back_to_the_running_period(client, group, group_admin, member):
    client.force_login(group_admin)

    response = client.get(reverse("groups:anomalies", args=[group.pk]), {"start": "quatsch"})

    assert response.status_code == 200
    assert response.context["start_day"] == group.current_period().start
    assert response.context["end_day"] == timezone.localdate()
