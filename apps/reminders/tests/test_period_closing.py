"""Erinnerung an einen abgelaufenen, noch offenen Zeitraum (Issue 34)."""

from datetime import datetime, time, timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.corrections.models import CorrectionRequest
from apps.groups import closing
from apps.reminders import jobs
from apps.reminders.models import Reminder
from apps.tracking.models import TimeEntry


@pytest.fixture
def last_period(group):
    return group.current_period().previous()


def _entry_in(period, user, group, *, incomplete=False):
    start = timezone.make_aware(datetime.combine(period.start + timedelta(days=1), time(8, 0)))
    return TimeEntry.objects.create(
        user=user,
        group=group,
        start=start,
        end=start + timedelta(hours=8),
        is_incomplete=incomplete,
    )


def test_admins_are_reminded_once_after_the_period_ended(group, group_admin, last_period):
    first = jobs.remind_period_closing(days=3)
    second = jobs.remind_period_closing(days=3)

    assert (first.created, second.created) == (1, 0)
    reminder = Reminder.objects.get(recipient=group_admin)
    assert reminder.kind == Reminder.Kind.PERIOD_CLOSING
    assert last_period.label in reminder.message


def test_a_long_deadline_holds_the_reminder_back(group, group_admin):
    assert jobs.remind_period_closing(days=400).created == 0


def test_a_closed_period_is_not_mentioned(group, group_admin, last_period):
    closing.close_period(group, last_period, group_admin)

    assert jobs.remind_period_closing(days=3).created == 0


def test_closing_the_period_closes_the_reminder(group, group_admin, last_period):
    jobs.remind_period_closing(days=3)

    closing.close_period(group, last_period, group_admin)

    assert Reminder.objects.get().resolved_at is not None


def test_the_message_counts_the_open_work(group, group_admin, member, last_period, activity):
    _entry_in(last_period, member, group, incomplete=True)
    entry = _entry_in(last_period, member, group)
    CorrectionRequest.objects.create(
        time_entry=entry,
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.EDIT,
        proposed_start=entry.start,
        proposed_end=entry.end,
        reason="Pause nachtragen",
    )

    jobs.remind_period_closing(days=3)

    message = Reminder.objects.get().message
    assert "Offene Anträge: 1" in message
    assert "unvollständige Einträge: 1" in message


def test_work_of_another_period_is_not_counted(group, group_admin, member, last_period):
    _entry_in(last_period.previous(), member, group, incomplete=True)

    jobs.remind_period_closing(days=3)

    assert "unvollständige Einträge: 0" in Reminder.objects.get().message


def test_a_lock_set_elsewhere_is_swept_up(group, group_admin, last_period):
    jobs.remind_period_closing(days=3)
    # Ohne close_period, etwa über die Datenbank oder einen Import.
    group.period_locks.create(period_start=last_period.start, period_end=last_period.end)

    result = jobs.remind_period_closing(days=3)

    assert result.resolved == 1
    assert Reminder.objects.get().resolved_at is not None


def test_mail_links_to_the_periods_page(group, group_admin, last_period, settings):
    settings.REMINDER_EMAILS_ENABLED = True
    settings.SITE_BASE_URL = "https://zeiterfassung.example.com"

    jobs.remind_period_closing(days=3)

    assert len(mail.outbox) == 1
    assert f"/gruppen/{group.pk}/abschluss/" in mail.outbox[0].body
