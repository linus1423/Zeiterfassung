"""Ein bestehender Eintrag bleibt in seiner Gruppe (Issue 13)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.corrections import services
from apps.corrections.models import CorrectionRequest
from apps.groups.models import GroupMembership
from apps.tracking.models import TimeEntry


@pytest.fixture
def entry(member, group, activity):
    start = timezone.now() - timedelta(hours=9)
    return TimeEntry.objects.create(
        user=member, group=group, activity=activity, start=start, end=start + timedelta(hours=8)
    )


@pytest.fixture
def member_of_both(member, other_group):
    GroupMembership.objects.create(user=member, group=other_group)
    return member


def _post_data(entry, group_id):
    start = timezone.localtime(entry.start)
    return {
        "group": group_id,
        "activity": entry.activity_id,
        "start": start.strftime("%Y-%m-%dT%H:%M"),
        "end": (start + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M"),
        "reason": "Ich habe zu spät ausgestempelt.",
        "pausen-TOTAL_FORMS": 6,
        "pausen-INITIAL_FORMS": 0,
        "pausen-MIN_NUM_FORMS": 0,
        "pausen-MAX_NUM_FORMS": 6,
    }


def test_request_keeps_the_group_of_the_entry(client, member_of_both, entry, other_group):
    client.force_login(member_of_both)

    response = client.post(
        reverse("corrections:create_for_entry", args=[entry.pk]),
        _post_data(entry, other_group.pk),
    )

    assert response.status_code == 200
    assert not CorrectionRequest.objects.exists()
    assert "group" in response.context["form"].errors


def test_request_for_own_group_still_works(client, member_of_both, entry, group):
    client.force_login(member_of_both)

    response = client.post(
        reverse("corrections:create_for_entry", args=[entry.pk]),
        _post_data(entry, group.pk),
    )

    assert response.status_code == 302
    assert CorrectionRequest.objects.get().group_id == group.pk


def test_service_rejects_a_foreign_group(member, group, other_group, entry):
    """Auch ohne Formular: die Gruppe muss zum Eintrag passen."""
    with pytest.raises(services.CorrectionError):
        services.create_request(
            requested_by=member,
            group=other_group,
            kind=CorrectionRequest.Kind.EDIT,
            reason="Ich habe zu spät ausgestempelt.",
            entry=entry,
            proposed_start=entry.start,
            proposed_end=entry.start + timedelta(hours=12),
        )

    assert not CorrectionRequest.objects.exists()


def test_admin_of_another_group_cannot_decide(member_of_both, entry, other_group, make_user):
    """Der Antrag landet bei den Admins der Gruppe des Eintrags."""
    other_admin = make_user("fremd@example.com")
    GroupMembership.objects.create(
        user=other_admin, group=other_group, role=GroupMembership.Role.ADMIN
    )

    correction = services.create_request(
        requested_by=member_of_both,
        group=entry.group,
        kind=CorrectionRequest.Kind.EDIT,
        reason="Ich habe zu spät ausgestempelt.",
        entry=entry,
        proposed_activity=entry.activity,
        proposed_start=entry.start,
        proposed_end=entry.start + timedelta(hours=12),
    )

    assert services.may_decide(other_admin, correction) is False
    with pytest.raises(services.CorrectionError):
        services.approve(correction, other_admin)
