"""Erinnerung ans Ausstempeln (Issue 34)."""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.reminders import jobs, services
from apps.reminders.models import Reminder
from apps.tracking import services as tracking
from apps.tracking.models import TimeEntry


@pytest.fixture
def emails_on(settings):
    settings.REMINDER_EMAILS_ENABLED = True
    settings.SITE_BASE_URL = "https://zeiterfassung.example.com"
    return settings


def _open_entry(member, group, hours):
    return TimeEntry.objects.create(
        user=member, group=group, start=timezone.now() - timedelta(hours=hours)
    )


def test_long_open_entry_creates_one_reminder(member, group):
    entry = _open_entry(member, group, hours=12)

    first = jobs.remind_open_entries(hours=10)
    second = jobs.remind_open_entries(hours=10)

    assert (first.created, second.created) == (1, 0)
    reminder = Reminder.objects.get(recipient=member)
    assert reminder.kind == Reminder.Kind.OPEN_ENTRY
    assert reminder.subject_key == services.entry_key(entry.pk)
    assert reminder.group_id == group.pk


def test_short_entry_stays_quiet(member, group):
    _open_entry(member, group, hours=3)

    assert jobs.remind_open_entries(hours=10).created == 0
    assert not Reminder.objects.exists()


def test_message_names_the_automatic_deadline(member, group, settings):
    settings.MAX_OPEN_ENTRY_HOURS = 16
    entry = _open_entry(member, group, hours=12)
    deadline = timezone.localtime(entry.start + timedelta(hours=16))

    jobs.remind_open_entries(hours=10)

    message = Reminder.objects.get().message
    assert f"{deadline:%d.%m.%Y um %H:%M}" in message
    assert group.name in message


def test_clocking_out_closes_the_reminder(member, group):
    _open_entry(member, group, hours=12)
    jobs.remind_open_entries(hours=10)

    tracking.clock_out(member)

    assert Reminder.objects.get().resolved_at is not None


def test_automatic_close_also_closes_the_reminder(member, group):
    _open_entry(member, group, hours=20)
    jobs.remind_open_entries(hours=10)

    tracking.close_stale_entries(max_hours=16)

    assert Reminder.objects.get().resolved_at is not None


def test_a_missed_close_is_swept_up_on_the_next_run(member, group):
    entry = _open_entry(member, group, hours=12)
    jobs.remind_open_entries(hours=10)
    # Ohne den Weg über die Stempel-Logik, etwa ein Eingriff in der Datenbank.
    TimeEntry.objects.filter(pk=entry.pk).update(end=timezone.now())

    result = jobs.remind_open_entries(hours=10)

    assert result.resolved == 1
    assert Reminder.objects.get().resolved_at is not None


def test_a_dismissed_reminder_does_not_come_back(member, group):
    _open_entry(member, group, hours=12)
    jobs.remind_open_entries(hours=10)
    Reminder.objects.get().resolve()

    assert jobs.remind_open_entries(hours=10).created == 0
    assert Reminder.objects.count() == 1


def test_mail_goes_out_once_when_enabled(member, group, emails_on):
    _open_entry(member, group, hours=12)

    first = jobs.remind_open_entries(hours=10)
    second = jobs.remind_open_entries(hours=10)

    assert (first.mailed, second.mailed) == (1, 0)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [member.email]
    assert "https://zeiterfassung.example.com" in mail.outbox[0].body
    assert Reminder.objects.get().emailed_at is not None


def test_without_mail_the_hint_still_exists(member, group, settings):
    settings.REMINDER_EMAILS_ENABLED = False
    _open_entry(member, group, hours=12)

    result = jobs.remind_open_entries(hours=10)

    assert result.created == 1
    assert result.mailed == 0
    assert mail.outbox == []
    assert Reminder.objects.get().emailed_at is None
