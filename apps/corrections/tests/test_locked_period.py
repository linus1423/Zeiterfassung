"""Nach dem Monatsabschluss sind keine Korrekturen mehr moeglich (Issue 5)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.corrections import services
from apps.corrections.models import CorrectionRequest
from apps.groups import closing
from apps.tracking.models import TimeEntry
from apps.tracking.utils import day_bounds


@pytest.fixture
def closed_entry(member, group, activity, group_admin):
    """Ein Eintrag in einem Zeitraum, der danach abgeschlossen wird."""
    period = group.current_period().previous()
    start, _ = day_bounds(period.start)
    entry = TimeEntry.objects.create(
        user=member,
        group=group,
        activity=activity,
        start=start + timedelta(hours=8),
        end=start + timedelta(hours=16),
    )
    closing.close_period(group, period, group_admin)
    return entry


def test_a_request_for_a_closed_period_is_refused(closed_entry, member, group, activity):
    with pytest.raises(services.CorrectionError):
        services.create_request(
            requested_by=member,
            group=group,
            kind=CorrectionRequest.Kind.EDIT,
            reason="Zu spaet ausgestempelt",
            entry=closed_entry,
            proposed_start=closed_entry.start,
            proposed_end=closed_entry.end - timedelta(hours=1),
            proposed_activity=activity,
        )

    assert not CorrectionRequest.objects.exists()


def test_an_older_request_can_no_longer_be_approved(
    member, group, activity, group_admin, closed_entry
):
    # Der Antrag entsteht, bevor abgeschlossen wird.
    lock = closing.lock_for(group, timezone.localtime(closed_entry.start).date())
    correction = CorrectionRequest.objects.create(
        time_entry=closed_entry,
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.EDIT,
        proposed_start=closed_entry.start,
        proposed_end=closed_entry.end - timedelta(hours=1),
        proposed_activity=activity,
        reason="Zu spaet ausgestempelt",
    )

    with pytest.raises(services.CorrectionError) as error:
        services.approve(correction, group_admin)

    assert lock.period.label in str(error.value)
    correction.refresh_from_db()
    assert correction.is_pending


def test_a_deletion_request_is_refused_through_the_web(client, closed_entry, member):
    client.force_login(member)

    response = client.post(
        reverse("corrections:request_delete", args=[closed_entry.pk]),
        {"reason": "Eintrag war doppelt."},
        follow=True,
    )

    assert response.status_code == 200
    assert "abgeschlossen" in response.content.decode()
    assert not CorrectionRequest.objects.exists()


def test_the_open_period_stays_correctable(member, group, activity, group_admin, closed_entry):
    now = timezone.now()
    open_entry = TimeEntry.objects.create(
        user=member,
        group=group,
        activity=activity,
        start=now - timedelta(hours=9),
        end=now - timedelta(hours=1),
    )

    correction = services.create_request(
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.EDIT,
        reason="Pause vergessen",
        entry=open_entry,
        proposed_start=open_entry.start,
        proposed_end=open_entry.end,
        proposed_activity=activity,
    )
    services.approve(correction, group_admin)

    correction.refresh_from_db()
    assert correction.status == CorrectionRequest.Status.APPROVED


def test_the_form_names_the_closed_period(client, closed_entry, member, group, activity):
    client.force_login(member)
    local_start = timezone.localtime(closed_entry.start)

    response = client.post(
        reverse("corrections:create_for_entry", args=[closed_entry.pk]),
        {
            "group": group.pk,
            "activity": activity.pk,
            "start": local_start.strftime("%Y-%m-%dT%H:%M"),
            "end": local_start.strftime("%Y-%m-%dT17:00"),
            "reason": "Zu spaet ausgestempelt",
            "pausen-TOTAL_FORMS": "1",
            "pausen-INITIAL_FORMS": "0",
            "pausen-MIN_NUM_FORMS": "0",
            "pausen-MAX_NUM_FORMS": "6",
            "pausen-0-start": "",
            "pausen-0-end": "",
        },
    )

    assert response.status_code == 200
    assert "ist abgeschlossen" in response.content.decode()
    assert not CorrectionRequest.objects.exists()
