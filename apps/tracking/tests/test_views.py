from django.urls import reverse

from apps.tracking import services
from apps.tracking.models import TimeEntry


def test_clock_page_requires_login(client):
    response = client.get(reverse("tracking:clock"))

    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


def test_clock_page_shows_state(client, member):
    client.force_login(member)

    response = client.get(reverse("tracking:clock"))

    assert response.status_code == 200
    assert "Ausgestempelt" in response.content.decode()


def test_clock_in_and_out_through_the_web(client, member, group, activity):
    client.force_login(member)

    client.post(
        reverse("tracking:clock_in"),
        {"group": group.pk, "activity": activity.pk, "note": ""},
        follow=True,
    )
    assert TimeEntry.objects.open().filter(user=member).exists()

    client.post(reverse("tracking:break_start"), follow=True)
    assert services.get_state(member).is_on_break

    client.post(reverse("tracking:break_end"), follow=True)
    client.post(reverse("tracking:clock_out"), follow=True)

    entry = TimeEntry.objects.get(user=member)
    assert entry.end is not None


def test_clock_in_needs_post(client, member):
    client.force_login(member)

    response = client.get(reverse("tracking:clock_in"))

    assert response.status_code == 405


def test_cannot_clock_in_for_a_foreign_group(client, make_user, group, activity):
    stranger = make_user("fremd@example.com")
    client.force_login(stranger)

    response = client.post(
        reverse("tracking:clock_in"),
        {"group": group.pk, "activity": activity.pk},
        follow=True,
    )

    assert not TimeEntry.objects.filter(user=stranger).exists()
    assert "Gruppe" in response.content.decode()


def test_my_entries_lists_only_own_times(client, member, make_user, group, activity):
    from datetime import timedelta

    from django.utils import timezone

    from apps.groups.models import GroupMembership

    colleague = make_user("kollege@example.com", last_name="Kollege")
    GroupMembership.objects.create(user=colleague, group=group)
    start = timezone.now() - timedelta(hours=5)
    TimeEntry.objects.create(
        user=colleague, group=group, activity=activity, start=start, end=start + timedelta(hours=4)
    )
    client.force_login(member)

    response = client.get(reverse("tracking:my_entries"))

    assert response.status_code == 200
    assert "Kollege" not in response.content.decode()
