from datetime import timedelta

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
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


def test_approve_changes_entry_and_logs(pending_request, group_admin, entry):
    services.approve(pending_request, group_admin)

    entry.refresh_from_db()
    pending_request.refresh_from_db()
    assert entry.end == pending_request.proposed_end
    assert entry.source == TimeEntry.Source.CORRECTION
    assert pending_request.status == CorrectionRequest.Status.APPROVED
    assert pending_request.decided_by == group_admin
    assert AuditLog.objects.filter(action=AuditLog.Action.CORRECTION_APPROVED).exists()


def test_requester_cannot_decide_own_request(pending_request, member, group):
    from apps.groups.models import GroupMembership

    GroupMembership.objects.filter(user=member, group=group).update(role=GroupMembership.Role.ADMIN)

    assert services.may_decide(member, pending_request) is False
    with pytest.raises(services.CorrectionError):
        services.approve(pending_request, member)


def test_member_of_other_group_cannot_decide(pending_request, make_user):
    stranger = make_user("fremd@example.com")

    with pytest.raises(services.CorrectionError):
        services.approve(pending_request, stranger)


def test_reject_needs_a_reason(pending_request, group_admin):
    with pytest.raises(services.CorrectionError):
        services.reject(pending_request, group_admin, "   ")

    services.reject(pending_request, group_admin, "Zeit war korrekt erfasst.")
    pending_request.refresh_from_db()
    assert pending_request.status == CorrectionRequest.Status.REJECTED


def test_approve_create_adds_entry(member, group, activity, group_admin):
    start = timezone.now() - timedelta(days=1)
    request_obj = CorrectionRequest.objects.create(
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.CREATE,
        proposed_start=start,
        proposed_end=start + timedelta(hours=4),
        proposed_activity=activity,
        reason="Stempeln vergessen.",
    )

    services.approve(request_obj, group_admin)

    entry = TimeEntry.objects.get(user=member, source=TimeEntry.Source.CORRECTION)
    assert entry.start == start
    assert entry.end == start + timedelta(hours=4)


def test_approve_delete_removes_entry(entry, member, group, group_admin):
    request_obj = CorrectionRequest.objects.create(
        time_entry=entry,
        requested_by=member,
        group=group,
        kind=CorrectionRequest.Kind.DELETE,
        reason="Doppelt erfasst.",
    )

    services.approve(request_obj, group_admin)

    assert not TimeEntry.objects.filter(pk=entry.pk).exists()
    assert AuditLog.objects.filter(action=AuditLog.Action.ENTRY_DELETED).exists()


def test_withdraw_only_by_requester(pending_request, member, group_admin):
    with pytest.raises(services.CorrectionError):
        services.withdraw(pending_request, group_admin)

    services.withdraw(pending_request, member)
    pending_request.refresh_from_db()
    assert pending_request.status == CorrectionRequest.Status.WITHDRAWN


def test_decided_request_cannot_be_decided_again(pending_request, group_admin):
    services.approve(pending_request, group_admin)
    pending_request.refresh_from_db()

    with pytest.raises(services.CorrectionError):
        services.reject(pending_request, group_admin, "Doch nicht.")
