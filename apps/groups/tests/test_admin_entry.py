"""Gruppen-Admin ändert und ergänzt Zeiten direkt (Issue 31)."""

from datetime import datetime, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.groups import closing
from apps.groups.periods import period_for
from apps.tracking import entry_editing
from apps.tracking.models import TimeEntry


def _minute(value):
    """Das Formular kennt nur Minuten, die Vergleiche im Test deshalb auch."""
    return value.replace(second=0, microsecond=0)


@pytest.fixture
def entry(member, group, activity):
    start = _minute(timezone.now() - timedelta(hours=9))
    return TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=start, end=start + timedelta(hours=8)
    )


def _form_data(start, end, **extra):
    """Formularfelder inklusive der leeren Pausenzeilen."""
    data = {
        "start": timezone.localtime(start).strftime("%Y-%m-%dT%H:%M"),
        "end": timezone.localtime(end).strftime("%Y-%m-%dT%H:%M"),
        "reason": "Mia hat vergessen auszustempeln.",
        "pausen-TOTAL_FORMS": "6",
        "pausen-INITIAL_FORMS": "0",
        "pausen-MIN_NUM_FORMS": "0",
        "pausen-MAX_NUM_FORMS": "6",
    }
    for index in range(6):
        data[f"pausen-{index}-start"] = ""
        data[f"pausen-{index}-end"] = ""
    data.update(extra)
    return data


def test_admin_changes_an_entry(client, group_admin, group, entry, member):
    client.force_login(group_admin)
    new_end = entry.start + timedelta(hours=7)

    response = client.post(
        reverse("groups:entry_edit", args=[group.pk, entry.pk]),
        _form_data(entry.start, new_end, note="Feierabend früher"),
    )

    assert response.status_code == 302
    entry.refresh_from_db()
    assert entry.end == new_end
    assert entry.note == "Feierabend früher"
    assert entry.source == TimeEntry.Source.CORRECTION
    log_entry = AuditLog.objects.filter(action=AuditLog.Action.ENTRY_UPDATED).latest("created_at")
    assert log_entry.actor == group_admin
    assert log_entry.subject == member
    assert log_entry.changes["vorher"]["end"] != log_entry.changes["nachher"]["end"]


def test_admin_adds_a_missing_entry_with_breaks(client, group_admin, group, member, activity):
    client.force_login(group_admin)
    start = _minute(timezone.now() - timedelta(days=1, hours=9))
    end = start + timedelta(hours=8)

    data = _form_data(start, end, user=member.pk, activity=activity.pk)
    data["pausen-0-start"] = timezone.localtime(start + timedelta(hours=4)).strftime(
        "%Y-%m-%dT%H:%M"
    )
    data["pausen-0-end"] = timezone.localtime(start + timedelta(hours=4, minutes=30)).strftime(
        "%Y-%m-%dT%H:%M"
    )
    response = client.post(reverse("groups:entry_create", args=[group.pk]), data)

    assert response.status_code == 302
    created = TimeEntry.objects.get(user=member, start=start)
    assert created.source == TimeEntry.Source.CORRECTION
    assert created.breaks.count() == 1
    assert AuditLog.objects.filter(
        action=AuditLog.Action.ENTRY_UPDATED, note__startswith="Direkt nachgetragen"
    ).exists()


def test_member_may_not_open_the_admin_form(client, member, group, entry):
    client.force_login(member)

    response = client.get(reverse("groups:entry_edit", args=[group.pk, entry.pk]))

    assert response.status_code == 403


def test_accounting_may_not_change_times(client, accountant, group, entry):
    client.force_login(accountant)

    response = client.get(reverse("groups:entry_edit", args=[group.pk, entry.pk]))

    assert response.status_code == 403
    assert entry_editing.may_edit(accountant, group) is False


def test_reason_is_required(client, group_admin, group, entry):
    client.force_login(group_admin)
    data = _form_data(entry.start, entry.end, reason="")

    response = client.post(reverse("groups:entry_edit", args=[group.pk, entry.pk]), data)

    assert response.status_code == 200
    assert "reason" in response.context["form"].errors


def test_a_closed_period_blocks_the_change(group_admin, group, member, activity):
    """Ein abgeschlossener Zeitraum ist auch für den Admin zu."""
    previous = period_for(timezone.localdate(), group.month_start_day).previous()
    start = timezone.make_aware(datetime.combine(previous.start, time(8, 0)))
    old_entry = TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=start, end=start + timedelta(hours=8)
    )
    closing.close_period(group, previous, group_admin, "")

    with pytest.raises(entry_editing.EntryEditError) as exc:
        entry_editing.update_entry(
            old_entry,
            editor=group_admin,
            reason="Korrektur",
            start=old_entry.start,
            end=old_entry.start + timedelta(hours=7),
        )

    assert "abgeschlossen" in str(exc.value)


def test_an_overlapping_time_is_refused(group_admin, group, member, entry):
    other_start = entry.end + timedelta(hours=1)
    TimeEntry.objects.create(
        user=member, group=group, start=other_start, end=other_start + timedelta(hours=2)
    )

    with pytest.raises(entry_editing.EntryEditError) as exc:
        entry_editing.update_entry(
            entry,
            editor=group_admin,
            reason="Korrektur",
            start=entry.start,
            end=other_start + timedelta(minutes=30),
        )

    assert "überschneidet" in str(exc.value)


def test_a_running_entry_cannot_be_changed(client, group_admin, group, member):
    running = TimeEntry.objects.create(
        user=member, group=group, start=timezone.now() - timedelta(hours=1)
    )
    client.force_login(group_admin)

    response = client.get(reverse("groups:entry_edit", args=[group.pk, running.pk]))

    assert response.status_code == 302
    assert response["Location"] == reverse("groups:detail", args=[group.pk])


def test_only_members_of_the_group_can_be_backdated(group_admin, group, make_user):
    stranger = make_user("fremd@example.com")
    start = timezone.now() - timedelta(hours=5)

    with pytest.raises(entry_editing.EntryEditError) as exc:
        entry_editing.create_entry(
            editor=group_admin,
            user=stranger,
            group=group,
            reason="Nachtrag",
            start=start,
            end=start + timedelta(hours=2),
        )

    assert "kein Mitglied" in str(exc.value)


def test_a_deactivated_activity_stays_on_the_entry(client, group_admin, group, entry, activity):
    """Deaktivierte Tätigkeiten bleiben an alten Einträgen stehen, auch beim Ändern."""
    activity.is_active = False
    activity.save(update_fields=["is_active"])
    client.force_login(group_admin)

    form = client.get(reverse("groups:entry_edit", args=[group.pk, entry.pk])).context["form"]
    assert activity in form.fields["activity"].queryset

    response = client.post(
        reverse("groups:entry_edit", args=[group.pk, entry.pk]),
        _form_data(entry.start, entry.end - timedelta(minutes=30), activity=activity.pk),
    )

    assert response.status_code == 302
    entry.refresh_from_db()
    assert entry.activity == activity


def test_the_log_shows_a_changed_break(client, group_admin, group, entry):
    """Ändert sich nur die Pause, muss das Protokoll den Unterschied zeigen."""
    client.force_login(group_admin)
    data = _form_data(entry.start, entry.end)
    pause_start = entry.start + timedelta(hours=4)
    data["pausen-0-start"] = timezone.localtime(pause_start).strftime("%Y-%m-%dT%H:%M")
    data["pausen-0-end"] = timezone.localtime(pause_start + timedelta(minutes=30)).strftime(
        "%Y-%m-%dT%H:%M"
    )

    client.post(reverse("groups:entry_edit", args=[group.pk, entry.pk]), data)

    log_entry = AuditLog.objects.filter(action=AuditLog.Action.ENTRY_UPDATED).latest("created_at")
    assert log_entry.changes["vorher"]["breaks"] == []
    assert len(log_entry.changes["nachher"]["breaks"]) == 1
