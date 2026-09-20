"""Genehmigen mit Änderung (Issue 31)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.corrections import services
from apps.corrections.models import CorrectionRequest
from apps.tracking.models import TimeEntry


def _minute(value):
    return value.replace(second=0, microsecond=0)


@pytest.fixture
def entry(member, group, activity):
    start = _minute(timezone.now() - timedelta(hours=9))
    return TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=start, end=start + timedelta(hours=8)
    )


@pytest.fixture
def pending_request(entry, member, group, activity):
    return CorrectionRequest.objects.create(
        time_entry=entry,
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.EDIT,
        proposed_start=entry.start,
        proposed_end=entry.start + timedelta(hours=7),
        proposed_activity=activity,
        reason="Ich habe zu spät ausgestempelt.",
    )


def _post_data(start, end, **extra):
    data = {
        "action": "approve_adjusted",
        "start": timezone.localtime(start).strftime("%Y-%m-%dT%H:%M"),
        "end": timezone.localtime(end).strftime("%Y-%m-%dT%H:%M"),
        "note": "Eine Viertelstunde weniger, so steht es im Werkstattbuch.",
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


def test_admin_approves_an_edit_with_changed_times(client, group_admin, pending_request, entry):
    client.force_login(group_admin)
    applied_end = pending_request.proposed_end - timedelta(minutes=15)

    response = client.post(
        reverse("corrections:decide", args=[pending_request.pk]),
        _post_data(pending_request.proposed_start, applied_end),
    )

    assert response.status_code == 302
    entry.refresh_from_db()
    pending_request.refresh_from_db()
    assert entry.end == applied_end
    assert entry.source == TimeEntry.Source.CORRECTION
    assert pending_request.status == CorrectionRequest.Status.APPROVED
    assert pending_request.applied_end == applied_end
    assert pending_request.was_adjusted is True
    # Der Antrag selbst bleibt als Beleg unverändert.
    assert pending_request.proposed_end != applied_end


def test_the_requester_sees_what_was_applied(client, group_admin, member, pending_request):
    applied_end = pending_request.proposed_end - timedelta(minutes=15)
    services.approve(
        pending_request,
        group_admin,
        "",
        overrides={"start": pending_request.proposed_start, "end": applied_end},
    )

    client.force_login(member)
    body = client.get(reverse("corrections:mine")).content.decode()

    assert "Geändert übernommen" in body


def test_a_backdated_request_can_be_approved_with_changes(member, group, group_admin, activity):
    start = _minute(timezone.now() - timedelta(days=1, hours=9))
    correction = CorrectionRequest.objects.create(
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.CREATE,
        proposed_start=start,
        proposed_end=start + timedelta(hours=9),
        proposed_activity=activity,
        reason="Ich habe das Stempeln vergessen.",
    )
    applied_end = start + timedelta(hours=8)

    services.approve(correction, group_admin, "", overrides={"start": start, "end": applied_end})

    correction.refresh_from_db()
    assert correction.time_entry.end == applied_end
    assert correction.applied_end == applied_end


def test_a_deletion_cannot_be_approved_with_changes(member, group, group_admin, entry):
    correction = CorrectionRequest.objects.create(
        time_entry=entry,
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.DELETE,
        reason="Doppelt erfasst.",
    )

    with pytest.raises(services.CorrectionError):
        services.approve(
            correction,
            group_admin,
            "",
            overrides={"start": entry.start, "end": entry.end},
        )


def test_changed_times_are_checked_for_overlap(group_admin, member, group, pending_request, entry):
    later_start = entry.end + timedelta(hours=1)
    TimeEntry.objects.create(
        user=member, group=group, start=later_start, end=later_start + timedelta(hours=2)
    )

    with pytest.raises(services.CorrectionError) as exc:
        services.approve(
            pending_request,
            group_admin,
            "",
            overrides={"start": entry.start, "end": later_start + timedelta(minutes=30)},
        )

    assert "überschneidet" in str(exc.value)


def test_the_change_is_logged(group_admin, pending_request):
    services.approve(
        pending_request,
        group_admin,
        "",
        overrides={
            "start": pending_request.proposed_start,
            "end": pending_request.proposed_end - timedelta(minutes=15),
        },
    )

    log_entry = AuditLog.objects.filter(action=AuditLog.Action.CORRECTION_APPROVED).latest(
        "created_at"
    )
    assert log_entry.note == "Mit Änderung genehmigt."


def test_plain_approval_still_works(client, group_admin, pending_request, entry):
    """Das zweite Formular auf der Seite darf die einfache Entscheidung nicht stören."""
    client.force_login(group_admin)

    response = client.post(
        reverse("corrections:decide", args=[pending_request.pk]),
        {"action": "approve", "note": ""},
    )

    assert response.status_code == 302
    entry.refresh_from_db()
    pending_request.refresh_from_db()
    assert entry.end == pending_request.proposed_end
    assert pending_request.was_adjusted is False


def test_omitted_values_keep_what_was_requested(group_admin, pending_request, entry, activity):
    """Nennt der Aufruf nur Beginn und Ende, bleiben Tätigkeit und Pausen wie beantragt."""
    pending_request.proposed_breaks = [
        {
            "start": (pending_request.proposed_start + timedelta(hours=4)).isoformat(),
            "end": (pending_request.proposed_start + timedelta(hours=4, minutes=30)).isoformat(),
        }
    ]
    pending_request.save(update_fields=["proposed_breaks"])

    services.approve(
        pending_request,
        group_admin,
        "",
        overrides={
            "start": pending_request.proposed_start,
            "end": pending_request.proposed_end - timedelta(minutes=15),
        },
    )

    entry.refresh_from_db()
    pending_request.refresh_from_db()
    assert entry.activity == activity
    assert entry.breaks.count() == 1
    assert pending_request.applied_activity == activity
    assert len(pending_request.applied_breaks) == 1


def test_a_deactivated_activity_stays_selectable(client, group_admin, pending_request, activity):
    activity.is_active = False
    activity.save(update_fields=["is_active"])
    client.force_login(group_admin)

    response = client.get(reverse("corrections:decide", args=[pending_request.pk]))

    assert activity in response.context["adjust_form"].fields["activity"].queryset


def test_a_closed_period_inside_the_request_blocks_the_approval(member, group, group_admin):
    """Auch ein Antrag, der einen abgeschlossenen Zeitraum überspannt, ist gesperrt."""
    from datetime import datetime, time

    from apps.groups import closing
    from apps.groups.periods import period_for

    closed = period_for(timezone.localdate(), group.month_start_day).previous()
    start = timezone.make_aware(datetime.combine(closed.start - timedelta(days=5), time(8, 0)))
    end = timezone.make_aware(datetime.combine(closed.end + timedelta(days=3), time(17, 0)))
    correction = CorrectionRequest.objects.create(
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.CREATE,
        proposed_start=start,
        proposed_end=end,
        reason="Langer Einsatz, nie gestempelt.",
    )
    closing.close_period(group, closed, group_admin, "")

    with pytest.raises(services.CorrectionError) as exc:
        services.approve(correction, group_admin, "")

    assert "abgeschlossen" in str(exc.value)
