"""Hinweise in der Oberfläche (Issue 34)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.reminders import jobs
from apps.reminders.models import Reminder
from apps.tracking.models import TimeEntry


@pytest.fixture
def reminder(member, group, activity):
    TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=timezone.now() - timedelta(hours=12)
    )
    jobs.remind_open_entries(hours=10)
    return Reminder.objects.get()


def test_the_list_shows_my_hints(client, member, reminder):
    client.force_login(member)

    response = client.get(reverse("reminders:list"))

    assert response.status_code == 200
    assert reminder.message in response.content.decode()


def test_someone_else_does_not_see_them(client, group_admin, reminder):
    client.force_login(group_admin)

    response = client.get(reverse("reminders:list"))

    assert "Nichts zu tun." in response.content.decode()


def test_the_navigation_counts_open_hints(client, member, reminder):
    client.force_login(member)

    with_hint = client.get(reverse("tracking:clock")).content.decode()
    reminder.resolve()
    without_hint = client.get(reverse("tracking:clock")).content.decode()

    assert "Hinweise" in with_hint
    assert "Hinweise" not in without_hint


def test_a_dismissed_hint_disappears(client, member, reminder):
    client.force_login(member)

    response = client.post(reverse("reminders:dismiss", args=[reminder.pk]), follow=True)

    assert response.status_code == 200
    reminder.refresh_from_db()
    assert reminder.resolved_at is not None
    assert "Nichts zu tun." in response.content.decode()


def test_nobody_dismisses_a_foreign_hint(client, group_admin, reminder):
    client.force_login(group_admin)

    response = client.post(reverse("reminders:dismiss", args=[reminder.pk]))

    assert response.status_code == 404
    reminder.refresh_from_db()
    assert reminder.resolved_at is None


def test_a_dismiss_needs_a_post(client, member, reminder):
    client.force_login(member)

    assert client.get(reverse("reminders:dismiss", args=[reminder.pk])).status_code == 405
