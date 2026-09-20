"""Änderungsantrag, dessen Zeiteintrag nicht mehr da ist (Issue 14)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.corrections import services
from apps.corrections.models import CorrectionRequest
from apps.tracking.models import TimeEntry


@pytest.fixture
def entry(member, group, activity):
    start = timezone.now() - timedelta(hours=9)
    return TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=start, end=start + timedelta(hours=8)
    )


@pytest.fixture
def orphaned_request(entry, member, group):
    """Ein Änderungsantrag, dessen Eintrag danach gelöscht wurde."""
    correction = CorrectionRequest.objects.create(
        time_entry=entry,
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.EDIT,
        proposed_start=entry.start,
        proposed_end=entry.start + timedelta(hours=7),
        reason="Ich habe zu spät ausgestempelt.",
    )
    entry.delete()
    correction.refresh_from_db()
    return correction


def test_approve_reports_the_missing_entry(orphaned_request, group_admin):
    with pytest.raises(services.CorrectionError):
        services.approve(orphaned_request, group_admin)

    orphaned_request.refresh_from_db()
    assert orphaned_request.is_pending


def test_decide_page_shows_a_message_instead_of_an_error(client, orphaned_request, group_admin):
    client.force_login(group_admin)

    response = client.post(
        reverse("corrections:decide", args=[orphaned_request.pk]),
        {"action": "approve", "note": ""},
    )

    assert response.status_code == 200
    assert any("nicht mehr" in str(message) for message in response.context["messages"])


def test_rejecting_still_works(orphaned_request, group_admin):
    services.reject(orphaned_request, group_admin, "Der Eintrag ist bereits gelöscht.")

    orphaned_request.refresh_from_db()
    assert orphaned_request.status == CorrectionRequest.Status.REJECTED
