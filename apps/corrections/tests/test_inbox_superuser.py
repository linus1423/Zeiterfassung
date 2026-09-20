"""Der System-Admin sieht offene Anträge (Issue 16)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.corrections.models import CorrectionRequest
from apps.groups.models import GroupMembership


@pytest.fixture
def pending_request(member, group):
    start = timezone.now() - timedelta(days=1)
    return CorrectionRequest.objects.create(
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.CREATE,
        proposed_start=start,
        proposed_end=start + timedelta(hours=4),
        reason="Stempeln vergessen.",
    )


def test_superuser_sees_pending_requests(client, superuser, pending_request):
    client.force_login(superuser)

    response = client.get(reverse("corrections:inbox"))

    assert list(response.context["requests"]) == [pending_request]
    assert response.context["nav"]["pending_corrections"] == 1


def test_group_admin_sees_only_own_groups(client, group_admin, other_group, make_user):
    """Ein Gruppen-Admin bekommt durch die Änderung nichts Fremdes zu sehen."""
    outsider = make_user("extern@example.com")
    GroupMembership.objects.create(user=outsider, group=other_group)
    start = timezone.now() - timedelta(days=1)
    CorrectionRequest.objects.create(
        requested_by=outsider,
        group=other_group,
        kind=CorrectionRequest.Kind.CREATE,
        proposed_start=start,
        proposed_end=start + timedelta(hours=4),
        reason="Stempeln vergessen.",
    )
    client.force_login(group_admin)

    response = client.get(reverse("corrections:inbox"))

    assert list(response.context["requests"]) == []
    assert response.context["nav"]["pending_corrections"] == 0


def test_plain_member_has_no_counter(client, member, pending_request):
    client.force_login(member)

    response = client.get(reverse("tracking:clock"))

    assert response.context["nav"]["pending_corrections"] == 0
