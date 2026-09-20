"""Benachrichtigungen und Zähler zu Korrekturanträgen (Issue 3)."""

from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.corrections import services
from apps.corrections.models import CorrectionRequest
from apps.groups.models import GroupMembership
from apps.tracking.models import TimeEntry


@pytest.fixture
def emails_on(settings):
    settings.CORRECTION_EMAILS_ENABLED = True
    settings.SITE_BASE_URL = "https://zeiterfassung.example.com"
    return settings


@pytest.fixture
def entry(member, group, activity):
    start = timezone.now() - timedelta(hours=9)
    return TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=start, end=start + timedelta(hours=8)
    )


def _request(entry, member, group, activity):
    return services.create_request(
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.EDIT,
        reason="Zu spät ausgestempelt",
        entry=entry,
        proposed_start=entry.start,
        proposed_end=entry.end - timedelta(minutes=30),
        proposed_activity=activity,
    )


def test_admins_are_informed_about_a_new_request(
    emails_on, entry, member, group, activity, group_admin, django_capture_on_commit_callbacks
):
    # Versandt wird erst nach dem Commit, damit keine Mail zu einem
    # zurückgerollten Antrag rausgeht.
    with django_capture_on_commit_callbacks(execute=True):
        _request(entry, member, group, activity)

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [group_admin.email]
    assert "Korrekturantrag" in message.subject
    assert "Zu spät ausgestempelt" in message.body
    assert "https://zeiterfassung.example.com" in message.body


def test_the_requester_learns_the_decision(
    emails_on, entry, member, group, activity, group_admin, django_capture_on_commit_callbacks
):
    correction = _request(entry, member, group, activity)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        services.reject(correction, group_admin, "Die Zeit ist so erfasst worden.")

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [member.email]
    assert "Die Zeit ist so erfasst worden." in message.body


def test_without_a_mailserver_nothing_is_sent(
    entry, member, group, activity, group_admin, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        correction = _request(entry, member, group, activity)
        services.approve(correction, group_admin)

    assert mail.outbox == []


def test_an_admin_requesting_does_not_mail_themselves(
    emails_on, group, group_admin, activity, django_capture_on_commit_callbacks
):
    start = timezone.now() - timedelta(hours=9)
    own_entry = TimeEntry.objects.create(
        user=group_admin,
        group=group,
        activity=activity,
        start=start,
        end=start + timedelta(hours=8),
    )

    with django_capture_on_commit_callbacks(execute=True):
        _request(own_entry, group_admin, group, activity)

    assert mail.outbox == []


def test_a_second_admin_is_still_informed(
    emails_on, group, group_admin, activity, make_user, django_capture_on_commit_callbacks
):
    other_admin = make_user("admin2@example.com", last_name="Zweit")
    GroupMembership.objects.create(user=other_admin, group=group, role=GroupMembership.Role.ADMIN)
    start = timezone.now() - timedelta(hours=9)
    own_entry = TimeEntry.objects.create(
        user=group_admin,
        group=group,
        activity=activity,
        start=start,
        end=start + timedelta(hours=8),
    )

    with django_capture_on_commit_callbacks(execute=True):
        _request(own_entry, group_admin, group, activity)

    assert [message.to for message in mail.outbox] == [[other_admin.email]]


def test_the_counter_shows_open_requests_to_the_admin(
    client, entry, member, group, activity, group_admin
):
    _request(entry, member, group, activity)
    client.force_login(group_admin)

    response = client.get(reverse("tracking:clock"))

    assert 'class="badge count">1<' in response.content.decode()


def test_the_counter_shows_a_new_decision_and_then_goes_out(
    client, entry, member, group, activity, group_admin
):
    correction = _request(entry, member, group, activity)
    services.approve(correction, group_admin)
    client.force_login(member)

    assert 'class="badge count">1<' in client.get(reverse("tracking:clock")).content.decode()

    client.get(reverse("corrections:mine"))
    correction.refresh_from_db()

    assert correction.decision_seen_at is not None
    assert 'class="badge count">' not in client.get(reverse("tracking:clock")).content.decode()


def test_a_withdrawn_request_does_not_count(client, entry, member, group, activity):
    correction = _request(entry, member, group, activity)
    services.withdraw(correction, member)
    client.force_login(member)

    assert 'class="badge count">' not in client.get(reverse("tracking:clock")).content.decode()
