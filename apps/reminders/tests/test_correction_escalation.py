"""Eskalation liegengebliebener Anträge an die System-Admins (Issue 58)."""

import logging
from datetime import datetime, time, timedelta
from io import StringIO

import pytest
from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from apps.corrections import services as corrections
from apps.corrections.models import CorrectionRequest
from apps.groups.models import Activity, Group, GroupMembership
from apps.reminders import jobs
from apps.reminders.models import Reminder
from apps.tracking.models import TimeEntry


def at(days_ago: int, hour: int = 0):
    """Ein fester Zeitpunkt: der Tagesbeginn vor n Tagen, plus Stunden.

    "jetzt minus n Tage" läge je nach Uhrzeit des Laufs knapp über oder unter
    einer Frist. Vom Tagesbeginn aus liegen immer volle n Tage dazwischen.
    """
    day = timezone.localdate() - timedelta(days=days_ago)
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


def make_request(requested_by, group, activity, *, days_ago: int, entry_day: int = 30):
    """Ein offener Korrekturantrag, der seit `days_ago` Tagen wartet."""
    start = at(entry_day, 8)
    entry = TimeEntry.objects.create(
        user=requested_by,
        group=group,
        activity=activity,
        start=start,
        end=start + timedelta(hours=8),
    )
    correction = corrections.create_request(
        requested_by=requested_by,
        group=group,
        kind=CorrectionRequest.Kind.EDIT,
        reason="Pause vergessen",
        entry=entry,
        proposed_start=entry.start,
        proposed_end=entry.end - timedelta(minutes=30),
        proposed_activity=activity,
    )
    CorrectionRequest.objects.filter(pk=correction.pk).update(created_at=at(days_ago))
    return CorrectionRequest.objects.get(pk=correction.pk)


@pytest.fixture
def emails_on(settings):
    settings.REMINDER_EMAILS_ENABLED = True
    return settings


@pytest.fixture
def lonely_group(db, make_user):
    """Eine Gruppe ohne jeden Admin, mit einem Mitglied und einer Tätigkeit."""
    group = Group.objects.create(name="Lager")
    user = make_user("lager@example.com", first_name="Lars", last_name="Lager")
    GroupMembership.objects.create(user=user, group=group, role=GroupMembership.Role.MEMBER)
    return group, user, Activity.objects.create(group=group, name="Kommissionieren")


def escalations(recipient=None):
    hints = Reminder.objects.unresolved().filter(kind=Reminder.Kind.CORRECTION_ESCALATION)
    return hints.filter(recipient=recipient) if recipient else hints


# --- Die beiden Fristen -----------------------------------------------------


def test_the_first_deadline_reminds_only_the_group(member, group, group_admin, activity, superuser):
    make_request(member, group, activity, days_ago=5)

    result = jobs.remind_pending_corrections(days=3, escalation_days=14)

    assert result.created == 1
    assert Reminder.objects.get(recipient=group_admin).kind == Reminder.Kind.PENDING_CORRECTION
    assert not Reminder.objects.filter(recipient=superuser).exists()


def test_the_second_deadline_escalates_to_the_system_admin(
    member, group, group_admin, activity, superuser
):
    make_request(member, group, activity, days_ago=20)

    jobs.remind_pending_corrections(days=3, escalation_days=14)

    assert Reminder.objects.filter(recipient=group_admin).count() == 1
    hint = escalations(superuser).get()
    assert "Werkstatt" in hint.message
    assert "20 Tagen" in hint.message
    assert "Mia Mitglied" in hint.message
    assert hint.url == "/korrekturen/eingang/"


def test_escalating_twice_creates_nothing_new(member, group, group_admin, activity, superuser):
    make_request(member, group, activity, days_ago=20)

    first = jobs.remind_pending_corrections(days=3, escalation_days=14)
    second = jobs.remind_pending_corrections(days=3, escalation_days=14)

    assert (first.created, second.created) == (2, 0)
    assert escalations().count() == 1


def test_the_escalation_names_group_count_and_oldest(
    member, group, group_admin, activity, superuser, make_user
):
    other = make_user("zweite@example.com", first_name="Nina", last_name="Neu")
    GroupMembership.objects.create(user=other, group=group, role=GroupMembership.Role.MEMBER)
    make_request(member, group, activity, days_ago=20, entry_day=30)
    make_request(other, group, activity, days_ago=16, entry_day=29)

    jobs.remind_pending_corrections(days=3, escalation_days=14)

    hint = escalations(superuser).get()
    assert "2 Korrekturanträge" in hint.message
    assert "Mia Mitglied" in hint.message
    assert "20 Tagen" in hint.message
    # Der Grund gehört dazu, sonst steht der System-Admin vor einer Zahl.
    assert "Frist von 14 Tagen" in hint.message


def test_a_decision_closes_the_escalation(member, group, group_admin, activity, superuser):
    correction = make_request(member, group, activity, days_ago=20)
    jobs.remind_pending_corrections(days=3, escalation_days=14)

    corrections.reject(correction, superuser, "Nicht nachvollziehbar")

    assert escalations().count() == 0
    assert Reminder.objects.filter(kind=Reminder.Kind.CORRECTION_ESCALATION).count() == 1


def test_after_a_decision_the_next_oldest_escalates(
    member, group, group_admin, activity, superuser, make_user
):
    other = make_user("zweite@example.com", first_name="Nina", last_name="Neu")
    GroupMembership.objects.create(user=other, group=group, role=GroupMembership.Role.MEMBER)
    oldest = make_request(member, group, activity, days_ago=20, entry_day=30)
    make_request(other, group, activity, days_ago=16, entry_day=29)
    jobs.remind_pending_corrections(days=3, escalation_days=14)

    corrections.reject(oldest, superuser, "Nicht nachvollziehbar")
    jobs.remind_pending_corrections(days=3, escalation_days=14)

    hint = escalations(superuser).get()
    assert "1 Korrekturantrag wartet" in hint.message
    assert "Nina Neu" in hint.message


# --- Die Gruppe, in der niemand entscheiden kann -----------------------------


def test_a_group_without_an_active_admin_is_named(lonely_group, superuser):
    group, user, activity = lonely_group
    make_request(user, group, activity, days_ago=5)

    jobs.remind_pending_corrections(days=3, escalation_days=14)

    hint = escalations(superuser).get()
    assert "keinen aktiven Admin" in hint.message
    assert "Lager" in hint.message


def test_an_admin_on_his_own_request_escalates_with_the_first_deadline(
    group, group_admin, activity, superuser
):
    # Der Anlass des Issues: nur der Antragsteller ist Admin, also entscheidet
    # in der Gruppe niemand. Auf die zweite Frist zu warten hieße hier, auf
    # jemanden zu warten, den es nicht gibt.
    make_request(group_admin, group, activity, days_ago=5)

    result = jobs.remind_pending_corrections(days=3, escalation_days=90)

    assert result.created == 1
    hint = escalations(superuser).get()
    assert "einzige aktive Admin" in hint.message


def test_a_deactivated_admin_does_not_hold_the_group(
    member, group, group_admin, activity, superuser
):
    group_admin.is_active = False
    group_admin.save(update_fields=["is_active"])
    make_request(member, group, activity, days_ago=5)

    jobs.remind_pending_corrections(days=3, escalation_days=90)

    assert "keinen aktiven Admin" in escalations(superuser).get().message


def test_a_fresh_request_escalates_to_nobody(member, group, group_admin, activity, superuser):
    make_request(member, group, activity, days_ago=1)

    assert jobs.remind_pending_corrections(days=3, escalation_days=14).created == 0
    assert escalations().count() == 0


def test_an_inactive_system_admin_is_left_alone(member, group, group_admin, activity, superuser):
    superuser.is_active = False
    superuser.save(update_fields=["is_active"])
    make_request(member, group, activity, days_ago=20)

    jobs.remind_pending_corrections(days=3, escalation_days=14)

    assert escalations().count() == 0


def test_the_same_person_is_not_told_twice(member, group, activity, make_user):
    # Ein System-Admin, der die Gruppe selbst verwaltet, hat den Antrag schon
    # als Gruppen-Admin auf dem Tisch.
    both = make_user("chef@example.com", last_name="Chef", is_superuser=True, is_staff=True)
    GroupMembership.objects.create(user=both, group=group, role=GroupMembership.Role.ADMIN)
    make_request(member, group, activity, days_ago=20)

    result = jobs.remind_pending_corrections(days=3, escalation_days=14)

    assert result.created == 1
    assert Reminder.objects.get(recipient=both).kind == Reminder.Kind.PENDING_CORRECTION


# --- Die Frist selbst --------------------------------------------------------


def test_the_second_deadline_never_falls_below_the_first():
    assert jobs.escalation_deadline(3, 14) == 14
    assert jobs.escalation_deadline(3, 1) == 3


def test_a_deadline_below_the_first_is_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="apps.reminders.jobs"):
        assert jobs.escalation_deadline(3, 1) == 3

    assert "PENDING_CORRECTION_ESCALATION_DAYS" in caplog.text


def test_no_escalation_before_the_group_was_reminded(
    member, group, group_admin, activity, superuser
):
    make_request(member, group, activity, days_ago=2)

    # Mit escalation_days=0 würde ein zwei Tage alter Antrag sofort eskalieren,
    # obwohl die Gruppe noch nicht einmal erinnert worden ist.
    assert jobs.remind_pending_corrections(days=3, escalation_days=0).created == 0
    assert escalations().count() == 0


def test_the_command_warns_about_a_deadline_below_the_first(db, settings):
    settings.PENDING_CORRECTION_REMINDER_DAYS = 3
    settings.PENDING_CORRECTION_ESCALATION_DAYS = 1
    out = StringIO()

    call_command("remind_pending_corrections", stdout=out)

    assert "zweite Frist" in out.getvalue()


# --- Mail --------------------------------------------------------------------


def test_the_mail_reaches_the_system_admin(
    member, group, group_admin, activity, superuser, emails_on
):
    make_request(member, group, activity, days_ago=20)

    jobs.remind_pending_corrections(days=3, escalation_days=14)

    escalation = [message for message in mail.outbox if message.to == [superuser.email]]
    assert len(escalation) == 1
    assert "Werkstatt" in escalation[0].subject
    assert "Pause vergessen" in escalation[0].body
    assert "/korrekturen/eingang/" in escalation[0].body


def test_a_broken_mailserver_does_not_stop_the_run(
    member, group, group_admin, activity, superuser, emails_on, monkeypatch
):
    make_request(member, group, activity, days_ago=20)

    def kaputt(*args, **kwargs):
        raise OSError("kein Mailserver")

    monkeypatch.setattr("zeiterfassung.mailing.send_mail", kaputt)
    result = jobs.remind_pending_corrections(days=3, escalation_days=14)

    assert (result.created, result.mailed) == (2, 0)
    assert escalations(superuser).exists()
