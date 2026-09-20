"""Erinnerung an liegengebliebene Korrekturanträge (Issue 34)."""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.corrections import services as corrections
from apps.corrections.models import CorrectionRequest
from apps.groups.models import GroupMembership
from apps.reminders import jobs
from apps.reminders.models import Reminder
from apps.tracking.models import TimeEntry


@pytest.fixture
def emails_on(settings):
    settings.REMINDER_EMAILS_ENABLED = True
    return settings


@pytest.fixture
def old_request(member, group, group_admin, activity):
    start = timezone.now() - timedelta(days=10)
    entry = TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=start, end=start + timedelta(hours=8)
    )
    correction = corrections.create_request(
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.EDIT,
        reason="Pause vergessen",
        entry=entry,
        proposed_start=entry.start,
        proposed_end=entry.end - timedelta(minutes=30),
        proposed_activity=activity,
    )
    CorrectionRequest.objects.filter(pk=correction.pk).update(
        created_at=timezone.now() - timedelta(days=5)
    )
    return CorrectionRequest.objects.get(pk=correction.pk)


def test_admins_are_reminded_once(old_request, group_admin):
    first = jobs.remind_pending_corrections(days=3)
    second = jobs.remind_pending_corrections(days=3)

    assert (first.created, second.created) == (1, 0)
    reminder = Reminder.objects.get(recipient=group_admin)
    assert reminder.kind == Reminder.Kind.PENDING_CORRECTION
    assert "5 Tagen" in reminder.message


def test_a_fresh_request_stays_quiet(old_request):
    CorrectionRequest.objects.filter(pk=old_request.pk).update(created_at=timezone.now())

    assert jobs.remind_pending_corrections(days=3).created == 0


def test_the_requester_is_not_reminded_of_his_own_request(group, group_admin, activity):
    # Ein Admin, der selbst einen Antrag stellt, entscheidet nicht darüber.
    start = timezone.now() - timedelta(days=10)
    entry = TimeEntry.objects.create(
        user=group_admin,
        group=group,
        activity=activity,
        start=start,
        end=start + timedelta(hours=4),
    )
    correction = corrections.create_request(
        requested_by=group_admin,
        group=group,
        kind=CorrectionRequest.Kind.EDIT,
        reason="Falsche Tätigkeit",
        entry=entry,
        proposed_start=entry.start,
        proposed_end=entry.end,
        proposed_activity=activity,
    )
    CorrectionRequest.objects.filter(pk=correction.pk).update(
        created_at=timezone.now() - timedelta(days=5)
    )

    assert jobs.remind_pending_corrections(days=3).created == 0


def test_every_admin_of_the_group_gets_one(old_request, group, make_user):
    second_admin = make_user("zweiter@example.com", last_name="Zweit")
    GroupMembership.objects.create(user=second_admin, group=group, role=GroupMembership.Role.ADMIN)

    assert jobs.remind_pending_corrections(days=3).created == 2
    assert Reminder.objects.filter(recipient=second_admin).exists()


def test_a_decision_closes_the_reminder(old_request, group_admin):
    jobs.remind_pending_corrections(days=3)

    corrections.reject(old_request, group_admin, "Nicht nachvollziehbar")

    assert Reminder.objects.get().resolved_at is not None


def test_withdrawing_closes_the_reminder(old_request, member):
    jobs.remind_pending_corrections(days=3)

    corrections.withdraw(old_request, member)

    assert Reminder.objects.get().resolved_at is not None


def test_mail_names_the_reason(old_request, group_admin, emails_on):
    jobs.remind_pending_corrections(days=3)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [group_admin.email]
    assert "Pause vergessen" in mail.outbox[0].body
