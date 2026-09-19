"""Korrekturen duerfen keine doppelte Zeit erzeugen (Issue 17)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.corrections import services
from apps.corrections.models import CorrectionRequest
from apps.tracking.models import TimeEntry


@pytest.fixture
def workday(member, group, activity):
    """Ein abgeschlossener Arbeitstag von acht Stunden, gestern."""
    start = (timezone.localtime() - timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0
    )
    return TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=start, end=start + timedelta(hours=8)
    )


def test_nachtrag_inside_an_existing_entry_is_refused(workday, member, group):
    with pytest.raises(services.CorrectionError):
        services.create_request(
            requested_by=member,
            group=group,
            kind=CorrectionRequest.Kind.CREATE,
            reason="Stempeln vergessen.",
            proposed_start=workday.start + timedelta(hours=1),
            proposed_end=workday.start + timedelta(hours=7),
        )

    assert not CorrectionRequest.objects.exists()


def test_nachtrag_next_to_an_existing_entry_works(workday, member, group):
    correction = services.create_request(
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.CREATE,
        reason="Stempeln vergessen.",
        proposed_start=workday.end,
        proposed_end=workday.end + timedelta(hours=2),
    )

    assert correction.is_pending


def test_approval_refuses_an_overlap_that_appeared_later(workday, member, group, group_admin):
    """Zwischen Antrag und Entscheidung kann eine neue Zeit dazugekommen sein."""
    correction = services.create_request(
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.CREATE,
        reason="Stempeln vergessen.",
        proposed_start=workday.end + timedelta(hours=1),
        proposed_end=workday.end + timedelta(hours=3),
    )
    TimeEntry.objects.create(
        user=member,
        group=group,
        start=workday.end + timedelta(hours=2),
        end=workday.end + timedelta(hours=4),
    )

    with pytest.raises(services.CorrectionError):
        services.approve(correction, group_admin)

    correction.refresh_from_db()
    assert correction.is_pending
    assert TimeEntry.objects.filter(user=member).count() == 2


def test_editing_an_entry_does_not_clash_with_itself(workday, member, group, group_admin):
    correction = services.create_request(
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.EDIT,
        reason="Ich habe zu spaet ausgestempelt.",
        entry=workday,
        proposed_start=workday.start,
        proposed_end=workday.start + timedelta(hours=7),
    )

    services.approve(correction, group_admin)

    workday.refresh_from_db()
    assert workday.duration == timedelta(hours=7)


def test_editing_into_another_entry_is_refused(workday, member, group, activity):
    later = TimeEntry.objects.create(
        user=member,
        group=group,
        activity=activity,
        start=workday.end + timedelta(hours=1),
        end=workday.end + timedelta(hours=3),
    )

    with pytest.raises(services.CorrectionError):
        services.create_request(
            requested_by=member,
            group=group,
            kind=CorrectionRequest.Kind.EDIT,
            reason="Ich habe zu spaet ausgestempelt.",
            entry=workday,
            proposed_start=workday.start,
            proposed_end=later.start + timedelta(minutes=30),
        )


def test_delete_request_is_untouched(workday, member, group):
    correction = services.create_request(
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.DELETE,
        reason="Doppelt erfasst.",
        entry=workday,
    )

    assert correction.is_pending
