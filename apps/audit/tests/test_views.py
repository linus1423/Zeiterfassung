"""Protokollansicht in der Oberfläche (Issue 33)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog, log
from apps.audit.summary import describe_changes
from apps.corrections.models import CorrectionRequest
from apps.tracking.models import TimeEntry


@pytest.fixture
def entry(member, group, activity):
    start = timezone.now() - timedelta(hours=9)
    return TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=start, end=start + timedelta(hours=8)
    )


@pytest.fixture
def rows(entry, member, group, group_admin):
    """Ein geänderter Eintrag, ein Export ohne Gruppe und eine fremde Gruppe."""
    changed = log(
        AuditLog.Action.ENTRY_UPDATED,
        actor=group_admin,
        target=entry,
        group=group,
        subject=member,
        changes={
            "vorher": {"start": entry.start.isoformat(), "end": entry.end.isoformat()},
            "nachher": {
                "start": entry.start.isoformat(),
                "end": (entry.end - timedelta(minutes=30)).isoformat(),
            },
        },
        note="Direkt geändert: zu spät ausgestempelt",
    )
    exported = log(AuditLog.Action.EXPORT, actor=group_admin, note="Export ohne Gruppe")
    return changed, exported


def test_group_admin_sees_the_log_of_the_group(client, group_admin, group, rows):
    client.force_login(group_admin)

    response = client.get(reverse("audit:group", args=[group.pk]))

    assert response.status_code == 200
    assert "Direkt geändert" in response.content.decode()
    assert list(response.context["rows"]) == [rows[0]]


def test_entries_without_a_group_stay_out(client, group_admin, group, rows):
    client.force_login(group_admin)

    body = client.get(reverse("audit:group", args=[group.pk])).content.decode()

    assert "Export ohne Gruppe" not in body


def test_accounting_may_read_every_group(client, accountant, group, rows):
    client.force_login(accountant)

    response = client.get(reverse("audit:group", args=[group.pk]))

    assert response.status_code == 200


def test_a_member_has_no_access(client, member, group, rows):
    client.force_login(member)

    response = client.get(reverse("audit:group", args=[group.pk]))

    assert response.status_code == 403


def test_the_action_filter_narrows_the_list(client, group_admin, group, rows):
    log(AuditLog.Action.CLOCK_IN, actor=group_admin, group=group)
    client.force_login(group_admin)

    response = client.get(
        reverse("audit:group", args=[group.pk]), {"action": AuditLog.Action.CLOCK_IN}
    )

    assert [row.action for row in response.context["rows"]] == [AuditLog.Action.CLOCK_IN]


def test_the_period_filter_narrows_the_list(client, group_admin, group, rows):
    client.force_login(group_admin)
    tomorrow = timezone.localdate() + timedelta(days=1)

    response = client.get(reverse("audit:group", args=[group.pk]), {"start": tomorrow.isoformat()})

    assert list(response.context["rows"]) == []


def test_the_entry_log_shows_its_correction_requests(client, group_admin, group, member, entry):
    correction = CorrectionRequest.objects.create(
        time_entry=entry,
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.EDIT,
        proposed_start=entry.start,
        proposed_end=entry.end,
        reason="Zu spät ausgestempelt.",
    )
    log(AuditLog.Action.CORRECTION_REQUESTED, actor=member, target=correction, group=group)
    log(AuditLog.Action.ENTRY_UPDATED, actor=group_admin, target=entry, group=group)
    client.force_login(group_admin)

    response = client.get(reverse("audit:entry", args=[entry.pk]))

    actions = {row.action for row in response.context["rows"]}
    assert actions == {AuditLog.Action.CORRECTION_REQUESTED, AuditLog.Action.ENTRY_UPDATED}


def test_the_entry_log_is_closed_to_other_groups(client, make_user, other_group, entry):
    from apps.groups.models import GroupMembership

    stranger = make_user("fremd@example.com")
    GroupMembership.objects.create(
        user=stranger, group=other_group, role=GroupMembership.Role.ADMIN
    )
    client.force_login(stranger)

    response = client.get(reverse("audit:entry", args=[entry.pk]))

    assert response.status_code == 403


def test_changes_become_readable_lines():
    lines = describe_changes(
        {
            "vorher": {
                "start": "2026-09-01T08:00:00+02:00",
                "end": "2026-09-01T16:00:00+02:00",
                "activity": "Montage",
                "breaks": [],
                "note": "",
            },
            "nachher": {
                "start": "2026-09-01T08:00:00+02:00",
                "end": "2026-09-01T15:30:00+02:00",
                "activity": "Montage",
                "breaks": [
                    {"start": "2026-09-01T12:00:00+02:00", "end": "2026-09-01T12:30:00+02:00"}
                ],
                "note": "Kunde",
            },
        }
    )

    assert lines[0].startswith("vorher: 01.09.2026 08:00 bis 16:00, Montage, ohne Pause")
    assert "Pausen 12:00 bis 12:30" in lines[1]
    assert "Notiz: Kunde" in lines[1]


def test_paging_keeps_the_filters(client, group_admin, group):
    for _ in range(55):
        log(AuditLog.Action.CLOCK_IN, actor=group_admin, group=group)
    client.force_login(group_admin)

    first = client.get(reverse("audit:group", args=[group.pk]), {"action": "clock_in"})
    second = client.get(
        reverse("audit:group", args=[group.pk]), {"action": "clock_in", "seite": "2"}
    )

    assert len(first.context["rows"]) == 50
    assert len(second.context["rows"]) == 5
    assert "action=clock_in" in first.context["query"]


def test_the_person_filter_finds_both_roles(client, group_admin, group, member, rows):
    """Gefiltert wird auf die Person, ob sie gehandelt hat oder betroffen war."""
    log(AuditLog.Action.CLOCK_IN, actor=member, group=group)
    client.force_login(group_admin)

    response = client.get(reverse("audit:group", args=[group.pk]), {"person": str(member.pk)})

    assert {row.action for row in response.context["rows"]} == {
        AuditLog.Action.CLOCK_IN,
        AuditLog.Action.ENTRY_UPDATED,
    }


def test_other_changes_stay_readable():
    """Unter vorher und nachher stehen nicht nur Zeiten, sondern etwa Stammdaten."""
    lines = describe_changes(
        {
            "vorher": {"personnel_number": "", "is_accounting": False},
            "nachher": {"personnel_number": "4711", "is_accounting": True},
        }
    )

    assert lines == [
        "vorher: personnel_number: , is_accounting: False",
        "nachher: personnel_number: 4711, is_accounting: True",
    ]


def test_lists_in_changes_stay_readable():
    assert describe_changes({"erhalten": ["Werkstatt"], "entzogen": []}) == [
        "erhalten: Werkstatt",
        "entzogen: nichts",
    ]
